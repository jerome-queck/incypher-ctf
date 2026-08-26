"""Intake — the Solver's copy of a Board, kept in step with one that moves underneath it.

**A sync, not a fetch.** A Board releases Challenges mid-event, adds a hint to a description at
13:00 and replaces a file that was wrong; re-running this is how the Solver notices any of it. So
every method here answers "what does the Board say *now*", and the previous answer is kept rather
than overwritten, because the two together are what "changed" means.

It touches no model and costs no tokens. What changed is decided by comparing **the Board's own
strings** — a file's URL carries a content hash, so a replaced file arrives at a new address and
nothing has to be downloaded to notice. Comparing our own digest of the bytes would mean fetching
every attachment on every cycle to find out that none of them moved.

Three refusals shape the rest:

- **An empty list is never an emptied Board.** Corroborated by ADR-0016's read-contract control, an
  uncorroborated one is a **failed sync** that keeps the previous snapshot. A Run ends when the
  window closes or it crashes, never because the Board looks finished, and the cheapest way to end
  one early is to believe a list that arrived from something that is not CTFd. The control's verdict
  is **sticky**: failing it is a read-contract fact rather than weather, and must never be retried
  into a pass.
- **A failed sync is a record, never an exception.** Nothing here raises at a caller for a Board
  fault: the Solver's answer to a Board it could not read is to keep working what it already has.
- **Category and CTFd `type` are open strings**, carried as read. Code that switches on a fixed
  list does not fail loudly when an event ships a category it has never met — it silently drops the
  Challenge (`CONTEXT.md`, *Category*).

The size cap on a fetch lives on the seam (`solver/board.py`) rather than here, because it is a
property of every fetch through it and not of the caller that happened to remember. What Intake
owns is the response: a refused attachment is **recorded against its Challenge** and the Challenge
is still worked, because 22 of Brunner's 74 Challenges ship no file at all and one more is not a
Challenge we stop reading.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from solver.board import ABSENT, ANSWERED, Board, BoardFailure, Mana, Standing, TooLarge
from solver.flag import Slots
from solver.instance import Terms
from solver.record import Recorder

# Every line this module writes about itself opens with this, for the reason `solver/recon.py`
# gives: a reader of a stream can tell what the Solver said from what the Board said.
MARK = "[intake]"

# How one cycle ended, named rather than left as a sentence for a caller to match on. The two
# failures are not interchangeable and spec #63 asks for opposite responses to them: a Board that
# fails the read contract at boot is a Board to refuse to start against, where the same failure
# mid-Run keeps the snapshot and carries on. A caller cannot make that split off a string.
#
# `detail` carries the sentence beside the name, because a post-mortem asking why a Run worked
# fourteen Challenges when the Board had sixty wants the reason on the line and not a lookup table.
SYNCED = "synced"
UNCORROBORATED = "uncorroborated"
UNREADABLE = "unreadable"

# The sentence behind `UNCORROBORATED`, beside the name so the two cannot drift apart.
UNCORROBORATED_SAYS = (
    "the list came back empty and this Board also answers 200 to a query CTFd refuses, so an empty "
    "collection here is evidence of nothing (ADR-0016) — keeping the previous snapshot"
)

# A file whose name the Board chose and we could not use. The Board names the file and we write it
# under `/state`, which makes the name an input: `../../codex/auth.json` is a path traversal into
# the one live credential on disk. The last path segment is taken and nothing else, and where that
# leaves nothing this is what the file is called.
UNNAMED = "attachment"

# The two `/mana` answers that are facts rather than weather: the plugin answering, and a 404 saying
# this Board does not have it. Everything else — the lock, a 403, an unreachable host — is asked
# again next cycle rather than remembered as a total of zero.
SETTLED_MANA = (ANSWERED, ABSENT)

# What happened to one listed file, named rather than left as a sentence, because the two ways of
# not holding one want **opposite** responses on the next cycle.
#
# `OVER_THE_CAP` is a decision and is never revisited: the file has not changed, our cap has not
# changed, and re-fetching it costs the whole cap again every cycle for the same refusal — 66 cycles
# of a 5.5-hour Run against a Board with three over-cap attachments is gigabytes spent to learn
# nothing. `UNFETCHED` is the Board or the network failing, and a 500 at 11:00 is not a 500 at 11:05,
# so it is tried again.
#
# Measured rather than reasoned: the first live run of `scripts/intake_probe.py` against Brunner
# reported a second cycle that changed nothing and still downloaded three files.
HELD = "held"
OVER_THE_CAP = "over-the-cap"
UNFETCHED = "unfetched"


@dataclass(frozen=True)
class Limits:
    """What one Intake cycle costs, as parameters rather than constants.

    Neither number is calibrated. `cycle_seconds` trades noticing a Challenge released at 13:00
    against one detail GET per Challenge per cycle on a rate-limited Board — the LIST payload
    carries no description, so prose is never free.
    """

    cycle_seconds: float = 300.0
    # Stored and never acted on in v1: the shape of the race, kept so that fitting the scoring
    # curve later needs no new Solver code (ADR-0015).
    scoreboard_top: int = 10


@dataclass(frozen=True)
class Attachment:
    """One file the Board lists for a Challenge, and where our copy of it landed.

    `identity` is the Board's own URL with any signature dropped, and it is the whole of change
    detection. The query is not part of a file's identity: where CTFd signs a file URL the
    signature is minted per read, so comparing whole URLs would report every file replaced on every
    cycle and re-download the Board once a minute.

    `outcome` is one of the three names above and `detail` is the sentence behind it. A file we do
    not hold is kept apart from "the Challenge has no files" on purpose: an attachment we decided
    not to fetch is a fact every later judgement about that Challenge is made without.
    """

    identity: str
    name: str
    path: Path | None = None
    nbytes: int = 0
    # The hosts a fetch passed through, empty where the Board served it inline. An attachment that
    # came from object storage is not a problem; one nobody noticed came from elsewhere is.
    hosts: tuple[str, ...] = ()
    outcome: str = HELD
    detail: str = ""

    @property
    def held(self) -> bool:
        return self.outcome == HELD and self.path is not None

    @property
    def settled(self) -> bool:
        """Whether asking again could change anything. A cap is our decision and a fault is not."""
        return self.held or self.outcome == OVER_THE_CAP


@dataclass(frozen=True)
class Sighting:
    """One Challenge as the Board described it this cycle, plus our copy of what it ships.

    This is the manifest Triage reads and the local copy every Attempt opens onto. `category` and
    `challenge_type` are carried as the open strings they are.

    `attempts` is our own submission count against this Challenge, **held server-side**, which
    makes it the one thing about a Run that survives the container being restarted — a count kept
    in memory would come back as zero and hand a limited Board a second full budget of wrong Flags.
    """

    challenge_id: int | str
    name: str
    category: str
    challenge_type: str
    value: int
    solves: int
    # Where the Board itself put this Challenge in the list, and **Order's last tie-break**. CTFd
    # sends the field in the LIST payload, so it costs no request — and it is read defensively for
    # a measured reason rather than a cautious one: Brunner sends `position` for all 74 Challenges
    # and every one of them is `0`. That is precisely why the rule is position *then* id — a Board
    # with no ordering of its own leaves the whole set tied and the id carries it, so the ranking
    # stays total and stable across a Run either way
    # ([ADR-0015](../docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)).
    position: int
    description: str
    attempts: int
    max_attempts: int | None
    solved: bool
    terms: Terms
    attachments: tuple[Attachment, ...] = ()
    # Whether the Board moved this Challenge since the last cycle — a new Challenge, an edited
    # description, a replaced or added file. `solves` and `value` move on their own every cycle and
    # are not a change to the Challenge, which is why they are snapshotted rather than diffed.
    changed: bool = True
    # The detail GET failed this cycle and the prose and manifest here are the last ones we read.
    # Recorded rather than hidden: a Tier extracted from a stale description is still a Tier, and a
    # reader has to be able to tell it from one extracted from what the Board says now.
    stale: bool = False

    @classmethod
    def of(
        cls,
        listed: Mapping[str, Any],
        detail: Mapping[str, Any],
        attachments: tuple[Attachment, ...],
        before: Sighting | None,
    ) -> Sighting:
        """One Challenge out of the Board's two payloads, with nothing decided that was not read.

        Which half a field comes from is the point. `solves`, `value`, `position` and
        `solved_by_me` are in the list, and the first two move every cycle; the description, `attempts`, `max_attempts` and the deploy terms
        are detail-only, and are what the per-Challenge GET is paid for. `category` and `type` come
        through as the open strings they are.

        **A detail GET that failed does not cost the Challenge**: every detail-side field is taken
        from the last cycle that read it and the Sighting is marked stale. The description matters
        because a Challenge with no description is one recon opens onto a filename
        (`solver/recon.py`); `attempts` matters more, because it is our own submission count and a
        zero here would hand a Board with limited attempts a second full budget of wrong Flags.
        """
        carried = before if not detail and before else None
        description = str(detail.get("description", "") or (carried.description if carried else ""))
        return cls(
            challenge_id=listed.get("id"),
            name=str(listed.get("name", "")),
            category=str(listed.get("category", "")),
            challenge_type=str(listed.get("type", "")),
            value=int(listed.get("value") or 0),
            solves=int(listed.get("solves") or 0),
            position=int(listed.get("position") or 0),
            description=description,
            attempts=carried.attempts if carried else int(detail.get("attempts") or 0),
            max_attempts=carried.max_attempts if carried else detail.get("max_attempts"),
            solved=bool(listed.get("solved_by_me", False)),
            terms=carried.terms if carried else Terms.of(detail or listed),
            attachments=attachments,
            changed=_moved(before, description, attachments),
            stale=carried is not None,
        )

    @property
    def slots(self) -> Slots:
        """This Challenge's submission budget as the Board states it, for the one caller that
        spends it. It is a property rather than two fields a caller pairs up itself, because the
        pair is only ever meaningful together — a count of what we have spent with no maximum
        beside it says nothing about whether another submission is affordable.

        A Sighting whose detail GET never succeeded carries `max_attempts` `None`, which the gate
        reads as **limited** — so the zero `attempts` underneath it can never buy a Challenge a
        second full budget of wrong Flags (`solver/flag.py`).
        """
        return Slots(self.max_attempts, self.attempts)


@dataclass(frozen=True)
class Snapshot:
    """What one cycle saw, or — where the sync failed — what the last one that worked saw.

    `outcome` is `SYNCED` on a snapshot that was read and one of the two failure names on one that
    was not, with `detail` carrying the sentence. Nothing here is ever half-believed: a failed sync
    answers with the previous snapshot's Challenges, so the Solver keeps working what it already has
    rather than watching the Board empty.
    """

    at: dt.datetime
    cycle: int
    challenges: tuple[Sighting, ...] = ()
    scoreboard: tuple[Standing, ...] = ()
    mana: Mana | None = None
    outcome: str = SYNCED
    detail: str = ""

    @property
    def believable(self) -> bool:
        return self.outcome == SYNCED

    @property
    def unsolved(self) -> tuple[Sighting, ...]:
        return tuple(one for one in self.challenges if not one.solved)

    @property
    def changed(self) -> tuple[Sighting, ...]:
        return tuple(one for one in self.challenges if one.changed)

    @property
    def mana_enabled(self) -> bool:
        """`total <= 0` is the feature switched off (`CONTEXT.md`, *Mana*), which is a different
        fact from having none left — and it short-circuits affordability entirely, so a Board with
        mana disabled costs no accounting at all."""
        return bool(self.mana and self.mana.total > 0)


class _Uncorroborated(BoardFailure):
    """The read-contract control did not corroborate an empty list.

    Its own class so that the one failure spec #63 wants a policy for — refuse to start at boot,
    keep the snapshot mid-Run — is told apart from every ordinary transport fault by type rather
    than by matching a sentence. It never leaves this module: `sync` turns it into the Snapshot's
    `UNCORROBORATED`, because a failed sync is an outcome Intake records and not one it raises.
    """


class Intake:
    """The Solver's copy of one Board, and the cycle that keeps it current.

    Holds exactly one thing between calls — the last snapshot it believed — because everything else
    is on the Board or on disk. `sync` never raises for a Board fault; it answers with a `Snapshot`
    that says whether it can be believed.
    """

    def __init__(
        self,
        board: Board,
        recorder: Recorder,
        *,
        limits: Limits = Limits(),
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self.root = Path(recorder.run_dir) / "intake"
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshot = Snapshot(at=dt.datetime.min.replace(tzinfo=dt.timezone.utc), cycle=0)
        self._board = board
        self._recorder = recorder
        self._limits = limits
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._cycle = 0
        self._synced_at: dt.datetime | None = None
        self._mana: Mana | None = None
        self._uncorroborated = False

    def due(self) -> bool:
        """Whether the cycle has come round. A sync that has never run is always due — the first
        one is the Run's whole picture of the Board, not a refresh of it."""
        if self._synced_at is None:
            return True
        return (self._now() - self._synced_at).total_seconds() >= self._limits.cycle_seconds

    def sync(self) -> Snapshot:
        """Re-read the Board, fetch what moved, and answer with what is now believed.

        The second run over an unmoved Board is a no-op except for the reads that discover that:
        the list, one detail GET per Challenge, the scoreboard, and no download at all.
        """
        self._cycle += 1
        try:
            snapshot = self._read()
        except _Uncorroborated as lying:
            return self._failed(UNCORROBORATED, str(lying))
        except (BoardFailure, OSError) as fault:
            return self._failed(UNREADABLE, f"{MARK} the Board could not be read — {fault}")
        self.snapshot = snapshot
        self._synced_at = snapshot.at
        self._record(snapshot)
        return snapshot

    def _read(self) -> Snapshot:
        listed = self._board.challenges()
        # Asked only where the list came back empty, which is the whole cost argument for the
        # control: a Board that lists Challenges never pays for it. And asked only *once* — ADR-0016
        # is explicit that failing it is not transient and must never be retried into a pass, so a
        # Board that failed at 12:00 does not get its empty list believed at 12:05.
        if not listed and (self._uncorroborated or not self._board.collection_endpoints_reach_ctfd()):
            self._uncorroborated = True
            raise _Uncorroborated(f"{MARK} {UNCORROBORATED_SAYS}")
        seen = {one.challenge_id: one for one in self.snapshot.challenges}
        challenges = tuple(self._sighting(entry, seen.get(entry.get("id"))) for entry in listed)
        return Snapshot(
            at=self._now(),
            cycle=self._cycle,
            challenges=challenges,
            scoreboard=self._scoreboard(),
            mana=self._read_mana_once(),
        )

    def _sighting(self, listed: dict[str, Any], before: Sighting | None) -> Sighting:
        """One Challenge: the list entry, the detail GET the list makes unavoidable, and the files.

        The I/O is here and the mapping is on `Sighting`, which is where `Terms.of` already puts the
        same job. A detail GET that failed re-fetches nothing, because the manifest we would fetch
        from is the manifest we did not see.
        """
        challenge_id = listed.get("id")
        try:
            detail = self._board.challenge(challenge_id)
        except (BoardFailure, OSError):
            detail = {}
        attachments = (
            before.attachments
            if not detail and before
            else self._attachments(challenge_id, detail.get("files") or [], before)
        )
        return Sighting.of(listed, detail, attachments, before)

    def _attachments(
        self, challenge_id: int | str, files: Sequence[Any], before: Sighting | None
    ) -> tuple[Attachment, ...]:
        """Every file the Board lists, fetching only the ones we do not already hold.

        A file is the same file when the Board's own address for it has not moved **and our copy is
        still on disk**. The second half is not belt-and-braces: `/state` is deletable mid-Run
        without costing the ability to solve, so an identity match over a file that is gone would
        leave a Challenge permanently missing its attachment.

        A file we decided not to fetch is settled in the same way one we hold is — see
        `OVER_THE_CAP` — and one that merely failed is not, so it is asked for again.
        """
        known = {one.identity: one for one in (before.attachments if before else ())}
        fetched = []
        for listing in files:
            identity = _identity(str(listing))
            already = known.get(identity)
            if already and already.settled and (already.path is None or already.path.exists()):
                fetched.append(already)
            else:
                fetched.append(self._fetch(challenge_id, str(listing), identity))
        return tuple(fetched)

    def _fetch(self, challenge_id: int | str, listing: str, identity: str) -> Attachment:
        name = _named(listing)
        try:
            payload, hops = self._board.download(listing)
        except TooLarge as over:
            return Attachment(identity, name, outcome=OVER_THE_CAP, detail=f"{MARK} {over}")
        except (BoardFailure, OSError) as fault:
            return Attachment(identity, name, outcome=UNFETCHED, detail=f"{MARK} {name} could not be fetched — {fault}")
        destination = self.root / str(challenge_id) / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return Attachment(identity, name, destination, len(payload), tuple(hops))

    def _scoreboard(self) -> tuple[Standing, ...]:
        """The top of the scoreboard, and no rows at all where it could not be read.

        Not an empty scoreboard: on a Board that fails the read contract this endpoint answers a
        canned empty payload, and ADR-0016 is explicit that such a Board contributes **no**
        scoreboard rather than zero rows.
        """
        try:
            return self._board.scoreboard(self._limits.scoreboard_top)
        except (BoardFailure, OSError):
            return ()

    def _read_mana_once(self) -> Mana | None:
        """Read once for the Run and branched on, never tracked (ADR-0007).

        Once is not an optimisation: `/mana` takes the same per-team lock as a deploy and **blocks**
        rather than failing while one is in flight, so a call on a cycle is a call that can hang for
        minutes behind an Attempt that is deploying.

        **Once means once it answered.** A read that failed is not a total, and remembering one
        would be ADR-0008's own named failure — a discovered profile discovering the wrong thing,
        with a transient `/mana` 403 as the example it had in mind. Cached, that fault reads as
        `total: 0`, which is *mana switched off*, and a mana-limited Board would then be treated as
        one with no cap at all for the rest of the Run. So only a settled answer is kept: the plugin
        answering, or a 404 that says this Board does not have it.
        """
        try:
            if self._mana is None or self._mana.outcome not in SETTLED_MANA:
                self._mana = self._board.mana()
        except (BoardFailure, OSError):
            self._mana = None
        return self._mana

    def _failed(self, outcome: str, detail: str) -> Snapshot:
        """A sync that did not happen, recorded, with the previous snapshot handed back unchanged.

        The cycle number moves and the snapshot does not, which is what makes a Run that spent an
        hour unable to read its Board legible afterwards.

        The cycle clock is stamped even though nothing was read, so a Board that is down is asked
        again on the next cycle rather than on the next pass of whatever loop is calling: a tight
        retry against a Board that stopped answering is a tight retry against its rate limiter.
        """
        self._synced_at = self._now()
        failed = replace(self.snapshot, at=self._now(), cycle=self._cycle, outcome=outcome, detail=detail)
        self._record(failed)
        return failed

    def _record(self, snapshot: Snapshot) -> None:
        self._recorder.intake(
            cycle=snapshot.cycle,
            challenges=[_as_record(one) for one in snapshot.challenges],
            scoreboard=[{"rank": one.rank, "name": one.name, "score": one.score} for one in snapshot.scoreboard],
            mana=_mana_record(snapshot),
            outcome=snapshot.outcome,
            detail=snapshot.detail,
        )


