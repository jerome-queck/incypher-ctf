"""Production composition of fixed domain probes, Remedies, and probation."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from solver.recovery.contracts import ChangedAction, FaultKind, ProbeObservation, ProbationOutcome, RecoveryContext
from solver.recovery.incident import Fault, IncidentEngine, IncidentResult
from solver.redaction import Redactor
from solver.write_reservation import WriteAuthority


@dataclass(frozen=True)
class AuthoritativeChange:
    before: str
    after: str
    source: str


@dataclass
class DomainRecovery:
    """Actual domain containment/effect callbacks around one authoritative projection."""

    dimension: str
    project_change: Callable[[], AuthoritativeChange | None]
    apply: Callable[[], bool]
    prove_probation: Callable[[], ProbationOutcome]
    fence: Callable[[], None] = lambda: None
    capture: Callable[[], bytes] = lambda: b""
    teardown: Callable[[], None] = lambda: None
    adapter_id: str = ""
    adapter_config: dict[str, str] | None = None


class RecoveryRegistry:
    """Boot-scoped reconstruction of the adapter named by each durable Incident."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[dict[str, str]], DomainRecovery]] = {}

    def register(self, adapter_id: str, factory: Callable[[dict[str, str]], DomainRecovery]) -> None:
        if not adapter_id or adapter_id in self._factories:
            raise ValueError("Recovery adapter identity must be unique")
        self._factories[adapter_id] = factory

    def resolve(self, context: RecoveryContext) -> DomainRecovery:
        try:
            factory = self._factories[context.adapter_id]
        except KeyError:
            raise RuntimeError(f"Recovery adapter {context.adapter_id!r} is unavailable at Boot") from None
        config = json.loads(context.adapter_config)
        if not isinstance(config, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in config.items()
        ):
            raise ValueError("Recovery adapter configuration is invalid")
        return factory(config)

    @property
    def identities(self) -> frozenset[str]:
        return frozenset(self._factories)


class _DomainPorts:
    def __init__(self, recovery: DomainRecovery) -> None:
        self._recovery = recovery

    def fence(self, _fault: Fault) -> None:
        self._recovery.fence()

    def evidence(self, _fault: Fault) -> bytes:
        return self._recovery.capture()

    def teardown(self, _fault: Fault) -> None:
        self._recovery.teardown()

    def replace(self, _fault: Fault) -> bool:
        raise RuntimeError("deterministic Recovery cannot use legacy replacement")

    def probe(self, _fault: Fault, _probe_id: str) -> ProbeObservation:
        change = self._recovery.project_change()
        if change is None:
            return ProbeObservation.unsettled("authoritative domain projection is unsettled")
        return ProbeObservation.settled(
            ChangedAction(self._recovery.dimension, change.before, change.after, change.source)
        )

    def apply_remedy(self, _fault: Fault, _remedy_id: str, _changed_action: ChangedAction) -> bool:
        return self._recovery.apply()

    def probation(self, _fault: Fault, _remedy_id: str) -> ProbationOutcome:
        return self._recovery.prove_probation()


class DeterministicRecovery:
    """Public production entrypoint for every fixed Core Recovery domain."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        *,
        now: Callable[[], dt.datetime] | None = None,
        authority: WriteAuthority | None = None,
    ) -> None:
        self._state = Path(state)
        self._run_id = run_id
        self._redactor = redactor
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._authority = authority

    def handle(
        self,
        *,
        kind: FaultKind,
        fault_id: str,
        scope: str,
        generation_id: str,
        evidence: str,
        failed_action_value: str,
        original_deadline: dt.datetime,
        recovery: DomainRecovery,
        allowance: int = 1,
    ) -> IncidentResult:
        adapter_id = recovery.adapter_id or f"{kind.value}:{scope}:v1"
        adapter_config = json.dumps(recovery.adapter_config or {}, sort_keys=True, separators=(",", ":"))
        context = RecoveryContext(
            original_deadline.isoformat(), allowance, failed_action_value, adapter_id, adapter_config
        )
        fault = Fault(fault_id, scope, generation_id, evidence, kind, context)
        return IncidentEngine(
            self._state,
            self._run_id,
            _DomainPorts(recovery),
            self._redactor,
            now=self._now,
            authority=self._authority,
        ).report(fault)

    def validate_boot_adapters(self) -> None:
        from solver.event_store import EventStore
        from solver.final_interval_runtime import FINAL_INTERVAL_RECOVERY_ADAPTER
        from solver.recovery.contracts import INCIDENT_RECORDED
        from solver.recovery.instance import INSTANCE_ADAPTER
        from solver.recovery.safe_read import SAFE_READ_ADAPTER
        from solver.recovery.storage import STORAGE_ADAPTER
        from solver.recovery.submission import SUBMISSION_ADAPTER
        from solver.route_and_quota import ROUTE_RECOVERY_ADAPTER
        from solver.supervisor import PROCESS_RECOVERY_ADAPTER

        known = {
            FINAL_INTERVAL_RECOVERY_ADAPTER,
            INSTANCE_ADAPTER,
            SAFE_READ_ADAPTER,
            STORAGE_ADAPTER,
            SUBMISSION_ADAPTER,
            ROUTE_RECOVERY_ADAPTER,
            PROCESS_RECOVERY_ADAPTER,
        }
        latest = {
            event.payload["incident_id"]: event.payload
            for event in EventStore(self._state, run_id=self._run_id, redactor=self._redactor).events()
            if event.event_type == INCIDENT_RECORDED
        }
        for incident in latest.values():
            if (
                not incident["terminal"]
                and incident.get("catalogue_version")
                and incident.get("adapter_id") not in known
            ):
                raise ValueError(f"unavailable Recovery adapter: {incident.get('adapter_id')}")

    def replay(self, registry: RecoveryRegistry) -> tuple[IncidentResult, ...]:
        return IncidentEngine(
            self._state,
            self._run_id,
            _RegistryPorts(registry),
            self._redactor,
            now=self._now,
            authority=self._authority,
        ).replay(adapter_ids=registry.identities)


class _RegistryPorts:
    """Select the correct domain adapter from the persisted Fault context."""

    def __init__(self, registry: RecoveryRegistry) -> None:
        self._registry = registry
        self._ports: dict[str, _DomainPorts] = {}

    def _for(self, fault: Fault) -> _DomainPorts:
        assert fault.recovery is not None
        ports = self._ports.get(fault.identity)
        if ports is None:
            ports = _DomainPorts(self._registry.resolve(fault.recovery))
            self._ports[fault.identity] = ports
        return ports

    def fence(self, fault):
        return self._for(fault).fence(fault)

    def evidence(self, fault):
        return self._for(fault).evidence(fault)

    def teardown(self, fault):
        return self._for(fault).teardown(fault)

    def replace(self, fault):
        return self._for(fault).replace(fault)

    def probe(self, fault, probe_id):
        return self._for(fault).probe(fault, probe_id)

    def apply_remedy(self, fault, remedy_id, changed_action):
        return self._for(fault).apply_remedy(fault, remedy_id, changed_action)

    def probation(self, fault, remedy_id):
        return self._for(fault).probation(fault, remedy_id)
