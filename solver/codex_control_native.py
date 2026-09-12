"""Migration adapters from the v1 native probe to measured Codex Control Turns."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from solver.codex import CLAIM, CLOSE, Credential, Invocation, Launch, Taken, run_attempt
from solver.codex_control import CodexControlClient
from solver.codex_control_contracts import CodexRequest, CodexTurn, LimitObservation, NativeResponse
from solver.record import Recorder
from solver.stall import Deadline

LimitReader = Callable[[], Sequence[LimitObservation]]


class ControlledNativeProbe:
    """Run the existing native parser and return its measured Turn to Control."""

    def __init__(
        self,
        *,
        workdir: Path,
        deadline: Deadline,
        recorder: Recorder,
        attempt_id: str,
        credential: Credential,
        invocation: Invocation,
        launch: Launch | None = None,
        now: Callable[[], dt.datetime] | None = None,
        limits: LimitReader = tuple,
        first_step: int = 1,
    ) -> None:
        self._arguments = (workdir, deadline, recorder, attempt_id, credential, invocation, launch, now)
        self._limits = limits
        self._first_step = first_step
        self._active: dict[str, Deadline] = {}

    def __call__(self, request: CodexRequest) -> NativeResponse:
        workdir, deadline, recorder, attempt_id, credential, invocation, launch, now = self._arguments
        if credential.model != request.model or invocation.reasoning_effort != request.effort:
            raise ValueError("controlled native probe disagrees with catalogue request")
        self._active[request.request_id] = deadline
        began = (now or (lambda: dt.datetime.now(dt.timezone.utc)))()
        try:
            taken = tuple(
                run_attempt(
                    request.prompt,
                    workdir,
                    deadline,
                    recorder=recorder,
                    attempt_id=attempt_id,
                    chain=(credential,),
                    invocation=invocation,
                    launch=launch,
                    now=now,
                    first_step=self._first_step,
                )
            )
        finally:
            self._active.pop(request.request_id, None)
        ended = (now or (lambda: dt.datetime.now(dt.timezone.utc)))()
        usage = _turn_usage(recorder, taken)
        text = "\n".join(item.shown for item in taken if item.kind == CLAIM)
        turn = CodexTurn(
            request.request_id,
            request.turn_id,
            request.model,
            text,
            usage[0],
            usage[1],
            max(0, int((ended - began).total_seconds() * 1000)),
            tuple(
                {
                    "kind": item.kind,
                    "tool": item.tool,
                    "command": item.command,
                    "exit_code": item.exit_code,
                    "shown": item.shown,
                    "digest": item.digest,
                    "step_index": item.step_index,
                }
                for item in taken
            ),
        )
        return NativeResponse(turn, tuple(self._limits()))

    def cancel(self, request_id: str) -> bool:
        deadline = self._active.get(request_id)
        if deadline is None:
            return False
        deadline.shorten(dt.datetime.now(dt.timezone.utc))
        return True


class V1CodexControlAdapter:
    """Keep the v1 caller shape while every request crosses Codex Control IPC."""

    def __init__(self, client: CodexControlClient, model: str, effort: str) -> None:
        self._client, self._model, self._effort = client, model, effort
        self.last_limits = ()

    def turn(
        self,
        prompt: str,
        *,
        request_id: str,
        turn_id: str,
        workdir: Path | None = None,
        attempt_id: str = "",
        deadline: dt.datetime | None = None,
        first_step: int = 1,
    ) -> CodexTurn:
        result = self._client.request(
            CodexRequest(
                request_id,
                turn_id,
                self._model,
                self._effort,
                prompt,
                str(workdir) if workdir else "",
                attempt_id,
                deadline.isoformat() if deadline else "",
                first_step,
            )
        )
        if result.outcome != "answered" or result.turn is None:
            raise NativeControlFailure(result.outcome)
        self.last_limits = result.limits
        return result.turn

    def cancel(self, request_id: str) -> bool:
        return self._client.cancel(request_id).outcome == "cancelled"


class NativeControlFailure(RuntimeError):
    def __init__(self, outcome: str) -> None:
        super().__init__(f"native Codex Control {outcome}")
        self.outcome = outcome


class OwnerNativeTransport:
    """Build each legacy native invocation only inside the subscription-owner service."""

    def __init__(self, *, state: Path, run_id: str, codex_home: Path, redactor) -> None:
        self._state, self._run_id, self._home, self._redactor = Path(state), run_id, Path(codex_home), redactor
        self._active: dict[str, ControlledNativeProbe] = {}

    def __call__(self, request: CodexRequest) -> NativeResponse:
        if not request.workdir or not request.attempt_id or not request.deadline:
            raise ValueError("native owner request lacks v1 execution binding")
        probe = ControlledNativeProbe(
            workdir=Path(request.workdir),
            deadline=Deadline(budget=dt.datetime.fromisoformat(request.deadline)),
            recorder=Recorder(self._state, self._run_id, self._redactor),
            attempt_id=request.attempt_id,
            credential=Credential("codex-subscription", request.model, self._home),
            invocation=Invocation(reasoning_effort=request.effort),
            first_step=request.first_step,
        )
        self._active[request.request_id] = probe
        try:
            return probe(request)
        finally:
            self._active.pop(request.request_id, None)

    def cancel(self, request_id: str) -> bool:
        probe = self._active.get(request_id)
        return probe.cancel(request_id) if probe is not None else False


def _turn_usage(recorder: Recorder, taken: Sequence[Taken]) -> tuple[int, int]:
    closes = {item.step_index for item in taken if item.kind == CLOSE}
    tokens_in = tokens_out = 0
    for line in recorder.stream_path.read_text().splitlines():
        record = json.loads(line)
        if record.get("record") == "step-end" and record.get("step_index") in closes:
            tokens_in += int(record.get("tokens_in", 0))
            tokens_out += int(record.get("tokens_out", 0))
    return tokens_in, tokens_out


__all__ = ["ControlledNativeProbe", "NativeControlFailure", "OwnerNativeTransport", "V1CodexControlAdapter"]
