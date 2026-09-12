"""Narrow storage boundary for exact, non-model-readable Intake response evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from solver.board import MAX_FETCH_BYTES
from solver.event_store_contracts import BlobDigestMismatchError, MissingBlobError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

PRIVATE_BOARD_RESPONSE_CLASS = "canonical-private-board-response"
MAX_PRIVATE_RESPONSE_BYTES = MAX_FETCH_BYTES + 1
DEFAULT_PRIVATE_EVIDENCE_BUDGET = 64 * MAX_PRIVATE_RESPONSE_BYTES


class IntakeEvidenceWriter:
    """Board-broker writer for bounded raw evidence and its retention registration."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        *,
        max_response_bytes: int = MAX_PRIVATE_RESPONSE_BYTES,
        max_total_bytes: int = DEFAULT_PRIVATE_EVIDENCE_BUDGET,
    ) -> None:
        self._state = Path(state)
        self._run_id = run_id
        self._root = Path(state) / "runs" / run_id / "sealed" / "private-board-response"
        self._blobs = self._root / "sha256"
        self._references = self._root / "references"
        self._max_response_bytes = max_response_bytes
        self._max_total_bytes = max_total_bytes
        self.reconcile()

    def seal(
        self,
        body: bytes,
        *,
        classified_event_id: str,
        sanitized: bytes,
        redaction_proof: dict[str, object],
    ) -> str:
        if not classified_event_id:
            raise ValueError("private Intake evidence needs a classified event identity")
        if len(body) > self._max_response_bytes:
            raise ValueError("private Intake evidence exceeds its bounded storage class")
        digest = digest_bytes(body)
        path = self._blobs / digest
        if path.exists():
            if path.read_bytes() != body:
                raise BlobDigestMismatchError(f"private Intake blob {digest!r} has different content")
        else:
            used = sum(item.stat().st_size for item in self._blobs.glob("*") if item.is_file())
            free = shutil.disk_usage(self._state).free
            if used + len(body) > self._max_total_bytes or len(body) > free:
                raise OSError("private Intake evidence admission refused under storage pressure")
            atomic_write(path, body)
            path.chmod(0o600)
        registration = {
            "schema_version": 1,
            "storage_class": "restart-private-broker",
            "evidence_class": PRIVATE_BOARD_RESPONSE_CLASS,
            "blob_digest": digest,
            "bytes": len(body),
            "classified_event_id": classified_event_id,
            "reachability": "pending-classified-event",
            "event_sequence": 0,
            "event_digest": "",
            "sanitized_digest": digest_bytes(sanitized),
            "sanitized_bytes": len(sanitized),
            "redaction": redaction_proof,
            "retention": "retain-for-replay-unless-interrupted-attempt-retires",
            "export": False,
            "model_readable": False,
        }
        reference = self._references / (hashlib.sha256(classified_event_id.encode()).hexdigest() + ".json")
        encoded = canonical_bytes(registration) + b"\n"
        if reference.exists() and reference.read_bytes() != encoded:
            raise BlobDigestMismatchError("private Intake evidence registration changed identity")
        atomic_write(reference, encoded)
        reference.chmod(0o600)
        return digest

    def commit(self, classified_event_id: str, *, event_sequence: int, event_digest: str) -> None:
        reference = self._reference(classified_event_id)
        registration = self._load(reference)
        if registration.get("classified_event_id") != classified_event_id:
            raise BlobDigestMismatchError("private Intake evidence registration identity changed")
        registration.update(
            {
                "reachability": "canonical-board-classification",
                "event_sequence": event_sequence,
                "event_digest": event_digest,
            }
        )
        atomic_write(reference, canonical_bytes(registration) + b"\n")
        reference.chmod(0o600)

    def abort(self, classified_event_id: str) -> None:
        reference = self._reference(classified_event_id)
        if not reference.exists():
            return
        registration = self._load(reference)
        reference.unlink()
        self._remove_unreferenced_blob(str(registration.get("blob_digest", "")))

    def reconcile(self) -> None:
        references = tuple(self._references.iterdir()) if self._references.exists() else ()
        for reference in references:
            if reference.is_symlink() or not reference.is_file() or reference.suffix != ".json":
                raise BlobDigestMismatchError("private Intake evidence reference inventory is invalid")
            registration = self._load(reference)
            event = _classified_event(
                self._state,
                self._run_id,
                str(registration.get("classified_event_id", "")),
            )
            if event is None:
                if registration.get("reachability") == "pending-classified-event":
                    reference.unlink()
                    self._remove_unreferenced_blob(str(registration.get("blob_digest", "")))
                continue
            if event.payload.get("raw_blob_digest") != registration.get("blob_digest") or event.payload.get(
                "raw_blob_bytes"
            ) != registration.get("bytes"):
                raise BlobDigestMismatchError("private Intake evidence disagrees with canonical classification")
            if registration.get("reachability") == "pending-classified-event":
                self.commit(
                    str(registration["classified_event_id"]),
                    event_sequence=event.sequence,
                    event_digest=event.event_digest,
                )
        registered = (
            {str(self._load(reference).get("blob_digest", "")) for reference in self._references.iterdir()}
            if self._references.exists()
            else set()
        )
        if self._blobs.exists():
            for blob in tuple(self._blobs.iterdir()):
                if (
                    blob.is_symlink()
                    or not blob.is_file()
                    or len(blob.name) != 64
                    or set(blob.name) - set("0123456789abcdef")
                ):
                    raise BlobDigestMismatchError("private Intake evidence blob inventory is invalid")
                if blob.name not in registered:
                    blob.unlink()

    def retire_interrupted_attempt(self, attempt_id: str) -> tuple[str, ...]:
        from solver.event_store import EventStore

        events = EventStore(self._state, run_id=self._run_id).events()
        terminals = [
            event
            for event in events
            if event.event_type == "intake-decision.recorded"
            and event.payload.get("attempt_id") == attempt_id
            and event.payload.get("record") == "unsettled"
            and str(event.payload.get("reason", "")).startswith("interrupted-")
        ]
        if len(terminals) != 1:
            raise ValueError("private Intake evidence retirement needs one interrupted terminal")
        classified_ids = {
            str(event.payload["classified_event_id"])
            for event in events
            if event.event_type == "intake-observation.recorded" and event.payload.get("attempt_id") == attempt_id
        }
        retired = []
        for classified_event_id in sorted(classified_ids):
            reference = self._reference(classified_event_id)
            if not reference.exists():
                continue
            registration = self._load(reference)
            digest = str(registration["blob_digest"])
            reference.unlink()
            self._remove_unreferenced_blob(digest)
            retired.append(classified_event_id)
        return tuple(retired)

    def _reference(self, classified_event_id: str) -> Path:
        return self._references / (hashlib.sha256(classified_event_id.encode()).hexdigest() + ".json")

    @staticmethod
    def _load(path: Path) -> dict[str, object]:
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MissingBlobError("private Intake evidence registration is unavailable") from error
        if not isinstance(value, dict) or raw != canonical_bytes(value) + b"\n":
            raise BlobDigestMismatchError("private Intake evidence registration is invalid")
        return value

    def _remove_unreferenced_blob(self, digest: str) -> None:
        if not digest:
            return
        for reference in self._references.glob("*.json"):
            if self._load(reference).get("blob_digest") == digest:
                return
        path = self._blobs / digest
        if path.exists():
            path.unlink()


