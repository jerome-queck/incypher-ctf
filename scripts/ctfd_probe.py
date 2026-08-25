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
import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import env_file

PASS, SKIP, FAIL = "pass", "skip", "fail"

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_FLAG = "brunner{ctfd-probe-deliberately-wrong}"

# `python3 scripts/ctfd_probe.py` puts `scripts/` on the import path and not the repository root,
# so the package this probe consumes has to be pointed at. Importing the seam rather than keeping
# a Board here is the whole point: the check run before an event and the code that competes are
# then provably the same code rather than two things that agree today (ADR-0008).
sys.path.insert(0, str(REPO_ROOT))

from solver.board import Board, BoardFailure  # noqa: E402


class ProbeFailure(BoardFailure):
    """A check this script makes on top of the seam, that the board failed.

    A `BoardFailure` because that is what it reports; its own class because the seam raises the
    base for a transport fault, and a reader at a raise site should be able to tell which of the
    two they are looking at.
    """


class Unproven(Exception):
    """A check that could not run — reported as its own outcome, never as a pass.

    The board being unpopulated is not evidence that downloads work. Calling that PASS is the
    same silent green this probe exists to catch, one level up.
    """


def board_window(board: Board) -> tuple[dt.datetime | None, dt.datetime | None]:
    """The event window, as the board itself publishes it.

    CTFd embeds `start` and `end` in `window.init` on every HTML page. `/api/v1/configs` is
    admin-only, so this is the one place a competitor can read the schedule the board enforces.
    """
    status, html, _ = board.request("GET", "/")
    if status != 200:
        return None, None
    page = html.decode("utf-8", "replace")

    def moment(name: str) -> dt.datetime | None:
        found = re.search(rf"'{name}':\s*(\d+)", page)
        return dt.datetime.fromtimestamp(int(found.group(1)), dt.timezone.utc) if found else None

    return moment("start"), moment("end")


def refuse_to_blame_the_token(board: Board, status: int) -> None:
    """Reinterpret a 403 that is the event window rather than a broken credential.

    CTFd gates the challenge endpoints until the event opens, and answers 403 whether your token
    is perfect or absent. Calling that a failure tells someone to fix credentials that are fine —
    the worst possible instruction on the evening of an event.
    """
    if status != 403:
        return
    opens, _ = board_window(board)
    if opens and opens > dt.datetime.now(dt.timezone.utc):
        raise Unproven(
            f"the board opens {opens.astimezone():%Y-%m-%d %H:%M %Z} — this 403 is that gate, "
            "not your token; re-run once it is open"
        )


def collection_endpoints_reach_ctfd(board: Board) -> bool:
    """Whether a collection endpoint's reply was composed by CTFd, asked with a query it must refuse.

    `field` is validated against an enumeration before any handler runs, so CTFd answers an unknown
    one with a 400 naming the permitted values. A 200 to it cannot have come from CTFd, and a board
    that agreeable is one whose empty collections mean nothing at all — the IN-CYPHER practice arena
    answers every collection endpoint this way while `/api/v1/challenges/8/solves` returns real rows
    ([ADR-0016](../docs/adr/0016-an-empty-list-is-not-an-empty-board.md)).

    Any refusal counts as CTFd-shaped: the control is here to catch the reply that is too agreeable,
    not to certify the stack behind a normal one.
    """
    status, _body, _reason = board.request("GET", "/api/v1/challenges?field=probe-is-not-a-field&q=a")
    return status != 200


