"""The one place that knows how a CTFd board is talked to.

Everything above this module deals in challenges, files and verdicts; nothing above it imports
`urllib` or learns what wire format an answer arrived in
([ADR-0008](../docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).

The four transport rules here each cost a day to find, and each fails *silently* when it is
missing — the board answers, the JSON parses, and the caller concludes there is nothing to solve.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class Reply:
    """What one call on the Instance resource answered.

    `detail` is the plugin's own message where it sent one, because the Solver writes it into the
    record and a shape without the sentence that produced it is unreviewable afterwards.
    """

    outcome: str
    connection_info: str = ""
    until: str = ""
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


# `Board`'s one edge to the network: it is handed a request with every transport rule already
# applied, and answers with the status, the body and any `Location`. Injectable because that is
# the honest place to stand a test — above it are this module's rules, below it is a socket.
Transport = Callable[[urllib.request.Request], tuple[int, bytes, str]]


class BoardFailure(Exception):
    """The board answered in a way the Solver would have silently mis-read."""


class Board:
    """A CTFd board addressed the way the Solver addresses it.

    API redirects are never followed. CTFd answers an unauthenticated API request with a 302 to
    /login, and a follower turns that into a 200 holding an HTML login page — the exact shape
    that reads downstream as "the board has no challenges". `download` is the one exception, and
    says why.
    """

    def __init__(self, url: str, token: str, transport: Transport | None = None) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._transport = transport or _over_the_network()

    @property
    def authenticated(self) -> bool:
        """Some boards serve challenges to anyone, so an absent token narrows what a caller can
        prove rather than stopping it."""
        return bool(self._token)

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, json_content_type: bool = True
    ) -> tuple[int, bytes, str]:
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
        return self._transport(request)

    def json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, raw, location = self.request(method, path, body)
        if status != 200:
            raise BoardFailure(f"{method} {path} answered {status}" + (f" → {location}" if location else ""))
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            raise BoardFailure(f"{method} {path} answered 200 but not JSON — {raw[:120]!r}") from None
        if not document.get("success", False):
            raise BoardFailure(f"{method} {path} answered success=false — {document}")
        return document["data"]

    def deploy_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("POST", "", {"challengeId": challenge_id})

    def read_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("GET", f"?challengeId={challenge_id}")

    def renew_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("PATCH", f"?challengeId={challenge_id}")

    def terminate_instance(self, challenge_id: int | str) -> Reply:
        return self._instance_call("DELETE", f"?challengeId={challenge_id}")

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
            raise BoardFailure(f"the instance ledger answered {status}" + (f" \u2192 {location}" if location else ""))
        rows = _LedgerTable.rows_of(raw.decode("utf-8", "replace"))
        if rows is None:
            raise BoardFailure("the instance ledger carried no table — a login page reads as an empty ledger")
        return tuple(Held(name) for name in rows)

    def _instance_call(self, method: str, query: str, body: dict[str, Any] | None = None) -> Reply:
        outcome, data, detail = self._plugin_call(method, f"{CHALL_MANAGER}/instance{query}", body)
        return Reply(outcome, str(data.get("connectionInfo", "")), str(data.get("until", "") or ""), detail)

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
            return (
                named,
                {},
                message or f"{method} {path} answered {status}" + (f" \u2192 {location}" if location else ""),
            )
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


def _json_or_none(raw: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # noqa: D102 - urllib hook
        return None


def _over_the_network() -> Transport:
    """The real transport: one no-redirect opener, reused for the life of a `Board`.

    An `HTTPError` is an answer, not an accident — CTFd says 401, 403 and 404 through it — so it
    is normalised into the same triple as a 200 rather than raised at a caller who would have to
    know that urllib splits the status range in two.
    """
    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(request: urllib.request.Request) -> tuple[int, bytes, str]:
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, response.read(), response.headers.get("Location", "")
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers.get("Location", "")

    return fetch
