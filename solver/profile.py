"""The Board profile — read off the live Board wherever it can be, configured only where it cannot.

`CONTEXT.md` (*Board profile*) is the definition and ADR-0008 is the mechanism: a configured value
we could have measured goes stale without saying so, so the only things a tracked
`docs/competitions/<event>.board.json` carries are the ones that exist in prose and nowhere in the
API — the Board's address, the Flag wrapper its rules state, what those rules forbid, and how long
the event runs. Everything else here is asked of the Board at startup.

ADR-0008 names the failure this module is shaped around: **a discovered profile can discover the
wrong thing**, with a transient `/mana` 403 reading as *mana disabled* as the shape it had in mind.
So every discovery below either answers with a settled fact or refuses the Run. Nothing here guesses
and nothing here retries a control into a pass.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from solver.board import INSTANCE_LEDGER, Board, BoardFailure, Mana
from solver.boot import MARK as BOOT
from solver.boot import Refusal
from solver.credentials import NOT_SECRETS, SECRETS
from solver.instance import INSTANCED_TYPE
from solver.intake import SETTLED_MANA

MARK = "[profile]"

# Where the tracked profiles land in the image. ADR-0008 bakes the event's `.board.json` in beside
# `solver/`, and every event's is baked rather than one: which Board this Run plays is decided by
# `CTFD_URL` at run time, and an image carrying only one Board's file would be an image per event.
BOARDS = Path("/opt/solver/boards")
SUFFIX = ".board.json"

# CTFd's own default, assumed because `/api/v1/configs` is admin-only on every Board we hold a token
# for. Named as an assumption rather than folded into a default argument, because the profile has to
# be able to say which of the two it is.
CTFD_DEFAULT_INCORRECT_PER_MIN = 10
STATED = "stated"
ASSUMED = "assumed"

# Whether `ctfd-chall-manager` is installed, in ADR-0008's own three words. Installed is a ledger we
# can read and therefore a leak sweep that works; absent is a Board without the plugin, which is no
# kind of fault; and unreadable is the third state — *"discovery has to distinguish absent from
# failed, and where it cannot, it fails the run rather than guessing"*.
INSTALLED = "installed"
ABSENT = "absent"
UNREADABLE = "unreadable"

# Every key a tracked profile may carry. Read strictly, because the failure mode of a permissive
# reader is a mistyped key silently taking its default — which is a prohibition that never reached a
# prompt, on a Board whose rules make one of them an immediate ban.
REQUIRED_KEYS = frozenset({"event", "url", "flag_wrapper", "window_seconds", "prohibitions"})
OPTIONAL_KEYS = frozenset({"closes_at", "web_search", "requires"})


@dataclass(frozen=True)
class Rules:
    """One Board's rules as facts, which is the whole of what a tracked file is allowed to carry.

    Not *config* and not *the profile* — `CONTEXT.md` is explicit that this file is one input to a
    Board profile rather than the profile itself. Every field here was read out of a rules page by a
    human and exists nowhere in the API: the wrapper Brunner states in prose, the window the
    organisers announced, the ban on broad automated enumeration whose sanction is an immediate ban,
    and the ban on sandbagging.
    """

    event: str
    url: str
    flag_wrapper: str
    window_seconds: float
    prohibitions: tuple[str, ...] = ()
    closes_at: dt.datetime | None = None
    # Declared names this Board's Run cannot start without. The team key gates IN-CYPHER's raw-TCP
    # Challenges and Brunner has never heard of it, so *which* credentials a Run needs is a fact
    # about the Board and belongs here rather than in one list the boot check hardcodes.
    requires: tuple[str, ...] = ()
    # ADR-0014 makes web search a profile value, default on: every advantage counts on a 5.5-hour
    # clock, and a Challenge shipping an image or an audio clip is often solvable only by looking
    # something up. A Board whose rules withdraw it sets this false, and `check-rules-drift.sh` is
    # what catches the rules change that should have.
    web_search: bool = True


@dataclass(frozen=True)
class Profile:
    """Everything this Run knows about the Board it is pointed at, rules and wire together.

    Written whole at run-open, because ADR-0008's named failure needs a post-mortem to be able to
    tell *the Solver behaved wrongly* from *the Solver read the Board wrongly* — and those want
    completely different fixes.
    """

    rules: Rules
    chall_manager: str
    instanced_challenges: int
    unauthenticated_read: bool
    mana: Mana | None
    submissions_per_minute: int
    submissions_per_minute_source: str
    # What the Board itself says its event window is, kept beside the rules-derived one rather than
    # replacing it. The two genuinely disagree: IN-CYPHER's CTFd window runs to 22 Sep 00:00 SGT
    # while the scored Run is 10:30–16:00 that same day, so the wire value is the practice window
    # and taking it would end the Run before it started.
    board_window: dict[str, Any] = field(default_factory=dict)

    @property
    def instances_reachable(self) -> bool:
        """Whether a leak sweep can run at all. `Board.instances_held` raises on a Board with no
        plugin, and the Run's tail must not die reclaiming Instances that could never exist."""
        return self.chall_manager == INSTALLED

    def recorded(self) -> dict[str, Any]:
        return {
            "event": self.rules.event,
            "url": self.rules.url,
            "flag_wrapper": self.rules.flag_wrapper,
            "window_seconds": self.rules.window_seconds,
            "closes_at": self.rules.closes_at.isoformat() if self.rules.closes_at else None,
            "web_search": self.rules.web_search,
            "prohibitions": list(self.rules.prohibitions),
            "chall_manager": self.chall_manager,
            "instanced_challenges": self.instanced_challenges,
            "unauthenticated_read": self.unauthenticated_read,
            "mana": None
            if self.mana is None
            else {"outcome": self.mana.outcome, "used": self.mana.used, "total": self.mana.total},
            "submissions_per_minute": self.submissions_per_minute,
            "submissions_per_minute_source": self.submissions_per_minute_source,
            "board_window": self.board_window,
        }


