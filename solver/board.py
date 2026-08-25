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
