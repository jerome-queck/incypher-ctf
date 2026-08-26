"""Promote a Run's stream into the repository, and refuse rather than leak it.

    python3 scripts/promote_run.py [run_id ...] [--state state] [--into runs] [--dry-run]

A Run writes its stream to `/state`, which dies with the laptop. `runs/<run_id>.jsonl` is where it
survives, and this is the only thing that puts it there — **the Solver has no git binary and never
commits during a Run** ([ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md)).

Four rules, and each of them is here because of what it costs to get wrong:

- **The stream, not a projection.** The file is copied byte for byte. A per-Attempt summary row
  would be a judgement frozen at the moment it was taken, and every number in v1 is uncalibrated —
  the whole payoff of ADR-0009 is that a projection can be redefined in v4 *against v1 runs*.
- **Bodies stay behind.** Observation and Claim bodies live in files beside the stream and are not
  copied, so the JSONL keeps its digests and the repository does not grow gigabytes of command
  output. What that costs is named in ADR-0009: delete `/state` and those digests can never be
  resolved back to content again.
- **Never during a live Run**, which is asked twice because neither answer is enough alone. The
  stream's own **flock** is the positive test — the recorder takes it on every write
  (`solver/record.py`), so a lock we cannot get means a writer is there right now — and the copy is
  taken while holding it, so what lands is a consistent snapshot rather than a half-written line.
  Then a **staleness window**: a stream last added to inside `--stale-after` may still be live
  between writes. A Run that stopped writing long ago is promoted even where it never reached
  `run-close`, and the missing close is printed on its line: that Run was killed, not running, and
  its record is exactly the one a post-mortem needs.
- **It re-scans, and refuses on a hit.** There is no human between the file this writes and the
  push, so the guard has to be here. `conformance/check-secrets.sh` fires *after* the push, by
  which time a credential is already on GitHub's servers and burned — this is the same scan, moved
  to the one place it can still prevent something.

The re-scan has two legs and both must pass. The first is the **declared secret set** — every value
this machine actually holds, in the same encoded forms `solver/redaction.py` redacts, so a
credential the recorder somehow missed is caught by the same rule twice. The second is **gitleaks**,
pinned by `conformance/install-gitleaks.sh`, which is what CI will run over the commit: catching it
here rather than there is the whole point. An absent scanner is a usage error and never a pass, for
the reason `check-secrets.sh` gives — a check that quietly exits 0 because its tool is missing is
the silent green this repository keeps building controls against.

**Nothing moves if anything hits.** A batch is refused whole rather than cherry-picked: a hit means
a live credential is on this disk, and the next action is to rotate it rather than to promote the
other four Runs.

Which Runs. **Every Run that reached Attempt-open**, and gate Runs are mandatory — a version tag is
a verdict on a gate Run, and a verdict whose evidence was never committed is an assertion. A Run
that never opened an Attempt is skipped rather than refused; that is a pre-flight probe, not a Run.
Batched one pull request per gate or practice weekend.

Exit codes follow `conformance/check-secrets.sh`, because the failures want different responses:
**0** promoted, **1** refused — a credential, a Run still live, or a record that would shrink — and
**2** the check could not run at all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import credentials_held  # noqa: E402
import env_file  # noqa: E402
import stream  # noqa: E402
from solver.credentials import SECRETS  # noqa: E402
from solver.redaction import Redactor  # noqa: E402

# Where this repository's own instructions tell `conformance/install-gitleaks.sh` to put the pinned
# build — `.cache/` is the declared home for disposable tooling. Looked at only after `--gitleaks`
# and `PATH`, so a caller who named a scanner is never second-guessed.
CACHED_SCANNER = REPO_ROOT / ".cache" / "scanners" / "gitleaks"

# How long a stream must have been quiet before it is taken as finished rather than as between
# writes. A parameter with no calibration behind it, like every threshold in this repository: the
# longest silence a live Run can produce is one model turn thinking, and this is well past that
# while being nowhere near the age of yesterday's gate Run.
STALE_AFTER_SECONDS = 900.0

SKIP, REFUSE, TAKE = "skip", "REFUSE", "ok"

BURNED = """
A declared credential is in a stream that was about to be committed.

