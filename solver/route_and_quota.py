"""Deterministic policy above the native and CPA inference transports."""

from __future__ import annotations

import enum
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.write_reservation import Capacity, EffectIdentity, ReservedEffect, WriteAuthority

SCHEMA_VERSION = 1
RECEIPT_TYPE = "route-and-quota"
RECEIPT_FILENAME = "route-and-quota.receipt.json"
MANIFEST_RECEIPT_REF = "receipt:route-and-quota"
MANIFEST_ROW_ID = "core.adaptive-routing"
MAX_RECEIPT_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


class InferenceRoute(str, enum.Enum):
    NATIVE = "native-codex"
    CPA = "private-cpa"

    @property
    def alternate(self) -> "InferenceRoute":
        return InferenceRoute.CPA if self is InferenceRoute.NATIVE else InferenceRoute.NATIVE


class FailureKind(str, enum.Enum):
    ROUTE_LOCAL = "route-local"
    SHARED_EXHAUSTION = "shared-exhaustion"
    UNKNOWN = "unknown"
    AUTH = "route-auth"
    CANCELLATION = "cancellation"
    RECOVERY = "recovery"


class RouteTransportFailure(RuntimeError):
    """A transport-owned, typed result; controller policy never parses prose."""

    _STATUS = {
        "timeout": FailureKind.ROUTE_LOCAL,
        "crashed": FailureKind.ROUTE_LOCAL,
        "model-mismatch": FailureKind.ROUTE_LOCAL,
        "auth-failure": FailureKind.AUTH,
        "observed-exhaustion": FailureKind.SHARED_EXHAUSTION,
        "cancelled": FailureKind.CANCELLATION,
        "recovered": FailureKind.RECOVERY,
    }

    def __init__(self, status: str, kind: FailureKind, route: InferenceRoute, evidence_digest: str) -> None:
        super().__init__(status)
        self.status, self.kind, self.route, self.evidence_digest = status, kind, route, evidence_digest

    @classmethod
    def classified(cls, status: str, route: InferenceRoute, evidence_digest: str) -> "RouteTransportFailure":
        kind = cls._STATUS.get(status, FailureKind.UNKNOWN)
        trusted = bool(_SHA256.fullmatch(evidence_digest))
        if kind in {FailureKind.ROUTE_LOCAL, FailureKind.AUTH, FailureKind.RECOVERY} and not trusted:
            kind = FailureKind.UNKNOWN
        return cls(status, kind, route, evidence_digest if trusted else "")


@dataclass(frozen=True)
class RouteObservation:
    sequence: int
    kind: FailureKind
    route: InferenceRoute | None = None
    evidence: str = ""


@dataclass(frozen=True)
class QuotaObservation:
    sequence: int
    observation_id: str
    route: InferenceRoute
    limit_id: str
    used_percent: float | None
    source: str


@dataclass(frozen=True)
class RequestObservation:
    sequence: int
    request_id: str
    route: InferenceRoute


PolicyObservation = RouteObservation | QuotaObservation | RequestObservation


@dataclass(frozen=True)
class QuotaProjection:
    limits: tuple[tuple[str, float | None], ...] = ()
    observation_ids: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    route: InferenceRoute
    switch_count: int
    switch_reason: str
    may_request: bool
    quota: QuotaProjection = QuotaProjection()
    request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControllerDecision:
    route: InferenceRoute
    dispatch: bool
    reason: str


class RouteAndQuotaPolicy:
    """Replay typed observations into one bounded route decision."""

    def __init__(self, *, primary: InferenceRoute) -> None:
        self.primary = primary

    def replay(self, observations: tuple[PolicyObservation, ...]) -> RouteDecision:
        route = self.primary
        switch_count = 0
        switch_reason = ""
        may_request = True
        shared_blocked = False
        last_sequence = 0
        limits: dict[str, float | None] = {}
        observation_ids: list[str] = []
        seen_observations: set[str] = set()
        provenance: set[str] = set()
        request_ids: list[str] = []
        for observation in observations:
            if observation.sequence <= last_sequence:
                raise ValueError("route observations must have a strictly increasing sequence")
            last_sequence = observation.sequence
            if isinstance(observation, QuotaObservation):
                if not observation.observation_id or not observation.limit_id or not observation.source:
                    raise ValueError("quota observation identity and provenance are required")
                if observation.used_percent is not None and (
                    not math.isfinite(observation.used_percent) or not 0.0 <= observation.used_percent <= 100.0
                ):
                    raise ValueError("used percent is outside its observed range")
                if observation.observation_id in seen_observations:
                    continue
                seen_observations.add(observation.observation_id)
                observation_ids.append(observation.observation_id)
                provenance.add(observation.source)
                limits[observation.limit_id] = observation.used_percent
                shared_blocked = any(value is None or value >= 100.0 for value in limits.values())
                may_request = not shared_blocked
                continue
            if isinstance(observation, RequestObservation):
                if not observation.request_id:
                    raise ValueError("request identity is required")
                if observation.request_id not in request_ids:
                    request_ids.append(observation.request_id)
                continue
            if observation.kind in {FailureKind.ROUTE_LOCAL, FailureKind.AUTH}:
                classified = observation.route is route and bool(observation.evidence)
                if classified and switch_count == 0:
                    route = route.alternate
                    switch_count = 1
                    switch_reason = observation.kind.value
                    may_request = True
                else:
                    may_request = False
            elif observation.kind in {
                FailureKind.SHARED_EXHAUSTION,
                FailureKind.UNKNOWN,
                FailureKind.CANCELLATION,
            }:
                may_request = False
                if observation.kind is FailureKind.SHARED_EXHAUSTION:
                    shared_blocked = True
            elif observation.kind is FailureKind.RECOVERY:
                may_request = not shared_blocked
        quota = QuotaProjection(tuple(sorted(limits.items())), tuple(observation_ids), tuple(sorted(provenance)))
        return RouteDecision(route, switch_count, switch_reason, may_request, quota, tuple(request_ids))


