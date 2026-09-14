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
from solver.target_broker_contracts import TargetLimits
from solver.tool_control import _snapshot_digest
from solver.tool_control_receipt import verify_receipt as verify_tool_receipt

SCHEMA_VERSION = 1
RECEIPT_TYPE = "resident-handle-solve"

TARGET_CLASSIFIED_FIELDS = (
    "request_id",
    "binding_digest",
    "request_digest",
    "challenge_id",
    "endpoint",
    "resolved_address",
    "protocol",
    "operation",
    "outcome",
    "request_bytes",
    "response_bytes",
    "status",
    "elapsed_ms",
    "max_connections",
    "max_request_bytes",
    "max_response_bytes",
    "timeout_ms",
    "max_exchanges",
    "max_total_request_bytes",
    "max_total_response_bytes",
    "max_total_seconds_ms",
    "max_redirects",
    "max_cookies",
    "transcript_digest",
    "image_manifest_digest",
    "image_config_digest",
    "platform",
    "profile_digest",
    "generation_id",
    "step_id",
)
RESEARCH_PROOF_FIELDS = (
    "request_id",
    "generation_id",
    "attempt_id",
    "url_digest",
    "outcome",
    "content_type",
    "dns_chain",
    "redirect_chain",
    "observed_at",
    "expires_at",
    "elapsed_ms",
    "cached",
    "max_body_bytes",
    "timeout_ms",
    "max_redirects",
    "cache_seconds",
    "kind",
    "source_id",
    "terms",
    "robots",
    "origin",
    "query_digest",
    "max_requests",
    "max_total_bytes",
    "max_total_seconds_ms",
    "min_interval_ms",
    "blob_digest",
    "blob_bytes",
)