def name_the_cause_of_an_empty_list(board: Board) -> NoReturn:
    """Say which situation emptied the challenge list, because all of them answer the same JSON.

    A 200 has already cleared the credential, and that is what makes the causes separable at all:
    every way of *not being shown* the board is a non-200 — no token answers 302, a rejected one
    401, a request without `Content-Type: application/json` 302, and the edge 403. So what is left
    is the clock, the difference between a stranger's board and an account's, and a board that is
    genuinely listing nothing. The message this replaced offered "auth degraded silently" as one
    of two causes, which sends someone to rotate a token the response shape had already exonerated.
    """
    # Asked first, because it is prior to every cause below: the clock and the account are facts
    # about a board that answered, and this is the question of whether the board answered at all.
    if not collection_endpoints_reach_ctfd(board):
        raise ProbeFailure(
            "the empty list did not come from CTFd — this board also answers 200 to a query CTFd "
            "rejects with a 400, so its collection endpoints are being composed by something else "
            "and an empty one is evidence of nothing. Ask a known id directly "
            "(GET /api/v1/challenges/<id>/solves) before believing anything this board lists"
        )

    opens, closes = board_window(board)
    now = dt.datetime.now(dt.timezone.utc)
    if opens and opens > now:
        raise Unproven(
            f"the board opens {opens.astimezone():%Y-%m-%d %H:%M %Z} — it lists nothing because it "
            "has not started, and that is the clock rather than a fault"
        )
    if closes and closes < now:
        raise Unproven(
            f"the board closed {closes.astimezone():%Y-%m-%d %H:%M %Z} — it lists nothing because "
            "the event is over, and that is the clock rather than a fault"
        )
    if not board.authenticated:
        raise ProbeFailure(
            "the board answered 200 and listed nothing to an anonymous reader — this is what a "
            "stranger is shown, which is not what the Solver will be shown. Set CTFD_API_TOKEN "
            "and re-run before recording the board as empty"
        )
    raise ProbeFailure(
        "the board answered 200 and listed nothing to an authenticated account inside its own "
        "event window — not a credential fault and not a login page. A Challenge withdrawn from "
        "the list reads exactly like one that was never there, and still answers "
        "GET /api/v1/challenges/<id>/solves; ask a known id before recording the board as empty"
    )


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
    if not board.authenticated:
        raise Unproven("no CTFD_API_TOKEN set — running the anonymous read path only")
    identity = board.json("GET", "/api/v1/users/me")
    team = identity.get("team_id")
    return f"authenticated as {identity['name']!r} (user {identity['id']}, team {team})"


def check_content_type_discipline(board: Board) -> str:
    """CTFd is widely reported to ignore token auth when Content-Type is absent. Measure it."""
    with_header, _, _ = board.request("GET", "/api/v1/challenges")
    refuse_to_blame_the_token(board, with_header)
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
    status, _, _ = board.request("GET", "/api/v1/challenges")
    refuse_to_blame_the_token(board, status)
    if status == 403:
        raise ProbeFailure(
            "403 on enumeration with no scheduled open — check the account is on a team, and that "
            "the token has not been revoked"
        )
    challenges = board.json("GET", "/api/v1/challenges")
    if not challenges:
        name_the_cause_of_an_empty_list(board)
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
    if not board.authenticated:
        raise Unproven("submitting needs a token; the anonymous read path cannot prove this")
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
            payload, hops = board.download(files[0])
            if not payload:
                raise ProbeFailure(f"{files[0]} downloaded as zero bytes")
            served_from = f" via {', '.join(hops)}" if hops else " served inline by the board"
            return f"{len(payload)} bytes from {challenge['name']!r}{served_from} — no browser needed"
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
    """Put the file's assignments into the environment, without overriding what is already set.

    What a line *means* is `env_file`'s, shared with the credential reporter — the two read the same
    files and used to disagree about `export NAME=value`, which this one silently turned into a
    variable called `export NAME` and so started the Solver without a credential the file held (#61).
    """
    if not path.exists():
        return
    for key, value in env_file.assignments(path.read_text()).items():
        os.environ.setdefault(key, value)


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
    if not url:
        print("CTFD_URL must be set — run `bash scripts/setup-board.sh`", file=sys.stderr)
        return 2

    board = Board(url, token)
    print(f"probing {board.url}" + ("" if board.authenticated else " (anonymously — no token set)") + "\n")

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
        print(f"nothing is wrong, but {unproven} check(s) could not run — each SKIP above says why.", flush=True)
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
    except (BoardFailure, urllib.error.URLError, KeyError, json.JSONDecodeError) as failure:
        print(f"  FAIL  {name}: {failure}", flush=True)
        return FAIL


def _unproven(reason: str) -> Callable[[], str]:
    """A check deliberately not run — so the caller reports SKIP rather than inventing a PASS."""

    def refuse_to_claim_a_pass() -> str:
        raise Unproven(reason)

    return refuse_to_claim_a_pass


if __name__ == "__main__":
    sys.exit(main())
