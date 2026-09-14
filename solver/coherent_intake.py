"""Controller-owned Board collection and canonical coherent Intake publication."""

from __future__ import annotations

import datetime as dt
import base64
import hashlib
import json
import urllib.parse
from pathlib import Path
from pathlib import PurePosixPath
from typing import Protocol

from solver.board import READ_CONTRACT_CONTROL, BoardFailure, Mana, Standing
from solver.board_broker import BoardBrokerClient
from solver.board_broker_contracts import PRIVATE_BOARD_RESPONSE_CLASS, IntakeReadValue
from solver.capability import CapabilityBinding
from solver.event_store import EventStore
from solver.event_store_storage import atomic_write
from solver.instance import Terms
from solver.intake import Attachment, Limits, Sighting, Snapshot, SYNCED
from solver.intake_contracts import INTAKE_DECISION_RECORDED, IntakeRecord
from solver.intake_evidence import IntakeEvidenceReader
from solver.intake_journal import IntakeJournal
from solver.intake_qualification import (
    IntakeContract,
    IntakeAuthority,
    IntakeDecision,
    IntakeDocument,
    IntakePass,
    IntakeProbe,
    NoCoherentSnapshot,
    SnapshotAvailable,
    availability,
    qualify,
)
from solver.intake_receipt import write_interrupted_receipt, write_receipt
from solver.record import Recorder
from solver.redaction import Redactor
from solver.work_generation import GenerationDisposition, GenerationFence


class IntakeSource(Protocol):
    def begin(self, attempt_id: str) -> IntakeAuthority: ...

    def read(
        self,
        *,
        pass_no: int,
        kind: str,
        path: str,
        page: int = 0,
        challenge_id: int | str | None = None,
        hop: int = 0,
        resource_identity: str = "",
    ) -> IntakeDocument: ...

    def close(self) -> None: ...


