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

# One deliberately-wrong Flag, its wrapper, and a command that emits it — so the planted Observation
# the submission check works from is one the replay can genuinely reproduce, exactly as a Challenge's
# own command would be. The wrapper is passed in like any Board profile value; discovering it off a
# live Board is [#74](https://github.com/jerome-queck/incypher-ctf/issues/74)'s.
PROBE_FLAG = "brunner{ctfd-probe-deliberately-wrong}"
PROBE_WRAPPER = r"brunner\{[^}]{1,64}\}"
PROBE_COMMAND = f"printf '%s\\n' '{PROBE_FLAG}'"
PROBE_ATTEMPT = "ctfd-probe"

# `python3 scripts/ctfd_probe.py` puts `scripts/` on the import path and not the repository root,
# so the package this probe consumes has to be pointed at. Importing the seam rather than keeping
# a Board here is the whole point: the check run before an event and the code that competes are
# then provably the same code rather than two things that agree today (ADR-0008).
sys.path.insert(0, str(REPO_ROOT))

from solver.board import CHALLENGES, INCORRECT, Board, BoardFailure  # noqa: E402
from solver.flag import Flags, Slots  # noqa: E402
from solver.instance import INSTANCED_TYPE, Instances, Terms  # noqa: E402
from solver.record import NO_MODEL, Recorder  # noqa: E402
from solver.redaction import Redactor  # noqa: E402


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
    if not board.collection_endpoints_reach_ctfd():
        raise ProbeFailure(
            "the empty list did not come from a corroborated CTFd read — this board did not return "
            "a distinct JSON 404 for known-absent Challenge 0, so its collection endpoints may be "
            "composed by something else and an empty one is evidence of nothing. Ask a known id directly "
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
    with_header, _, _ = board.request("GET", CHALLENGES)
    refuse_to_blame_the_token(board, with_header)
    without_header, _, location = board.request("GET", CHALLENGES, json_content_type=False)
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
    status, _, _ = board.request("GET", CHALLENGES)
    refuse_to_blame_the_token(board, status)
    if status == 403:
        raise ProbeFailure(
            "403 on enumeration with no scheduled open — check the account is on a team, and that "
            "the token has not been revoked"
        )
    challenges = board.json("GET", CHALLENGES)
    if not challenges:
        name_the_cause_of_an_empty_list(board)
    kinds = sorted({challenge.get("type", "?") for challenge in challenges})
    categories = sorted({challenge.get("category", "?") for challenge in challenges})
    return (
        f"{len(challenges)} challenges; types {kinds}; categories {categories}",
        challenges,
    )


def check_a_planted_flag_is_swept_and_graded(board: Board, challenges: list[dict[str, Any]]) -> str:
    """Plant a Flag in a real Observation and walk it all the way to a verdict.

    One wrong submission is not the "indiscriminate brute-forcing" the rules ban, and it buys two
    things at once: that the verdict lives in `data.status` while the HTTP code says whatever it
    likes, and that the whole submission path — sweep, replay, guard, submit — is the path that will
    run at 14:00 rather than one that agrees with it today.

    The planted Observation is written the way the adapter writes one, so nothing here is a
    rehearsal of the real thing: `solver/flag.py` reads it back out of the record itself.

    It has to be aimed at a Challenge this team has **not** solved. A solved one answers any Flag,
    right or wrong, with `already_solved` and the message *"Incorrect but you already solved this"* —
    which proves that the verdict is in the body and proves nothing whatever about grading.
    """
    if not board.authenticated:
        raise Unproven("submitting needs a token; the anonymous read path cannot prove this")
    unsolved = [challenge for challenge in challenges if not challenge.get("solved_by_me")]
    if not unsolved:
        raise Unproven(
            "this team has solved every Challenge on the board, and a solved one grades "
            "'already_solved' whatever it is sent — there is nothing left here to submit against"
        )
    challenge = unsolved[0]
    recorder = _probe_recorder()
    step = recorder.step_begin(
        attempt_id=PROBE_ATTEMPT,
        step_index=1,
        command_raw=PROBE_COMMAND,
        command_normalised=PROBE_COMMAND,
        tool="shell",
    )
    step.end(exit_code=0, output=f"{PROBE_FLAG}\n".encode(), usage=NO_MODEL)

    detail = board.json("GET", f"{CHALLENGES}/{challenge['id']}")
    flags = Flags(board, recorder, flag_wrappers=(PROBE_WRAPPER,))
    candidates = flags.candidates(attempt_id=PROBE_ATTEMPT)
    if not candidates:
        raise ProbeFailure("the sweep found no candidate in an Observation that holds one")
    outcome = flags.submit(
        candidates,
        attempt_id=PROBE_ATTEMPT,
        challenge_id=challenge["id"],
        slots=Slots(detail.get("max_attempts"), int(detail.get("attempts") or 0)),
        workdir=REPO_ROOT,
    )
    if not outcome.graded:
        raise ProbeFailure(f"nothing was submitted — the gate held every candidate: {outcome.held}")
    answer = outcome.graded[0]
    if answer.verdict.outcome != INCORRECT:
        raise ProbeFailure(f"expected a deliberately-wrong flag to grade 'incorrect', got {answer.verdict}")
    return (
        f"a {answer.candidate.strength} candidate swept out of the record and submitted against "
        f"{challenge['name']!r}: HTTP {answer.verdict.http_status} carrying "
        f"data.status={answer.verdict.outcome!r} — read the verdict from the body, never the status"
    )


def check_files_download_headlessly(board: Board, challenges: list[dict[str, Any]]) -> str:
    for challenge in challenges:
        files = board.json("GET", f"{CHALLENGES}/{challenge['id']}").get("files") or []
        if files:
            payload, hops = board.download(files[0])
            if not payload:
                raise ProbeFailure(f"{files[0]} downloaded as zero bytes")
            served_from = f" via {', '.join(hops)}" if hops else " served inline by the board"
            return f"{len(payload)} bytes from {challenge['name']!r}{served_from} — no browser needed"
    raise Unproven("no challenge exposes a file yet — re-run once the board is populated")


def check_instance_lifecycle(board: Board, challenges: list[dict[str, Any]]) -> str:
    """Deploy an Instance, renew it, terminate it, and confirm the ledger is empty afterwards.

    This is v1's gate criterion — *"≥1 Instance it deployed and terminated itself"* — run as a
    pre-flight rather than discovered during a Run. Thirty-two of seventy-four Brunner Challenges
    cannot be reconned without one, so a board where this path is broken is a board where two
    Challenges in five are unreachable.

    The renew here is immediate, which proves the call and not the policy: renewing *late* is what
    `solver/instance.py` decides and `tests/test_instance_path.py` holds, because a renew sets
    `until = now + timeout` and one taken early throws away whatever remained.
    """
    if not board.authenticated:
        raise Unproven("deploying needs a token; the anonymous read path cannot prove this")
    instanced = next((one for one in challenges if one.get("type") == INSTANCED_TYPE), None)
    if instanced is None:
        raise Unproven(f"no {INSTANCED_TYPE} challenge on this board yet — re-run once one appears")

    terms = Terms.of(board.json("GET", f"{CHALLENGES}/{instanced['id']}"))
    instances = Instances(board, _probe_recorder())
    deployed = instances.deploy(terms, attempt_id=PROBE_ATTEMPT)
    if deployed.lease is None:
        raise ProbeFailure(f"the deploy produced no Instance — {deployed.shape}: {deployed.shown}")
    try:
        renewed = instances.renew(deployed.lease, attempt_id=PROBE_ATTEMPT)
        if renewed.shape:
            raise ProbeFailure(f"the renew answered {renewed.shape}: {renewed.shown}")
    finally:
        # In a `finally` because a probe that failed halfway is exactly the run that must not leave
        # an Instance behind: chall-manager never evicts, so a leak here costs capacity all day.
        released = instances.terminate(terms.challenge_id, attempt_id=PROBE_ATTEMPT)

    still_held = [record.challenge_name for record in board.instances_held()]
    if instanced["name"] in still_held:
        raise ProbeFailure(
            f"the ledger still lists {instanced['name']!r} after a terminate answered "
            f"{released.shown!r} — chall-manager never evicts, so this is capacity lost for the run"
        )
    return (
        f"deployed {instanced['name']!r} at {deployed.lease.connection_info!r} until "
        f"{deployed.lease.until}, renewed, terminated, and the ledger holds {still_held or 'nothing'}"
    )


def _probe_recorder() -> Recorder:
    """The Instance path records Steps like everything else, so the probe gives it somewhere to
    write — `state/`, which is where every Run's stream goes and is gitignored (`MAP.md`). The run
    id is fixed, so a re-run appends to the same stream rather than scattering one file per probe.
    """
    return Recorder(REPO_ROOT / "state", "ctfd-probe", redactor=Redactor.for_declared_secrets(os.environ))


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
                "a planted flag is swept and graded",
                _unproven("--no-attempt was passed, so submission semantics are unverified")
                if arguments.no_attempt
                else lambda: check_a_planted_flag_is_swept_and_graded(board, challenges),
            )
        )
        outcomes.append(
            _report("files download headlessly", lambda: check_files_download_headlessly(board, challenges))
        )
        outcomes.append(_report("instance lifecycle", lambda: check_instance_lifecycle(board, challenges)))

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
