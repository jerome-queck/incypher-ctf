"""Eval question 2 — **do the stall thresholds cut early or late?** Replayed, at any value.

    python3 scripts/eval_thresholds.py [--event <name>] [stream ...] [--repeats 1,2] [--novelty 3,5,8] [--cliff 15,25,40]

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) asks for a replay at
N thresholds against known outcomes, and this is it. Every number in `solver/stall.py` is
uncalibrated and none of them has a source — there is no academic work allocating a fixed budget
across a Board — so the only way they stop being guesses is a Run replayed at values other than the
one that was live.

**Shadow mode is not a Solver feature, and this is why it never had to be.** The counters are pure
functions over the Step stream (ADR-0009), so replaying at a threshold is an offline query rather
than a second set of counters running in the hot path — and it reports what *every* threshold would
have done rather than only the one that was live. Nothing in `solver/` knows this file exists.

**It is not a second implementation of the rule either.** The `Watch` this drives is the one
`solver/run.py` drives, seeded and segmented the same way: a fresh `Watch` per turn carrying the
model Steps spent so far, one `Deadline` for the whole Attempt, and only the **model's** Steps fed
to it — never the spawn, the recon cascade, the deploy or a Flag submission. A replay against a
re-written counter would measure a rule that never ran.

Three things about the clocks, named rather than hidden:

- **The budget leg is reconstructed** from `budget_s` and the Attempt's first Step, because the
  scheduler computed the real deadline just before that Step and never wrote it down. It is out by
  the length of one deploy.
- **The Instance leg is the Board's, not ours.** `instance_until` is read off the Attempt's open
  line, so it is exact where the budget leg is reconstructed — but an Attempt whose Lease carried no
  stated deadline records `null`, and there the Instance clock is simply absent rather than wrong.
- **The circuit breaker is not replayed.** It is a judgement about the Solver rather than a
  threshold about the Challenge (`solver/stall.py`), and an Attempt it closed is reported here under
  whatever the counters reached.

`--repeats`, `--novelty` and `--cliff` each take a comma-separated list and the grid is their cross
product, so one command answers what a whole region of the parameter space would have done. The row
marked `live` is the setting the code ships with today.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import stream  # noqa: E402
from solver.record import FLAG  # noqa: E402
from solver.stall import Deadline, Thresholds, Watch  # noqa: E402

# The Step a Flag arrives on. A threshold that would have cut before it is a threshold that costs a
# Flag, which is the one outcome no amount of saved budget pays for.
SUBMIT = "flag-submit"


@dataclass(frozen=True)
class Replayed:
    """What one Attempt would have done under one set of thresholds.

    `at_seq` is the record the cut lands on, so it can be compared against the seq a Flag arrived
    at without either side needing a clock. Zero means nothing ever fired: the Attempt would have
    run to whatever ended it in life.
    """

    attempt_id: str
    cause: str
    at_seq: int
    steps: int
    seconds: float
    checkpoints: tuple[int, ...]

    @property
    def cut(self) -> bool:
        return bool(self.cause)


def flag_at(attempt: stream.Attempt) -> int:
    """The seq of the submission the Board accepted, or 0 where this Attempt won nothing."""
    if attempt.cause != FLAG:
        return 0
    accepted = [step.seq for step in attempt.spent(SUBMIT) if step.exit_code == 0]
    return accepted[-1] if accepted else 0


def replay(attempt: stream.Attempt, thresholds: Thresholds, *, said: Sequence[tuple[int, str]] = ()) -> Replayed:
    """Drive the real stall call over one recorded Attempt, at thresholds of the caller's choosing.

    The Claims are handed over interleaved with the Steps by sequence number, because a volunteered
    "impossible" shortens the budget where it lands and not at the end. A promoted stream carries no
    Claim bodies, so `said` is empty there and the one cause that reads prose simply cannot fire —
    which the caller reports rather than papering over.
    """
    steps = attempt.model_steps
    began = steps[0].ts if steps else None
    if began is None:
        return Replayed(attempt.attempt_id, "", 0, 0, 0.0, ())

    deadline = Deadline(
        budget=began + dt.timedelta(seconds=attempt.budget_s or 0),
        # The Board's own deadline for this Attempt's Instance, off the open line. Without it the
        # replay has only one clock and `cut:instance-expired` could never fire under any threshold
        # — a whole cause of the closed vocabulary silently unreachable.
        instance=stream.at(str(attempt.instance_until or "")),
    )
    spoken = dict(said)
    spent, marks = 0, []

    for turn in attempt.turns:
        # A fresh `Watch` per turn, seeded with what the model has spent — `solver/run.py`'s rule
        # exactly: the counters are about one trajectory, and a turn that re-orients itself is not
        # the same trajectory, while the one `Deadline` belongs to the whole Attempt.
        watch = Watch(deadline=deadline, thresholds=thresholds, steps=spent)
        for step in turn.steps:
            now = step.ts or began
            for seq in sorted(spoken):
                if seq >= step.seq:
                    break
                watch.said(spoken.pop(seq), now=now)
            before = len(watch.checkpoints)
            watch.observed(step.command_raw, exit_code=step.exit_code, digest=step.observation_digest)
            spent = watch.steps
            if len(watch.checkpoints) > before:
                marks.append(spent)
            if cause := watch.cause(now):
                elapsed = (now - began).total_seconds()
                return Replayed(attempt.attempt_id, cause, step.seq, spent, elapsed, tuple(marks))

    ended = steps[-1].ts or began
    return Replayed(attempt.attempt_id, "", 0, spent, (ended - began).total_seconds(), tuple(marks))


@dataclass
class Verdict:
    """One threshold setting, read across every Attempt: what it saves, and what it costs."""

    thresholds: Thresholds
    cut: int = 0
    steps_saved: int = 0
    seconds_saved: float = 0.0
    flags_lost: int = 0
    by_cause: dict[str, int] = field(default_factory=dict)

    def took(self, attempt: stream.Attempt, replayed: Replayed) -> None:
        if not replayed.cut:
            return
        self.cut += 1
        self.by_cause[replayed.cause] = self.by_cause.get(replayed.cause, 0) + 1
        self.steps_saved += max(0, len(attempt.model_steps) - replayed.steps)
        self.seconds_saved += max(0.0, attempt.seconds - replayed.seconds)
        won = flag_at(attempt)
        if won and replayed.at_seq < won:
            self.flags_lost += 1


def _grid(repeats: Sequence[int], novelty: Sequence[int], cliff: Sequence[int]) -> list[Thresholds]:
    return [Thresholds(repeats=one, novelty=two, cliff=three) for one in repeats for two in novelty for three in cliff]


def _numbers(given: str) -> list[int]:
    return [int(part) for part in given.split(",") if part.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = stream.asking(__doc__)
    parser.add_argument("--repeats", type=_numbers, default=[1, 2], help="repetition thresholds to replay at")
    parser.add_argument("--novelty", type=_numbers, default=[3, 5, 8], help="novelty thresholds to replay at")
    parser.add_argument("--cliff", type=_numbers, default=[15, 25, 40], help="step cliffs to replay at")
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths, event=arguments.event)
    print(stream.heading(runs, event=arguments.event))
    attempts = [(run, attempt) for run in runs for attempt in run.attempts if attempt.opened]
    if not attempts:
        print("\nno Attempt in these streams — nothing to replay")
        return 0

    live = Thresholds()
    settings = _grid(arguments.repeats, arguments.novelty, arguments.cliff)
    if live not in settings:
        settings.insert(0, live)

    said = {attempt.attempt_id: run.said(attempt) for run, attempt in attempts}
    prose = sum(1 for spoken in said.values() if spoken)
    print(f"\n{len(attempts)} attempt(s), {len(settings)} threshold setting(s); Claim bodies for {prose} of them")
    if not prose:
        print("  no Claim bodies here, so cut:self-reported-impossible cannot fire in this replay")

    verdicts = []
    for thresholds in settings:
        verdict = Verdict(thresholds)
        for _, attempt in attempts:
            verdict.took(attempt, replay(attempt, thresholds, said=said[attempt.attempt_id]))
        verdicts.append(verdict)

    print("\nwhat each setting would have done:\n")
    print(
        stream.table(
            ["repeats", "novelty", "cliff", "", "cut", "steps saved", "minutes saved", "FLAGS LOST", "by cause"],
            [
                [
                    verdict.thresholds.repeats,
                    verdict.thresholds.novelty,
                    verdict.thresholds.cliff,
                    "live" if verdict.thresholds == live else "",
                    f"{verdict.cut}/{len(attempts)}",
                    verdict.steps_saved,
                    f"{verdict.seconds_saved / 60:.1f}",
                    verdict.flags_lost or "",
                    ", ".join(f"{cause} {count}" for cause, count in sorted(verdict.by_cause.items())),
                ]
                for verdict in verdicts
            ],
        )
    )

    print("\nthe live setting, attempt by attempt:\n")
    print(
        stream.table(
            ["attempt", "category", "model steps", "replayed to", "would cut", "really ended", "checkpoints at"],
            [
                [
                    attempt.ref,
                    attempt.category,
                    len(attempt.model_steps),
                    replayed.steps,
                    replayed.cause or "—",
                    attempt.cause or stream.NEVER_CLOSED,
                    ", ".join(str(mark) for mark in replayed.checkpoints) or "none",
                ]
                for _, attempt in attempts
                if (replayed := replay(attempt, live, said=said[attempt.attempt_id]))
            ],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