class BrokerIntakeSource:
    """Attempt-bound controller capability over the qualified Board broker."""

    def __init__(
        self,
        socket_path: Path,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        board_url: str,
        profile_handle: str,
        contract: IntakeContract,
        generations: GenerationFence,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._state = Path(state)
        self._run_id = run_id
        self._boot_id = boot_id
        self._board_url = board_url.rstrip("/")
        self._profile_handle = profile_handle
        self._contract = contract
        self._generations = generations
        self._client: BoardBrokerClient | None = None
        self._generation_id = ""
        self._controller_peer_digest = ""

    def begin(self, attempt_id: str) -> IntakeAuthority:
        generation = self._generations.replace("intake-controller", attempt_id)
        binding = CapabilityBinding(
            self._run_id,
            self._boot_id,
            generation.generation_id,
            "intake",
            attempt_id,
            "capture",
        )
        self._client, self._controller_peer_digest, profile_digest = BoardBrokerClient.open_intake(
            self._socket_path,
            binding,
            self._profile_handle,
        )
        if profile_digest != self._contract.profile_digest:
            self._client.close()
            self._client = None
            raise ValueError("Intake capability profile differs from its contract")
        self._generation_id = generation.generation_id
        return IntakeAuthority(
            profile_digest,
            self._contract.profile_decision_event_digest,
            self._controller_peer_digest,
            binding.digest,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._generation_id:
            self._generations.close(self._generation_id, GenerationDisposition.COMPLETE)
            self._generation_id = ""

    def read(
        self,
        *,
        pass_no: int,
        kind: str,
        path: str,
        page: int = 0,
        challenge_id: int | str | None = None,
        hop: int = 0,
        resource_identity: str = "",
    ) -> IntakeDocument:
        if self._client is None:
            raise RuntimeError("Intake broker attempt is not open")
        result = self._client.intake_read(path)
        provenance = result.provenance
        if not isinstance(result.value, IntakeReadValue):
            raise ValueError("Intake broker response carries no bounded raw document")
        raw = result.value.body
        profile_digest = provenance.profile_digest
        origin = path if path.startswith("http") else f"{self._board_url}{path}"
        auth_forwarded = origin.startswith(self._board_url + "/") or origin == self._board_url
        complete = (
            bool(provenance.raw_blob_digest)
            and provenance.raw_blob_class == PRIVATE_BOARD_RESPONSE_CLASS
            and provenance.original_bytes == provenance.raw_blob_bytes
            and provenance.raw_blob_bytes == len(raw)
            and provenance.response_digest == _sha256(raw)
            and result.outcome.value != "too-large"
        )
        return IntakeDocument(
            request_id=result.request_id,
            classified_event_id=provenance.classified_event_id,
            pass_no=pass_no,
            kind=kind,
            endpoint=path,
            status=provenance.http_status,
            content_type=provenance.content_type,
            raw=raw,
            original_bytes=provenance.raw_blob_bytes,
            complete=complete,
            profile_digest=profile_digest,
            subject_digest=self._contract.subject_digest,
            capability_digest=provenance.binding_digest,
            peer_digest=provenance.peer_identity_digest,
            page=page,
            challenge_id=challenge_id,
            outcome=result.outcome.value,
            location=provenance.location,
            raw_blob_digest=provenance.raw_blob_digest,
            sanitized_blob_digest=provenance.sanitized_blob_digest,
            request_digest=provenance.request_digest,
            auth_forwarded=auth_forwarded,
            hop=hop,
            resource_identity=resource_identity,
        )

    def resolve(self, current: str, location: str) -> str:
        base = current if current.startswith("http") else f"{self._board_url}/{current.lstrip('/')}"
        return urllib.parse.urljoin(base, location)


class CoherentIntake:
    """Deep Intake boundary: capture twice, qualify purely, publish once."""

    def __init__(
        self,
        source: IntakeSource,
        recorder: Recorder,
        contract: IntakeContract,
        redactor: Redactor,
        *,
        limits: Limits = Limits(),
        now=None,
    ) -> None:
        self._source = source
        self._recorder = recorder
        self._contract = contract
        self._limits = limits
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        state = Path(recorder.run_dir).parents[1]
        self._journal = IntakeJournal(state, recorder.run_id, redactor, timestamp=lambda: self._now().isoformat())
        for interrupted in self._journal.close_orphans():
            write_interrupted_receipt(state, recorder.run_id, str(interrupted.event.payload["attempt_id"]))
        self._canonical = self._journal.latest_snapshot()
        self._fence = self._journal.latest_fence(contract.profile_digest)
        self._cycle = 0
        self._next_due: dt.datetime | None = None
        self._failure_count = 0
        self._last_failure_fingerprint = ""
        self.latest_receipt: Path | None = None
        self.snapshot = (
            self._legacy_snapshot(self._canonical)
            if self._canonical
            else Snapshot(
                at=dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                cycle=0,
                outcome="unreadable",
                detail="no coherent Intake snapshot has been published",
            )
        )

    @property
    def available(self) -> bool:
        return self._canonical is not None

    def order_authority(self):
        """Expose only verified canonical Intake facts to the v2 Order boundary."""

        from solver.order_policy import OrderAuthority

        replayed = self._journal._replay(self._contract.profile_digest)
        if replayed.snapshot is None:
            raise LookupError("no coherent Intake snapshot has been published")
        crowd_source = crowd_source_from_contract(
            Path(self._recorder.run_dir).parents[1], self._recorder.run_id, self._contract
        )
        return OrderAuthority(
            snapshot=replayed.snapshot,
            fence=replayed.fence,
            history=tuple(reversed(replayed.history)),
            effective_max_challenges=self._contract.max_challenges,
            crowd_source=crowd_source,
        )

    def due(self, *, at: dt.datetime | None = None) -> bool:
        moment = at or self._now()
        return self._next_due is None or moment >= self._next_due

    def sync(self) -> SnapshotAvailable | NoCoherentSnapshot:
        self._cycle += 1
        attempt_id = self._next_attempt_id()
        started = False
        try:
            authority = self._source.begin(attempt_id)
            self._journal.start(attempt_id, self._contract, self._fence, authority)
            started = True
            passes = (self._collect_pass(attempt_id, 1), self._collect_pass(attempt_id, 2))
            self._source.close()
        except (BoardFailure, OSError, PermissionError, RuntimeError, ValueError):
            try:
                self._source.close()
            except (BoardFailure, OSError, PermissionError, RuntimeError, ValueError):
                pass
            if not started:
                decision = IntakeDecision(attempt_id, False, "authority-unavailable", self._fence)
                return self._finish(
                    decision,
                    self._now(),
                    failure_fingerprint=decision.reason,
                    record_projection=False,
                )
            closed = next(
                item for item in self._journal.close_orphans() if item.event.payload["attempt_id"] == attempt_id
            )
            self.latest_receipt = write_interrupted_receipt(
                Path(self._recorder.run_dir).parents[1],
                self._recorder.run_id,
                attempt_id,
            )
            decision = IntakeDecision(
                attempt_id,
                False,
                closed.reason,
                self._fence,
                observation_ids=tuple(str(item) for item in closed.event.payload["observation_ids"]),
            )
            return self._finish(decision, self._now(), failure_fingerprint=decision.reason)
        observed_at = self._now()
        probe = IntakeProbe(
            attempt_id,
            self._contract,
            self._fence,
            passes,
            observed_at,
            self._canonical,
        )
        decision = qualify(probe)
        published = self._journal.publish(decision)
        if published.record == IntakeRecord.FENCE_CONFLICT.value:
            self.latest_receipt = write_receipt(
                Path(self._recorder.run_dir).parents[1],
                self._recorder.run_id,
                probe,
                decision,
                publication_record=published.record,
                observed_fence=published.observed_fence,
            )
            decision = IntakeDecision(
                attempt_id,
                False,
                published.reason,
                self._fence,
                observation_ids=decision.observation_ids,
            )
            self._canonical = self._journal.latest_snapshot()
            self._fence = self._journal.latest_fence(self._contract.profile_digest)
        else:
            self.latest_receipt = write_receipt(
                Path(self._recorder.run_dir).parents[1],
                self._recorder.run_id,
                probe,
                decision,
            )
            if decision.settled:
                self._canonical = decision.snapshot
                self._fence = published.observed_fence
                self.snapshot = self._legacy_snapshot(self._canonical)
        return self._finish(
            decision,
            observed_at,
            failure_fingerprint=_failure_fingerprint(probe, decision),
        )

    def _finish(
        self,
        decision: IntakeDecision,
        observed_at: dt.datetime,
        *,
        failure_fingerprint: str = "",
        record_projection: bool = True,
    ):
        if decision.settled:
            self._failure_count = 0
            self._last_failure_fingerprint = ""
            self._next_due = observed_at + dt.timedelta(seconds=self._limits.cycle_seconds)
        else:
            if failure_fingerprint != self._last_failure_fingerprint:
                self._failure_count = 1
                self._last_failure_fingerprint = failure_fingerprint
            else:
                self._failure_count += 1
            delay = min(300, 5 * (2 ** (self._failure_count - 1)))
            self._next_due = observed_at + dt.timedelta(seconds=delay)
        if record_projection:
            self._record_projection(decision)
        return availability(
            decision,
            prior=self._canonical if not decision.settled else None,
            retry_at=self._next_due,
        )

    def _record_projection(self, decision: IntakeDecision) -> None:
        snapshot = self.snapshot
        self._recorder.intake(
            cycle=self._cycle,
            challenges=[
                {
                    "challenge_id": item.challenge_id,
                    "name": item.name,
                    "category": item.category,
                    "type": item.challenge_type,
                    "value": item.value,
                    "solves": item.solves,
                    "position": item.position,
                    "attempts": item.attempts,
                    "max_attempts": item.max_attempts,
                    "solved": item.solved,
                    "shared": item.terms.shared,
                    "timeout": item.terms.timeout,
                    "destroy_on_flag": item.terms.destroy_on_flag,
                    "mana_cost": item.terms.mana_cost,
                    "changed": item.changed,
                    "stale": item.stale,
                    "files": [
                        {
                            "identity": attachment.identity,
                            "name": attachment.name,
                            "bytes": attachment.nbytes,
                            "hosts": list(attachment.hosts),
                            "outcome": attachment.outcome,
                        }
                        for attachment in item.attachments
                    ],
                }
                for item in snapshot.challenges
            ],
            scoreboard=[{"rank": item.rank, "name": item.name, "score": item.score} for item in snapshot.scoreboard],
            mana=(
                {
                    "outcome": snapshot.mana.outcome,
                    "used": snapshot.mana.used,
                    "total": snapshot.mana.total,
                    "enabled": snapshot.mana_enabled,
                }
                if snapshot.mana
                else None
            ),
            outcome=SYNCED if decision.settled else "unreadable",
            detail=decision.reason,
        )

    def _collect_pass(self, attempt_id: str, pass_no: int) -> IntakePass:
        before = self._read(attempt_id, pass_no, "identity", "/api/v1/users/me")
        landing = self._read(attempt_id, pass_no, "landing", "/")
        read_control = self._read(
            attempt_id,
            pass_no,
            "read-control",
            READ_CONTRACT_CONTROL,
        )
        scoreboard = None
        mana = None
        if self._contract.scoreboard_top:
            scoreboard = self._read(
                attempt_id,
                pass_no,
                "scoreboard",
                f"/api/v1/scoreboard/top/{self._contract.scoreboard_top}",
            )
            mana = self._read(
                attempt_id,
                pass_no,
                "mana",
                "/api/v1/plugins/ctfd-chall-manager/mana",
            )
        collection_path = (
            "/api/v1/challenges" if self._contract.pagination_shape == "single" else "/api/v1/challenges?page=1"
        )
        pages = [self._read(attempt_id, pass_no, "list", collection_path, page=1)]
        entries, page_count = _list_entries(pages[0], max_pages=self._contract.max_pages)
        if self._contract.pagination_shape == "single":
            page_count = 1
        for page in range(2, page_count + 1):
            document = self._read(
                attempt_id,
                pass_no,
                "list",
                f"/api/v1/challenges?page={page}",
                page=page,
            )
            pages.append(document)
            entries.extend(_page_entries(document))
        details = []
        for entry in entries:
            challenge_id = entry.get("id")
            if isinstance(challenge_id, (int, str)) and not isinstance(challenge_id, bool):
                details.append(
                    self._read(
                        attempt_id,
                        pass_no,
                        "detail",
                        _challenge_path(challenge_id),
                        challenge_id=challenge_id,
                    )
                )
        attachments = []
        for detail in details:
            for listing in _detail_files(detail):
                attachments.extend(
                    self._attachment_chain(
                        attempt_id,
                        pass_no,
                        listing,
                        detail.challenge_id,
                    )
                )
        current_ids = {
            entry.get("id")
            for entry in entries
            if isinstance(entry.get("id"), (int, str)) and not isinstance(entry.get("id"), bool)
        }
        absence = []
        if self._canonical:
            for previous in self._canonical.challenges:
                if previous.challenge_id.value not in current_ids:
                    challenge_id = previous.challenge_id.value
                    absence.append(
                        self._read(
                            attempt_id,
                            pass_no,
                            "absence",
                            _challenge_path(challenge_id),
                            challenge_id=challenge_id,
                        )
                    )
        after = self._read(attempt_id, pass_no, "identity", "/api/v1/users/me")
        return IntakePass(
            pass_no,
            before,
            tuple(pages),
            tuple(details),
            tuple(attachments),
            after,
            tuple(absence),
            landing,
            read_control,
            scoreboard,
            mana,
        )

    def _read(
        self,
        attempt_id: str,
        pass_no: int,
        kind: str,
        path: str,
        *,
        page: int = 0,
        challenge_id: int | str | None = None,
        hop: int = 0,
        resource_identity: str = "",
    ) -> IntakeDocument:
        document = self._source.read(
            pass_no=pass_no,
            kind=kind,
            path=path,
            page=page,
            challenge_id=challenge_id,
            hop=hop,
            resource_identity=resource_identity,
        )
        self._journal.observe(attempt_id, document)
        return document

    def _attachment_chain(
        self,
        attempt_id: str,
        pass_no: int,
        listing: str,
        challenge_id: int | str | None,
    ) -> tuple[IntakeDocument, ...]:
        documents = []
        current = listing if listing.startswith("http") else "/" + listing.lstrip("/")
        visited = set()
        for hop in range(5):
            if current in visited:
                break
            visited.add(current)
            document = self._read(
                attempt_id,
                pass_no,
                "attachment",
                current,
                challenge_id=challenge_id,
                hop=hop,
                resource_identity=listing,
            )
            documents.append(document)
            if document.status not in {301, 302, 303, 307, 308} or not document.location:
                break
            resolver = getattr(self._source, "resolve", None)
            current = (
                resolver(current, document.location) if resolver else urllib.parse.urljoin(current, document.location)
            )
        return tuple(documents)

    def _next_attempt_id(self) -> str:
        serial = sum(
            event.event_type == INTAKE_DECISION_RECORDED and event.payload.get("record") == IntakeRecord.STARTED.value
            for event in self._journal.store.events()
        )
        return f"intake-attempt-{serial + 1:06d}"

    def _legacy_snapshot(self, canonical) -> Snapshot:
        previous = {item.challenge_id: item for item in self.snapshot.challenges} if hasattr(self, "snapshot") else {}
        challenges = []
        for item in canonical.challenges:
            challenge_id = item.challenge_id.value
            before = previous.get(challenge_id)
            terms = Terms(
                challenge_id,
                item.challenge_type,
                bool(_legacy(item.shared, False)),
                _legacy(item.timeout, None),
                bool(_legacy(item.destroy_on_flag, False)),
                int(_legacy(item.mana_cost, 0)),
            )
            attachments = tuple(self._legacy_attachment(challenge_id, one) for one in item.attachments)
            moved = (
                before is None
                or before.description != item.statement
                or tuple(one.identity for one in before.attachments)
                != tuple(one.resource_identity for one in item.attachments)
            )
            challenges.append(
                Sighting(
                    challenge_id,
                    item.name,
                    item.category,
                    item.challenge_type,
                    int(item.value.value),
                    int(item.solves.value),
                    int(_legacy(item.position, 0)),
                    item.statement,
                    item.attempts,
                    _legacy(item.max_attempts, None),
                    bool(item.solved.value),
                    terms,
                    attachments,
                    moved,
                    False,
                )
            )
        scoreboard = tuple(
            Standing(int(item["rank"]), str(item["name"]), int(item["score"])) for item in canonical.scoreboard
        )
        mana = (
            Mana(
                str(canonical.mana["outcome"]),
                int(canonical.mana["used"]),
                int(canonical.mana["total"]),
            )
            if canonical.mana
            else None
        )
        return Snapshot(
            canonical.observed_at,
            self._cycle,
            tuple(challenges),
            scoreboard=scoreboard,
            mana=mana,
            outcome=SYNCED,
        )

    def _legacy_attachment(self, challenge_id, attachment) -> Attachment:
        if attachment.outcome != "held":
            return Attachment(
                attachment.resource_identity,
                attachment.name,
                None,
                attachment.nbytes,
                (),
                "over-the-cap",
            )
        classified_event_id = str(attachment.hops[-1]["classified_event_id"])
        event = next(
            (
                candidate
                for candidate in self._journal.store.events()
                if candidate.event_type == "intake-observation.recorded"
                and candidate.payload.get("classified_event_id") == classified_event_id
            ),
            None,
        )
        if event is None:
            raise ValueError("published Intake attachment has no canonical observation")
        private_digest = str(event.payload.get("raw_blob_digest", ""))
        body = (
            IntakeEvidenceReader(Path(self._recorder.run_dir).parents[1], self._recorder.run_id).read(
                private_digest,
                classified_event_id=str(event.payload["classified_event_id"]),
            )
            if private_digest
            else event.body
        )
        if _sha256(body) != attachment.content_digest or len(body) != attachment.nbytes:
            raise ValueError("published Intake attachment bytes disagree with its snapshot")
        identity_digest = hashlib.sha256(attachment.resource_identity.encode()).hexdigest()[:16]
        safe_name = PurePosixPath(attachment.name).name.replace("/", "_").replace("\\", "_").strip()
        name = safe_name if safe_name not in {"", ".", ".."} else "attachment"
        path = Path(self._recorder.run_dir) / "intake" / str(challenge_id) / identity_digest / name
        if not path.exists() or path.read_bytes() != body:
            atomic_write(path, body)
        hosts = tuple(
            urllib.parse.urlsplit(str(hop["endpoint"])).hostname or ""
            for hop in attachment.hops
            if urllib.parse.urlsplit(str(hop["endpoint"])).hostname
        )
        return Attachment(attachment.resource_identity, attachment.name, path, len(body), hosts, "held")


def _list_entries(document: IntakeDocument, *, max_pages: int) -> tuple[list[dict[str, object]], int]:
    wrapper = _json(document)
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("data"), list):
        return [], 1
    entries = [item for item in wrapper["data"] if isinstance(item, dict)]
    meta = wrapper.get("meta")
    pagination = meta.get("pagination") if isinstance(meta, dict) else None
    pages = pagination.get("pages") if isinstance(pagination, dict) else 1
    if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= max_pages:
        pages = 1
    return entries, pages


def _page_entries(document: IntakeDocument) -> list[dict[str, object]]:
    wrapper = _json(document)
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("data"), list):
        return []
    return [item for item in wrapper["data"] if isinstance(item, dict)]