class IntakeEvidenceReader:
    """Verifier-only reader requiring the canonical event that keeps evidence reachable."""

    def __init__(self, state: Path, run_id: str) -> None:
        self._state = Path(state)
        self._run_id = run_id
        self._root = Path(state) / "runs" / run_id / "sealed" / "private-board-response"

    def read(self, digest: str, *, classified_event_id: str, sanitized: bytes | None = None) -> bytes:
        reference = self._root / "references" / (hashlib.sha256(classified_event_id.encode()).hexdigest() + ".json")
        try:
            raw_registration = reference.read_bytes()
            registration = json.loads(raw_registration)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MissingBlobError("private Intake evidence registration is unavailable") from error
        expected = {
            "schema_version": 1,
            "storage_class": "restart-private-broker",
            "evidence_class": PRIVATE_BOARD_RESPONSE_CLASS,
            "blob_digest": digest,
            "bytes": registration.get("bytes"),
            "classified_event_id": classified_event_id,
            "reachability": "canonical-board-classification",
            "event_sequence": registration.get("event_sequence"),
            "event_digest": registration.get("event_digest"),
            "sanitized_digest": registration.get("sanitized_digest"),
            "sanitized_bytes": registration.get("sanitized_bytes"),
            "redaction": registration.get("redaction"),
            "retention": "retain-for-replay-unless-interrupted-attempt-retires",
            "export": False,
            "model_readable": False,
        }
        if registration != expected or raw_registration != canonical_bytes(registration) + b"\n":
            raise BlobDigestMismatchError("private Intake evidence registration is invalid")
        event = _classified_event(self._state, self._run_id, classified_event_id)
        if (
            event is None
            or event.sequence != registration["event_sequence"]
            or event.event_digest != registration["event_digest"]
            or event.payload.get("raw_blob_digest") != digest
            or event.payload.get("raw_blob_bytes") != registration["bytes"]
            or event.payload.get("redaction_policy_digest") != registration.get("redaction", {}).get("policy_digest")
        ):
            raise BlobDigestMismatchError("private Intake evidence is not canonically reachable")
        path = self._root / "sha256" / digest
        try:
            body = path.read_bytes()
        except OSError as error:
            raise MissingBlobError(f"private Intake blob {digest!r} is unavailable") from error
        if digest_bytes(body) != digest or len(body) != registration["bytes"]:
            raise BlobDigestMismatchError(f"private Intake blob {digest!r} has different content")
        if sanitized is not None:
            _verify_redaction(body, sanitized, registration)
        return body

    def redaction_policy_digest(self, classified_event_id: str) -> str:
        reference = self._root / "references" / (hashlib.sha256(classified_event_id.encode()).hexdigest() + ".json")
        try:
            raw = reference.read_bytes()
            registration = json.loads(raw)
            proof = registration["redaction"]
            policy_digest = proof["policy_digest"]
        except (OSError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MissingBlobError("private Intake redaction policy is unavailable") from error
        if raw != canonical_bytes(registration) + b"\n" or not isinstance(policy_digest, str):
            raise BlobDigestMismatchError("private Intake redaction policy is invalid")
        return policy_digest


def _verify_redaction(body: bytes, sanitized: bytes, registration: dict[str, object]) -> None:
    proof = registration.get("redaction")
    if not isinstance(proof, dict) or set(proof) != {"policy", "policy_digest", "steps"}:
        raise BlobDigestMismatchError("private Intake redaction proof is invalid")
    policy = proof["policy"]
    if (
        not isinstance(policy, dict)
        or set(policy) != {"version", "forms"}
        or policy["version"] != 1
        or not isinstance(policy["forms"], list)
        or digest_bytes(canonical_bytes(policy)) != proof["policy_digest"]
    ):
        raise BlobDigestMismatchError("private Intake redaction policy is invalid")
    current = body
    previous_index = -1
    steps = proof["steps"]
    if not isinstance(steps, list):
        raise BlobDigestMismatchError("private Intake redaction steps are invalid")
    for step in steps:
        if not isinstance(step, dict) or set(step) != {
            "policy_index",
            "positions",
            "before_digest",
            "after_digest",
        }:
            raise BlobDigestMismatchError("private Intake redaction step is invalid")
        index = step["policy_index"]
        if not isinstance(index, int) or isinstance(index, bool) or not previous_index < index < len(policy["forms"]):
            raise BlobDigestMismatchError("private Intake redaction order is invalid")
        entry = policy["forms"][index]
        positions = step["positions"]
        if (
            not isinstance(entry, dict)
            or set(entry) != {"name", "bytes", "digest"}
            or not isinstance(entry["name"], str)
            or not isinstance(entry["bytes"], int)
            or entry["bytes"] <= 0
            or not isinstance(positions, list)
            or not positions
            or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in positions)
            or digest_bytes(current) != step["before_digest"]
        ):
            raise BlobDigestMismatchError("private Intake redaction step is invalid")
        first = positions[0]
        form = current[first : first + entry["bytes"]]
        if digest_bytes(form) != entry["digest"] or _positions(current, form) != positions:
            raise BlobDigestMismatchError("private Intake redaction occurrence proof is invalid")
        current = current.replace(form, f"[redacted:{entry['name']}]".encode())
        if digest_bytes(current) != step["after_digest"]:
            raise BlobDigestMismatchError("private Intake redaction result is invalid")
        previous_index = index
    if (
        current != sanitized
        or digest_bytes(sanitized) != registration.get("sanitized_digest")
        or len(sanitized) != registration.get("sanitized_bytes")
    ):
        raise BlobDigestMismatchError("private Intake sanitized response differs from its redaction proof")


def _positions(body: bytes, needle: bytes) -> list[int]:
    positions = []
    start = 0
    while True:
        found = body.find(needle, start)
        if found < 0:
            return positions
        positions.append(found)
        start = found + len(needle)


def _classified_event(state: Path, run_id: str, classified_event_id: str):
    from solver.event_store import EventStore

    matches = [
        event
        for event in EventStore(state, run_id=run_id).events()
        if event.event_type == "board-broker.recorded"
        and event.payload.get("record") == "classified"
        and event.payload.get("event_id") == classified_event_id
        and event.payload.get("operation") == "intake-read"
    ]
    if len(matches) > 1:
        raise BlobDigestMismatchError("private Intake evidence has ambiguous canonical reachability")
    return matches[0] if matches else None


__all__ = ["IntakeEvidenceReader", "IntakeEvidenceWriter", "PRIVATE_BOARD_RESPONSE_CLASS"]
