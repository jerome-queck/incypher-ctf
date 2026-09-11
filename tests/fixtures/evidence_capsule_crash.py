"""Abrupt-exit driver for evidence-capsule process-loss tests."""

import os
import sys
from pathlib import Path

from solver.evidence_capsule import CapsuleRequest, EvidenceCapsulePromoter
from solver.evidence_capsule_contracts import BlobSelection, CapsuleRefused, ReceiptContract, ReceiptRegistry
from solver.evidence_capsule_scan import HostSanitizationAuthority
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.manifest import parse_manifest
from solver.write_reservation import Capacity, WriteAuthority, WriteProfile


AREA = Capacity(
    bytes=2_000_000,
    objects=40,
    operations=200,
    create=200,
    append=200,
    rename=200,
    unlink=200,
    durability=500,
)
PROFILE = WriteProfile(AREA, AREA, AREA)


class FileVaultReader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> bytes:
        return self.path.read_bytes()


def main() -> None:
    root, crash_point = Path(sys.argv[1]), sys.argv[2]
    store = EventStore(root / "source", run_id="process-loss")
    events = store.terminal_snapshot().events
    evidence_digest = next(event.blob_digest for event in events if event.event_type == "observation.recorded")

    def validate(document, source):
        causal = [
            event["payload"]["blob_digest"] for event in source.events if event["event_type"] == "observation.recorded"
        ]
        if causal != [document.get("evidence_digest")]:
            raise CapsuleRefused("receipt evidence claim does not match its causal source")
        return [BlobSelection(causal[0], "application/json", True)]

    receipt = canonical_bytes(
        {
            "schema_version": 1,
            "kind": "promotion-transaction",
            "producer": "synthetic-proof",
            "evidence_digest": evidence_digest,
            "result": "passed",
        }
    )

    def crash(phase):
        if phase == crash_point:
            os._exit(91)

    promoter = EvidenceCapsulePromoter(
        store=store,
        candidate_manifest=parse_manifest((root / "candidate.json").read_bytes()),
        manifest_row_id="core.receipts-capsules",
        registry=ReceiptRegistry([ReceiptContract("promotion-transaction", 1, "synthetic-proof", validate)]),
        scan_authority=HostSanitizationAuthority(
            root / "host-scan-authority",
            host_paths=(root,),
            vault_reader=FileVaultReader(root / "scanner-vault.json"),
        ),
        write_authority=WriteAuthority(root / "authority", PROFILE),
        runs_directory=root / "runs",
        hook=crash,
    )
    promoter.promote(CapsuleRequest(receipt, "receipt:promotion-transaction"))


if __name__ == "__main__":
    main()
