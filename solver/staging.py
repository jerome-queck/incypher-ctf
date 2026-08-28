"""What one Attempt holds of the Board's files, named as the model will see them.

A type of its own rather than `intake.Attachment` reused, and the field it does **not** have is the
point. `Attachment.path` is Intake's copy under `/state/runs/`, inside the Run's own record: a
prompt naming it pointed the model two levels above the stream its own stall is judged from
([#126](https://github.com/jerome-queck/incypher-ctf/issues/126)), and `Intake._attachments` asks
`path.exists()` of that same copy every cycle to decide whether to re-download the Board — a
question whose whole worth is that nobody but Intake writes the file it asks about. Neither path can
be mistaken for the other here, because only one of them is reachable.

It lives outside `solver/run.py`, which mints it, because `solver/prompt.py` cannot import that
module: `run` imports `prompt` for the one marker the model writes back, and the second edge would
be a cycle.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Staged:
    """One of the Board's files as this Attempt holds it.

    `nbytes` is what the Board served, not what is on disk now: an Attempt stages once and is worked
    over several turns, and the model spends those turns changing the directory underneath.

    `landing` is not optional. A file we do not hold is never staged at all, so an Attempt holding
    one of these has the bytes on disk under that path.
    """

    name: str
    nbytes: int
    landing: Path

    @property
    def under_the_boards_name(self) -> bool:
        """Whether `ls` and the Board's own prose agree about this file.

        They do not where the name was already taken when the Attempt opened, and the Board's copy
        landed under one minted from its own digest instead (`Run._landed`,
        [#119](https://github.com/jerome-queck/incypher-ctf/issues/119)).
        """
        return self.landing.name == self.name
