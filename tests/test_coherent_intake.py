"""Controller-owned collection into coherent Intake authority."""

import datetime as dt
import hashlib
import json

from solver.board_profile import qualify as qualify_profile
from solver.board_profile_receipt import write_receipt as write_profile_receipt
from solver.coherent_intake import CoherentIntake, contract_from_profile_receipt
from solver.event_store_storage import canonical_bytes
from solver.intake_qualification import (
    IntakeAuthority,
    IntakeContract,
    IntakeDocument,
    NoCoherentSnapshot,
    SnapshotAvailable,
)
from solver.intake_receipt import verify_receipt
from solver.record import Recorder
from solver.redaction import Redactor
from test_board_profile_qualification import RULES, decide_probe, probe, record_probe, valid_cycle


PROFILE = "1" * 64
SUBJECT = "db187520f6abbe69fa69847b0f7167afbb6963a34d69647774f9f3f4291beb62"
CAPABILITY = "3" * 64
PEER = "4" * 64
NOW = dt.datetime(2026, 9, 22, 2, 30, tzinfo=dt.timezone.utc)


class ScriptedSource:
    def __init__(self, *, with_attachment=False):
        self.calls = []
        self.changed = False
        self.serial = 0
        self.with_attachment = with_attachment

    def begin(self, attempt_id):
        self.attempt_id = attempt_id
        return IntakeAuthority(PROFILE, "f" * 64, PEER, CAPABILITY)

    def close(self):
        pass

    def read(self, *, pass_no, kind, path, page=0, challenge_id=None, hop=0, resource_identity=""):
        self.calls.append((pass_no, kind, path))
        self.serial += 1
        if kind == "identity":
            body = {"success": True, "data": {"id": 17, "team_id": None}}
        elif kind == "landing":
            body = b"<html>qualified landing</html>"
            raw = body
            return IntakeDocument(
                f"request-{self.serial}",
                f"broker-event-{self.serial}",
                pass_no,
                kind,
                path,
                200,
                "text/html",
                raw,
                len(raw),
                True,
                PROFILE,
                SUBJECT,
                CAPABILITY,
                PEER,
            )
        elif kind == "read-control":
            body = {"success": False, "message": "invalid field"}
        elif kind == "scoreboard":
            body = {"success": True, "data": {"1": {"name": "team", "score": 900}}}
        elif kind == "mana":
            body = {"success": True, "data": {"used": 1, "total": 5}}
        elif kind == "list" and page == 1:
            body = {
                "success": True,
                "data": [
                    {
                        "id": 2,
                        "name": "second",
                        "category": "web",
                        "type": "standard",
                        "value": 500,
                        "solves": 1,
                        "solved_by_me": False,
                    }
                ],
                "meta": {"pagination": {"page": 1, "pages": 2, "total": 2, "next": 2, "prev": None}},
            }
        elif kind == "list":
            body = {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "first",
                        "category": "crypto",
                        "type": "standard",
                        "value": 400,
                        "solves": 2,
                        "solved_by_me": False,
                    }
                ],
                "meta": {"pagination": {"page": 2, "pages": 2, "total": 2, "next": None, "prev": 1}},
            }
        elif kind == "detail":
            moved = self.changed and pass_no == 2 and challenge_id == 1
            body = {
                "success": True,
                "data": {
                    "id": challenge_id,
                    "description": "moved" if moved else f"statement-{challenge_id}",
                    "attempts": 0,
                    "files": ["/files/abc/evidence.txt"] if self.with_attachment and challenge_id == 1 else [],
                },
            }
        elif kind == "attachment":
            body = b"canonical attachment bytes"
            raw = body
            return IntakeDocument(
                f"request-{self.serial}",
                f"broker-event-{self.serial}",
                pass_no,
                kind,
                path,
                200,
                "text/plain",
                raw,
                len(raw),
                True,
                PROFILE,
                SUBJECT,
                CAPABILITY,
                PEER,
                page=page,
                challenge_id=challenge_id,
                hop=hop,
                resource_identity=resource_identity,
            )
        else:
            raise AssertionError((kind, path))
        raw = json.dumps(body, separators=(",", ":")).encode()
        return IntakeDocument(
            f"request-{self.serial}",
            f"broker-event-{self.serial}",
            pass_no,
            kind,
            path,
            200,
            "application/json",
            raw,
            len(raw),
            True,
            PROFILE,
            SUBJECT,
            CAPABILITY,
            PEER,
            page=page,
            challenge_id=challenge_id,
            hop=hop,
            resource_identity=resource_identity,
        )


