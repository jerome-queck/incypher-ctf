"""PID-1 ownership of one Run and its Boot processes."""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import signal
import subprocess
import sys
import threading
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

from solver import boot
from solver.attempt_executor_pool import (
    POOL_ENV,
    AttemptPool,
    close_attempt_pool,
    prepare_attempt_pool,
)
from solver.boot import Refusal
from solver.board_broker_contracts import BOARD_BROKER_HOLDINGS_ENV, BOARD_BROKER_SOCKET_ENV
from solver.bootstrap_custody import Broker
from solver.credentials import SECRETS
from solver.event_store import EventStore, EventStoreDamage
from solver.event_store_contracts import (
    BootClosed,
    BootOpened,
    ChildReaped,
    ForwardedSignal,
    RunClosed,
    ServiceStarted,
    SignalForwarded,
    TerminalDisposition,
)
from solver.isolation import strict_preflight
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.replay import verify_and_materialize_run_state
from solver.supervisor_process import ProcessOutcome, ProcessOwner, SpawnedBoot
from solver.supervisor_services import ServiceName, SupervisorServices
from solver.supervisor_custody import SupervisorCustody
from solver.supervisor_lifecycle import LifecycleWriter

NORMAL = "normal"
REFUSED = "refused"
INTERRUPTED = "interrupted"
CRASHED = "crashed"

RUN_STATE = Path("/state")
CLEAN_EXIT = 0
BROKEN_EXIT = 1
REFUSED_EXIT = 2


class RunAlreadySupervised(RuntimeError):
    """Another PID 1 already owns this Run's process tree."""


class SupervisorLock:
    """One live PID-1 owner per Run state root."""

    def __init__(self, state: Path, run_id: str) -> None:
        self._path = Path(state) / "runs" / run_id / "supervisor.lock"
        self._file = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        self._file = os.fdopen(descriptor, "a+b")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._file.close()
            self._file = None
            raise RunAlreadySupervised(f"Run {self._path.parent.name!r} already has a live Supervisor") from error
        return self

    def __exit__(self, _error_type, _error, _traceback) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


@dataclass(frozen=True)
class SupervisorResult:
    run_id: str
    boot_id: str
    disposition: str
    receipt_path: Path


