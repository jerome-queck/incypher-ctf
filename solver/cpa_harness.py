"""Bounded tool loop executed only inside the CPA service process."""

from __future__ import annotations

from solver.cpa_contracts import (
    CPAConfig,
    CPAModel,
    CPAModelReply,
    CPAModelRequest,
    CPAOutcome,
    CPAStatus,
    SERVICE_VERSION,
)
from solver.lead_contracts import MeasuredLeadTurn, TurnMeasure


class CPAHarness:
    def __init__(self, config: CPAConfig, *, model: CPAModel, credential: str, execute_tool) -> None:
        if not credential:
            raise ValueError("CPA credential is absent")
        self.config, self._model, self._credential, self._execute_tool = config, model, credential, execute_tool

    def execute(self, prompt: str) -> CPAOutcome:
        results: list[tuple[str, str]] = []
        audit = ["loop-started"]
        tool_count = 0
        for turn_count in range(1, self.config.max_turns + 1):
            reply = self._respond(prompt, results, turn_count, tool_count, audit)
            if isinstance(reply, CPAOutcome):
                return reply
            if reply.proposal is not None:
                if reply.tool_calls or reply.tokens_in < 0 or reply.tokens_out < 0:
                    return CPAOutcome(CPAStatus.MALFORMED, turn_count, tool_count, audit=tuple(audit))
                measure = TurnMeasure(self.config.model, 0, reply.tokens_in, reply.tokens_out, True)
                audit.append("proposal-returned")
                return CPAOutcome(
                    CPAStatus.PROPOSED,
                    turn_count,
                    tool_count,
                    MeasuredLeadTurn("CPA bounded loop", reply.proposal, measure),
                    audit=tuple(audit),
                )
            if not reply.tool_calls:
                return CPAOutcome(CPAStatus.MALFORMED, turn_count, tool_count, audit=tuple(audit))
            if len(reply.tool_calls) != 1:
                audit.append("denied:parallel-tool-call")
                return CPAOutcome(
                    CPAStatus.REFUSED,
                    turn_count,
                    tool_count,
                    detail="parallel tool calls are forbidden",
                    audit=tuple(audit),
                )
            call = reply.tool_calls[0]
            if call.name not in self.config.allowed_tools:
                audit.append(f"denied:tool:{call.name}")
                return CPAOutcome(
                    CPAStatus.REFUSED, turn_count, tool_count, detail="tool is not allowed", audit=tuple(audit)
                )
            if tool_count >= self.config.max_tools:
                audit.append("denied:tool-bound")
                return CPAOutcome(
                    CPAStatus.REFUSED, turn_count, tool_count, detail="tool bound reached", audit=tuple(audit)
                )
            results.append((call.name, str(self._execute_tool(call.name, call.arguments))))
            tool_count += 1
            audit.append("tool-completed")
        return CPAOutcome(
            CPAStatus.TIMEOUT, self.config.max_turns, tool_count, detail="turn bound reached", audit=tuple(audit)
        )

    def _respond(self, prompt, results, turn_count, tool_count, audit):
        try:
            reply = self._model.respond(
                CPAModelRequest(prompt, tuple(results)), credential=self._credential, parallel_tool_calls=False
            )
        except PermissionError:
            audit.append("denied:authentication")
            return CPAOutcome(
                CPAStatus.REFUSED, turn_count, tool_count, detail="authentication failed", audit=tuple(audit)
            )
        except TimeoutError:
            return CPAOutcome(
                CPAStatus.TIMEOUT, turn_count, tool_count, detail="request deadline reached", audit=tuple(audit)
            )
        if not isinstance(reply, CPAModelReply):
            return CPAOutcome(
                CPAStatus.MALFORMED, turn_count, tool_count, detail="response is malformed", audit=tuple(audit)
            )
        if reply.service_version != SERVICE_VERSION:
            audit.append("denied:service-version")
            return CPAOutcome(
                CPAStatus.REFUSED, turn_count, tool_count, detail="service version mismatch", audit=tuple(audit)
            )
        return reply


__all__ = ["CPAHarness"]