def qualification_target_limits(capability_id: str) -> TargetLimits:
    """Keep legacy network probes narrow while typed session profiles use cumulative bounds."""

    if capability_id.startswith("network."):
        return TargetLimits(1, 4096, 4096, 2)
    return TargetLimits(
        8,
        64 * 1024,
        1024 * 1024,
        10,
        max_exchanges=16,
        max_total_request_bytes=1024 * 1024,
        max_total_response_bytes=8 * 1024 * 1024,
        max_redirects=4,
        max_total_seconds=60,
    )


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _required_integer(value: object, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(message)
    return value


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
    research_events = [item for item in events if str(item.get("event_id", "")).startswith("research-broker:")]
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
        research = None
        if policy["network"] == "target-broker":
            generation_events = [item for item in target_events if item.get("generation_id") == generation_id]
            grouped: dict[str, list[Mapping[str, Any]]] = {}
            for item in generation_events:
                grouped.setdefault(str(item["request_id"]), []).append(item)
            exchanges = []
            for request_id in sorted(grouped):
                pair = grouped[request_id]
                if len(pair) != 2 or {item["record"] for item in pair} != {"reserved", "classified"}:
                    raise ValueError("brokered resident capability lacks a reserved/classified exchange")
                reserved_event = next(item for item in pair if item["record"] == "reserved")
                classified = next(item for item in pair if item["record"] == "classified")
                transcript = blobs[str(classified["blob_digest"])]
                exchanges.append(
                    {
                        "reserved": {
                            key: reserved_event[key] for key in ("request_id", "binding_digest", "request_digest")
                        },
                        "classified": {key: classified[key] for key in TARGET_CLASSIFIED_FIELDS},
                        "transcript": transcript.hex(),
                    }
                )
            if not exchanges:
                raise ValueError("brokered resident capability has no exchange")
            target = exchanges[0] if len(exchanges) == 1 else exchanges
        elif policy["network"] == "research-broker":
            generation_events = sorted(
                (item for item in research_events if item.get("generation_id") == generation_id),
                key=lambda item: str(item["request_id"]),
            )
            if len(generation_events) != 2:
                raise ValueError("brokered research capability must have recorded and live canonical queries")
            research = [
                {
                    "record": {key: event[key] for key in RESEARCH_PROOF_FIELDS},
                    "body": blobs[str(event["blob_digest"])].hex(),
                }
                for event in generation_events
            ]
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
                "research": research,
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


def _target_limits_document(capability_id: str) -> dict[str, int]:
    limits = qualification_target_limits(capability_id)
    return {
        "max_connections": limits.max_connections,
        "max_request_bytes": limits.max_request_bytes,
        "max_response_bytes": limits.max_response_bytes,
        "timeout_ms": int(limits.timeout_seconds * 1000),
        "max_exchanges": limits.max_exchanges,
        "max_total_request_bytes": limits.max_total_request_bytes,
        "max_total_response_bytes": limits.max_total_response_bytes,
        "max_total_seconds_ms": int(limits.max_total_seconds * 1000),
        "max_redirects": limits.max_redirects,
        "max_cookies": limits.max_cookies,
    }


def _validate_target_proof(
    target: object,
    capability_id: str,
    generation_id: object,
    image: Mapping[str, object],
) -> None:
    exchanges = [target] if isinstance(target, Mapping) else target
    if not isinstance(exchanges, list) or not exchanges:
        raise ValueError("resident Handle Solve broker proof is absent")
    expected_limits = _target_limits_document(capability_id)
    http = capability_id == "network.http" or capability_id.startswith("web.")
    protocols = {"http", "https"} if http else {"tcp"}
    expected_operation: str | set[str] = {
        "web.browser": "browser",
        "web.discovery": {"http-fuzz", "http-session"},
        "protocol.relay": "tcp-session",
    }.get(capability_id, "exchange")
    request_ids = set()
    operations = []
    total_request = total_response = total_elapsed = 0
    for exchange in exchanges:
        if not isinstance(exchange, Mapping):
            raise ValueError("resident Handle Solve broker exchange is invalid")
        reserved, classified = exchange.get("reserved"), exchange.get("classified")
        if not isinstance(reserved, Mapping) or not isinstance(classified, Mapping):
            raise ValueError("resident Handle Solve broker exchange is invalid")
        if set(reserved) != {"request_id", "binding_digest", "request_digest"} or set(classified) != set(
            TARGET_CLASSIFIED_FIELDS
        ):
            raise ValueError("resident Handle Solve broker provenance is invalid")
        if (
            classified.get("request_id") != reserved.get("request_id")
            or classified.get("binding_digest") != reserved.get("binding_digest")
            or classified.get("request_digest") != reserved.get("request_digest")
            or classified.get("challenge_id") != f"fixture:{capability_id}"
            or classified.get("protocol") not in protocols
            or (
                classified.get("operation") not in expected_operation
                if isinstance(expected_operation, set)
                else classified.get("operation") != expected_operation
            )
            or classified.get("outcome") != "answered"
            or classified.get("generation_id") != generation_id
            or classified.get("step_id") != f"fixture:{capability_id}"
            or classified.get("image_manifest_digest") != image["manifest_digest"]
            or classified.get("image_config_digest") != image["config_digest"]
            or classified.get("platform") != image["platform"]
            or classified.get("profile_digest") != STRICT_PROFILE_DIGEST
            or any(classified.get(name) != value for name, value in expected_limits.items())
            or classified.get("request_id") in request_ids
            or not classified.get("endpoint")
            or not classified.get("resolved_address")
        ):
            raise ValueError("resident Handle Solve broker provenance is invalid")
        request_ids.add(classified.get("request_id"))
        operations.append(classified.get("operation"))
        request_bytes = _required_integer(
            classified.get("request_bytes"), "resident Handle Solve broker exchange bound is invalid"
        )
        response_bytes = _required_integer(
            classified.get("response_bytes"), "resident Handle Solve broker exchange bound is invalid"
        )
        elapsed_ms = _required_integer(
            classified.get("elapsed_ms"), "resident Handle Solve broker exchange bound is invalid"
        )
        if (
            request_bytes < 0
            or request_bytes > expected_limits["max_request_bytes"]
            or response_bytes < 0
            or response_bytes > expected_limits["max_response_bytes"]
            or elapsed_ms < 0
            or elapsed_ms > expected_limits["timeout_ms"]
        ):
            raise ValueError("resident Handle Solve broker exchange bound is invalid")
        if classified.get("operation") != "http-fuzz":
            total_request += request_bytes
            total_response += response_bytes
            total_elapsed += elapsed_ms
        try:
            transcript = bytes.fromhex(str(exchange.get("transcript", "")))
        except ValueError as error:
            raise ValueError("resident Handle Solve broker transcript is invalid") from error
        if _sha(transcript) != classified.get("transcript_digest"):
            raise ValueError("resident Handle Solve broker transcript is invalid")
    if len([operation for operation in operations if operation != "http-fuzz"]) > expected_limits["max_exchanges"]:
        raise ValueError("resident Handle Solve broker cumulative limit is invalid")
    if capability_id == "web.discovery" and operations.count("http-fuzz") != 1:
        raise ValueError("resident Handle Solve broker provenance is invalid")
    if (
        total_request > expected_limits["max_total_request_bytes"]
        or total_response > expected_limits["max_total_response_bytes"]
        or total_elapsed > expected_limits["max_total_seconds_ms"]
    ):
        raise ValueError("resident Handle Solve broker cumulative limit is invalid")


def _validate_research_proof(research: object, capability_id: str, generation_id: object) -> None:
    if isinstance(research, Mapping):
        observations = (research,)
    elif isinstance(research, list) and len(research) == 2 and all(isinstance(item, Mapping) for item in research):
        observations = tuple(research)
    else:
        raise ValueError("resident Handle Solve Research proof is absent")
    origins = {
        str(observation.get("record", {}).get("origin", ""))
        for observation in observations
        if isinstance(observation.get("record"), Mapping)
    }
    if len(observations) == 2 and origins != {"recorded", "live"}:
        raise ValueError("resident Handle Solve Research proof lacks recorded/live coverage")
    for observation in observations:
        _validate_research_observation(observation, capability_id, generation_id)


def _validate_research_observation(research: Mapping[str, object], capability_id: str, generation_id: object) -> None:
    if set(research) != {"record", "body"}:
        raise ValueError("resident Handle Solve Research proof is absent")
    record = research.get("record")
    if not isinstance(record, Mapping) or set(record) != set(RESEARCH_PROOF_FIELDS):
        raise ValueError("resident Handle Solve Research provenance is invalid")
    source = {
        "osint.dns": "dns",
        "osint.identity": "github",
        "osint.email": "gravatar",
        "osint.domain": "rdap",
        "osint.geo": "nominatim",
    }.get(capability_id)
    content_type = "application/vnd.incypher.osint+json" if source == "dns" else "application/json"
    try:
        body = bytes.fromhex(str(research["body"]))
    except ValueError as error:
        raise ValueError("resident Handle Solve Research body is invalid") from error
    if (
        record.get("generation_id") != generation_id
        or record.get("attempt_id") != f"qualification:{capability_id}"
        or record.get("kind") != capability_id.removeprefix("osint.")
        or record.get("source_id") != source
        or record.get("outcome") != "answered"
        or record.get("origin") not in {"recorded", "live"}
        or record.get("content_type") != content_type
        or len(str(record.get("url_digest", ""))) != 64
        or len(str(record.get("query_digest", ""))) != 64
        or not record.get("terms")
        or not record.get("robots")
        or not record.get("observed_at")
        or not record.get("expires_at")
        or _sha(body) != record.get("blob_digest")
        or len(body) != record.get("blob_bytes")
    ):
        raise ValueError("resident Handle Solve Research provenance is invalid")
    limits = {
        name: _required_integer(record.get(name), "resident Handle Solve Research bound is invalid")
        for name in (
            "max_body_bytes",
            "timeout_ms",
            "max_redirects",
            "max_requests",
            "max_total_bytes",
            "max_total_seconds_ms",
            "min_interval_ms",
        )
    }
    elapsed_ms = _required_integer(record.get("elapsed_ms"), "resident Handle Solve Research bound is invalid")
    redirect_chain = record.get("redirect_chain")
    dns_chain = record.get("dns_chain")
    if (
        not isinstance(redirect_chain, list)
        or not isinstance(dns_chain, list)
        or not isinstance(record.get("cached"), bool)
        or min(
            limits["max_body_bytes"],
            limits["timeout_ms"],
            limits["max_requests"],
            limits["max_total_bytes"],
            limits["max_total_seconds_ms"],
        )
        <= 0
        or min(limits["max_redirects"], limits["min_interval_ms"], elapsed_ms) < 0
        or len(body) > min(limits["max_body_bytes"], limits["max_total_bytes"])
        or elapsed_ms > min(limits["timeout_ms"], limits["max_total_seconds_ms"])
        or len(redirect_chain) > limits["max_redirects"]
    ):
        raise ValueError("resident Handle Solve Research bound is invalid")


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
            _validate_target_proof(target, capability_id, proof_value.get("generation_id"), image)
        elif target is not None:
            raise ValueError("resident Handle Solve grants undeclared broker access")
        research = proof_value.get("research")
        if policy["network"] == "research-broker":
            _validate_research_proof(research, capability_id, proof_value.get("generation_id"))
        elif research is not None:
            raise ValueError("resident Handle Solve grants undeclared Research access")
        handle = str(proof_value.get("handle_digest", ""))
        if (
            len(handle) != 64
            or proof_value.get("invocation_id", "") == ""
            or proof_value.get("generation_id", "") == ""
        ):
            raise ValueError("resident Handle Solve authority proof is invalid")
    if receipt.get("strict_profile_digest") != STRICT_PROFILE_DIGEST or receipt.get("identity") != _identity(receipt):
        raise ValueError("resident Handle Solve identity is invalid")


__all__ = ["RECEIPT_TYPE", "create_receipt", "qualification_target_limits", "validate_receipt"]
