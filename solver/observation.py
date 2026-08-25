"""What a Step produced: stored whole, shown short, and identified by a digest over both.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) asks for two things
that read as a contradiction until the reader is named:

- **The record keeps the body whole**, in a file beside the stream, so a JSONL line stays small and
  fixed-size and a run that produced gigabytes is still cheap to parse.
- **The model is shown head and tail with the middle elided behind a visible marker.** Silent
  truncation is a lie the model then reasons over — a listing cut mid-way reads as a file that is
  not there.

The digest is a function of content alone, because novelty is a rule over digests and every rule
this repository has is expected to change. The caller hands it *redacted* bytes, which is what
stops it being an oracle for a secret.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Big enough to hold a `file`, an `exiftool` or a directory listing outright, small enough that a
# core dump does not become the Attempt's whole context. A parameter rather than a constant at the
# call site: the number is uncalibrated until a Run has been replayed, like every other number here.
OBSERVATION_LIMIT_BYTES = 24_000


@dataclass(frozen=True)
class Observation:
    """One Step's output, as the stream refers to it.

    Carries no body: the bytes are on disk under `ref`, and holding them here would put the run's
    largest artefact in the orchestrator's memory for as long as the Step's record lived.
    """

    digest: str
    nbytes: int
    ref: str
    shown: str


def digest_of(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def elide(body: bytes, limit: int = OBSERVATION_LIMIT_BYTES) -> str:
    """The body as the model is shown it — whole, or head and tail around a marker that says so.

    Both ends are kept because both carry signal: a tool names itself at the top and reports its
    verdict at the bottom, so a head-only cut loses the half that says what happened.
    """
    if len(body) <= limit:
        return _readable(body)
    head = limit // 2
    tail = limit - head
    missing = len(body) - limit
    return f"{_readable(body[:head])}\n[... {missing} bytes elided from the middle ...]\n{_readable(body[-tail:])}"


def _readable(body: bytes) -> str:
    """Bytes rendered for a model. Lossy by design — the body file is what keeps them exact."""
    return body.decode("utf-8", "replace")