def test_controller_collects_all_pages_twice_then_publishes_and_projects_v1(tmp_path):
    redactor = Redactor({})
    recorder = Recorder(tmp_path, "run-1", redactor)
    source = ScriptedSource()
    intake = CoherentIntake(source, recorder, IntakeContract(PROFILE, SUBJECT), redactor, now=lambda: NOW)

    result = intake.sync()

    assert isinstance(result, SnapshotAvailable)
    assert intake.available
    assert [one.challenge_id for one in intake.snapshot.challenges] == [1, 2]
    assert intake.snapshot.challenges[0].description == "statement-1"
    assert [call[2] for call in source.calls] == [
        "/api/v1/users/me",
        "/",
        "/api/v1/challenges?field=intake-is-not-a-field&q=a",
        "/api/v1/challenges?page=1",
        "/api/v1/challenges?page=2",
        "/api/v1/challenges/2",
        "/api/v1/challenges/1",
        "/api/v1/users/me",
    ] * 2
    assert verify_receipt(intake.latest_receipt).exists()
    records = (
        [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]
        if recorder.stream_path.exists()
        else []
    )
    derived = records[-1]
    assert derived["record"] == "intake"
    assert derived["outcome"] == "synced"
    assert [item["challenge_id"] for item in derived["challenges"]] == [1, 2]


def test_verified_profile_receipt_derives_the_exact_intake_subject_contract(tmp_path):
    observed = probe(valid_cycle("first"), valid_cycle("second"))
    decision = qualify_profile(observed, RULES)
    record_probe(tmp_path, observed)
    receipt = write_profile_receipt(tmp_path, "run-1", observed, RULES, decision)
    decide_probe(tmp_path, observed, receipt, decision)

    contract = contract_from_profile_receipt(tmp_path, "run-1")

    expected_subject = {
        "id": {"type": "integer", "value": "7"},
        "team_id": {"state": "value", "value": {"type": "integer", "value": "3"}},
    }
    assert contract.profile_digest == hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert contract.subject_digest == hashlib.sha256(canonical_bytes(expected_subject)).hexdigest()
    assert contract.pagination_shape == "single"
    assert contract.scoreboard_top == 10
    assert contract.mana_outcome == "answered"


def test_profile_selected_single_page_collection_is_reachable_without_query_rewrite(tmp_path):
    class SinglePageSource(ScriptedSource):
        def read(self, **request):
            document = super().read(**request)
            if request["kind"] != "list":
                return document
            body = json.loads(document.raw)
            body.pop("meta")
            raw = json.dumps(body, separators=(",", ":")).encode()
            return IntakeDocument(**{**document.__dict__, "raw": raw, "original_bytes": len(raw)})

    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    source = SinglePageSource()
    intake = CoherentIntake(
        source,
        recorder,
        IntakeContract(PROFILE, SUBJECT, pagination_shape="single"),
        Redactor({}),
        now=lambda: NOW,
    )

    result = intake.sync()

    assert isinstance(result, SnapshotAvailable)
    assert [call[2] for call in source.calls if call[1] == "list"] == ["/api/v1/challenges"] * 2


def test_controller_preserves_scoreboard_and_mana_in_canonical_and_v1_views(tmp_path):
    redactor = Redactor({})
    recorder = Recorder(tmp_path, "run-1", redactor)
    source = ScriptedSource()
    contract = IntakeContract(PROFILE, SUBJECT, scoreboard_top=10, mana_outcome="answered")
    intake = CoherentIntake(source, recorder, contract, redactor, now=lambda: NOW)

    result = intake.sync()

    assert isinstance(result, SnapshotAvailable)
    assert [(item.rank, item.name, item.score) for item in intake.snapshot.scoreboard] == [(1, "team", 900)]
    assert intake.snapshot.mana is not None
    assert (intake.snapshot.mana.outcome, intake.snapshot.mana.used, intake.snapshot.mana.total) == (
        "answered",
        1,
        5,
    )


