"""Exactly five things cross an Attempt boundary, and this is the only place they are named.

[ADR-0005](../docs/adr/0005-the-stall-call-lives-outside-the-solving-model.md): Observations and
orchestrator-derived records cross; hypotheses, notes and conclusions do not. **v1 writes no
summaries at all** — a summary is a Claim with a permanent address, and that is exactly how a
verdict survives a reset. Reset-on-stall is the only compaction there is.

The five, and why each survives:

1. **Board-given facts** — description, category, files, connection info. Not ours to forget.
2. **The recon block** — all of it Observation, none of it Claim (`solver/recon.py`).
3. **The Checkpoint list, each with its replay command** — state transitions, re-verifiable.
4. **Commands already tried, with exit status** — *information, never prohibition.* A command that
   failed before a Checkpoint may be exactly right after one, so the list clears whenever a new
   Checkpoint lands (`solver/stall.py`) and never becomes a ban.
5. **One Attempt line per Attempt**, orchestrator-composed, with exactly one model-authored field.

The single carve-out is that field, and it is deliberate: a reset carrying only verified facts is a
*deterministic* starting state, and the next Attempt would have every reason to repeat the last. The
model may name **what it tried**; it may not state what it concluded. `0 checkpoints` is a fact and
does the work a verdict would have done.

**One line, enforced rather than intended**, so K Attempts cost K lines — that bound is the whole
defence against the overloaded-findings-file doom loop, and it is why the label is cut to length
here rather than trusted to be short.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from solver.stall import Checkpoint, Tried, Watch

# The provenance marker every derived line opens with, so a derived record can never be mistaken
# for an Observation. Flag verification and the Checkpoint rule both skip a line carrying it: the
# approach label is model-authored, it crosses a boundary, and a model that wrote a plausible Flag
# into its own label must not have it swept back in as something a command produced.
DERIVED = "[derived]"

# One line means one line. The label is the only field a model writes, so it is the only field that
# could arrive as a paragraph.
LABEL_LIMIT = 80
UNNAMED = "unnamed"
NO_COMMAND = "none"

# The field separator the line is read by, taken out of the label so a label cannot forge a field.
FIELD = " · "

# The five, in the order they are rendered. A list rather than five calls, so "exactly five" is
# something the code counts rather than something a reader has to.
SECTIONS = (
    "What the Board gave us",
    "What recon observed",
    "Checkpoints — the environment moved, and this replays it",
    "Commands already tried — information, never a ban",
    "Earlier Attempts on this Challenge",
)

NOTHING_YET = "— nothing yet"


def trimmed(text: str, *, empty: str) -> str:
    """One field of the line, cut to one field of one line.

    Applied to the approach label and to the last command alike. The label is model-authored and the
    command was model-chosen, so both are text a model picked: everything that could make either
    more than a field is taken out — the provenance marker, so neither can forge a derived record;
    the separator, so neither can forge a field; and every newline, because a heredoc in a command
    would cost the same bound the label does.
    """
    bare = text.replace(DERIVED, " ").replace(FIELD, " ").replace("·", " ")
    said = " ".join(bare.split())
    if not said:
        return empty
    return said if len(said) <= LABEL_LIMIT else said[: LABEL_LIMIT - 1].rstrip() + "…"


def label(approach: str) -> str:
    """The one model-authored field the boundary carries."""
    return trimmed(approach, empty=UNNAMED)


@dataclass(frozen=True)
class Line:
    """One finished Attempt, in one line — five orchestrator-composed facts and one model field."""

    sequence: int
    approach: str
    steps: int
    checkpoints: int
    cause: str
    last: str

    @classmethod
    def of(cls, watch: Watch, *, sequence: int, approach: str, cause: str) -> Line:
        """One finished Attempt as its line — the facts read here rather than by whatever is
        folding it in, because these are the fields this type is made of."""
        return cls(
            sequence=sequence,
            approach=approach,
            steps=watch.steps,
            checkpoints=len(watch.checkpoints),
            cause=cause,
            last=watch.last,
        )

    def render(self) -> str:
        return FIELD.join(
            [
                f"{DERIVED} Attempt {self.sequence}",
                f'approach: "{label(self.approach)}"',
                f"{self.steps} steps",
                f"{self.checkpoints} checkpoints",
                self.cause,
                f"last: {trimmed(self.last, empty=NO_COMMAND)}",
            ]
        )


@dataclass
class Boundary:
    """What one Challenge carries from each Attempt into its next, and the guarantee of no sixth.

    It accumulates across a Challenge's Attempts and holds nothing a model wrote except the labels.
    The Checkpoint list grows; the tried list is **replaced** by the last Attempt's whenever that
    Attempt found a Checkpoint, because the list clears on a Checkpoint and a Checkpoint in the
    middle of an Attempt invalidates everything carried in before it.
    """

    checkpoints: list[Checkpoint] = field(default_factory=list)
    tried: list[Tried] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)

    def closed(self, watch: Watch, *, approach: str, cause: str) -> Line:
        """Fold one finished Attempt in, and answer with the line it costs."""
        self.checkpoints += watch.checkpoints
        self.tried = list(watch.tried) if watch.checkpoints else _merged(self.tried, watch.tried)
        line = Line.of(watch, sequence=len(self.lines) + 1, approach=approach, cause=cause)
        self.lines.append(line)
        return line

    def carried(self, *, facts: str, recon: str) -> str:
        """The five things, and the only route by which anything reaches the next Attempt.

        `strict` is what makes "exactly five" something the code enforces rather than something a
        reader counts: a sixth thing added to either tuple and this raises where it was written.
        """
        bodies = (facts, recon, self._checkpoints(), self._tried(), self._lines())
        return "\n\n".join(
            f"## {heading}\n{body or NOTHING_YET}" for heading, body in zip(SECTIONS, bodies, strict=True)
        )

    def _checkpoints(self) -> str:
        return "\n".join(f"- {found.moved}\n  replay: {found.replay}" for found in self.checkpoints)

    def _tried(self) -> str:
        return "\n".join(f"- {entry.command} → exit {entry.exit_code}" for entry in self.tried)

    def _lines(self) -> str:
        return "\n".join(line.render() for line in self.lines)


def _merged(carried_in: list[Tried], spent: list[Tried]) -> list[Tried]:
    """One entry per command, the newest exit status winning — a list that grew a duplicate every
    Attempt would spend the next Attempt's context on the same command five times."""
    latest = {entry.command: entry for entry in [*carried_in, *spent]}
    return list(latest.values())
