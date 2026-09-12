"""The one place that knows how a CTFd board is talked to.

Everything above this module deals in challenges, files and verdicts; nothing above it imports
`urllib` or learns what wire format an answer arrived in
([ADR-0008](../docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).

The four transport rules here each cost a day to find, and each fails *silently* when it is
missing — the board answers, the JSON parses, and the caller concludes there is nothing to solve.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

# Enough for CTFd -> object storage and a storage-side hop. More than that is a loop,
# and a client that chases one indefinitely hangs instead of reporting.
MAX_FILE_REDIRECTS = 4

# The ceiling on any single fetch through this seam, and the reason it is a transport rule rather
# than a caller's discipline: Brunner ships one `.7z` of **6.15 GB**, linked from description prose
# on a different host, and a Solver that fetches it has spent the Run on one Challenge's download.
# A caller that had to remember the cap is a caller that will one day forget it on the one path
# that matters.
#
# It is enforced **where the bytes are** — `_over_the_network` reads one byte past it and no more —
# so an over-large fetch costs a bounded read rather than a bounded check over an unbounded read.
# Over the cap is **refused and never truncated**: half an archive written to `/state` under the
# right name is an artefact every later Step reasons over as though it were the file.
#
# It bounds **every** response through this seam and not only a file's, which is deliberate: a
# reply big enough to matter is a reply nobody meant to send us. An API answer clipped at the cap
# fails loudly at the JSON parse rather than parsing short, so the cap cannot quietly become a
# Board that lists fewer Challenges than it has.
#
# The number is where it is because the body arrives whole, in memory, on the way to disk: a
# gigabyte here would be a gigabyte resident inside a container whose memory nobody is watching at
# 14:00. Like every other number in v1 it is a parameter, and a Board that legitimately ships more
# than this raises it at the call site rather than losing the file silently.
MAX_FETCH_BYTES = 256 * 1024 * 1024

# The two collection endpoints Intake reads every cycle, and the detail GET the LIST payload makes
# unavoidable — the list carries no description, and prose is the one place a password or a second
# download host is ever written down.
CHALLENGES = "/api/v1/challenges"
SCOREBOARD_TOP = "/api/v1/scoreboard/top"

# ADR-0016's read-contract control. `field` is validated against an enumeration before any handler
# runs, so CTFd answers an unknown one with a refusal and **a 200 is proof the reply came from
# somewhere else**. `q` is sent with it because CTFd only reaches that validation on a search.
READ_CONTRACT_CONTROL = f"{CHALLENGES}?field=intake-is-not-a-field&q=a"

# CTFd boards sit behind Cloudflare, which 403s `Python-urllib/3.x` before the request ever reaches
# the application. Unset, every call below fails as though the token were rejected. Any HTTP client
# the Solver uses announces itself as a browser or it never sees the board.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


# Every team operation on an Instance sits on one resource, addressed by `challengeId`; the
# ledger is not on the API at all and answers HTML.
CHALL_MANAGER = "/api/v1/plugins/ctfd-chall-manager"
INSTANCE_LEDGER = "/plugins/ctfd-chall-manager/instances"
INSTANCE_LEDGER_API = f"{CHALL_MANAGER}/instances"

# What a chall-manager call answered, named rather than numbered. The plugin says three different
# things through one 403, says "you already hold this" through a 200 that reads like a success, and
# 429s from a lock another Challenge is holding — so a caller branching on the status code branches
# on the wrong thing, which is the whole reason these names exist here rather than above.
ANSWERED = "answered"
DENIED = "denied"
REFUSED = "refused"
LOCKED = "locked"
ABSENT = "absent"
UNREACHABLE = "unreachable"

# Where a submitted Flag is graded, and the vocabulary CTFd grades it in. The status code is not the
# verdict and never agrees with it: `correct` and `incorrect` both answer 200, the rate limiter
# answers 429 and a paused Board 403, and every one of them carries the verdict in `data.status`.
# The names are here rather than above this seam because they are CTFd's wire words — a caller that
# had to know them would be a caller holding a wire format ADR-0008 puts here.
ATTEMPT = "/api/v1/challenges/attempt"

CORRECT = "correct"
INCORRECT = "incorrect"
ALREADY_SOLVED = "already_solved"
RATE_LIMITED = "ratelimited"
PAUSED = "paused"
# Ours rather than CTFd's: the Board answered something no verdict could be read out of. It is not
# `incorrect` — a Flag nobody graded must never be recorded as one that was graded wrong.
UNREAD = "unread"


@dataclass(frozen=True)
class Verdict:
    """How the Board graded one submitted Flag.

    `http_status` is carried so a post-mortem can see what the wire said, and is never what the
    verdict is read from: a wrong Flag answers 200 carrying `incorrect`, and a Solver reading the
    status would record it as a solve.
    """

    outcome: str
    message: str = ""
    http_status: int = 0

    @property
    def correct(self) -> bool:
        """Whether **this Flag** was right, which is a narrower thing than the Challenge being ours
        (see `solved`) and is the only verdict that says anything about the string we sent."""
        return self.outcome == CORRECT

    @property
    def incorrect(self) -> bool:
        """Whether the Board read this Flag and said no.

        Narrower than `not correct`, and the difference is the whole point: a Board that was rate
        limited, paused, or answered something no verdict could be read out of has declined to
        answer rather than answered, and only an answer says the Solver was wrong.
        """
        return self.outcome == INCORRECT

    @property
    def solved(self) -> bool:
        """Whether the Challenge is this team's now — so there is nothing left here to win.

        `already_solved` says exactly that and says **nothing about the Flag**: Brunner answered a
        deliberately wrong Flag against a solved Challenge with `already_solved` and the message
        *"Incorrect but you already solved this"* (read live, 25 Aug 2026). A caller that treated the
        two as one verdict would record a junk string as the Flag that solved a Challenge.
        """
        return self.outcome in (CORRECT, ALREADY_SOLVED)

    @property
    def spent_a_slot(self) -> bool:
        """Whether this submission cost one of the Challenge's attempts — the Fail CTFd counts
        `max_attempts` in and states back as `attempts`.

        Read as *anything the Board did not refuse outright*, which puts `unread` on the counted
        side: a submission no verdict could be read out of may well have reached CTFd and been
        graded, and a caller keeping a floor under the Board's count has to assume it did. Only
        `ratelimited` and `paused` are answers instead of gradings, and a solve is not a Fail.

        It is a property here rather than a comparison at the call site for the reason the wire
        words above it are not exported: the question is *did this cost a slot*, and a caller that
        had to spell it as an outcome name would be a caller holding a wire format (ADR-0008).
        """
        return self.outcome not in (CORRECT, ALREADY_SOLVED, RATE_LIMITED, PAUSED)


@dataclass(frozen=True)
class Reply:
    """What one call on the Instance resource answered.

    `until` arrives as a moment rather than as the RFC3339 the plugin wrote, because repairing that
    text is exactly the wire format this module exists to stop at its own edge.

    `detail` is the plugin's own message where it sent one, because the Solver writes it into the
    record and a shape without the sentence that produced it is unreviewable afterwards.
    """

    outcome: str
    connection_info: str = ""
    until: dt.datetime | None = None
    detail: str = ""


@dataclass(frozen=True)
class Mana:
    """chall-manager's concurrency cap, as it reports it. `total <= 0` is the feature switched off
    (`CONTEXT.md`, *Mana*), which is a different fact from having none left."""

    outcome: str
    used: int = 0
    total: int = 0
    detail: str = ""


@dataclass(frozen=True)
class Held:
    """One row of the team-scoped ledger — an Instance this team holds right now.

    The challenge name and nothing else: the page keys its rows by name, and renders `until`
    through a template that truncates it to whole seconds and strips the timezone, so a deadline
    taken from here would be quietly wrong by up to an hour. Existence is this page's question and
    the deadline is the deploy response's, and neither is ever asked the other's
    ([ADR-0007](../docs/adr/0007-truth-about-an-instance-lives-on-the-board.md)).
    """

    challenge_name: str


@dataclass(frozen=True)
class Standing:
    """One row of the scoreboard, as this Board publishes it.

    The rank is the Board's own and is kept rather than recomputed from `score`, because the two
    can disagree — a Board that breaks ties on solve time orders rows by a fact the score does not
    carry, and re-sorting here would quietly invent a different scoreboard from the one being
    played on.
    """

    rank: int
    name: str
    score: int


# `Board`'s one edge to the network: it is handed a request with every transport rule already
# applied, and answers with the status, the body and any `Location`. Injectable because that is
# the honest place to stand a test — above it are this module's rules, below it is a socket.
TransportResult = tuple[int, bytes, str] | tuple[int, bytes, str, str]
Transport = Callable[[urllib.request.Request], TransportResult]


@dataclass(frozen=True)
class BoardDocument:
    """One raw Board answer where profile qualification needs response metadata."""

    status: int
    body: bytes
    location: str
    content_type: str


class BoardFailure(Exception):
    """The board answered in a way the Solver would have silently mis-read."""


class TooLarge(BoardFailure):
    """A fetch was over the cap, and so was refused rather than truncated.

    Its own class because the two responses are opposite: a `BoardFailure` on a download is a
    Challenge whose file we could not read and should try again for, and this is a Challenge whose
    file we have **decided** not to read — retrying it is spending the Run twice on the same
    refusal. A caller records it against the Challenge and carries on.
    """


class Board:
    """A CTFd board addressed the way the Solver addresses it.

    API redirects are never followed. CTFd answers an unauthenticated API request with a 302 to
    /login, and a follower turns that into a 200 holding an HTML login page — the exact shape
    that reads downstream as "the board has no challenges". `download` is the one exception, and
    says why.
    """

    def __init__(
        self,
        url: str,
        token: str,
        transport: Transport | None = None,
        *,
        fetch_bytes: int = MAX_FETCH_BYTES,
    ) -> None:
        self.url = url.rstrip("/")
        self.fetch_bytes = fetch_bytes
        self._token = token
        self._transport = transport or _over_the_network(fetch_bytes)

    @property
    def authenticated(self) -> bool:
        """Some boards serve challenges to anyone, so an absent token narrows what a caller can
        prove rather than stopping it."""
        return bool(self._token)

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, json_content_type: bool = True
    ) -> tuple[int, bytes, str]:
        answer = self.inspect(method, path, body, json_content_type=json_content_type)
        return answer.status, answer.body, answer.location

    def inspect(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, json_content_type: bool = True
    ) -> BoardDocument:
        """Return the metadata needed to distinguish a contract from a same-status catch-all."""

        payload = json.dumps(body).encode() if body is not None else None
        url = path if path.startswith("http") else f"{self.url}{path}"
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("User-Agent", BROWSER_USER_AGENT)
        # The token authenticates us to the board and to nobody else. A file redirect lands on
        # third-party object storage carrying its own presigned credentials, and forwarding ours
        # there would hand a working CTFd token to a host that never asked for it.
        if self._token and urllib.parse.urlparse(url).netloc == urllib.parse.urlparse(self.url).netloc:
            request.add_header("Authorization", f"Token {self._token}")
        request.add_header("Accept", "application/json")
        if json_content_type:
            request.add_header("Content-Type", "application/json")
        result = self._transport(request)
        if len(result) == 3:
            status, raw, location = result
            content_type = ""
        else:
            status, raw, location, content_type = result
        return BoardDocument(status, raw, location, content_type)

    def json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, raw, location = self.request(method, path, body)
        if status != 200:
            raise BoardFailure(_answered(method, path, status, location))
        document = _json_or_none(raw)
        if document is None:
            raise BoardFailure(f"{method} {path} answered 200 but not JSON — {raw[:120]!r}")
        if not document.get("success", False):
            raise BoardFailure(f"{method} {path} answered success=false — {document}")
        return document["data"]

    def challenges(self) -> list[dict[str, Any]]:
        """Everything the Board lists, which is **not** everything it knows.

        The LIST payload carries no description and no `shared` flag, so prose and deployability
        both cost a detail GET (`challenge`). What it does carry is `solves` and `value`, which is
        the pair a later version fits the scoring curve from.

        An empty list is not an empty Board and must never be read as one — corroborate it with
        `collection_endpoints_reach_ctfd` before believing it (ADR-0016).
        """
        listed = self.json("GET", CHALLENGES)
        return [entry for entry in listed if isinstance(entry, dict)] if isinstance(listed, list) else []

    def challenge(self, challenge_id: int | str) -> dict[str, Any]:
        """One Challenge in full: the description, its files, `shared` and the deploy terms — and
        `attempts`, our own submission count held **server-side**, which is what makes it the one
        thing about a Run that survives the container being restarted."""
        detail = self.json("GET", f"{CHALLENGES}/{challenge_id}")
        return detail if isinstance(detail, dict) else {}

    def scoreboard(self, top: int) -> tuple[Standing, ...]:
        """The top `top` of the scoreboard, as rows rather than as CTFd's rank-keyed object.

        Read every Intake cycle and never acted on in v1: it is the shape of the race, stored so
        that fitting the scoring curve later needs no new Solver code (ADR-0015).
        """
        published = self.json("GET", f"{SCOREBOARD_TOP}/{top}")
        rows = published.items() if isinstance(published, dict) else enumerate(published or [], start=1)
        standings = (_standing(rank, row) for rank, row in rows if isinstance(row, dict))
        return tuple(sorted((row for row in standings if row), key=lambda row: row.rank))

    def collection_endpoints_reach_ctfd(self) -> bool:
        """Whether a collection endpoint's reply was composed by CTFd, asked with a query it must
        refuse ([ADR-0016](../docs/adr/0016-an-empty-list-is-not-an-empty-board.md)).

        A 200 here cannot have come from CTFd, and a Board that agreeable is one whose empty
        collections mean nothing at all — the IN-CYPHER practice arena answers every collection
        endpoint this way while `/api/v1/challenges/8/solves` returns real rows. **Any refusal
        counts**: a 400, a 403 and a 302 all pass, because this catches the reply that is too
        agreeable rather than certifying the stack behind a normal one.

        It lives on the seam rather than above it because it is a property of this Board's read
        contract, and every reader of an empty list needs it — the pre-flight probe asks it before
        naming any other cause, and Intake asks it before recording a Board as having emptied.
        """
        status, _body, _location = self.request("GET", READ_CONTRACT_CONTROL)
        return status != 200

    def submit(self, challenge_id: int | str, flag: str) -> Verdict:
        """Submit one Flag, and answer with what the **body** said about it.

        A transport fault is a `Verdict` rather than an exception for the same reason a
        chall-manager fault is a name: the caller is spending a submission slot under a Board-wide
        limiter, and an unhandled `URLError` there says "we do not know whether that was graded" as
        a crash. `UNREAD` says it as an answer.
        """
        try:
            status, raw, location = self.request("POST", ATTEMPT, {"challenge_id": challenge_id, "submission": flag})
        except OSError as unreachable:
            return Verdict(UNREAD, f"the submission never reached the Board — {unreachable}")
        data = (_json_or_none(raw) or {}).get("data")
        graded = data if isinstance(data, dict) else {}
        if not (outcome := str(graded.get("status", ""))):
            return Verdict(UNREAD, _answered("POST", ATTEMPT, status, location), status)
        return Verdict(outcome, str(graded.get("message", "")), status)

    def deploy_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("POST", "", {"challengeId": challenge_id})

    def read_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("GET", f"?challengeId={challenge_id}")

    def renew_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("PATCH", f"?challengeId={challenge_id}", {"challengeId": challenge_id})

    def terminate_instance(self, challenge_id: int | str) -> Reply:
        # The id goes in the query *and* in a body. The one source-read of the plugin has the
        # deploy taking a JSON body, the read taking a query string, and the terminate taking a
        # body ([#38](https://github.com/jerome-queck/incypher-ctf/issues/38)) — and no board we
        # hold a token for has a `dynamic_iac` Challenge to re-verify that against. Sending both
        # costs a line; sending the wrong one costs every terminate, and so every leak sweep.
        return self._instance_call("DELETE", f"?challengeId={challenge_id}", {"challengeId": challenge_id})

    def mana(self) -> Mana:
        """Read once and branched on rather than tracked (ADR-0007).

        It takes the same per-team lock as a deploy and **blocks** rather than failing while one is
        in flight, so a call here during a slow deploy can hang for minutes. That is what makes it
        a read on cause and never a poll.
        """
        outcome, data, detail = self._plugin_call("GET", f"{CHALL_MANAGER}/mana")
        return Mana(outcome, int(data.get("used", 0)), int(data.get("total", 0)), detail)

    def instances_held(self) -> tuple[Held, ...]:
        """What this team holds right now, from the page that bypasses CTFd's sixty-second cache.

        The HTML stops here. A caller that received a page instead of records would be a caller
        holding this seam's one job, and ADR-0008 is explicit that a seam leaking its wire format
        upward is decorative.
        """
        status, raw, location = self.request("GET", INSTANCE_LEDGER)
        if status != 200:
            raise BoardFailure(_answered("GET", INSTANCE_LEDGER, status, location))
        rows = instance_ledger_rows(raw)
        if rows is None:
            raise BoardFailure("the instance ledger carried no table — a login page reads as an empty ledger")
        return rows

    def instance_ledger_page(self, page: int):
        """Read one identity-bearing page; policy and ownership stay above the wire seam."""
        from solver.instance_ledger import LedgerPage, LedgerRow

        status, raw, _location = self.request("GET", f"{INSTANCE_LEDGER_API}?page={page}")
        digest = hashlib.sha256(raw).hexdigest()
        document = _json_or_none(raw)
        data = document.get("data") if isinstance(document, dict) else None
        if status != 200 or not isinstance(data, dict):
            return LedgerPage(status=status, body=raw, page=page, complete=False, response_digest=digest)
        try:
            rows = tuple(
                LedgerRow(
                    str(row["instanceId"]),
                    row["challengeId"],
                    row.get("userId"),
                    row.get("teamId"),
                )
                for row in data["rows"]
            )
            return LedgerPage(
                status=status,
                body=raw,
                rows=rows,
                page=int(data["page"]),
                total_pages=int(data["totalPages"]),
                total_rows=int(data["totalRows"]),
                complete=True,
                response_digest=digest,
            )
        except (KeyError, TypeError, ValueError):
            return LedgerPage(status=status, body=raw, page=page, complete=False, response_digest=digest)

    def _instance_call(self, method: str, query: str, body: dict[str, Any] | None = None) -> Reply:
        outcome, data, detail = self._plugin_call(method, f"{CHALL_MANAGER}/instance{query}", body)
        return Reply(outcome, str(data.get("connectionInfo", "")), _moment(str(data.get("until", "") or "")), detail)

    def _plugin_call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[str, dict, str]:
        """One chall-manager call, answered as a name.

        A transport fault is one of the named shapes rather than an exception, because
        *chall-manager-down-at-submit* is a thing the Solver is required to say out loud and an
        unhandled `URLError` two frames above a submission says it as a crash.
        """
        try:
            status, raw, location = self.request(method, path, body)
        except OSError as unreachable:
            return UNREACHABLE, {}, str(unreachable)
        document = _json_or_none(raw) or {}
        data = document.get("data") if isinstance(document.get("data"), dict) else {}
        message = str(data.get("message", ""))
        if status != 200:
            # The plugin sends its refusals as JSON and its lock as an empty body, so the status is
            # what names them and the message is only ever what the record quotes afterwards.
            named = {403: REFUSED, 429: LOCKED, 404: ABSENT}.get(status, UNREACHABLE)
            return named, {}, message or _answered(method, path, status, location)
        if not document:
            return UNREACHABLE, {}, f"{method} {path} answered 200 but not JSON — {raw[:120]!r}"
        return (ANSWERED if document.get("success") else DENIED), data, message

    def download(self, file_path: str) -> tuple[bytes, list[str]]:
        """Fetch a challenge file, following redirects off the platform if that is where it lives.

        Not following redirects is what stops an expired session masquerading as an empty board,
        so the API path keeps that rule. Files are the exception: CTFd commonly answers `/files/`
        with a 302 to object storage holding a presigned URL, and refusing to follow it means
        never opening a forensics, reversing or pwn challenge at all.

        Returns the bytes and the hosts the fetch passed through, so an off-platform host is
        something the caller can state rather than something nobody notices.
        """
        target, hops = f"/{file_path.lstrip('/')}", []
        for _ in range(MAX_FILE_REDIRECTS):
            status, payload, location = self.request("GET", target)
            if status == 200:
                if b"<html" in payload[:512].lower():
                    raise BoardFailure("the file fetch returned an HTML page — auth degraded to a login screen")
                if len(payload) > self.fetch_bytes:
                    raise TooLarge(f"{target} is over the {self.fetch_bytes}-byte cap on a single fetch")
                return payload, hops
            if status not in (301, 302, 303, 307, 308) or not location:
                raise BoardFailure(f"the file fetch answered {status}" + (f" → {location}" if location else ""))
            target = urllib.parse.urljoin(f"{self.url}{target}", location)
            # Following redirects must not become a way for a login screen to arrive as a pass.
            # A file request sent to /login means the session degraded; chasing it only burns
            # hops before the HTML check catches the same thing less clearly.
            if "/login" in urllib.parse.urlparse(target).path:
                raise BoardFailure(f"the file fetch was redirected to {target} — auth degraded to a login screen")
            hops.append(urllib.parse.urlparse(target).netloc)
        raise BoardFailure(f"the file fetch still redirecting after {MAX_FILE_REDIRECTS} hops via {hops}")


class _LedgerTable(HTMLParser):
    """The ledger page's table, read by its own column headings.

    Position would be the shorter parser and the wrong one twice over: the live page's first column
    is the Category, and a Mana Cost column appears in front of both wherever mana is enabled — so
    an index that is the challenge name on one board is a category on the next.

    A heading is decided by the tag that **opened** the cell, because the plugin's own template
    opens every heading `<th>` and closes it `</td>` (read live, 25 Aug 2026). A parser trusting the
    closing tag finds no headings at all and reports a page it could not read as a ledger holding
    nothing, which is the one reading that leaves a leak in place.
    """

    NAME_HEADING = "challenge"

    @classmethod
    def rows_of(cls, page: str) -> list[str] | None:
        """The challenge names the page lists, or `None` where it holds no table at all.

        The two are kept apart because a leak sweep acts on the answer: a page that could not be
        read reported as an empty ledger leaves a leaked Instance held for the rest of the Run.
        """
        reader = cls()
        reader.feed(page)
        if not reader.headings:
            return None
        return [row[reader.name_column] for row in reader.rows if len(row) > reader.name_column]

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._cell_is_heading = False
        self._inside_head = False

    @property
    def name_column(self) -> int:
        headings = [heading.lower() for heading in self.headings]
        return next((at for at, heading in enumerate(headings) if self.NAME_HEADING in heading), 0)

    def handle_starttag(self, tag: str, _attrs: list) -> None:
        if tag == "thead":
            self._inside_head = True
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell, self._cell_is_heading = [], tag == "th" or self._inside_head

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "thead":
            self._inside_head = False
        elif tag in ("td", "th") and self._cell is not None:
            text = "".join(self._cell).strip()
            into = self.headings if self._cell_is_heading else self._row
            if into is not None:
                into.append(text)
            self._cell = None
        elif tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = None


def instance_ledger_rows(raw: bytes) -> tuple[Held, ...] | None:
    """Parse the authenticated ledger's structural contract without trusting its rows yet."""

    rows = _LedgerTable.rows_of(raw.decode("utf-8", "replace"))
    return None if rows is None else tuple(Held(name) for name in rows)