def tracked(directory: Path = BOARDS) -> list[Rules]:
    """Every event's tracked profile in one directory, read.

    Public because *which Boards did this image ship rules for* is a question with two askers that
    are not this module: the pre-flight check inside the built image, and `rules_for` below.
    """
    return [_read(path) for path in sorted(Path(directory).glob(f"*{SUFFIX}"))]


def rules_for(url: str, directory: Path = BOARDS) -> Rules:
    """The tracked profile for the Board `CTFD_URL` points at, and a refusal where there is not one.

    Selection is by URL rather than by a second variable naming the file, which is what keeps
    `CTFD_URL` the one guard `docs/credentials.md` claims it is: an image carrying Brunner's rules
    and pointed at the Danish board matches nothing and refuses, instead of playing a strict no-AI
    board under a profile that says AI is fine.
    """
    wanted = url.rstrip("/")
    known_boards = tracked(directory)
    found = [rules for rules in known_boards if rules.url == wanted]
    if not found:
        known = ", ".join(sorted(rules.url for rules in known_boards)) or "nothing"
        raise Refusal(
            f"{BOOT} no Board profile in {directory} is for {wanted} — the image knows {known}. A Board "
            f"we have never met needs a `{SUFFIX}` holding its URL and what its rules forbid (ADR-0008)"
        )
    if len(found) > 1:
        raise Refusal(f"{BOOT} {len(found)} profiles in {directory} claim {wanted}, so which rules bind is undecided")
    return found[0]


def discovered(board: Board, anyone: Board, rules: Rules) -> Profile:
    """Ask the Board everything about itself that it can answer, and refuse where it cannot.

    A Board that does not answer at all refuses the Run like every other missing fact, rather than
    reaching the entry point as a traceback: at boot there is a human present and a sentence is
    worth more to them than a stack. Mid-Run the same fault is Intake's and is handled the opposite
    way, because by then there is a snapshot worth keeping and nobody to read a sentence.
    """
    try:
        return _asked(board, anyone, rules)
    except (BoardFailure, OSError) as unreachable:
        raise Refusal(f"{BOOT} the Board could not be read at boot — {unreachable}") from None


