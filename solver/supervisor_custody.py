"""Production bootstrap custody owned for the lifetime of one Supervisor Boot."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from pathlib import Path

from solver import boot
from solver.boot import Refusal
from solver.board_broker import denied_probe
from solver.bootstrap_custody import BootstrapCustody, BootstrapResult, Broker, SecretSource
from solver.capability_evidence import CapabilityEvidence
from solver.credentials import SECRETS
from solver.event_store import EventStore
from solver.executor_secret_probe import ExecutorProbeResult, probe_executor
from solver.redaction import Redactor

_OWNER_BY_ENV_NAME = {
    "CTFD_API_TOKEN": Broker.BOARD,
    "TEAM_KEY": Broker.BOARD,
    "CPA_TOKEN": Broker.CPA,
}


class SupervisorCustody:
    """Move inherited env secrets to brokers, prove executor absence, and return their lease."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        environment: MutableMapping[str, str],
        redactor: Redactor,
        timestamp: Callable[[], str],
        probe: Callable[..., ExecutorProbeResult] = probe_executor,
    ) -> None:
        self._state = Path(state)
        self._run_id = run_id
        self._environment = environment
        self._redactor = redactor
        self._timestamp = timestamp
        self._probe = probe

    def open(self, boot_id: str) -> BootstrapResult:
        sources = self._sources()
        if not sources:
            raise Refusal(f"{boot.MARK} bootstrap custody found no sanctioned credential source")
        fixtures = [bytearray(source.bytes_for_transfer()) for source in sources]
        custody = BootstrapCustody(
            state=self._state,
            run_id=self._run_id,
            boot_id=boot_id,
            redactor=self._redactor,
            timestamp=self._timestamp,
        )
        result: BootstrapResult | None = None
        try:
            result = custody.transfer(sources, self._environment)
            board = result.brokers.get(Broker.BOARD)
            if board is not None:
                result.endpoints[Broker.BOARD] = board.configure_board(
                    state=self._state,
                    run_id=self._run_id,
                    boot_id=boot_id,
                    url=self._environment.get("CTFD_URL", ""),
                )
                result.profile_handles[Broker.BOARD] = board.profile_handle
            probe_result = self._probe_executor(result, fixtures)
            codex = result.brokers.get(Broker.CODEX)
            if codex is not None:
                result.endpoints[Broker.CODEX] = codex.configure_codex(
                    state=self._state,
                    run_id=self._run_id,
                    boot_id=boot_id,
                    model=self._environment.get("CODEX_MODEL", "") or boot.DEFAULT_MODEL,
                    probe=probe_result,
                )
            evidence = CapabilityEvidence(
                self._state,
                self._run_id,
                boot_id,
                self._redactor,
                self._timestamp,
            )
            socket_refused = True
            if board is not None:
                probe_response = denied_probe(
                    result.endpoints[Broker.BOARD], b"\0".join(bytes(item) for item in fixtures)
                )
                socket_refused = evidence.record_socket_probe(probe_response, tuple(bytes(item) for item in fixtures))
            evidence.record_executor_probe(probe_result)
            if not socket_refused:
                raise Refusal(f"{boot.MARK} Board broker socket probe did not prove refusal")
            if not probe_result.memory_complete or not all(clear for _kind, clear in probe_result.checks):
                raise Refusal(f"{boot.MARK} executor secret probe did not prove every surface clear")
            return result
        except Exception:
            if result is not None:
                result.close()
            raise
        finally:
            _zero(fixtures)

    def _sources(self) -> list[SecretSource]:
        sources = [
            SecretSource.from_environment(self._environment, name, _OWNER_BY_ENV_NAME[name])
            for name in SECRETS
            if name in _OWNER_BY_ENV_NAME and self._environment.get(name, "")
        ]
        codex_home = self._state / "codex"
        if (codex_home / "auth.json").is_file() and not any(source.owner is Broker.CODEX for source in sources):
            sources.append(SecretSource("CODEX_HOME", Broker.CODEX, bytearray(str(codex_home).encode())))
        return sources

    def _probe_executor(
        self,
        result: BootstrapResult,
        fixtures: Sequence[bytearray],
    ) -> ExecutorProbeResult:
        workdir = self._state / "runs" / self._run_id / "runtime" / "custody-probe"
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = EventStore(self._state, run_id=self._run_id)
        event_paths = tuple(path for path in store.canonical_dir.rglob("*") if path.is_file())
        return self._probe(
            tuple(bytes(fixture) for fixture in fixtures),
            environment=result.executor_environment,
            workdir=workdir,
            event_paths=event_paths,
        )


def _zero(fixtures: Sequence[bytearray]) -> None:
    for fixture in fixtures:
        for index in range(len(fixture)):
            fixture[index] = 0


__all__ = ["SupervisorCustody"]
