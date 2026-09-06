#!/usr/bin/env python3
"""PROTOTYPE: exercise agent harnesses against a deterministic fake Responses endpoint.

This is disposable evidence for GitHub issue 184, not production Solver code. It deliberately
keeps every input, raw event, process result, and server request visible in one HTML report.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROMPT = (
    "Use the shell tool exactly once. Run this command verbatim: "
    "printf 'alpha\\nbeta\\n' > harness-proof.txt && shasum -a 256 harness-proof.txt. "
    "Then report only the digest."
)
EXPECTED_DIGEST = hashlib.sha256(b"alpha\nbeta\n").hexdigest()
VOLATILE_KEYS = {"prompt_cache_key", "x-session-affinity", "x-session-id"}
VOLATILE_ID = re.compile(r"\b(?:ses|msg|prt)_[A-Za-z0-9]+\b|\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.IGNORECASE)


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "<volatile-id>" if key.lower() in VOLATILE_KEYS else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return VOLATILE_ID.sub("<volatile-id>", value)
    return value


@dataclass
class Result:
    candidate: str
    case: str
    command: list[str]
    exit_code: int | None
    timed_out: bool
    seconds: float
    proof_digest: str | None
    stdout: str
    stderr: str
    request_count: int
    request_repetitions: dict[str, int]
    requests: list[dict[str, Any]]


class FakeResponses:
    def __init__(self, case: str):
        self.case = case
        self.requests: list[dict[str, Any]] = []
        self.request_repetitions: dict[str, int] = {}
        self.request_count = 0
        self.calls = 0

    def handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _json(self, status: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.endswith("/models"):
                    self._json(200, {"object": "list", "data": [{"id": "trial-model", "object": "model"}]})
                else:
                    self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {"_raw": raw.decode(errors="replace")}
                recorded = scrub(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items() if k.lower() != "authorization"},
                        "body": body,
                    }
                )
                digest = hashlib.sha256(json.dumps(recorded, sort_keys=True).encode()).hexdigest()
                owner.request_count += 1
                owner.request_repetitions[digest] = owner.request_repetitions.get(digest, 0) + 1
                if owner.request_repetitions[digest] == 1:
                    owner.requests.append({"digest": digest, **recorded})
                owner.calls += 1
                if owner.case == "quota":
                    self._json(
                        429,
                        {
                            "error": {
                                "message": "usage limit reached (prototype)",
                                "type": "rate_limit_error",
                                "code": "rate_limit_exceeded",
                            }
                        },
                    )
                    return
                if owner.case == "malformed":
                    chunk = b'event: response.created\ndata: {"type":"response.created"}\n\nevent: response.output_item.added\ndata: {'
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
                self._stream(body)

            def _stream(self, body: dict[str, Any]) -> None:
                output_seen = "function_call_output" in json.dumps(body.get("input", []))
                if output_seen:
                    events = text_events(body.get("model", "trial-model"))
                else:
                    tool_name, arguments = choose_tool(body.get("tools", []))
                    events = tool_events(body.get("model", "trial-model"), tool_name, arguments)
                raw = b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)
                raw += b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler


def choose_tool(tools: list[dict[str, Any]]) -> tuple[str, str]:
    functions = [tool for tool in tools if tool.get("type") == "function"]
    preferred = next((tool for tool in functions if tool.get("name") in {"bash", "shell"}), None)
    tool = preferred or (functions[0] if functions else None)
    if tool is None:
        return "bash", json.dumps(
            {"command": "printf 'alpha\\nbeta\\n' > harness-proof.txt && shasum -a 256 harness-proof.txt"}
        )
    props = tool.get("parameters", {}).get("properties", {})
    values: dict[str, Any] = {}
    command = "printf 'alpha\\nbeta\\n' > harness-proof.txt && shasum -a 256 harness-proof.txt"
    for name, spec in props.items():
        if name in {"command", "cmd"}:
            values[name] = command
        elif name in {"description", "purpose", "i"}:
            values[name] = "write and hash deterministic proof"
        elif name in {"timeout", "timeout_ms"}:
            values[name] = 5000 if name.endswith("_ms") or "millisecond" in spec.get("description", "") else 5
        elif name == "workdir":
            values[name] = "."
        elif name == "args" and spec.get("type") == "array":
            values[name] = ["-lc", command]
    return str(tool.get("name")), json.dumps(values)


def response_base(model: str, output: list[dict[str, Any]], status: str) -> dict[str, Any]:
    return {
        "id": "resp_trial",
        "object": "response",
        "created_at": 1788660000,
        "status": status,
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": 256,
        "model": model,
        "output": output,
        "parallel_tool_calls": False,
        "previous_response_id": None,
        "reasoning": {"effort": "medium", "summary": None},
        "store": False,
        "temperature": 1,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 20,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 120,
        },
    }


def tool_events(model: str, name: str, arguments: str) -> list[dict[str, Any]]:
    item = {
        "id": "fc_trial",
        "call_id": "call_trial",
        "type": "function_call",
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }
    response = response_base(model, [item], "completed")
    return [
        {"type": "response.created", "sequence_number": 0, "response": response_base(model, [], "in_progress")},
        {"type": "response.in_progress", "sequence_number": 1, "response": response_base(model, [], "in_progress")},
        {
            "type": "response.output_item.added",
            "sequence_number": 2,
            "output_index": 0,
            "item": {**item, "arguments": "", "status": "in_progress"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "sequence_number": 3,
            "item_id": "fc_trial",
            "output_index": 0,
            "delta": arguments,
        },
        {
            "type": "response.function_call_arguments.done",
            "sequence_number": 4,
            "item_id": "fc_trial",
            "output_index": 0,
            "arguments": arguments,
        },
        {"type": "response.output_item.done", "sequence_number": 5, "output_index": 0, "item": item},
        {"type": "response.completed", "sequence_number": 6, "response": response},
    ]


def text_events(model: str) -> list[dict[str, Any]]:
    content = {"type": "output_text", "text": EXPECTED_DIGEST, "annotations": [], "logprobs": []}
    item = {"id": "msg_trial", "type": "message", "role": "assistant", "status": "completed", "content": [content]}
    response = response_base(model, [item], "completed")
    return [
        {"type": "response.created", "sequence_number": 0, "response": response_base(model, [], "in_progress")},
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {**item, "status": "in_progress", "content": []},
        },
        {
            "type": "response.content_part.added",
            "sequence_number": 2,
            "item_id": "msg_trial",
            "output_index": 0,
            "content_index": 0,
            "part": {**content, "text": ""},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 3,
            "item_id": "msg_trial",
            "output_index": 0,
            "content_index": 0,
            "delta": EXPECTED_DIGEST,
            "logprobs": [],
        },
        {
            "type": "response.output_text.done",
            "sequence_number": 4,
            "item_id": "msg_trial",
            "output_index": 0,
            "content_index": 0,
            "text": EXPECTED_DIGEST,
            "logprobs": [],
        },
        {
            "type": "response.content_part.done",
            "sequence_number": 5,
            "item_id": "msg_trial",
            "output_index": 0,
            "content_index": 0,
            "part": content,
        },
        {"type": "response.output_item.done", "sequence_number": 6, "output_index": 0, "item": item},
        {"type": "response.completed", "sequence_number": 7, "response": response},
    ]


@contextlib.contextmanager
def server(case: str):
    fake = FakeResponses(case)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    finally:
        httpd.shutdown()
        thread.join()


def candidate_commands(
    root: Path, endpoint: str, args: argparse.Namespace
) -> dict[str, tuple[list[str], dict[str, str]]]:
    root.mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root / "home")}
    (root / "home").mkdir()
    commands: dict[str, tuple[list[str], dict[str, str]]] = {}
    commands["Custom control"] = (
        [sys.executable, str(Path(__file__).resolve()), "--custom-worker", endpoint],
        env,
    )
    if args.omp:
        omp_home = root / "omp-home"
        omp_home.mkdir()
        (omp_home / "models.yml").write_text(
            "providers:\n  trial:\n"
            f"    baseUrl: {endpoint}\n    apiKey: TRIAL_KEY\n    api: openai-responses\n"
            "    models:\n      - id: trial-model\n        name: Trial Model\n        api: openai-responses\n"
            "        reasoning: true\n        input: [text]\n        contextWindow: 128000\n        maxTokens: 4096\n"
        )
        omp_env = {**env, "PI_CODING_AGENT_DIR": str(omp_home)}
        commands["OMP 18.1.10"] = (
            [
                args.omp,
                "--model",
                "trial/trial-model",
                "--mode",
                "json",
                "--print",
                "--no-session",
                "--no-extensions",
                "--no-skills",
                "--no-rules",
                "--no-lsp",
                "--no-pty",
                "--tools",
                "bash",
                "--approval-mode",
                "yolo",
                "--max-time",
                "8s",
                PROMPT,
            ],
            omp_env,
        )
    if args.opencode:
        config = {
            "$schema": "https://opencode.ai/config.json",
            "plugin": [],
            "provider": {
                "trial": {
                    "npm": "@ai-sdk/openai",
                    "name": "Trial",
                    "options": {"baseURL": endpoint, "apiKey": "TRIAL_KEY"},
                    "models": {"trial-model": {"name": "Trial Model"}},
                }
            },
            "permission": {"*": "deny", "bash": "allow"},
        }
        (root / "opencode.json").write_text(json.dumps(config, indent=2))
        xdg = root / "xdg"
        opencode_env = {
            **env,
            "OPENCODE_CONFIG": str(root / "opencode.json"),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_AUTOCOMPACT": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "OPENCODE_DISABLE_PRUNE": "true",
            "XDG_DATA_HOME": str(xdg / "data"),
            "XDG_CONFIG_HOME": str(xdg / "config"),
            "XDG_CACHE_HOME": str(xdg / "cache"),
            "XDG_STATE_HOME": str(xdg / "state"),
        }
        commands["OpenCode 1.18.29"] = (
            [
                args.opencode,
                "run",
                "--pure",
                "--format",
                "json",
                "--print-logs",
                "--log-level",
                "DEBUG",
                "--model",
                "trial/trial-model",
                "--auto",
                PROMPT,
            ],
            opencode_env,
        )
    return commands


def custom_request(endpoint: str, body: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request = urllib.request.Request(
        f"{endpoint}/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer TRIAL_KEY",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    events: list[dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=8) as response:
        for raw in response:
            line = raw.decode().rstrip("\r\n")
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line.removeprefix("data: "))
            if not isinstance(event, dict) or "type" not in event:
                raise ValueError("unclassified Responses event")
            events.append(event)
    completed = [event["response"] for event in events if event["type"] == "response.completed"]
    if len(completed) != 1 or completed[0].get("status") != "completed":
        raise ValueError("missing unique completed response")
    return events, completed[0]


def custom_worker(endpoint: str) -> int:
    tool = {
        "type": "function",
        "name": "shell",
        "description": "Run one bounded shell command.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    first_input = [{"role": "user", "content": [{"type": "input_text", "text": PROMPT}]}]
    first_events, first = custom_request(
        endpoint,
        {"model": "trial-model", "input": first_input, "tools": [tool], "stream": True, "store": False},
    )
    calls = [item for item in first.get("output", []) if item.get("type") == "function_call"]
    if len(calls) != 1 or calls[0].get("name") != "shell":
        raise ValueError("response did not contain exactly one allowed shell call")
    arguments = json.loads(calls[0]["arguments"])
    if set(arguments) != {"command"}:
        raise ValueError("shell arguments escaped the closed schema")
    print(json.dumps({"type": "step-begin", "call_id": calls[0]["call_id"], "command": arguments["command"]}))
    completed = subprocess.run(
        arguments["command"],
        shell=True,
        executable="/bin/sh",
        capture_output=True,
        text=True,
        timeout=5,
        start_new_session=True,
    )
    print(
        json.dumps(
            {
                "type": "step-end",
                "call_id": calls[0]["call_id"],
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    )
    second_input = [
        *first_input,
        calls[0],
        {"type": "function_call_output", "call_id": calls[0]["call_id"], "output": completed.stdout + completed.stderr},
    ]
    second_events, second = custom_request(
        endpoint,
        {"model": "trial-model", "input": second_input, "tools": [tool], "stream": True, "store": False},
    )
    messages = [item for item in second.get("output", []) if item.get("type") == "message"]
    if len(messages) != 1:
        raise ValueError("response did not contain one final message")
    text = "".join(part.get("text", "") for part in messages[0].get("content", []) if part.get("type") == "output_text")
    print(
        json.dumps(
            {
                "type": "close",
                "text": text,
                "usage": [first.get("usage"), second.get("usage")],
                "event_types": [[event["type"] for event in first_events], [event["type"] for event in second_events]],
            }
        )
    )
    return 0


def run_one(
    candidate: str, case: str, command: list[str], env: dict[str, str], workdir: Path, fake: FakeResponses
) -> Result:
    started = time.monotonic()
    proc = subprocess.Popen(
        command, cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    proof = workdir / "harness-proof.txt"
    proof_digest = hashlib.sha256(proof.read_bytes()).hexdigest() if proof.exists() else None
    return Result(
        candidate,
        case,
        command,
        proc.returncode,
        timed_out,
        time.monotonic() - started,
        proof_digest,
        scrub(stdout),
        scrub(stderr),
        fake.request_count,
        dict(fake.request_repetitions),
        list(fake.requests),
    )


def render(results: list[Result], path: Path) -> None:
    payload = json.dumps([asdict(result) for result in results]).replace("</", "<\\/")
    rows = "".join(
        f"<tr><td>{html.escape(r.candidate)}</td><td>{r.case}</td><td>{r.exit_code}</td><td>{r.seconds:.2f}s</td>"
        f"<td>{'yes' if r.proof_digest == EXPECTED_DIGEST else 'no'}</td><td>{r.request_count}</td></tr>"
        for r in results
    )
    path.write_text(
        "<!doctype html><meta charset=utf-8><title>Secondary harness trial — PROTOTYPE</title>"
        "<style>body{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:.45rem;text-align:left}"
        "button{margin:.25rem;padding:.5rem}.panel{white-space:pre-wrap;background:#111;color:#eee;padding:1rem;overflow:auto}</style>"
        "<h1>Secondary harness trial — PROTOTYPE</h1><p>Throwaway evidence for issue 184. Select a run to inspect full state.</p>"
        f"<p>Expected proof digest: <code>{EXPECTED_DIGEST}</code></p>"
        f"<table><thead><tr><th>Candidate</th><th>Case</th><th>Exit</th><th>Wall</th><th>Tool fidelity</th><th>Requests</th></tr></thead><tbody>{rows}</tbody></table>"
        "<h2>Guided inspection</h2><div id=buttons></div><pre id=panel class=panel></pre>"
        f"<script>const results={payload};const b=document.querySelector('#buttons'),p=document.querySelector('#panel');"
        "results.forEach((r,i)=>{const x=document.createElement('button');x.textContent=r.candidate+' · '+r.case;"
        "x.onclick=()=>p.textContent=JSON.stringify(r,null,2);b.append(x)});if(results.length){p.textContent=JSON.stringify(results[0],null,2)}</script>"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--omp")
    parser.add_argument("--opencode")
    parser.add_argument("--custom-worker")
    parser.add_argument("--output", default="solver/secondary_harness_trial_prototype.html")
    args = parser.parse_args()
    if args.custom_worker:
        try:
            return custom_worker(args.custom_worker)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, urllib.error.HTTPError) as error:
            print(json.dumps({"type": "failure", "error": str(error)}), file=sys.stderr)
            return 1
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="secondary-harness-") as temp:
        root = Path(temp)
        for case in ("success", "malformed", "quota"):
            for wanted in ("Custom", "OMP", "OpenCode"):
                with server(case) as (fake, endpoint):
                    available = candidate_commands(root / f"{case}-{wanted.lower()}", endpoint, args)
                    for candidate, (command, env) in available.items():
                        if not candidate.startswith(wanted):
                            continue
                        workdir = root / f"work-{case}-{candidate.split()[0].lower()}"
                        workdir.mkdir()
                        results.append(run_one(candidate, case, command, env, workdir, fake))
    render(results, Path(args.output))
    for result in results:
        print(
            result.candidate,
            result.case,
            f"exit={result.exit_code}",
            f"timeout={result.timed_out}",
            f"tool_fidelity={result.proof_digest == EXPECTED_DIGEST}",
            f"requests={result.request_count}",
            f"seconds={result.seconds:.2f}",
        )
    return 0 if results else 2


if __name__ == "__main__":
    raise SystemExit(main())