def _asked(board: Board, anyone: Board, rules: Rules) -> Profile:
    """The discovery itself.

    `anyone` is the same Board addressed with no token: whether an unauthenticated read is answered
    is a profile field, and asking it needs a second address rather than a flag, because the token
    is applied by the seam and not by the caller.

    The read-contract control is asked here and **first**, unconditionally. Intake asks it only
    where a list came back empty, which is the right rule mid-Run and the wrong one at boot: `solves`
    and `value` ride the same LIST payload, so a Board that answers a canned success would let Order
    rank an empty set while reporting success for five and a half hours (ADR-0016).
    """
    if not board.collection_endpoints_reach_ctfd():
        raise Refusal(
            f"{BOOT} this Board answered 200 to a query CTFd validates and must refuse, so its replies "
            f"were not composed by CTFd and an empty collection from it means nothing. Failing this is "
            f"not transient and is never retried into a pass (ADR-0016)"
        )
    _compiles(rules.flag_wrapper)
    listed = board.challenges()
    instanced = sum(1 for one in listed if one.get("type") == INSTANCED_TYPE)
    chall_manager = _ledger(board)
    if instanced and chall_manager != INSTALLED:
        raise Refusal(
            f"{BOOT} {instanced} Challenge(s) are {INSTANCED_TYPE} but the Instance ledger reads "
            f"{chall_manager} — a Run that deploys what it cannot sweep leaks capacity nobody reclaims, "
            f"because chall-manager never evicts (ADR-0007)"
        )
    configs = _configs(board)
    limit, source = _submission_limit(configs)
    return Profile(
        rules=rules,
        chall_manager=chall_manager,
        instanced_challenges=instanced,
        unauthenticated_read=_answers_anyone(anyone),
        mana=_mana(board, chall_manager),
        submissions_per_minute=limit,
        submissions_per_minute_source=source,
        board_window={key: configs[key] for key in ("start", "end") if configs.get(key) not in (None, "")},
    )


def _read(path: Path) -> Rules:
    """One tracked profile, read strictly — an unknown key is a refusal rather than a default.

    The permissive reading is the dangerous one: `"prohibition"` for `"prohibitions"` would leave a
    Board's rules out of every Attempt prompt while the file still looked right, and on Brunner one
    of those rules carries an immediate ban.
    """
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError) as unreadable:
        raise Refusal(f"{BOOT} {path} could not be read — {unreadable}") from None
    if not isinstance(document, dict):
        raise Refusal(f"{BOOT} {path} is not a JSON object")
    if missing := sorted(REQUIRED_KEYS - set(document)):
        raise Refusal(f"{BOOT} {path} states no {', '.join(missing)}")
    if unknown := sorted(set(document) - REQUIRED_KEYS - OPTIONAL_KEYS):
        raise Refusal(f"{BOOT} {path} carries {', '.join(unknown)}, which this profile has no meaning for")
    if float(document["window_seconds"]) <= 0:
        raise Refusal(f"{BOOT} {path} states a window of {document['window_seconds']}, which buys no Attempt at all")
    return Rules(
        event=str(document["event"]),
        url=str(document["url"]).rstrip("/"),
        flag_wrapper=str(document["flag_wrapper"]),
        window_seconds=float(document["window_seconds"]),
        prohibitions=tuple(str(one) for one in document["prohibitions"]),
        closes_at=_moment(path, document.get("closes_at")),
        web_search=bool(document.get("web_search", True)),
        requires=_requires(path, document.get("requires") or ()),
    )


def _requires(path: Path, stated: Any) -> tuple[str, ...]:
    """The credentials this Board demands, each one a name the declared set already knows.

    An undeclared name is refused rather than carried: the boot check reads its holdings from
    `solver/credentials.py`, so a profile asking for a variable nobody declared would be asking for
    something that is absent by construction and could never be satisfied.
    """
    named = tuple(str(one) for one in stated)
    if unknown := sorted(set(named) - set(SECRETS) - set(NOT_SECRETS)):
        raise Refusal(f"{BOOT} {path} requires {', '.join(unknown)}, which `solver/credentials.py` does not declare")
    return named


