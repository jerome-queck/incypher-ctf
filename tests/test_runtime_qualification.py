"""The retained qualification runner creates fresh #293 and #296 observations."""

import json

from solver.recovery.incident import verify_receipt as verify_incident
from scripts.runtime_qualification import qualify_incident, qualify_serial_submission
from solver.submission.receipt import verify_receipt as verify_submission


def test_serial_qualification_observes_each_exactly_once_crash_boundary(tmp_path):
    receipt, trace_path = qualify_serial_submission(tmp_path)

    assert verify_submission(receipt) == receipt
    document = json.loads(receipt.read_text())
    assert [row["states"] for row in document["submissions"]] == [
        ["reserved", "started", "committed"],
        ["reserved", "started", "committed"],
        ["reserved", "started", "committed"],
        ["reserved", "started", "possibly-sent"],
        ["reserved", "aborted"],
    ]
    assert document["in_flight_maximum"] == 1
    trace = json.loads(trace_path.read_text())
    assert trace == {
        "committed_replay_posts": 3,
        "competing_dispatch_order": ["1" * 64, "2" * 64, "3" * 64],
        "competing_ready_orders": [0, 2, 1],
        "first_ready_candidate": "2" * 64,
        "possibly_sent_replay": "EffectIndeterminate",
        "possibly_sent_replay_posts": 1,
        "wire_in_flight_maximum": 1,
    }


def test_incident_qualification_observes_real_crash_replay_and_process_coalescing(tmp_path):
    receipt, trace_path = qualify_incident(tmp_path)

    assert verify_incident(receipt) == receipt
    trace = json.loads(trace_path.read_text())
    assert trace["crashed_exit_code"] == 17
    assert trace["replacement_exit_code"] == 0
    assert trace["crashed_pid"] != trace["replacement_pid"]
    assert trace["containment_crash"]["step"] == "evidence-capture"
    assert trace["replay_count"] == 1
    assert trace["concurrency"]["process_count"] == 6
    assert trace["concurrency"]["duplicate_reports"] == 5
    assert len(set(trace["concurrency"]["incident_ids"])) == 1
    assert trace["generation_active_after"] is False
    assert trace["group_extinguished"] is True
