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

from solver.board import Board, Mana
from solver.boot import MARK as BOOT
from solver.boot import Refusal
from solver.credentials import NOT_SECRETS, SECRETS

MARK = "[profile]"

# Where the tracked profiles land in the image. ADR-0008 bakes the event's `.board.json` in beside
# `solver/`, and every event's is baked rather than one: which Board this Run plays is decided by
# `CTFD_URL` at run time, and an image carrying only one Board's file would be an image per event.
BOARDS = Path("/opt/solver/boards")
SUFFIX = ".board.json"

# What an `event` may be, now that it names a directory under `/state/work` and not only a tracked
# file (ADR-0025). A separator or a `..` in it would put a Board's memory outside the root that is
# namespacing it, which is the failure the namespace exists to prevent.
EVENT_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# CTFd's own default, assumed because `/api/v1/configs` is admin-only on every Board we hold a token
# for. Named as an assumption rather than folded into a default argument, because the profile has to
# be able to say which of the two it is.
CTFD_DEFAULT_INCORRECT_PER_MIN = 10
STATED = "stated"
ASSUMED = "assumed"

ANONYMOUS_ANSWERED = "answered"
ANONYMOUS_REFUSED = "refused"
ANONYMOUS_UNREADABLE = "unreadable"

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
REQUIRED_KEYS = frozenset({"event", "url", "flag_wrappers", "window_seconds", "prohibitions"})
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
    # A list, and never one pattern built out of several: joining two shapes into `a|b` loses the
    # Flags the first would have found, which is `solver/wrapper.py`'s whole subject. The first
    # entry is the Board's primary shape.
    flag_wrappers: tuple[str, ...]
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
    unauthenticated_read: str
    mana: Mana | None
    mana_outcome: str
    submissions_per_minute: int
    submissions_per_minute_source: str
    configs_outcome: str
    # What the Board itself says its event window is, kept beside the rules-derived one rather than
    # replacing it. The two genuinely disagree: IN-CYPHER's CTFd window runs to 22 Sep 00:00 SGT
    # while the scored Run is 10:30–16:00 that same day, so the wire value is the practice window
    # and taking it would end the Run before it started.
    board_window: dict[str, Any] = field(default_factory=dict)
    board_window_observations: dict[str, Any] = field(default_factory=dict)
    authenticated_user_id: int = 0
    authenticated_team_id: int | None = None
    instance_ledger_mode: str = ""

    @property
    def instances_reachable(self) -> bool:
        """Whether a leak sweep can run at all. `Board.instances_held` raises on a Board with no
        plugin, and the Run's tail must not die reclaiming Instances that could never exist."""
        return self.chall_manager == INSTALLED

    def recorded(self) -> dict[str, Any]:
        return {
            "event": self.rules.event,
            "url": self.rules.url,
            "flag_wrappers": list(self.rules.flag_wrappers),
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
            "mana_outcome": self.mana_outcome,
            "submissions_per_minute": self.submissions_per_minute,
            "submissions_per_minute_source": self.submissions_per_minute_source,
            "configs_outcome": self.configs_outcome,
            "board_window": self.board_window,
            "board_window_observations": self.board_window_observations,
            "authenticated_user_id": self.authenticated_user_id,
            "authenticated_team_id": self.authenticated_team_id,
            "instance_ledger_mode": self.instance_ledger_mode,
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
    """Compatibility adapter into the same qualifier the production broker owns."""

    from solver.board_profile import qualify_direct

    decision = qualify_direct(board, anyone, rules)
    if not decision.authoritative or decision.profile is None:
        raise Refusal(f"{BOOT} Board profile is incompatible — {decision.reason}")
    return decision.profile


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
        event=_event(path, document["event"]),
        url=str(document["url"]).rstrip("/"),
        flag_wrappers=_wrappers(path, document["flag_wrappers"]),
        window_seconds=float(document["window_seconds"]),
        prohibitions=tuple(str(one) for one in document["prohibitions"]),
        closes_at=_moment(path, document.get("closes_at")),
        web_search=bool(document.get("web_search", True)),
        requires=_requires(path, document.get("requires") or ()),
    )


def _event(path: Path, stated: Any) -> str:
    """The event's name, which since ADR-0025 is also a path segment.

    Checked here rather than where the path is built, because `Rules` is what leaves this module and
    a name refused at boot costs a Run nothing — while a Board whose working directories landed
    outside `/state/work/<event>/` is found afterwards, if at all.
    """
    name = str(stated)
    if EVENT_SEGMENT.fullmatch(name) is None:
        raise Refusal(f"{BOOT} {path} states the event {name!r}, which is not usable as a directory name (ADR-0025)")
    return name


def _wrappers(path: Path, stated: Any) -> tuple[str, ...]:
    """The Flag shapes this Board states, in the order it states them.

    A non-empty list, because a Board with no wrapper is a Board nothing could ever be swept for,
    and that is a refusal at boot rather than a Run that quietly finds nothing. A repeat is refused
    too: it buys no match a single entry would not, and costs a second full pass over every artefact
    the cascade reads.
    """
    if not isinstance(stated, list) or not stated:
        raise Refusal(f"{BOOT} {path} states flag_wrappers {stated!r}, which is not a non-empty list of patterns")
    named = tuple(str(one) for one in stated)
    if repeated := sorted({one for one in named if named.count(one) > 1}):
        raise Refusal(f"{BOOT} {path} states the Flag wrapper {', '.join(repr(one) for one in repeated)} twice")
    return named


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
