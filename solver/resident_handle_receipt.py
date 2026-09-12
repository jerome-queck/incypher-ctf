"""Self-contained proof of resident capabilities exercised through Tool handles."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.isolation import STRICT_CONTROLS, STRICT_PROFILE_DIGEST, STRICT_PROFILE_ID, STRICT_RUNTIME_PIN
from solver.tool_control import _snapshot_digest
from solver.tool_control_receipt import verify_receipt as verify_tool_receipt

SCHEMA_VERSION = 1
RECEIPT_TYPE = "resident-handle-solve"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(document: Mapping[str, object]) -> str:
    basis = dict(document)
    basis.pop("identity", None)
    return "sha256:" + _sha(canonical_bytes(basis))


def _policy_resources(policy: Mapping[str, object]) -> dict[str, int]:
    return {
        "cpu_seconds": int(policy["cpu_seconds"]),
        "filesystem_bytes": int(policy["filesystem_bytes"]),
        "memory_bytes": int(policy["memory_bytes"]),
        "output_bytes": int(policy["max_output_bytes"]),
        "pids": int(policy["pids"]),
        "wall_seconds": int(policy["wall_seconds"]),
    }


def _expected_argv(component: Mapping[str, Any], capability_id: str) -> list[str]:
    policy = component["capability_policies"][capability_id]
    supplied = component["fixture"]["capability_argv"][capability_id]
    if not isinstance(supplied, list) or len(supplied) != 1:
        raise ValueError("resident handle fixture must name one input")
    virtual = f"/work/inputs/{capability_id}/{Path(str(supplied[0])).name}"
    return [virtual if item == "{input}" else str(item) for item in policy["argv"]]


def _expected_input_digest(component: Mapping[str, Any], capability_id: str, closure: Mapping[str, bytes]) -> str:
    policy = component["capability_policies"][capability_id]
    sources = component["fixture"]["capability_input_files"][capability_id]
    with tempfile.TemporaryDirectory(prefix="resident-handle-input-") as temporary:
        root = Path(temporary) / Path(str(component["fixture"]["capability_argv"][capability_id][0])).name
        if policy["input_kind"] == "file":
            if len(sources) != 1:
                raise ValueError("file fixture must have one locked source")
            root.write_bytes(closure[str(sources[0])])
        else:
            root.mkdir()
            for source in sources:
                source_path = Path(str(source))
                (root / source_path.name).write_bytes(closure[str(source)])
        return _snapshot_digest(root, str(policy["input_kind"]), int(policy["max_input_bytes"]))


def _event_maps(state: Path) -> tuple[list[Mapping[str, Any]], dict[str, bytes]]:
    store = EventStore(state, run_id="tool-handle-qualification")
    events = [event.payload for event in store.events()]
    blobs: dict[str, bytes] = {}
    for payload in events:
        digest = str(payload.get("blob_digest", ""))
        if digest and digest != _sha(b""):
            blobs[digest] = (store.sealed_dir / digest).read_bytes()
    return events, blobs


def create_receipt(
    state: Path,
    component: Mapping[str, Any],
    inventory: bytes,
    closure: Mapping[str, bytes],
) -> dict[str, object]:
    """Project canonical runtime evidence into one independently checkable proof."""

    state = Path(state)
    canonical = state / "runs" / "tool-handle-qualification" / "canonical"
    tool_path = verify_tool_receipt(canonical / "tool-handle.receipt.json")
    tool = json.loads(tool_path.read_text())
    isolation = json.loads((canonical / "strict-isolation-preflight.receipt.json").read_text())
    events, blobs = _event_maps(state)
    reserved = {
        str(item["invocation_id"]): item
        for item in events
        if item.get("record") == "reserved" and str(item.get("event_id", "")).startswith("tool:")
    }
    envelopes = {
        str(item["generation_id"]): item
        for item in events
        if item.get("record") == "result" and str(item.get("event_id", "")).startswith("envelope-")
    }
    revocations = {str(item["generation_id"]): item for item in tool["revocations"]}
    target_events = [item for item in events if str(item.get("event_id", "")).startswith("target-broker:")]
    capabilities = []
    image: dict[str, object] | None = None
    for invocation in tool["invocations"]:
        capability_id = str(invocation["capability_id"])
        if capability_id not in component["capability_ids"]:
            continue
        policy = component["capability_policies"][capability_id]
        invocation_id = str(invocation["invocation_id"])
        generation_id = str(invocation["generation_id"])
        envelope = envelopes[generation_id]
        if image is None:
            image = {
                "manifest_digest": envelope["image_manifest_digest"],
                "config_digest": envelope["image_config_digest"],
                "platform": envelope["platform"],
            }
        output = blobs[str(invocation["output_digest"])]
        target = None
        if policy["network"] == "target-broker":
            pair = [item for item in target_events if item.get("generation_id") == generation_id]
            if len(pair) != 2:
                raise ValueError("brokered resident capability lacks one reserved/classified exchange")
            pair.sort(key=lambda item: str(item["record"]))
            classified = next(item for item in pair if item["record"] == "classified")
            transcript = blobs[str(classified["blob_digest"])]
            target = {
                "reserved": {key: pair[1][key] for key in ("request_id", "binding_digest", "request_digest")},
                "classified": {
                    key: classified[key]
                    for key in (
                        "request_id",
                        "binding_digest",
                        "request_digest",
                        "challenge_id",
                        "protocol",
                        "outcome",
                        "request_bytes",
                        "response_bytes",
                        "status",
                        "max_connections",
                        "max_request_bytes",
                        "max_response_bytes",
                        "timeout_ms",
                        "transcript_digest",
                        "image_manifest_digest",
                        "image_config_digest",
                        "platform",
                        "profile_digest",
                        "generation_id",
                        "step_id",
                    )
                },
                "transcript": transcript.hex(),
            }
        capabilities.append(
            {
                "capability_id": capability_id,
                "invocation_id": invocation_id,
                "generation_id": generation_id,
                "handle_digest": revocations[generation_id]["handle_digest"],
                "view_digest": invocation["view_digest"],
                "adapter_sha256": next(
                    item["sha256"] for item in component["files"] if item["destination"] == component["entrypoint"]
                ),
                "policy_sha256": _sha(canonical_bytes(policy)),
                "argv": _expected_argv(component, capability_id),
                "arguments_digest": invocation["arguments_digest"],
                "input_snapshot": {
                    "kind": policy["input_kind"],
                    "before_sha256": invocation["input_digest"],
                    "after_sha256": invocation["input_digest"],
                    "outcome": "unchanged",
                },
                "output": {
                    "schema": policy["output_schema"],
                    "sha256": invocation["output_digest"],
                    "bytes": output.hex(),
                    "facts": component["fixture"]["capability_expected_facts"][capability_id],
                    "outcome": "pass",
                },
                "resources": {
                    "requested": reserved[invocation_id]["resources"],
                    "observed": invocation["resources"],
                    "envelope": envelope["declared"],
                    "outcome": envelope["outcome"],
                },
                "target": target,
            }
        )
    document: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "component_id": component["component_id"],
        "catalogue_digest": _sha(inventory),
        "image": image or {},
        "strict_profile_digest": STRICT_PROFILE_DIGEST,
        "isolation": isolation,
        "capabilities": sorted(capabilities, key=lambda item: str(item["capability_id"])),
        "identity": "",
    }
    document["identity"] = _identity(document)
    validate_receipt(document, component, inventory, closure)
    return document


def validate_receipt(
    receipt: Mapping[str, object],
    component: Mapping[str, Any],
    inventory: bytes,
    closure: Mapping[str, bytes],
    *,
    expected_image: Mapping[str, object] | None = None,
) -> None:
    """Verify a promoted Handle Solve proof solely from embedded locked materials."""

    required = {
        "schema_version",
        "receipt_type",
        "component_id",
        "catalogue_digest",
        "image",
        "strict_profile_digest",
        "isolation",
        "capabilities",
        "identity",
    }
    if (
        set(receipt) != required
        or receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("receipt_type") != RECEIPT_TYPE
    ):
        raise ValueError("resident Handle Solve receipt shape is invalid")
    if receipt.get("component_id") != component["component_id"] or receipt.get("catalogue_digest") != _sha(inventory):
        raise ValueError("resident Handle Solve proof names another component or catalogue")
    image = receipt.get("image")
    if not isinstance(image, Mapping) or set(image) != {"manifest_digest", "config_digest", "platform"}:
        raise ValueError("resident Handle Solve image binding is invalid")
    if expected_image is not None and dict(image) != dict(expected_image):
        raise ValueError("resident Handle Solve proof names another image")
    isolation = receipt.get("isolation")
    expected_isolation = {
        "profile_id": STRICT_PROFILE_ID,
        "profile_digest": STRICT_PROFILE_DIGEST,
        "image_id": image["manifest_digest"],
        "runtime_pin": STRICT_RUNTIME_PIN,
        "outer_capabilities": "empty",
        "checks": {control: "pass" for control in STRICT_CONTROLS},
        "broker_peer_uid": 20000,
        "processes_after_kill": 0,
        "owned_residue": [],
    }
    if not isinstance(isolation, Mapping) or any(
        isolation.get(key) != value for key, value in expected_isolation.items()
    ):
        raise ValueError("resident Handle Solve isolation proof is invalid")
    proofs = receipt.get("capabilities")
    if not isinstance(proofs, list) or {
        item.get("capability_id") for item in proofs if isinstance(item, Mapping)
    } != set(component["capability_ids"]):
        raise ValueError("resident Handle Solve capability coverage is incomplete")
    adapter = next(item["sha256"] for item in component["files"] if item["destination"] == component["entrypoint"])
    expected_view = _sha(
        canonical_bytes(
            {"capability_ids": tuple(sorted(component["capability_ids"])), "image_digest": image["manifest_digest"]}
        )
    )
    for proof_value in proofs:
        if not isinstance(proof_value, Mapping):
            raise ValueError("resident Handle Solve capability proof is invalid")
        capability_id = str(proof_value["capability_id"])
        policy = component["capability_policies"][capability_id]
        argv = _expected_argv(component, capability_id)
        if (
            proof_value.get("adapter_sha256") != adapter
            or proof_value.get("policy_sha256") != _sha(canonical_bytes(policy))
            or proof_value.get("argv") != argv
            or proof_value.get("arguments_digest") != _sha(canonical_bytes(argv))
            or proof_value.get("view_digest") != expected_view
        ):
            raise ValueError("resident Handle Solve invocation policy is invalid")
        snapshot = proof_value.get("input_snapshot")
        expected_input = _expected_input_digest(component, capability_id, closure)
        if not isinstance(snapshot, Mapping) or snapshot != {
            "kind": policy["input_kind"],
            "before_sha256": expected_input,
            "after_sha256": expected_input,
            "outcome": "unchanged",
        }:
            raise ValueError("resident Handle Solve input confinement is invalid")
        output = proof_value.get("output")
        if not isinstance(output, Mapping):
            raise ValueError("resident Handle Solve output proof is invalid")
        try:
            output_bytes = bytes.fromhex(str(output["bytes"]))
        except (KeyError, ValueError) as error:
            raise ValueError("resident Handle Solve output bytes are invalid") from error
        facts = component["fixture"]["capability_expected_facts"][capability_id]
        if (
            output
            != {
                "schema": policy["output_schema"],
                "sha256": _sha(output_bytes),
                "bytes": output_bytes.hex(),
                "facts": facts,
                "outcome": "pass",
            }
            or output["sha256"] != component["fixture"]["capability_stdout_sha256"][capability_id]
            or any(str(fact).encode() not in output_bytes for fact in facts)
        ):
            raise ValueError("resident Handle Solve semantic output is invalid")
        resources = proof_value.get("resources")
        requested = _policy_resources(policy)
        if (
            not isinstance(resources, Mapping)
            or resources.get("requested") != requested
            or resources.get("outcome") != "exited"
        ):
            raise ValueError("resident Handle Solve resource request is invalid")
        envelope = resources.get("envelope")
        if (
            not isinstance(envelope, Mapping)
            or any(envelope.get(name) != value for name, value in requested.items() if name != "output_bytes")
            or envelope.get("network") != "deny"
        ):
            raise ValueError("resident Handle Solve envelope is invalid")
        observed = resources.get("observed")
        if (
            not isinstance(observed, Mapping)
            or int(observed.get("output_bytes", -1)) != len(output_bytes)
            or int(observed.get("processes_after_kill", -1)) != 0
        ):
            raise ValueError("resident Handle Solve observed resources are invalid")
        target = proof_value.get("target")
        if policy["network"] == "target-broker":
            if not isinstance(target, Mapping):
                raise ValueError("resident Handle Solve broker proof is absent")
            reserved, classified = target.get("reserved"), target.get("classified")
            if not isinstance(reserved, Mapping) or not isinstance(classified, Mapping):
                raise ValueError("resident Handle Solve broker exchange is invalid")
            protocol = "http" if capability_id == "network.http" else "tcp"
            if (
                classified.get("request_id") != reserved.get("request_id")
                or classified.get("binding_digest") != reserved.get("binding_digest")
                or classified.get("request_digest") != reserved.get("request_digest")
                or classified.get("challenge_id") != f"fixture:{capability_id}"
                or classified.get("protocol") != protocol
                or classified.get("outcome") != "answered"
                or classified.get("generation_id") != proof_value.get("generation_id")
                or classified.get("step_id") != f"fixture:{capability_id}"
                or classified.get("image_manifest_digest") != image["manifest_digest"]
                or classified.get("image_config_digest") != image["config_digest"]
                or classified.get("platform") != image["platform"]
                or classified.get("profile_digest") != STRICT_PROFILE_DIGEST
                or [
                    classified.get(name)
                    for name in ("max_connections", "max_request_bytes", "max_response_bytes", "timeout_ms")
                ]
                != [1, 4096, 4096, 2000]
            ):
                raise ValueError("resident Handle Solve broker provenance is invalid")
            transcript = bytes.fromhex(str(target.get("transcript", "")))
            if _sha(transcript) != classified.get("transcript_digest"):
                raise ValueError("resident Handle Solve broker transcript is invalid")
        elif target is not None:
            raise ValueError("resident Handle Solve grants undeclared broker access")
        handle = str(proof_value.get("handle_digest", ""))
        if (
            len(handle) != 64
            or proof_value.get("invocation_id", "") == ""
            or proof_value.get("generation_id", "") == ""
        ):
            raise ValueError("resident Handle Solve authority proof is invalid")
    if receipt.get("strict_profile_digest") != STRICT_PROFILE_DIGEST or receipt.get("identity") != _identity(receipt):
        raise ValueError("resident Handle Solve identity is invalid")


__all__ = ["RECEIPT_TYPE", "create_receipt", "validate_receipt"]
