"""Prove a CTFd board is reachable the way the Solver will reach it, before an event starts.

    python3 scripts/ctfd_probe.py [--no-attempt]

Reads CTFD_URL and CTFD_API_TOKEN from .env (or the environment). Every failure it looks for is
one that is silent in production: the board answers, the JSON parses, and the Solver concludes
there is nothing to solve.

Standard library only — it has to run inside the Solver image with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

PASS, SKIP, FAIL = "pass", "skip", "fail"

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_FLAG = "brunner{ctfd-probe-deliberately-wrong}"

# CTFd boards sit behind Cloudflare, which 403s `Python-urllib/3.x` before the request ever reaches
# the application. Unset, every check below fails as though the token were rejected. The Solver
# inherits this: any HTTP client it uses announces itself as a browser or it never sees the board.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


class Board:
    """A CTFd board addressed the way the Solver addresses it.

    Redirects are never followed. CTFd answers an unauthenticated API request with a 302 to
    /login, and a follower turns that into a 200 holding an HTML login page — the exact shape
    that reads downstream as "the board has no challenges".
    """

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, json_content_type: bool = True
    ) -> tuple[int, bytes, str]:
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.url}{path}", data=payload, method=method)
        request.add_header("User-Agent", BROWSER_USER_AGENT)
        request.add_header("Authorization", f"Token {self._token}")
        request.add_header("Accept", "application/json")
        if json_content_type:
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(request, timeout=30) as response:
                return response.status, response.read(), response.headers.get("Location", "")
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers.get("Location", "")

    def json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, raw, location = self.request(method, path, body)
        if status != 200:
            raise ProbeFailure(f"{method} {path} answered {status}" + (f" → {location}" if location else ""))
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            raise ProbeFailure(f"{method} {path} answered 200 but not JSON — {raw[:120]!r}") from None
        if not document.get("success", False):
            raise ProbeFailure(f"{method} {path} answered success=false — {document}")
        return document["data"]

    def download(self, file_path: str) -> bytes:
        """Fetch a challenge file through the same request path as everything else.

        A second hand-rolled path is how a header discipline proved in one check gets quietly
        violated in the next, so this goes through `request` rather than building its own.
        """
        status, payload, location = self.request("GET", f"/{file_path.lstrip('/')}")
        if status != 200:
            raise ProbeFailure(f"file fetch answered {status}" + (f" → {location}" if location else ""))
        if b"<html" in payload[:512].lower():
            raise ProbeFailure("file fetch returned an HTML page, not a file — auth degraded to a login screen")
        return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # noqa: D102 - urllib hook
        return None


class ProbeFailure(Exception):
    """A check the Solver would have silently mis-read."""


class Unproven(Exception):
    """A check that could not run — reported as its own outcome, never as a pass.

    The board being unpopulated is not evidence that downloads work. Calling that PASS is the
    same silent green this probe exists to catch, one level up.
    """


def check_edge_is_not_blocking(board: Board) -> str:
    """Cloudflare rejects a machine-looking client with a 403 indistinguishable from a bad token.

    Checked first and named separately so nobody spends the morning of an event rotating a
    credential that was never the problem.
    """
    _, raw, _ = board.request("GET", "/api/v1/users/me")
    if b"cloudflare" in raw.lower():
        raise ProbeFailure("the edge blocked this before CTFd saw it — a browser User-Agent is what it screens on")
    return "requests reach CTFd itself, not the edge"


def check_token_is_recognised(board: Board) -> str:
    identity = board.json("GET", "/api/v1/users/me")
    team = identity.get("team_id")
    return f"authenticated as {identity['name']!r} (user {identity['id']}, team {team})"


def check_content_type_discipline(board: Board) -> str:
    """CTFd is widely reported to ignore token auth when Content-Type is absent. Measure it."""
    with_header, _, _ = board.request("GET", "/api/v1/challenges")
    without_header, _, location = board.request("GET", "/api/v1/challenges", json_content_type=False)
    if with_header != 200:
        raise ProbeFailure(f"the correctly-typed request itself failed with {with_header}")
    if without_header == 200:
        return (
            "this board accepts a token without Content-Type too — send it anyway; "
            "the Solver must not depend on a lenience other CTFd versions do not have"
        )
    return (
        f"confirmed: dropping Content-Type: application/json answers {without_header}"
        + (f" → {location}" if location else "")
        + " — every Solver request must carry it"
    )


def check_challenges_enumerate(board: Board) -> tuple[str, list[dict[str, Any]]]:
    challenges = board.json("GET", "/api/v1/challenges")
    if not challenges:
        raise ProbeFailure("the board listed zero challenges — either it has not opened, or auth degraded silently")
    kinds = sorted({challenge.get("type", "?") for challenge in challenges})
    categories = sorted({challenge.get("category", "?") for challenge in challenges})
    return (
        f"{len(challenges)} challenges; types {kinds}; categories {categories}",
        challenges,
    )


def check_attempt_verdict_is_in_the_body(board: Board, challenge: dict[str, Any]) -> str:
    """Submit one deliberately-wrong flag.

    One wrong submission is not the "indiscriminate brute-forcing" the rules ban, but it is the
    only way to learn that the verdict lives in data.status while the HTTP code stays 200.
    """
    status, raw, _ = board.request(
        "POST",
        "/api/v1/challenges/attempt",
        {"challenge_id": challenge["id"], "submission": PROBE_FLAG},
    )
    if status != 200:
        raise ProbeFailure(f"attempt answered HTTP {status} — expected 200 carrying a verdict")
    verdict = json.loads(raw)["data"]
    if verdict.get("status") != "incorrect":
        raise ProbeFailure(f"expected status 'incorrect' for a junk flag, got {verdict}")
    return (
        f"HTTP 200 with data.status={verdict['status']!r} against {challenge['name']!r} — "
        "read the verdict from the body, never from the status code"
    )


def check_files_download_headlessly(board: Board, challenges: list[dict[str, Any]]) -> str:
    for challenge in challenges:
        files = board.json("GET", f"/api/v1/challenges/{challenge['id']}").get("files") or []
        if files:
            payload = board.download(files[0])
            if not payload:
                raise ProbeFailure(f"{files[0]} downloaded as zero bytes")
            return f"{len(payload)} bytes from {challenge['name']!r} — no browser needed"
    raise Unproven("no challenge exposes a file yet — re-run once the board is populated")


def check_rate_limits_are_visible(board: Board) -> str:
    status, raw, _ = board.request("GET", "/api/v1/configs")
    if status == 401:
        raise Unproven("the token was rejected above, so this says nothing either way")
    if status != 200:
        return f"/api/v1/configs is admin-only here ({status}) — assume CTFd's default of 10 wrong submissions/min"
    configs = {entry["key"]: entry["value"] for entry in json.loads(raw)["data"]}
    interesting = {key: configs.get(key) for key in ("incorrect_submissions_per_min", "max_attempts", "ctf_theme")}
    return f"visible: {interesting}"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-attempt",
        action="store_true",
        help="skip the one deliberately-wrong flag submission (leaves the verdict semantics unverified)",
    )
    arguments = parser.parse_args()

    load_env(REPO_ROOT / ".env")
    url, token = os.environ.get("CTFD_URL", ""), os.environ.get("CTFD_API_TOKEN", "")
    if not url or not token:
        print("CTFD_URL and CTFD_API_TOKEN must be set — run `bash scripts/setup-board.sh`", file=sys.stderr)
        return 2

    board = Board(url, token)
    print(f"probing {board.url}\n")

    outcomes = [
        _report("edge is not blocking", lambda: check_edge_is_not_blocking(board)),
        _report("token is recognised", lambda: check_token_is_recognised(board)),
        _report("Content-Type discipline", lambda: check_content_type_discipline(board)),
    ]

    challenges: list[dict[str, Any]] = []

    def enumerate_and_keep() -> str:
        nonlocal challenges
        summary, challenges = check_challenges_enumerate(board)
        return summary

    outcomes.append(_report("challenges enumerate", enumerate_and_keep))

    if challenges:
        outcomes.append(
            _report(
                "attempt verdict",
                _unproven("--no-attempt was passed, so submission semantics are unverified")
                if arguments.no_attempt
                else lambda: check_attempt_verdict_is_in_the_body(board, challenges[0]),
            )
        )
        outcomes.append(
            _report("files download headlessly", lambda: check_files_download_headlessly(board, challenges))
        )

    outcomes.append(_report("board settings", lambda: check_rate_limits_are_visible(board)))

    failed, unproven = outcomes.count(FAIL), outcomes.count(SKIP)
    print()
    if failed:
        print(f"{failed} check(s) failed — do not start a run until they pass.", flush=True)
        return 1
    if unproven:
        print(
            f"transport works, but {unproven} check(s) could not run — re-run once the board is populated.", flush=True
        )
        return 0
    print("board is reachable the way the Solver reaches it.")
    return 0


def _report(name: str, run: Callable[[], str]) -> str:
    """One line per check. The whole report goes to stdout so it reads in order when piped."""
    try:
        print(f"  PASS  {name}: {run()}", flush=True)
        return PASS
    except Unproven as gap:
        print(f"  SKIP  {name}: {gap}", flush=True)
        return SKIP
    except (ProbeFailure, urllib.error.URLError, KeyError, json.JSONDecodeError) as failure:
        print(f"  FAIL  {name}: {failure}", flush=True)
        return FAIL


def _unproven(reason: str) -> Callable[[], str]:
    """A check deliberately not run — so the caller reports SKIP rather than inventing a PASS."""

    def refuse_to_claim_a_pass() -> str:
        raise Unproven(reason)

    return refuse_to_claim_a_pass


if __name__ == "__main__":
    sys.exit(main())
