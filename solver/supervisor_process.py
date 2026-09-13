"""Boot process-group ownership, signals, reaping, and terminal quiescence."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class BootProcess(Protocol):
    """The process controls PID 1 needs from one Boot."""

    def wait(self, timeout: float | None = None) -> int: ...

    def forward(self, signal_number: int) -> None: ...

    def kill(self) -> None: ...

    def group_alive(self) -> bool: ...


class SpawnedBoot:
    """One Run-controller subprocess and its whole process group."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    def wait(self, timeout: float | None = None) -> int:
        try:
            return self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError from error

    @property
    def pid(self) -> int:
        return self._process.pid

    def forward(self, signal_number: int) -> None:
        try:
            os.killpg(self._process.pid, signal_number)
        except ProcessLookupError:
            pass

    def kill(self) -> None:
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def group_alive(self) -> bool:
        try:
            os.killpg(self._process.pid, 0)
        except ProcessLookupError:
            return False
        return True


@dataclass(frozen=True)
class ProcessOutcome:
    """The observed end of one Boot process tree."""

    exit_code: int | None
    signals: tuple[int, ...]
    reaped_children: int
    detail: str
    leader_pid: int | None = None
    group_extinguished: bool = False


class ProcessOwner:
    """Own exactly one Boot process group until every descendant is gone."""

    def __init__(
        self,
        *,
        launch: Callable[[str], BootProcess],
        reap: Callable[[], int],
        record_signal: Callable[[int, int], None],
        monotonic: Callable[[], float] = time.monotonic,
        grace_seconds: float = 330.0,
        stay_quiescent: bool = False,
    ) -> None:
        self._launch = launch
        self._reap = reap
        self._record_signal = record_signal
        self._monotonic = monotonic
        self._grace_seconds = grace_seconds
        self._stay_quiescent = stay_quiescent
        self._process: BootProcess | None = None
        self._signals: list[int] = []
        self._forwarded_signals = 0
        self._stop_deadline: float | None = None
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._terminal = False
        self._leader_reaped = False

    def run(self, boot_id: str, *, started: Callable[[], None]) -> ProcessOutcome:
        """Launch and fully reclaim one Boot, returning only after tree extinction."""

        detail = ""
        exit_code = None
        try:
            self._process = self._launch(boot_id)
            started()
            self._drain_signals()
            exit_code = self._wait_for_boot()
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
        reaped = self._extinguish_and_reap()
        group_extinguished = self._process is None or not self._process.group_alive()
        self._drain_signals()
        self._terminal = True
        return ProcessOutcome(
            exit_code=exit_code,
            signals=tuple(self._signals),
            reaped_children=reaped,
            detail=detail,
            leader_pid=getattr(self._process, "pid", None),
            group_extinguished=group_extinguished,
        )

    def request_stop(self, signal_number: int) -> None:
        """Latch TERM/INT; the owner loop records and forwards it serially."""

        if signal_number not in (signal.SIGTERM, signal.SIGINT):
            raise ValueError("the Supervisor forwards only TERM and INT")
        if self._terminal:
            self._shutdown.set()
            return
        if signal_number in self._signals:
            return
        self._signals.append(signal_number)
        if self._stop_deadline is None:
            self._stop_deadline = self._monotonic() + self._grace_seconds
        self._wake.set()

    def quiesce_terminal(self) -> None:
        """Keep PID 1 supervising adopted children until Docker stops it."""

        self._process = None
        self._terminal = True
        if self._signals:
            self._shutdown.set()
        if self._stay_quiescent:
            while not self._shutdown.wait(0.25):
                self._reap()

    def _wait_for_boot(self) -> int:
        if self._process is None:
            raise RuntimeError("Boot was not launched")
        while True:
            self._drain_signals()
            timeout = 1.0
            if self._stop_deadline is not None:
                timeout = max(0.0, min(timeout, self._stop_deadline - self._monotonic()))
                if timeout == 0.0:
                    self._process.kill()
                    exit_code = self._process.wait()
                    self._leader_reaped = True
                    return exit_code
            try:
                exit_code = self._process.wait(timeout)
                self._leader_reaped = True
                self._drain_signals()
                return exit_code
            except TimeoutError:
                if self._stop_deadline is not None and self._monotonic() >= self._stop_deadline:
                    self._process.kill()
                    exit_code = self._process.wait()
                    self._leader_reaped = True
                    return exit_code

    def _drain_signals(self) -> None:
        self._wake.clear()
        while self._forwarded_signals < len(self._signals):
            signal_number = self._signals[self._forwarded_signals]
            self._forwarded_signals += 1
            self._record_signal(self._forwarded_signals, signal_number)
            if self._process is not None:
                self._process.forward(signal_number)

    def _extinguish_and_reap(self) -> int:
        process = self._process
        if process is None:
            return self._reap()
        self._drain_signals()
        group_alive = process.group_alive()
        deadline = self._monotonic() + self._grace_seconds if group_alive else 0.0
        descendants_reaped = 0
        if group_alive and not self._signals:
            process.forward(signal.SIGTERM)
        while process.group_alive() and self._monotonic() < deadline:
            if not self._leader_reaped:
                try:
                    process.wait(timeout=min(0.25, max(0.0, deadline - self._monotonic())))
                    self._leader_reaped = True
                except (TimeoutError, RuntimeError):
                    pass
            descendants_reaped += self._reap()
            self._wake.wait(0.01)
            self._drain_signals()
        if process.group_alive():
            process.kill()
        if not self._leader_reaped:
            process.wait(5.0)
            self._leader_reaped = True
        reaped = 1 + descendants_reaped + self._reap()
        kill_deadline = time.monotonic() + 5.0
        while process.group_alive():
            reaped += self._reap()
            if time.monotonic() >= kill_deadline:
                raise RuntimeError("Boot process group survived SIGKILL")
            self._wake.wait(0.01)
        return reaped


__all__ = ["BootProcess", "ProcessOutcome", "ProcessOwner", "SpawnedBoot"]
