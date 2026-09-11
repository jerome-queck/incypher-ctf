"""Deterministic projection and legacy-view agreement for verified replay."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from solver.event_store import (
    OBSERVATION_RECORDED,
    CommittedEvent,
    EventStore,
    ProjectionMismatchError,
    projection_fields,
)
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.replay_contracts import PROJECTION_SCHEMA_VERSION, PROJECTION_VERSION, VersionedProjection, freeze, thaw


@dataclass
class LegacyRows:
    rows: list[tuple[int, dict[str, Any]]]
    by_sequence: dict[int, list[tuple[int, dict[str, Any]]]]
    by_identity: dict[tuple[object, object], list[tuple[int, dict[str, Any]]]]
    by_blob: dict[tuple[object, object], list[tuple[int, dict[str, Any]]]]

    @classmethod
    def read(cls, path: Path) -> LegacyRows:
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise ProjectionMismatchError("v1 compatibility stream cannot be read") from error
        rows: list[tuple[int, dict[str, Any]]] = []
        by_sequence: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        by_identity: dict[tuple[object, object], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        by_blob: dict[tuple[object, object], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for line_number, line in enumerate(lines, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("record") != "step-end":
                continue
            item = (line_number, row)
            rows.append(item)
            sequence = row.get("seq")
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                by_sequence[sequence].append(item)
            by_identity[(row.get("attempt_id"), row.get("step_index"))].append(item)
            by_blob[(row.get("observation_digest"), row.get("observation_bytes"))].append(item)
        return cls(rows, dict(by_sequence), dict(by_identity), dict(by_blob))


def project(events: list[CommittedEvent], run_id: str) -> VersionedProjection:
    rows = tuple(
        freeze(
            {
                "schema_version": PROJECTION_SCHEMA_VERSION,
                "record": "step-end",
                "seq": event.sequence,
                "run_id": run_id,
                "ts": event.payload["ts"],
                "mono": event.payload["mono"],
                **projection_fields(event, f"sealed/sha256/{event.blob_digest}"),
            }
        )
        for event in events
        if event.event_type == OBSERVATION_RECORDED
    )
    serialized = b"".join(canonical_bytes(thaw(row)) + b"\n" for row in rows)
    return VersionedProjection(
        version=PROJECTION_VERSION,
        schema_version=PROJECTION_SCHEMA_VERSION,
        rows=rows,
        serialized=serialized,
        digest=digest_bytes(serialized),
    )


def verify_legacy_view(store: EventStore, events: list[CommittedEvent]) -> None:
    stream_path = store.run_dir / "stream.jsonl"
    if not stream_path.exists():
        return
    indexed = LegacyRows.read(stream_path)
    used: set[int] = set()
    for event in events:
        if event.event_type != OBSERVATION_RECORDED:
            continue
        candidates = _candidates(indexed, event)
        if not candidates:
            continue
        if len(candidates) > 1:
            raise ProjectionMismatchError("canonical event has duplicate v1 projections", sequence=event.sequence)
        line_number, row = candidates[0]
        if line_number in used:
            raise ProjectionMismatchError("one v1 row projects multiple canonical events", sequence=event.sequence)
        used.add(line_number)
        _verify_row(store, event, row)
    if len(used) != len(indexed.rows):
        raise ProjectionMismatchError("v1 compatibility stream has an unmatched step-end row")


def _candidates(indexed: LegacyRows, event: CommittedEvent) -> list[tuple[int, dict[str, Any]]]:
    begin_sequence = _begin_sequence(event)
    if begin_sequence is not None and (by_sequence := indexed.by_sequence.get(begin_sequence + 1)):
        return by_sequence
    identity = (event.payload["attempt_id"], event.payload["step_index"])
    by_identity = [
        item
        for item in indexed.by_identity.get(identity, [])
        if begin_sequence is None or _sequence_after(item[1], begin_sequence)
    ]
    if by_identity:
        return by_identity
    return indexed.by_blob.get((event.blob_digest, event.blob_bytes), [])


def _verify_row(store: EventStore, event: CommittedEvent, row: Mapping[str, Any]) -> None:
    reference = row.get("observation_ref")
    if not isinstance(reference, str) or not _relative_reference(reference):
        raise ProjectionMismatchError("v1 observation reference is invalid", sequence=event.sequence)
    if row.get("run_id") != store.run_id:
        raise ProjectionMismatchError("v1 compatibility row has the wrong Run", sequence=event.sequence)
    expected = projection_fields(event, reference)
    if any(row.get(key) != value for key, value in expected.items()):
        raise ProjectionMismatchError("v1 compatibility row disagrees with canonical event", sequence=event.sequence)
    try:
        body = (store.run_dir / reference).read_bytes()
    except OSError as error:
        raise ProjectionMismatchError("v1 projection body is missing", sequence=event.sequence) from error
    if body != event.body:
        raise ProjectionMismatchError("v1 projection body disagrees with canonical blob", sequence=event.sequence)


def _begin_sequence(event: CommittedEvent) -> int | None:
    event_id = event.payload.get("event_id")
    if not isinstance(event_id, str):
        return None
    try:
        return int(event_id.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _sequence_after(row: Mapping[str, Any], begin_sequence: int) -> bool:
    sequence = row.get("seq")
    return isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > begin_sequence


def _relative_reference(reference: str) -> bool:
    path = Path(reference)
    return not path.is_absolute() and ".." not in path.parts