Nothing was promoted, so it has not reached GitHub — but the value is on this disk in `/state` and
in whatever was copied out of it, and the recorder was supposed to have redacted it at write time.
Treat the redaction as the bug and the credential as suspect:

  1. Rotate it at whatever issued it. The one exception is TEAM_KEY, which the Board shows rather
     than mints and offers no way to replace (`AGENTS.md`).
  2. Find out how it got past `solver/redaction.py` — a value that reached the stream in a form the
     redactor does not know about is a hole every future Run has too.
  3. Promote again once the stream is clean.

`docs/credentials.md` is the inventory; `CONTRIBUTING.md` carries the response to one that did
reach a commit.
"""


@dataclass(frozen=True)
class Judgement:
    """What promotion decided about one candidate stream, and the sentence behind it."""

    verdict: str
    said: str
    data: bytes = b""


def held(directory: Path) -> list[tuple[str, str]]:
    """Every declared secret value this machine holds, from `.env` and from every overlay.

    The overlays matter as much as `.env` and are the easy thing to forget: the team key lives in
    `.env.<event>` rather than in `.env` precisely so a Run pointed elsewhere never holds it
    (`docs/credentials.md`), so a scan that read only `.env` would be blind to the one credential
    that cannot be rotated.

    A **list of pairs and not a mapping**, because one name legitimately holds different values in
    different files — a `CTFD_API_TOKEN` per Board is the standing case — and a mapping keeps only
    the last one read. A stream from the other Board would then scan clean against a token it does
    not carry, which is the failure this whole function exists to prevent.
    """
    values: list[tuple[str, str]] = []
    for path in credentials_held.env_files(directory):
        for name, value in env_file.assignments(path.read_text()).items():
            if name in SECRETS and value.strip() and (name, value) not in values:
                values.append((name, value))
    return values


def carries(data: bytes, secrets: Sequence[tuple[str, str]]) -> list[str]:
    """Which declared credentials appear in these bytes, by name and never by value.

    Asked one value at a time, through the redactor the Solver itself runs: a hit is exactly "the
    recorder would have changed this", which covers the base64 and URL-encoded spellings without
    this file having to know what they are. One at a time rather than all at once because the answer
    has to name which credential, and a redactor built over the whole set says only that something
    changed.
    """
    return sorted({name for name, value in secrets if Redactor({name: value}).redact(data) != data})


def snapshot(source: Path) -> bytes | None:
    """The stream's bytes, taken under its own lock — or `None` where a writer holds it.

    The same `flock` the recorder takes on every append, which is what makes this both the liveness
    test and the consistency guarantee: hold it and no line can be half-written underneath the read.
    """
    with source.open("rb") as reading:
        try:
            fcntl.flock(reading.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        try:
            return reading.read()
        finally:
            fcntl.flock(reading.fileno(), fcntl.LOCK_UN)


def judge(source: Path, *, stale_after: float, now: dt.datetime, already: Path) -> Judgement:
    """Whether this stream may be promoted, and what its line will say.

    `already` is where it would land, read to refuse a promotion that would *shrink* the record: a
    Run survives a restart and its stream grows, so a shorter file replacing a longer one is a
    record being lost rather than updated, and nobody is standing here to notice.
    """
    data = snapshot(source)
    if data is None:
        return Judgement(REFUSE, "a writer holds this stream's lock — the Run is live")

    run = stream.read(source)
    if not run.worked:
        return Judgement(SKIP, "reached no Attempt — a probe rather than a Run")

    quiet = (now - last).total_seconds() if (last := run.last_written) else stale_after
    if quiet < 0:
        return Judgement(REFUSE, "last written in the future — this machine's clock disagrees with the Run's")
    if quiet < stale_after:
        return Judgement(REFUSE, f"last written {quiet:.0f}s ago, inside the {stale_after:.0f}s window — may be live")

    if already.is_file() and len(stream.read(already).records) > len(run.records):
        return Judgement(REFUSE, f"{already} already holds more records than this stream does")

    killed = "" if run.closed else "; no run-close — this Run was killed, and its line says so"
    said = f"{len(run.records)} record(s), {len(run.attempts)} attempt(s), bodies left behind{killed}"
    return Judgement(TAKE, said, data=data)


def scanner(named: str | None) -> Path | None:
    """The pinned secret scanner, wherever this machine keeps it."""
    if named:
        return Path(named) if Path(named).exists() else None
    if found := shutil.which("gitleaks"):
        return Path(found)
    return CACHED_SCANNER if CACHED_SCANNER.exists() else None


def gitleaks(scan: Path, directory: Path) -> tuple[bool, str]:
    """Run the scanner over a directory. Answers whether it is clean, and what it said if not.

    `--redact`, because this output is read by whoever ran it and reprinting the secret is not a
    thing a leak check should do.
    """
    finished = subprocess.run(
        [str(scan), "dir", str(directory), "--no-banner", "--no-color", "--redact", "-v"],
        capture_output=True,
        text=True,
        check=False,
    )
    return finished.returncode == 0, (finished.stdout + finished.stderr).strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_ids", nargs="*", help="which Runs to promote (default: every one under --state)")
    parser.add_argument("--state", default="state", help="the host mount a Run wrote through (default: state)")
    parser.add_argument("--into", default=stream.PROMOTED, help="where promoted streams land (default: runs)")
    parser.add_argument("--gitleaks", default=None, help="the pinned scanner, if it is not on PATH")
    parser.add_argument(
        "--stale-after",
        type=float,
        default=STALE_AFTER_SECONDS,
        help=f"how long a stream must be quiet before it counts as finished (default {STALE_AFTER_SECONDS:.0f}s)",
    )
    parser.add_argument("--dry-run", action="store_true", help="scan and report, but move nothing")
    arguments = parser.parse_args(argv)

    root = Path(arguments.state) / "runs"
    if not root.is_dir():
        print(f"no {root} to promote from — nothing was scanned, so nothing about it is known", file=sys.stderr)
        return 2

    scan = scanner(arguments.gitleaks)
    if scan is None:
        print("No gitleaks named by --gitleaks, none on PATH, and none in .cache/scanners.", file=sys.stderr)
        print("Install the pinned build first: sh conformance/install-gitleaks.sh .cache/scanners", file=sys.stderr)
        return 2

    into = Path(arguments.into)
    wanted = arguments.run_ids or sorted(one.name for one in root.iterdir() if (one / "stream.jsonl").is_file())
    holding = held(REPO_ROOT)
    print(f"{len(holding)} declared credential value(s) held on this machine, and every one of them scanned for")

    with tempfile.TemporaryDirectory(prefix="promote-") as scratch:
        staged: list[tuple[str, Path]] = []
        refused, burned = False, False

        for run_id in wanted:
            source = root / run_id / "stream.jsonl"
            if not source.is_file():
                print(f"{SKIP:6} {run_id}: no stream at {source}")
                continue
            judged = judge(
                source,
                stale_after=arguments.stale_after,
                now=dt.datetime.now(dt.timezone.utc),
                already=into / f"{run_id}.jsonl",
            )
            if judged.verdict != TAKE:
                print(f"{judged.verdict:6} {run_id}: {judged.said}")
                refused = refused or judged.verdict == REFUSE
                continue
            if found := carries(judged.data, holding):
                print(f"{REFUSE:6} {run_id}: the stream carries {', '.join(found)}")
                refused = burned = True
                continue
            landing = Path(scratch) / f"{run_id}.jsonl"
            landing.write_bytes(judged.data)
            staged.append((run_id, landing))
            print(f"{TAKE:6} {run_id}: {judged.said}")

        clean, said = gitleaks(scan, Path(scratch)) if staged else (True, "")
        if not clean:
            print(f"\ngitleaks found something in the batch:\n{said}", file=sys.stderr)
            refused = burned = True

        if refused:
            if burned:
                print(BURNED, file=sys.stderr)
            else:
                print("\nNothing was promoted: a Run above was refused, and a batch goes whole or not at all.")
            return 1
        if arguments.dry_run:
            print(f"\n--dry-run: {len(staged)} stream(s) scanned clean and left where they were")
            return 0

        into.mkdir(parents=True, exist_ok=True)
        for run_id, landing in staged:
            shutil.copyfile(landing, into / f"{run_id}.jsonl")

    print(f"\npromoted {len(staged)} stream(s) into {into}/ — commit them, and never the bodies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