def _standing(rank: Any, row: dict[str, Any]) -> Standing | None:
    """One scoreboard row, out of a payload whose shape CTFd changes by endpoint.

    `/top/<n>` keys its object by rank and gives each row its solves rather than a total, where the
    plain scoreboard gives a `score` and a list. Both are read here so that a Board serving either
    is a Board we can snapshot, and a row that is neither is dropped rather than guessed at.
    """
    try:
        place = int(rank)
    except (TypeError, ValueError):
        return None
    score = row.get("score")
    if not isinstance(score, int):
        solves = row.get("solves")
        score = (
            sum(int(one.get("value") or 0) for one in solves if isinstance(one, dict))
            if isinstance(solves, list)
            else 0
        )
    return Standing(place, str(row.get("name", "")), score)


def _answered(method: str, path: str, status: int, location: str) -> str:
    """One sentence for a board that answered something other than the 200 the caller wanted, with
    the destination where there was one — a bare status hides that this was a redirect to /login."""
    return f"{method} {path} answered {status}" + (f" → {location}" if location else "")


def _moment(until: str) -> dt.datetime | None:
    """chall-manager's `until`, which is RFC3339 written by Go and so may carry more fractional
    digits than `fromisoformat` accepts.

    A timestamp that cannot be read is **no deadline** rather than a wrong one: the Attempt's own
    budget then decides, which is early but never late.
    """
    if not until:
        return None
    text = until.replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = "".join(itertools.takewhile(str.isdigit, rest))
        text = f"{head}.{digits[:6]}{rest[len(digits) :]}"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _json_or_none(raw: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # noqa: D102 - urllib hook
        return None


def _over_the_network(fetch_bytes: int = MAX_FETCH_BYTES) -> Transport:
    """The real transport: one no-redirect opener, reused for the life of a `Board`.

    An `HTTPError` is an answer, not an accident — CTFd says 401, 403 and 404 through it — so it
    is normalised into the same triple as a 200 rather than raised at a caller who would have to
    know that urllib splits the status range in two.

    **One byte past the cap and no further.** This is the only place the cap can be honest: a check
    on `len(payload)` after a bare `read()` is a check made after 6.15 GB has already arrived in
    the memory of a container nobody is watching. Reading `fetch_bytes + 1` is what lets the caller
    tell "at the cap" from "over it" while never holding more than one byte more than it allows.
    """
    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(request: urllib.request.Request) -> TransportResult:
        try:
            with opener.open(request, timeout=30) as response:
                return (
                    response.status,
                    response.read(fetch_bytes + 1),
                    response.headers.get("Location", ""),
                    response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as error:
            return (
                error.code,
                error.read(fetch_bytes + 1),
                error.headers.get("Location", ""),
                error.headers.get("Content-Type", ""),
            )

    return fetch


network_transport = _over_the_network