def _as_record(sighting: Sighting) -> dict[str, Any]:
    """One Challenge on the intake line: the `(solves, value)` pair, and what we hold of it.

    The description is not here. It is on the Board, it is on disk, and a Board's prose copied into
    every cycle's record would be most of the stream by volume for a fact that has not changed.
    """
    return {
        "challenge_id": sighting.challenge_id,
        "name": sighting.name,
        "category": sighting.category,
        "type": sighting.challenge_type,
        "value": sighting.value,
        "solves": sighting.solves,
        "attempts": sighting.attempts,
        "max_attempts": sighting.max_attempts,
        "solved": sighting.solved,
        "changed": sighting.changed,
        "stale": sighting.stale,
        "files": [
            {"name": one.name, "bytes": one.nbytes, "hosts": list(one.hosts), "outcome": one.outcome}
            for one in sighting.attachments
        ],
    }


def _mana_record(snapshot: Snapshot) -> dict[str, Any] | None:
    mana = snapshot.mana
    if mana is None:
        return None
    return {"outcome": mana.outcome, "used": mana.used, "total": mana.total, "enabled": snapshot.mana_enabled}


def _moved(before: Sighting | None, description: str, attachments: tuple[Attachment, ...]) -> bool:
    """Whether this Challenge is different from the one we last saw.

    A Challenge we have never seen has changed, which is what makes the first cycle report the whole
    Board rather than nothing.
    """
    if before is None:
        return True
    was = tuple(one.identity for one in before.attachments)
    return before.description != description or was != tuple(one.identity for one in attachments)


def _identity(listing: str) -> str:
    """A file's address with any signature dropped — the string change detection turns on.

    CTFd hands out `<hash>/<name>?token=<signed>`, and the signature is minted per read: it carries
    its own timestamp and differs on every list even when the file behind it has not moved. Keeping
    it in the identity would make every cycle re-download the Board.
    """
    return listing.split("?", 1)[0]


def _named(listing: str) -> str:
    """What to call the local copy, taking the last path segment and nothing else.

    The Board chooses this name and we write it under `/state`, which makes it an input rather than
    a label: `../../codex/auth.json` is a path traversal into the one live credential on disk. The
    separator is stripped from what is left for the same reason, and a segment that names nothing
    at all falls back rather than writing to the directory itself.
    """
    segment = PurePosixPath(_identity(listing)).name.replace("/", "_").replace("\\", "_").strip()
    return segment if segment not in ("", ".", "..") else UNNAMED
