"""Serial authority adapter and retained ambiguity receipt projection."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.write_reservation import EffectIdentity
from solver.submission.ambiguity import (
    FENCE_SECONDS,
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    PROBE_OFFSETS,
    RECEIPT_TYPE,
    SCHEMA_VERSION,
)


def verify_receipt(path: Path) -> Path:
    receipt = Path(path)
    try:
        raw = receipt.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("ambiguous-submission receipt cannot be read") from error
    if raw != canonical_bytes(document) + b"\n":
        raise ValueError("ambiguous-submission receipt is not canonical")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("receipt_type") != RECEIPT_TYPE:
        raise ValueError("ambiguous-submission receipt contract is unsupported")
    if document.get("fence_seconds") != FENCE_SECONDS:
        raise ValueError("ambiguous-submission fence dial is invalid")
    if document.get("producer") == "external-evaluator":
        supplied = document.get("evaluator_seal")
        unsigned = dict(document)
        unsigned.pop("evaluator_seal", None)
        if supplied != digest_bytes(canonical_bytes(unsigned)):
            raise ValueError("ambiguous-submission Evaluator seal is invalid")
        observed = document.get("observed_effect_trace")
        if not isinstance(observed, list) or any(item.get("posts") != 1 for item in observed):
            raise ValueError("ambiguous-submission observed POST trace is invalid")
    starts: dict[str, Mapping[str, object]] = {}
    closed: dict[str, Mapping[str, object]] = {}
    probes: dict[str, list[Mapping[str, object]]] = {}
    for event in document.get("events", []):
        candidate = str(event.get("candidate_id", ""))
        if event.get("event") == "possibly-sent":
            if candidate in starts or event.get("posts") != 1:
                raise ValueError("ambiguous-submission no-resend trace is invalid")
            if float(event["deadline"]) != float(event["wire_started_at"]) + FENCE_SECONDS:
                raise ValueError("ambiguous-submission deadline is invalid")
            complete = event.get("complete_identity")
            if complete:
                basis = {
                    key: complete[key]
                    for key in (
                        "board_identity",
                        "challenge_id",
                        "challenge_revision",
                        "instance_provenance",
                        "candidate_digest",
                        "submission_epoch",
                    )
                }
                payload = digest_bytes(canonical_bytes(basis))
                expected_effect = EffectIdentity(
                    "board.submit-candidate", str(basis["challenge_id"]), payload
                ).fingerprint
                if (
                    complete.get("reservation_id") != f"serial-submit:{payload}"
                    or complete.get("effect_id") != expected_effect
                ):
                    raise ValueError("ambiguous-submission complete effect identity is inconsistent")
            starts[candidate] = event
        elif event.get("event") == "fence-closed":
            closed[candidate] = event
        elif event.get("event") == "evidence-probe":
            probes.setdefault(candidate, []).append(event)
    expected = [{"candidate_id": item, "posts": 1} for item in sorted(starts)]
    if document.get("no_resend_trace") != expected:
        raise ValueError("ambiguous-submission no-resend trace is invalid")
    if document.get("producer") == "external-evaluator":
        observed_ids = sorted(item["effect_id"] for item in document["observed_effect_trace"])
        started_ids = sorted(str(item["effect_id"]) for item in starts.values())
        if observed_ids != started_ids:
            raise ValueError("Evaluator POST trace does not match ambiguous effects")
    if any(item not in starts for item in closed):
        raise ValueError("ambiguous-submission close lacks a possibly-sent effect")
    exact_dispositions = {"accepted": "correct", "rejected": "incorrect"}
    for candidate, event in closed.items():
        disposition = str(event.get("disposition", ""))
        if disposition in exact_dispositions and not any(
            probe.get("authenticated") is True
            and probe.get("candidate_match") is True
            and probe.get("kind") == "exact-candidate-verdict"
            and probe.get("verdict") == exact_dispositions[disposition]
            and probe.get("source") == event.get("provenance")
            and all(
                probe.get(field)
                for field in ("request_id", "classified_event_id", "binding_digest", "peer_identity_digest")
            )
            and probe.get("evidence_effect_id") == starts[candidate].get("effect_id")
            and probe.get("submission_epoch") == starts[candidate].get("complete_identity", {}).get("submission_epoch")
            and probe.get("supplied_value_digest")
            == starts[candidate].get("complete_identity", {}).get("candidate_digest")
            and probe.get("evidence_complete_identity") == starts[candidate].get("complete_identity")
            and probe.get("row_type") == "submission"
            and probe.get("board_row_id")
            and probe.get("submitted_at")
            for probe in probes.get(candidate, [])
        ):
            raise ValueError("definitive ambiguity lacks exact authenticated provenance")
        if disposition == "unknown-and-spent" and event.get("provenance") == "fence-expired":
            if float(event.get("at", -1)) != float(starts[candidate]["deadline"]):
                raise ValueError("ambiguity expiry does not match its original deadline")
            offsets = [probe.get("scheduled_offset") for probe in probes.get(candidate, [])]
            if offsets != list(PROBE_OFFSETS):
                raise ValueError("ambiguity expiry lacks the complete reconciliation schedule")
    return receipt


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))