def _moment(path: Path, stated: Any) -> dt.datetime | None:
    """An absolute moment, or nothing. An offset is mandatory: a naive datetime here would be read
    in the container's timezone, and the container's is UTC while every event window we hold is
    written in the venue's."""
    if stated in (None, ""):
        return None
    try:
        moment = dt.datetime.fromisoformat(str(stated))
    except ValueError:
        raise Refusal(f"{BOOT} {path} states closes_at {stated!r}, which is not an ISO moment") from None
    if moment.tzinfo is None:
        raise Refusal(f"{BOOT} {path} states closes_at {stated!r} with no timezone offset")
    return moment


def _compiles(wrapper: str) -> None:
    """A wrapper that does not compile sweeps nothing, and does it silently — `solver/flag.py`
    records the failure as an Observation inside an Attempt already in flight, which is correct
    there and five and a half hours too late here."""
    try:
        re.compile(wrapper)
    except re.error as broken:
        raise Refusal(
            f"{BOOT} the Flag wrapper {wrapper!r} does not compile — {broken}. Nothing would ever be swept"
        ) from None


def _ledger(board: Board) -> str:
    """Whether chall-manager's team ledger answers us, as one of three names.

    A 404 is a Board without the plugin and is no kind of fault; anything else — a refusal, a
    redirect to a login page, a socket that never answered — is a ledger we cannot read, and the two
    are kept apart because only the second one can ever be a reason to refuse a Run.
    """
    try:
        status, _raw, _location = board.request("GET", INSTANCE_LEDGER)
    except OSError:
        return UNREADABLE
    if status == 404:
        return ABSENT
    if status != 200:
        return UNREADABLE
    try:
        board.instances_held()
    except BoardFailure:
        return UNREADABLE
    return INSTALLED


def _mana(board: Board, chall_manager: str) -> Mana | None:
    """The concurrency cap, read once, and only where there is a plugin to read it from.

    An unsettled answer refuses the Run. This is ADR-0008's own named failure and the reason this
    module exists in the shape it does: a transient 403 kept as a total reads as `total: 0`, which is
    *mana switched off*, and a mana-limited Board would then be treated as one with no cap at all
    for the rest of the Run.
    """
    if chall_manager != INSTALLED:
        return None
    reading = board.mana()
    if reading.outcome not in SETTLED_MANA:
        raise Refusal(
            f"{BOOT} /mana answered {reading.outcome} — {reading.detail}. That is neither a total nor "
            f"this Board saying it has no mana, and keeping it would read as the feature switched off"
        )
    return reading


def _answers_anyone(anyone: Board) -> bool:
    """Whether the Board serves its Challenge list with no token at all — a fact about the Board and
    never a route we take: every read in a Run is authenticated, because our own submission counts
    ride the authenticated payload."""
    try:
        return bool(anyone.challenges())
    except (BoardFailure, OSError):
        return False


def _submission_limit(configs: dict[str, Any]) -> tuple[int, str]:
    """The Board-wide wrong-submissions-per-minute cap, and whether it was read or assumed.

    An admin-only endpoint refusing us is **absent** rather than failed — it is the Board saying we
    may not read this, and CTFd's own default is the documented answer to that. What would be a
    fault is the Board not answering at all, and that no longer reaches here: `discovered` turns it
    into a Refusal. Which of the two happened is on the record either way, because a Run paced at a
    number nobody stated and one paced at the Board's own are different Runs.
    """
    stated = configs.get("incorrect_submissions_per_min")
    if stated in (None, ""):
        return CTFD_DEFAULT_INCORRECT_PER_MIN, ASSUMED
    try:
        return int(stated), STATED
    except (TypeError, ValueError):
        return CTFD_DEFAULT_INCORRECT_PER_MIN, ASSUMED


def _configs(board: Board) -> dict[str, Any]:
    """`/api/v1/configs` as a mapping, read **once** — the submission limit and the Board's own
    event window are two fields of one document, and asking twice is two round-trips for one answer.

    Empty where it answered anything else. It is admin-only on every Board we hold a token for, so
    the empty case is the rule rather than the exception, and a body that does not parse is an
    unread config rather than a Board fault.
    """
    status, raw, _location = board.request("GET", "/api/v1/configs")
    if status != 200:
        return {}
    try:
        document = json.loads(raw)
    except ValueError:
        return {}
    entries = document.get("data") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return {}
    return {str(entry.get("key")): entry.get("value") for entry in entries if isinstance(entry, dict)}
