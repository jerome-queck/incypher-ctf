"""Host-side Evaluator for the ADR-0052 ambiguity controlled proof."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes
from solver.submission.ambiguity import verify_receipt as verify_observation
from solver.submission.receipt import verify_receipt as verify_serial


def evaluate(observation: Path, serial: Path, destination: Path) -> Path:
    verify_observation(observation)
    verify_serial(serial)
    document = json.loads(observation.read_bytes())
    serial_document = json.loads(serial.read_bytes())
    observed = [
        {"effect_id": row["effect_id"], "posts": 1}
        for row in serial_document["submissions"]
        if row["states"][-1] == "possibly-sent"
    ]
    expected = sorted(event["effect_id"] for event in document["events"] if event["event"] == "possibly-sent")
    if sorted(item["effect_id"] for item in observed) != expected:
        raise ValueError("Evaluator did not observe each ambiguous Board effect exactly once")
    document.update(
        producer="external-evaluator",
        evidence_class="controlled-runtime-trace",
        observed_effect_trace=observed,
        source_digests={
            "solver_observation": hashlib.sha256(observation.read_bytes()).hexdigest(),
            "serial_authority": hashlib.sha256(serial.read_bytes()).hexdigest(),
        },
    )
    document["evaluator_seal"] = hashlib.sha256(canonical_bytes(document)).hexdigest()
    atomic_write(destination, canonical_bytes(document) + b"\n")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation", type=Path)
    parser.add_argument("serial", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args(argv)
    evaluate(arguments.observation, arguments.serial, arguments.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
