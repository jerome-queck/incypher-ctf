"""Canonical Supervisor lifecycle projection and receipt verification."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from solver.event_store import LIFECYCLE_RECORDED, EventStore, InvalidReceiptError, LifecycleRecorded
from solver.event_store_contracts import BootClosed, LifecycleFact, RunOpened, TerminalDisposition
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.redaction import Redactor

SCHEMA_VERSION = 1
RECEIPT_TYPE = "supervisor-lifecycle"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.supervisor"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


class LifecycleWriter:
    """Append identity-bound Supervisor facts through the one canonical writer."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        *,
        timestamp: Callable[[], str],
    ) -> None:
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self.run_id = run_id
        self._timestamp = timestamp

    def append(
        self,
        event_id: str,
        fact: LifecycleFact,
    ) -> None:
        self.store.append(
            LifecycleRecorded(
                event_id=event_id,
                fact=fact,
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def next_boot_id(self) -> str:
        opened = [
            event
            for event in self.store.events()
            if event.event_type == LIFECYCLE_RECORDED and event.payload["record"] == "boot-open"
        ]
        return f"boot-{len(opened) + 1:06d}"

    def ensure_run_open(self) -> None:
        if not any(event.payload["record"] == "run-open" for event in self._events()):
            self.append("run:open", RunOpened())

    def reconcile_unclosed_boots(self) -> None:
        events = self._events()
        opened = [str(event.payload["boot_id"]) for event in events if event.payload["record"] == "boot-open"]
        closed = {str(event.payload["boot_id"]) for event in events if event.payload["record"] == "boot-close"}
        for boot_id in opened:
            if boot_id not in closed:
                self.append(
                    f"{boot_id}:close",
                    BootClosed(
                        boot_id=boot_id,
                        disposition=TerminalDisposition.CRASHED,
                        detail="reconciled-after-supervisor-loss",
                    ),
                )

    def terminal(self) -> tuple[str, str] | None:
        events = self._events()
        terminal = [event for event in events if event.payload["record"] == "run-close"]
        if not terminal:
            return None
        if len(terminal) != 1:
            raise InvalidReceiptError("Supervisor lifecycle has multiple terminal Run dispositions")
        boot_ids = [str(event.payload["boot_id"]) for event in events if event.payload["record"] == "boot-open"]
        return str(terminal[0].payload["disposition"]), boot_ids[-1] if boot_ids else ""

    def _events(self):
        return [event for event in self.store.events() if event.event_type == LIFECYCLE_RECORDED]

    def write_receipt(self) -> Path:
        path = self.store.canonical_dir / RECEIPT_FILENAME
        atomic_write(path, canonical_bytes(build_receipt(self.store)) + b"\n")
        return path


def build_receipt(store: EventStore) -> dict[str, Any]:
    events = store.events()
    lifecycle = [event for event in events if event.event_type == LIFECYCLE_RECORDED]
    terminal = [event for event in lifecycle if event.payload["record"] == "run-close"]
    if len(terminal) != 1:
        raise InvalidReceiptError("Supervisor lifecycle has no unique terminal Run disposition")
    boot_ids = [str(event.payload["boot_id"]) for event in lifecycle if event.payload["record"] == "boot-open"]
    services = [str(event.payload["service"]) for event in lifecycle if event.payload["record"] == "service-started"]
    signals = [str(event.payload["signal"]) for event in lifecycle if event.payload["record"] == "signal-forwarded"]
    reaped = [event for event in lifecycle if event.payload["record"] == "child-reaped"]
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": store.run_id,
        "boot_ids": boot_ids,
        "service_order": services,
        "signal_trace": signals,
        "child_reap_result": {
            "reaped_children": sum(int(event.payload["reaped_children"]) for event in reaped),
        },
        "terminal_disposition": terminal[0].payload["disposition"],
        "event_chain_head": events[-1].event_digest if events else "",
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Supervisor lifecycle receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Supervisor lifecycle receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Supervisor lifecycle receipt path does not identify Run state")
    run_dir = receipt_path.parent.parent
    state = run_dir.parent.parent
    expected = build_receipt(EventStore(state, run_id=run_dir.name))
    if supplied != expected:
        raise InvalidReceiptError("Supervisor lifecycle receipt does not match canonical state")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


__all__ = [
    "LifecycleWriter",
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "build_receipt",
    "manifest_receipt",
    "verify_receipt",
]