class Supervisor:
    """Admit verified state before starting the effect-capable Run controller."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        redactor: Redactor,
        services: SupervisorServices,
        timestamp=None,
        monotonic=None,
        grace_seconds: float = 330.0,
        stay_quiescent: bool = False,
    ) -> None:
        self._state = Path(state)
        self._run_id = run_id
        self._redactor = redactor
        self._services = services
        self._boot_id = ""
        self._lifecycle = LifecycleWriter(
            self._state,
            run_id,
            redactor,
            timestamp=timestamp or (lambda: dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        process_options = {
            "launch": services.launch_controller,
            "reap": services.reap_children,
            "record_signal": self._record_signal,
            "grace_seconds": grace_seconds,
            "stay_quiescent": stay_quiescent,
        }
        if monotonic is not None:
            process_options["monotonic"] = monotonic
        self._owner = ProcessOwner(**process_options)

    def run(self) -> SupervisorResult:
        with SupervisorLock(self._state, self._run_id):
            return self._run_owned()

    def _run_owned(self) -> SupervisorResult:
        started_services = self._services.admit()
        if terminal := self._lifecycle.terminal():
            disposition, boot_id = terminal
            result = SupervisorResult(
                run_id=self._run_id,
                boot_id=boot_id,
                disposition=disposition,
                receipt_path=self._lifecycle.write_receipt(),
            )
            return self._finish(result)
        self._lifecycle.ensure_run_open()
        self._lifecycle.reconcile_unclosed_boots()
        boot_id = self._lifecycle.next_boot_id()
        for service in started_services:
            self._lifecycle.append(
                f"{boot_id}:service:{service.value}",
                ServiceStarted(boot_id=boot_id, service=service),
            )
        self._lifecycle.append(f"{boot_id}:open", BootOpened(boot_id))
        self._boot_id = boot_id
        custody = self._services.bootstrap_custody(boot_id)
        self._lifecycle.append(
            f"{boot_id}:service:{ServiceName.CREDENTIAL_CUSTODY.value}",
            ServiceStarted(boot_id=boot_id, service=ServiceName.CREDENTIAL_CUSTODY),
        )
        try:
            outcome = self._owner.run(boot_id, started=self._record_controller_started)
        finally:
            custody.close()
        disposition = self._disposition(outcome)
        self._lifecycle.append(
            f"{boot_id}:reaped",
            ChildReaped(boot_id=boot_id, reaped_children=outcome.reaped_children),
        )
        self._lifecycle.append(
            f"{boot_id}:close",
            BootClosed(
                boot_id=boot_id,
                disposition=TerminalDisposition(disposition),
                detail=outcome.detail,
            ),
        )
        self._lifecycle.append(
            "run:close",
            RunClosed(disposition=TerminalDisposition(disposition), detail=outcome.detail),
        )
        receipt_path = self._lifecycle.write_receipt()
        result = SupervisorResult(
            run_id=self._run_id,
            boot_id=boot_id,
            disposition=disposition,
            receipt_path=receipt_path,
        )
        return self._finish(result)

    def request_stop(self, signal_number: int) -> None:
        """Latch TERM/INT for serialized process ownership."""

        self._owner.request_stop(signal_number)

    def _finish(self, result: SupervisorResult) -> SupervisorResult:
        self._owner.quiesce_terminal()
        return result

    def _record_signal(self, index: int, signal_number: int) -> None:
        self._lifecycle.append(
            f"{self._boot_id}:signal:{index}",
            SignalForwarded(
                boot_id=self._boot_id,
                signal=ForwardedSignal(signal.Signals(signal_number).name),
            ),
        )

    def _record_controller_started(self) -> None:
        service = ServiceName.RUN_CONTROLLER.value
        self._lifecycle.append(
            f"{self._boot_id}:service:{service}",
            ServiceStarted(boot_id=self._boot_id, service=ServiceName.RUN_CONTROLLER),
        )

    @staticmethod
    def _disposition(outcome: ProcessOutcome) -> str:
        if outcome.signals:
            return INTERRUPTED
        if outcome.exit_code == 0:
            return NORMAL
        if outcome.exit_code == 2:
            return REFUSED
        return CRASHED


def admit_storage(state: Path, run_id: str) -> None:
    """Refuse before authority opens when the canonical Run root is not writable."""

    store = EventStore(state, run_id=run_id)
    store.events()
    try:
        available = os.statvfs(store.canonical_dir).f_bavail
    except OSError as error:
        raise Refusal(f"{boot.MARK} canonical storage admission failed — {error}") from error
    if available < 1 or not os.access(store.canonical_dir, os.W_OK):
        raise Refusal(f"{boot.MARK} canonical storage admission found no writable capacity")


def verify_state(state: Path, run_id: str, redactor: Redactor) -> None:
    """Translate canonical damage into the Boot-boundary Refusal contract."""

    try:
        verify_and_materialize_run_state(state, run_id, redactor)
    except EventStoreDamage as damage:
        raise Refusal(f"{boot.MARK} canonical state verification refused this Run — {damage.classification}") from None


def preflight_isolation(environ, state: Path, run_id: str, *, prepare_attempt_runtime=None) -> Path:
    """Admit the exact strict profile before the effect-capable controller opens."""

    return write_isolation_receipt(
        state,
        run_id,
        strict_preflight(environ, prepare_attempt_runtime=prepare_attempt_runtime),
    )


def launch_boot(boot_id: str, environ, attempt_pool: AttemptPool | None = None) -> SpawnedBoot:
    controller_environment = dict(environ)
    controller_environment["SUPERVISOR_BOOT_ID"] = boot_id
    pass_fds: tuple[int, ...] = ()
    if attempt_pool is not None:
        controller_environment[POOL_ENV], pass_fds = attempt_pool.controller_environment()
    process = subprocess.Popen(
        [sys.executable, "-m", "solver"],
        env=controller_environment,
        start_new_session=True,
        pass_fds=pass_fds,
    )
    for name in SECRETS:
        environ.pop(name, None)
    return SpawnedBoot(process)


def reap_children() -> int:
    """Reap every exited descendant adopted by container PID 1."""

    reaped = 0
    while True:
        try:
            child_pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if child_pid == 0:
            break
        reaped += 1
    return reaped


def quiesce_refusal() -> None:
    """Keep a pre-authority Refusal terminal until Docker deliberately stops PID 1."""

    stopped = threading.Event()
    previous = {caught: signal.getsignal(caught) for caught in (signal.SIGTERM, signal.SIGINT)}
    for caught in previous:
        signal.signal(caught, lambda _number, _frame: stopped.set())
    try:
        while not stopped.wait(0.25):
            reap_children()
    finally:
        for caught, handler in previous.items():
            signal.signal(caught, handler)


def main(environ, *, state: Path = RUN_STATE, stay_quiescent: bool = True) -> int:
    """Run PID 1 and report its terminal classification."""

    attempt_pool: AttemptPool | None = None
    try:
        run_id = boot.run_identity(environ)
        redactor = Redactor.for_declared_secrets(environ)
        if not isinstance(environ, MutableMapping):
            raise Refusal(f"{boot.MARK} Supervisor bootstrap environment is not mutable")
        controller_environment = dict(environ)

        def timestamp() -> str:
            return dt.datetime.now(dt.timezone.utc).isoformat()

        custody = SupervisorCustody(
            state=state,
            run_id=run_id,
            environment=environ,
            redactor=redactor,
            timestamp=timestamp,
        )

        def prepare_pool() -> None:
            nonlocal attempt_pool
            attempt_pool = prepare_attempt_pool()

        def open_custody(boot_id: str):
            result = custody.open(boot_id)
            endpoint = result.endpoints.get(Broker.BOARD)
            holdings = result.holdings.get(Broker.BOARD, ())
            if endpoint is None or "CTFD_API_TOKEN" not in holdings:
                result.close()
                raise Refusal(f"{boot.MARK} Board broker custody has no usable endpoint")
            controller_environment.clear()
            controller_environment.update(environ)
            for name in SECRETS:
                controller_environment.pop(name, None)
            controller_environment[BOARD_BROKER_SOCKET_ENV] = str(endpoint)
            controller_environment[BOARD_BROKER_HOLDINGS_ENV] = ",".join(holdings)
            return result

        supervisor = Supervisor(
            state=state,
            run_id=run_id,
            redactor=redactor,
            services=SupervisorServices(
                verify_replay=lambda: verify_state(state, run_id, redactor),
                admit_storage=lambda: admit_storage(state, run_id),
                preflight_isolation=lambda: preflight_isolation(
                    environ,
                    state,
                    run_id,
                    prepare_attempt_runtime=prepare_pool,
                ),
                bootstrap_custody=open_custody,
                launch_controller=lambda boot_id: launch_boot(boot_id, controller_environment, attempt_pool),
                reap_children=reap_children,
            ),
            stay_quiescent=stay_quiescent,
        )
        previous = {caught: signal.getsignal(caught) for caught in (signal.SIGTERM, signal.SIGINT)}
        for caught in previous:
            signal.signal(caught, lambda number, _frame: supervisor.request_stop(number))
        try:
            result = supervisor.run()
        finally:
            for caught, handler in previous.items():
                signal.signal(caught, handler)
    except (Refusal, EventStoreDamage, RunAlreadySupervised, OSError, ValueError) as refused:
        print(refused, file=sys.stderr, flush=True)
        if stay_quiescent:
            quiesce_refusal()
        return REFUSED_EXIT
    finally:
        close_attempt_pool(attempt_pool)
    print(f"{result.disposition}: Run {result.run_id}, Boot {result.boot_id}", flush=True)
    return {
        NORMAL: CLEAN_EXIT,
        REFUSED: REFUSED_EXIT,
        INTERRUPTED: BROKEN_EXIT,
        CRASHED: BROKEN_EXIT,
    }[result.disposition]


__all__ = [
    "CRASHED",
    "INTERRUPTED",
    "NORMAL",
    "REFUSED",
    "RunAlreadySupervised",
    "Supervisor",
    "SupervisorLock",
    "SupervisorResult",
    "admit_storage",
    "launch_boot",
    "main",
    "preflight_isolation",
    "quiesce_refusal",
    "reap_children",
    "verify_state",
]


if __name__ == "__main__":
    sys.exit(main(os.environ))