class RouteAndQuotaController:
    """Controller adapter that fences requests before either transport is invoked."""

    def __init__(self, policy: RouteAndQuotaPolicy, *, authority: WriteAuthority | None = None) -> None:
        self._policy = policy
        self._authority = authority
        self._effect = ReservedEffect(authority) if authority is not None else None

    @property
    def policy(self) -> RouteAndQuotaPolicy:
        return self._policy

    def admit(self, request_id: str, observations: tuple[PolicyObservation, ...]) -> ControllerDecision:
        decision = self._policy.replay(observations)
        if request_id in decision.request_ids:
            return ControllerDecision(decision.route, False, "duplicate-request")
        if not decision.may_request:
            return ControllerDecision(decision.route, False, "policy-paused")
        return ControllerDecision(decision.route, True, "admitted")

    def execute(
        self,
        *,
        request_id: str,
        generation_id: str,
        payload_digest: str,
        observations: tuple[PolicyObservation, ...],
        transports: Mapping[InferenceRoute, Callable[[], Result]],
        encode: Callable[[Result], Mapping[str, Any]],
        decode: Callable[[Mapping[str, Any]], Result],
    ) -> Result:
        """Reserve, invoke and persist one route result; replay never invokes again."""

        if self._effect is None:
            raise RuntimeError("route controller has no durable effect authority")
        if not request_id or not generation_id or _SHA256.fullmatch(payload_digest) is None:
            raise ValueError("request, generation and payload digest are required")
        self._record_quota_observations(observations)
        durable = self._durable_observations(generation_id)
        offset = durable[-1].sequence if durable else 0
        supplied = tuple(
            _with_sequence(item, item.sequence + offset)
            for item in observations
            if not isinstance(item, QuotaObservation)
        )
        all_observations = durable + supplied
        policy_decision = self._policy.replay(all_observations)
        decision = self.admit(request_id, all_observations)
        if not decision.dispatch:
            raise RuntimeError(decision.reason)
        route = decision.route
        first = self._execute_route(request_id, generation_id, payload_digest, route, transports, encode, decode)
        if not isinstance(first, _FailedRoute):
            return first
        failure = first.failure
        if failure.kind not in {FailureKind.ROUTE_LOCAL, FailureKind.AUTH} or policy_decision.switch_count:
            raise failure
        alternate = route.alternate
        if alternate not in transports:
            raise failure
        second = self._execute_route(
            request_id + ":switch", generation_id, payload_digest, alternate, transports, encode, decode
        )
        if isinstance(second, _FailedRoute):
            raise second.failure
        return second

    def _record_quota_observations(self, observations: tuple[PolicyObservation, ...]) -> None:
        assert self._effect is not None
        for observation in observations:
            if not isinstance(observation, QuotaObservation):
                continue
            document = {
                "observation_id": observation.observation_id,
                "route": observation.route.value,
                "limit_id": observation.limit_id,
                "used_percent": observation.used_percent,
                "source": observation.source,
            }
            self._effect.execute(
                f"quota-observation:{observation.observation_id}",
                EffectIdentity(
                    "quota.observe",
                    f"{observation.route.value}:{observation.limit_id}",
                    digest_bytes(canonical_bytes(document)),
                ),
                Capacity(bytes=2048, objects=1, operations=3),
                lambda: {"quota": [document]},
                encode=lambda value: {"outcome": "result", "result": value},
                decode=lambda value: dict(value["result"]),
            )

    def observations(self, generation_id: str = "") -> tuple[PolicyObservation, ...]:
        durable = list(self._durable_observations(generation_id))
        if self._authority is None:
            return tuple(durable)
        for reservation in self._authority.reservations():
            if reservation.identity.operation != "model.request":
                continue
            route = InferenceRoute(reservation.identity.subject.rsplit(":", 1)[-1])
            request_id = reservation.key.removeprefix("model-request:").removesuffix(":switch")
            durable.append(RequestObservation(len(durable) + 1, request_id, route))
        return tuple(_with_sequence(item, index) for index, item in enumerate(durable, 1))

    def _durable_observations(self, generation_id: str) -> tuple[PolicyObservation, ...]:
        if self._authority is None:
            return ()
        projected: list[PolicyObservation] = []
        seen_quota: set[str] = set()
        for reservation in self._authority.reservations():
            if reservation.identity.operation not in {"model.request", "quota.observe"}:
                continue
            document = reservation.observation
            if not isinstance(document, Mapping):
                continue
            if document.get("outcome") == "failure":
                projected.append(
                    RouteObservation(
                        len(projected) + 1,
                        FailureKind(str(document["kind"])),
                        InferenceRoute(str(document["route"])),
                        f"digest:{document['evidence_digest']}" if document.get("evidence_digest") else "",
                    )
                )
                continue
            result = document.get("result")
            if not isinstance(result, Mapping):
                continue
            quota = result.get("quota", ())
            if not isinstance(quota, list):
                continue
            for item in quota:
                if not isinstance(item, Mapping):
                    continue
                observation_id = str(item.get("observation_id", ""))
                if observation_id in seen_quota:
                    continue
                seen_quota.add(observation_id)
                used = item.get("used_percent")
                projected.append(
                    QuotaObservation(
                        len(projected) + 1,
                        observation_id,
                        InferenceRoute(str(item["route"])),
                        str(item["limit_id"]),
                        float(used) if used is not None else None,
                        str(item["source"]),
                    )
                )
        return tuple(projected)

    def _execute_route(
        self,
        request_id: str,
        generation_id: str,
        payload_digest: str,
        route: InferenceRoute,
        transports: Mapping[InferenceRoute, Callable[[], Result]],
        encode: Callable[[Result], Mapping[str, Any]],
        decode: Callable[[Mapping[str, Any]], Result],
    ) -> Result | "_FailedRoute":
        transport = transports.get(route)
        if transport is None:
            raise RuntimeError(f"selected route {route.value} is unavailable")

        def invoke() -> Result | _FailedRoute:
            try:
                return transport()
            except RouteTransportFailure as failure:
                return _FailedRoute(failure)

        def encode_outcome(outcome: Result | _FailedRoute) -> Mapping[str, Any]:
            if isinstance(outcome, _FailedRoute):
                failure = outcome.failure
                return {
                    "outcome": "failure",
                    "status": failure.status,
                    "kind": failure.kind.value,
                    "route": failure.route.value,
                    "evidence_digest": failure.evidence_digest,
                }
            return {"outcome": "result", "result": dict(encode(outcome))}

        def decode_outcome(document: Mapping[str, Any]) -> Result | _FailedRoute:
            if document.get("outcome") == "failure":
                return _FailedRoute(
                    RouteTransportFailure(
                        str(document["status"]),
                        FailureKind(str(document["kind"])),
                        InferenceRoute(str(document["route"])),
                        str(document.get("evidence_digest", "")),
                    )
                )
            result = document.get("result")
            if not isinstance(result, Mapping):
                raise ValueError("reserved model result is malformed")
            return decode(result)

        return self._effect.execute(
            f"model-request:{request_id}",
            EffectIdentity("model.request", f"{generation_id}:{request_id}:{route.value}", payload_digest),
            Capacity(bytes=4096, objects=1, operations=3),
            invoke,
            encode=encode_outcome,
            decode=decode_outcome,
        )


