"""Closed Responses transport used inside the private CPA process."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from solver.cpa_contracts import CPAModelReply, CPAModelRequest
from solver.lead_contracts import CandidateProposal, ProgressProposal, StopProposal


class CPAResponsesModel:
    def __init__(self, endpoint: str, model: str) -> None:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "api.openai.com",
            "chatgpt.com",
            "localhost",
            "127.0.0.1",
        }:
            raise ValueError("CPA Responses endpoint is not an admitted OpenAI or controlled-local route")
        self._endpoint = endpoint.rstrip("/")
        self._model = model

    def respond(self, request: CPAModelRequest, *, credential: str, parallel_tool_calls: bool) -> CPAModelReply:
        body = {
            "model": self._model,
            "input": request.prompt,
            "parallel_tool_calls": parallel_tool_calls,
            "store": False,
            "tools": [
                _proposal_tool("propose_progress"),
                _proposal_tool("propose_candidate"),
                _proposal_tool("propose_stop"),
            ],
        }
        call = urllib.request.Request(
            self._endpoint,
            data=json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
            headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(call, timeout=30) as response:
                document = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise PermissionError("CPA authentication failed") from None
            raise
        if not isinstance(document, dict) or document.get("status") != "completed":
            raise ValueError("CPA response did not complete")
        proposal = _proposal(document.get("output"))
        usage = document.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        return CPAModelReply(
            proposal=proposal,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
        )


def _proposal_tool(name: str) -> dict[str, object]:
    properties = {"detail": {"type": "string"}}
    required = ["detail"]
    if name == "propose_candidate":
        properties = {"value": {"type": "string"}, "evidence_refs": {"type": "array", "items": {"type": "string"}}}
        required = ["value", "evidence_refs"]
    return {
        "type": "function",
        "name": name,
        "description": "Return one measured proposal to the Solve Lead.",
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        "strict": True,
    }


def _proposal(output: object):
    if not isinstance(output, list):
        raise ValueError("CPA response output is malformed")
    calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"]
    if len(calls) != 1:
        raise ValueError("CPA response must return exactly one proposal")
    call = calls[0]
    arguments = json.loads(call.get("arguments", ""))
    if call.get("name") == "propose_progress" and set(arguments) == {"detail"}:
        return ProgressProposal(arguments["detail"])
    if call.get("name") == "propose_stop" and set(arguments) == {"detail"}:
        return StopProposal(arguments["detail"])
    if call.get("name") == "propose_candidate" and set(arguments) == {"value", "evidence_refs"}:
        return CandidateProposal(arguments["value"], tuple(arguments["evidence_refs"]))
    raise ValueError("CPA response proposal escaped the closed schema")


__all__ = ["CPAResponsesModel"]
