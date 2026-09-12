"""Canonical facts for generation-scoped Tool authority."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError

TOOL_CONTROL_RECORDED = "tool-control.recorded"


class ToolRecord(str, enum.Enum):
    RESERVED = "reserved"
    COMPLETED = "completed"
    DENIED = "denied"


@dataclass(frozen=True)
class ToolControlRecorded:
    event_id: str
    invocation_id: str
    record: ToolRecord
    binding: Mapping[str, str]
    capability_id: str
    component_id: str
    version: str
    image_digest: str
    view_digest: str
    arguments_digest: str
    input_digest: str
    resources: Mapping[str, int]
    exit_code: int = 0
    reason: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return TOOL_CONTROL_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "invocation_id": self.invocation_id,
            "record": self.record.value,
            **self.binding,
            "capability_id": self.capability_id,
            "component_id": self.component_id,
            "version": self.version,
            "image_digest": self.image_digest,
            "view_digest": self.view_digest,
            "arguments_digest": self.arguments_digest,
            "input_digest": self.input_digest,
            "resources": dict(self.resources),
            "exit_code": self.exit_code,
            "reason": self.reason,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "invocation_id",
            "record",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "capability_id",
            "component_id",
            "version",
            "image_digest",
            "view_digest",
            "arguments_digest",
            "input_digest",
            "reason",
            "ts",
            "blob_digest",
        )
        required = (*strings, "resources", "exit_code", "blob_bytes")
        if missing := [name for name in required if name not in payload]:
            raise InvalidEventError(f"Tool-control payload is missing: {', '.join(missing)}", sequence=sequence)
        if any(not isinstance(payload[name], str) for name in strings):
            raise InvalidEventError("Tool-control payload has a non-string field", sequence=sequence)
        if any(
            not isinstance(payload[name], int) or isinstance(payload[name], bool)
            for name in ("exit_code", "blob_bytes")
        ):
            raise InvalidEventError("Tool-control payload has an invalid count", sequence=sequence)
        resources = payload["resources"]
        if not isinstance(resources, Mapping) or any(
            not isinstance(name, str) or not name or not isinstance(value, int) or isinstance(value, bool) or value < 0
            for name, value in resources.items()
        ):
            raise InvalidEventError("Tool-control resources are invalid", sequence=sequence)
        if payload["record"] not in {item.value for item in ToolRecord}:
            raise InvalidEventError("Tool-control record is unsupported", sequence=sequence)
        if not all(payload[name] for name in strings[:2] + strings[3:16]):
            raise InvalidEventError("Tool-control authority identity is incomplete", sequence=sequence)
        for name in ("view_digest", "arguments_digest", "input_digest", "blob_digest"):
            if not _is_lowercase_sha256(payload[name]):
                raise InvalidEventError(f"Tool-control {name} is invalid", sequence=sequence)
        image = payload["image_digest"]
        if not isinstance(image, str) or not image.startswith("sha256:") or not _is_lowercase_sha256(image[7:]):
            raise InvalidEventError("Tool-control image digest is invalid", sequence=sequence)
        if payload["blob_bytes"] < 0:
            raise InvalidEventError("Tool-control blob count is negative", sequence=sequence)
        if payload["record"] != ToolRecord.COMPLETED.value and (
            payload["blob_bytes"] or payload["blob_digest"] != EMPTY_BLOB_DIGEST or payload["exit_code"]
        ):
            raise InvalidEventError("Non-completed Tool record carries output", sequence=sequence)
        if (payload["record"] == ToolRecord.DENIED.value) != bool(payload["reason"]):
            raise InvalidEventError("Tool denial reason disagrees with record", sequence=sequence)


def _is_lowercase_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - set("0123456789abcdef")