Result = TypeVar("Result")


@dataclass(frozen=True)
class _FailedRoute:
    failure: RouteTransportFailure


def _with_sequence(observation: PolicyObservation, sequence: int) -> PolicyObservation:
    if isinstance(observation, RouteObservation):
        return RouteObservation(sequence, observation.kind, observation.route, observation.evidence)
    if isinstance(observation, QuotaObservation):
        return QuotaObservation(
            sequence,
            observation.observation_id,
            observation.route,
            observation.limit_id,
            observation.used_percent,
            observation.source,
        )
    return RequestObservation(sequence, observation.request_id, observation.route)


def write_receipt(
    state: Path,
    run_id: str,
    policy: RouteAndQuotaPolicy,
    observations: tuple[PolicyObservation, ...],
) -> Path:
    """Write one immutable, replayable decision receipt."""

    document = _receipt_document(run_id, policy, observations)
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    body = canonical_bytes(document) + b"\n"
    if len(body) > MAX_RECEIPT_BYTES:
        raise ValueError("route-and-quota receipt exceeds one MiB")
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError("immutable route-and-quota receipt already differs")
        return path
    atomic_write(path, body)
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("route-and-quota receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("route-and-quota receipt is not canonical JSON")
    if supplied.get("schema_version") != SCHEMA_VERSION or supplied.get("receipt_type") != RECEIPT_TYPE:
        raise ValueError("route-and-quota receipt schema is unsupported")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise ValueError("route-and-quota receipt path is invalid")
    run_id = str(supplied.get("run_id", ""))
    if receipt_path.parents[1].name != run_id or receipt_path.parents[2].name != "runs":
        raise ValueError("route-and-quota receipt path differs from its Run")
    primary = InferenceRoute(str(supplied.get("primary_route", "")))
    observations = tuple(_observation_from_document(item) for item in supplied.get("observations", ()))
    expected = _receipt_document(run_id, RouteAndQuotaPolicy(primary=primary), observations)
    if supplied != expected:
        raise ValueError("route-and-quota receipt does not recompute")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verify_receipt(path)
    document = json.loads(Path(path).read_text())
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": document["receipt_digest"]}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    """Attach verified controller evidence to its release-candidate row."""

    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


def _receipt_document(
    run_id: str, policy: RouteAndQuotaPolicy, observations: tuple[PolicyObservation, ...]
) -> dict[str, object]:
    decision = policy.replay(observations)
    documents = [_observation_document(observation) for observation in observations]
    basis: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "primary_route": policy.primary.value,
        "observations": documents,
        "classifier_outputs": [
            observation.kind.value for observation in observations if isinstance(observation, RouteObservation)
        ],
        "selected_route": decision.route.value,
        "switch": {"count": decision.switch_count, "reason": decision.switch_reason},
        "shared_quota": {
            "limits": [[key, value] for key, value in decision.quota.limits],
            "observation_ids": list(decision.quota.observation_ids),
            "provenance": list(decision.quota.provenance),
        },
        "may_request": decision.may_request,
        "request_ids": list(decision.request_ids),
    }
    return {**basis, "receipt_digest": digest_bytes(canonical_bytes(basis))}


def _observation_document(observation: PolicyObservation) -> dict[str, object]:
    if isinstance(observation, RouteObservation):
        supplied_digest = observation.evidence.removeprefix("digest:")
        evidence_digest = (
            supplied_digest
            if observation.evidence.startswith("digest:") and _SHA256.fullmatch(supplied_digest)
            else digest_bytes(observation.evidence.encode())
            if observation.evidence
            else ""
        )
        return {
            "type": "route",
            "sequence": observation.sequence,
            "kind": observation.kind.value,
            "route": observation.route.value if observation.route is not None else None,
            "evidence_digest": evidence_digest,
        }
    if isinstance(observation, QuotaObservation):
        return {
            "type": "quota",
            "sequence": observation.sequence,
            "observation_id": observation.observation_id,
            "route": observation.route.value,
            "limit_id": observation.limit_id,
            "used_percent": observation.used_percent,
            "source": observation.source,
        }
    return {
        "type": "request",
        "sequence": observation.sequence,
        "request_id": observation.request_id,
        "route": observation.route.value,
    }


def _observation_from_document(document: object) -> PolicyObservation:
    if not isinstance(document, Mapping):
        raise ValueError("route-and-quota observation is malformed")
    kind = document.get("type")
    sequence = int(document["sequence"])
    if kind == "route":
        route_value = document.get("route")
        route = InferenceRoute(str(route_value)) if route_value is not None else None
        evidence = f"digest:{document['evidence_digest']}" if document.get("evidence_digest") else ""
        return RouteObservation(sequence, FailureKind(str(document["kind"])), route, evidence)
    if kind == "quota":
        used = document.get("used_percent")
        return QuotaObservation(
            sequence,
            str(document["observation_id"]),
            InferenceRoute(str(document["route"])),
            str(document["limit_id"]),
            float(used) if used is not None else None,
            str(document["source"]),
        )
    if kind == "request":
        return RequestObservation(sequence, str(document["request_id"]), InferenceRoute(str(document["route"])))
    raise ValueError("route-and-quota observation type is unknown")


__all__ = [
    "FailureKind",
    "InferenceRoute",
    "MANIFEST_ROW_ID",
    "ControllerDecision",
    "QuotaObservation",
    "QuotaProjection",
    "RequestObservation",
    "RouteAndQuotaController",
    "RouteAndQuotaPolicy",
    "RouteDecision",
    "RouteObservation",
    "RouteTransportFailure",
    "manifest_receipt",
    "link_manifest",
    "verify_receipt",
    "write_receipt",
]