def _detail_files(document: IntakeDocument) -> tuple[str, ...]:
    wrapper = _json(document)
    data = wrapper.get("data") if isinstance(wrapper, dict) else None
    files = data.get("files") if isinstance(data, dict) else None
    return tuple(item for item in files if isinstance(item, str)) if isinstance(files, list) else ()


def _json(document: IntakeDocument) -> object:
    try:
        return json.loads(document.raw)
    except (UnicodeDecodeError, ValueError):
        return None


def _legacy(value, default):
    return value.value if value.state == "value" else default


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _challenge_path(challenge_id: int | str) -> str:
    return "/api/v1/challenges/" + urllib.parse.quote(str(challenge_id), safe="")


def crowd_source_from_contract(state: Path, run_id: str, contract: IntakeContract):
    """Rebuild crowd trust only from canonical profile authority and its verified receipt."""

    from solver.order_policy import CrowdSource

    decision = next(
        (
            event
            for event in EventStore(state, run_id=run_id).events()
            if event.event_digest == contract.profile_decision_event_digest
            and event.event_type == "board-profile-phase.recorded"
            and event.payload.get("record") == "profile-decided"
            and event.payload.get("decision") == "authoritative"
        ),
        None,
    )
    trusted = False
    if decision is not None:
        try:
            from solver.board_profile_receipt import verify_receipt as verify_profile_receipt

            receipt = verify_profile_receipt(Path(state) / "runs" / run_id / "canonical" / "board-profile.receipt.json")
            trusted = decision.payload.get("receipt_digest") == _sha256(receipt.read_bytes())
        except ValueError:
            trusted = False
    control_digest = _sha256(
        canonical_subject(
            {
                "profile_decision_event_digest": contract.profile_decision_event_digest,
                "landing_digests": list(contract.landing_digests),
                "read_control_digests": list(contract.read_control_digests),
                "read_control_statuses": list(contract.read_control_statuses),
            }
        )
    )
    return CrowdSource(trusted, False, control_digest)