def test_failed_refresh_keeps_prior_projection_and_records_a_separate_unsettled_attempt(tmp_path):
    redactor = Redactor({})
    recorder = Recorder(tmp_path, "run-1", redactor)
    source = ScriptedSource()
    intake = CoherentIntake(source, recorder, IntakeContract(PROFILE, SUBJECT), redactor, now=lambda: NOW)
    first = intake.sync()
    before = intake.snapshot
    source.changed = True

    failed = intake.sync()

    assert isinstance(first, SnapshotAvailable)
    assert isinstance(failed, SnapshotAvailable)
    assert failed.snapshot is first.snapshot
    assert intake.snapshot is before
    assert failed.refresh.reason == "passes-disagree"


def test_first_unsettled_collection_returns_no_coherent_snapshot_and_remains_retryable(tmp_path):
    redactor = Redactor({})
    recorder = Recorder(tmp_path, "run-1", redactor)
    source = ScriptedSource()
    source.changed = True
    intake = CoherentIntake(source, recorder, IntakeContract(PROFILE, SUBJECT), redactor, now=lambda: NOW)

    result = intake.sync()

    assert isinstance(result, NoCoherentSnapshot)
    assert not intake.available
    assert intake.due(at=NOW + dt.timedelta(seconds=5))


def test_authority_failure_before_canonical_start_emits_no_derived_intake_row(tmp_path):
    class UnavailableSource(ScriptedSource):
        def begin(self, attempt_id):
            raise PermissionError("controller unavailable")

    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    intake = CoherentIntake(
        UnavailableSource(),
        recorder,
        IntakeContract(PROFILE, SUBJECT),
        Redactor({}),
        now=lambda: NOW,
    )

    result = intake.sync()

    assert isinstance(result, NoCoherentSnapshot)
    records = (
        [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]
        if recorder.stream_path.exists()
        else []
    )
    assert all(record["record"] != "intake" for record in records)


def test_transport_failure_closes_and_receipts_attempt_without_erasing_prior(tmp_path):
    redactor = Redactor({})
    recorder = Recorder(tmp_path, "run-1", redactor)
    source = ScriptedSource()
    intake = CoherentIntake(source, recorder, IntakeContract(PROFILE, SUBJECT), redactor, now=lambda: NOW)
    first = intake.sync()
    prior = intake.snapshot
    original = source.read

    def fail_on_list(**request):
        if request["kind"] == "list":
            raise OSError("broker disconnected")
        return original(**request)

    source.read = fail_on_list
    result = intake.sync()

    assert isinstance(first, SnapshotAvailable)
    assert isinstance(result, SnapshotAvailable)
    assert result.snapshot is first.snapshot
    assert intake.snapshot is prior
    assert result.refresh.reason == "interrupted-after-read"
    assert verify_receipt(intake.latest_receipt) == intake.latest_receipt


def test_accepted_attachment_is_materialized_from_canonical_evidence_after_restart(tmp_path):
    redactor = Redactor({})
    recorder = Recorder(tmp_path, "run-1", redactor)
    intake = CoherentIntake(
        ScriptedSource(with_attachment=True),
        recorder,
        IntakeContract(PROFILE, SUBJECT),
        redactor,
        now=lambda: NOW,
    )
    intake.sync()
    held = intake.snapshot.challenges[0].attachments[0]
    assert held.path.read_bytes() == b"canonical attachment bytes"
    held.path.unlink()

    restarted = CoherentIntake(
        ScriptedSource(with_attachment=True),
        recorder,
        IntakeContract(PROFILE, SUBJECT),
        redactor,
        now=lambda: NOW,
    )

    replayed = restarted.snapshot.challenges[0].attachments[0]
    assert replayed.path.read_bytes() == b"canonical attachment bytes"
