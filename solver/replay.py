"""Verify canonical Run state and materialize its deterministic projection."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from solver.event_store import CommittedEvent, EventStore, ProjectionMismatchError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.redaction import Redactor
from solver.replay_contracts import (
    PROJECTION_FILENAME,
    PROJECTIONS_DIRECTORY,
    RECEIPT_FILENAME,
    REPLAY_SCHEMA_VERSION,
    ReplayVerification,
    VersionedProjection,
    freeze,
)
from solver.replay_projection import project, verify_legacy_view
from solver.replay_receipt import (
    build_receipt,
    manifest_receipt,
    projection_digest,
    read_receipt,
    receipt_location,
    validate_receipt,
)
from solver.work_generation import GenerationFence


def verify_and_materialize_run_state(state: Path, run_id: str, redactor: Redactor | None = None) -> ReplayVerification:
    """Verify canonical state, then write only its deterministic derived artifacts."""

    store, events, projection = _verified_replay(state, run_id, redactor)
    fence = GenerationFence(
        state,
        run_id,
        redactor or Redactor({}),
        timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
    )
    if fence.reconcile_restart():
        store, events, projection = _verified_replay(state, run_id, redactor)
    fence.write_receipt()
    projection_path = store.canonical_dir / PROJECTIONS_DIRECTORY / PROJECTION_FILENAME
    if projection_path.exists():
        existing = _read_bytes(projection_path, "v1 projection")
        if existing != projection.serialized:
            if _stale_projection(store, events, existing):
                atomic_write(projection_path, projection.serialized)
            else:
                raise ProjectionMismatchError("v1 projection disagrees with canonical replay")
    else:
        atomic_write(projection_path, projection.serialized)

    receipt = build_receipt(run_id, events, projection)
    receipt_path = store.canonical_dir / RECEIPT_FILENAME
    receipt_body = canonical_bytes(receipt) + b"\n"
    if not receipt_path.exists() or _read_bytes(receipt_path, "verified-replay receipt") != receipt_body:
        atomic_write(receipt_path, receipt_body)
    return _result(run_id, events, projection, receipt, receipt_path)


def verify_replay_receipt(
    path: Path,
    *,
    state: Path | None = None,
    run_id: str | None = None,
    redactor: Redactor | None = None,
) -> ReplayVerification:
    """Verify an existing replay receipt without rewriting Run state."""

    receipt_path = Path(path)
    supplied = read_receipt(receipt_path)
    resolved_state, resolved_run_id = receipt_location(receipt_path, state=state, run_id=run_id)
    store, events, projection = _verified_replay(resolved_state, resolved_run_id, redactor)
    projection_path = store.canonical_dir / PROJECTIONS_DIRECTORY / PROJECTION_FILENAME
    if not projection_path.is_file() or _read_bytes(projection_path, "v1 projection") != projection.serialized:
        raise ProjectionMismatchError("v1 projection disagrees with canonical replay")
    expected = build_receipt(resolved_run_id, events, projection)
    validate_receipt(supplied, expected)
    return _result(resolved_run_id, events, projection, expected, receipt_path)


def verified_replay_manifest_receipt(result: ReplayVerification) -> dict[str, str]:
    """Describe this verified receipt for the release-candidate manifest."""

    return manifest_receipt(result)


def _verified_replay(
    state: Path, run_id: str, redactor: Redactor | None
) -> tuple[EventStore, list[CommittedEvent], VersionedProjection]:
    store = EventStore(Path(state), run_id=run_id, redactor=redactor)
    events = store.events()
    GenerationFence(
        state,
        run_id,
        redactor or Redactor({}),
        timestamp=lambda: "",
    ).projection()
    projection = project(events, run_id)
    verify_legacy_view(store, events)
    return store, events, projection


def _stale_projection(store: EventStore, events: list[CommittedEvent], existing: bytes) -> bool:
    receipt_path = store.canonical_dir / RECEIPT_FILENAME
    if not receipt_path.is_file():
        return False
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    old_count = receipt.get("event_count")
    old_head = receipt.get("chain_head")
    if not isinstance(old_count, int) or isinstance(old_count, bool) or old_count < 0 or old_count > len(events):
        return False
    expected_old_head = events[old_count - 1].event_digest if old_count else ""
    return (
        old_head == expected_old_head
        and projection_digest(receipt) == digest_bytes(existing)
        and old_count != len(events)
    )


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ProjectionMismatchError(f"{label} cannot be read") from error


def _result(
    run_id: str,
    events: list[CommittedEvent],
    projection: VersionedProjection,
    receipt: dict[str, object],
    receipt_path: Path,
) -> ReplayVerification:
    return ReplayVerification(
        schema_version=REPLAY_SCHEMA_VERSION,
        run_id=run_id,
        event_count=len(events),
        chain_head=events[-1].event_digest if events else "",
        projection=projection,
        receipt=freeze(receipt),
        receipt_path=receipt_path,
    )


__all__ = [
    "ReplayVerification",
    "VersionedProjection",
    "verify_and_materialize_run_state",
    "verify_replay_receipt",
    "verified_replay_manifest_receipt",
]