def contract_from_profile_receipt(state: Path, run_id: str) -> IntakeContract:
    """Derive Intake authority only from the independently verified selected Board profile."""

    from solver.board_profile_receipt import verify_receipt

    path = verify_receipt(Path(state) / "runs" / run_id / "canonical" / "board-profile.receipt.json")
    receipt = json.loads(path.read_text())
    subjects = []
    landing_digests = []
    read_control_digests = []
    read_control_statuses = []
    pagination_shapes = []
    try:
        cycles = receipt["probe"]["cycles"]
        for cycle in cycles:
            documents = cycle["documents"]
            landing_digests.append(str(documents["landing"]["body_digest"]))
            read_control_digests.append(str(documents["read_contract"]["body_digest"]))
            read_control_statuses.append(int(documents["read_contract"]["status"]))
            challenges = json.loads(base64.b64decode(documents["challenges"]["body"], validate=True))
            if not isinstance(challenges, dict) or not isinstance(challenges.get("data"), list):
                raise ValueError
            meta = challenges.get("meta")
            if meta is None:
                pagination_shapes.append("single")
            elif isinstance(meta, dict) and isinstance(meta.get("pagination"), dict):
                pagination_shapes.append("ctfd-pages")
            else:
                raise ValueError
            encoded = cycle["documents"]["identity"]["body"]
            wrapper = json.loads(base64.b64decode(encoded, validate=True))
            data = wrapper["data"]
            identity = data["id"]
            if isinstance(identity, bool) or not isinstance(identity, (int, str)):
                raise ValueError
            typed = {
                "type": "integer" if isinstance(identity, int) else "string",
                "value": str(identity),
            }
            if "team_id" not in data:
                team = {"state": "missing"}
            elif data["team_id"] is None:
                team = {"state": "null"}
            else:
                team_id = data["team_id"]
                if isinstance(team_id, bool) or not isinstance(team_id, (int, str)):
                    raise ValueError
                team = {
                    "state": "value",
                    "value": {
                        "type": "integer" if isinstance(team_id, int) else "string",
                        "value": str(team_id),
                    },
                }
            subjects.append({"id": typed, "team_id": team})
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Board-profile receipt has no usable authenticated subject") from error
    if len(subjects) != 2 or subjects[0] != subjects[1]:
        raise ValueError("Board-profile receipt authenticated subject is not coherent")
    if len(pagination_shapes) != 2 or pagination_shapes[0] != pagination_shapes[1]:
        raise ValueError("Board-profile receipt pagination shape is not coherent")
    decision = next(
        (
            event
            for event in EventStore(state, run_id=run_id).events()
            if event.event_type == "board-profile-phase.recorded"
            and event.payload.get("record") == "profile-decided"
            and event.payload.get("decision") == "authoritative"
            and event.payload.get("receipt_digest") == _sha256(path.read_bytes())
        ),
        None,
    )
    if decision is None:
        raise ValueError("Board-profile receipt has no authoritative canonical decision")
    origin = str(receipt["rules"]["document"]["url"]).rstrip("/")
    return IntakeContract(
        _sha256(path.read_bytes()),
        _sha256(canonical_subject(subjects[0])),
        profile_decision_event_digest=decision.event_digest,
        board_origin=origin,
        landing_digests=tuple(landing_digests),
        read_control_digests=tuple(read_control_digests),
        read_control_statuses=tuple(read_control_statuses),
        pagination_shape=pagination_shapes[0],
        scoreboard_top=10,
        mana_outcome=str(receipt["decision"]["profile"]["mana_outcome"]),
    )


def canonical_subject(subject: dict[str, object]) -> bytes:
    from solver.event_store_storage import canonical_bytes

    return canonical_bytes(subject)


def _failure_fingerprint(probe: IntakeProbe, decision: IntakeDecision) -> str:
    from solver.event_store_storage import canonical_bytes

    return _sha256(
        canonical_bytes(
            {
                "reason": decision.reason,
                "documents": [
                    {
                        "kind": document.kind,
                        "endpoint": document.endpoint,
                        "status": document.status,
                        "outcome": document.outcome,
                        "complete": document.complete,
                        "raw_digest": document.raw_digest,
                    }
                    for observed in probe.passes
                    for document in observed.documents
                ],
            }
        )
    )


__all__ = [
    "BrokerIntakeSource",
    "CoherentIntake",
    "IntakeSource",
    "contract_from_profile_receipt",
    "crowd_source_from_contract",
]
