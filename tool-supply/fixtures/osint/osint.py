#!/usr/bin/python3
"""Bounded OSINT adapter: every live or recorded query crosses Research."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


VERSION = "1.0.0"
RESEARCH_CLIENT = "/research-client.py"
CAPABILITIES = frozenset(
    {
        "osint.dns",
        "osint.identity",
        "osint.email",
        "osint.domain",
        "osint.geo",
    }
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _query(document: object, capability: str) -> dict[str, object]:
    if capability not in CAPABILITIES:
        raise ValueError("unsupported OSINT capability")
    fields = {"kind", "source_id", "subject", "body", "content_type"}
    if not isinstance(document, dict) or set(document) != fields:
        raise ValueError("ResearchQuery fields are invalid")
    kind = document["kind"]
    source_id = document["source_id"]
    subject = document["subject"]
    body = document["body"]
    content_type = document["content_type"]
    expected_kind = capability.removeprefix("osint.")
    if (
        kind != expected_kind
        or not isinstance(source_id, str)
        or not source_id
        or not isinstance(subject, str)
        or not subject
        or len(subject.encode()) > 1024
        or any(character in subject for character in "\r\n\0")
        or not isinstance(body, str)
        or not isinstance(content_type, str)
    ):
        raise ValueError("ResearchQuery values are invalid")
    try:
        decoded = base64.b64decode(body, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("ResearchQuery body is not base64") from error
    if bool(decoded) != bool(content_type):
        raise ValueError("ResearchQuery body and content type disagree")
    return {
        "kind": kind,
        "source_id": source_id,
        "subject": subject,
        "body": body,
        "content_type": content_type,
    }


def _queries(path: Path, capability: str) -> tuple[dict[str, object], ...]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("ResearchQuery input must be a regular file")
    try:
        document = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("ResearchQuery is not valid JSON") from error
    if isinstance(document, dict) and set(document) == {"queries"}:
        supplied = document["queries"]
        if not isinstance(supplied, list) or len(supplied) != 2:
            raise ValueError("ResearchQuery batch must contain recorded and live queries")
        queries = tuple(_query(item, capability) for item in supplied)
        if {bool(query["body"]) for query in queries} != {False, True} or len(
            {(query["kind"], query["source_id"], query["subject"]) for query in queries}
        ) != 1:
            raise ValueError("ResearchQuery batch must pair one subject's recorded and live observations")
        return queries
    return (_query(document, capability),)


def _research(query_path: Path) -> tuple[dict[str, object], ...]:
    if not Path(RESEARCH_CLIENT).is_file():
        raise RuntimeError("Research broker client is unavailable")
    result = subprocess.run(
        ["/usr/bin/python3", RESEARCH_CLIENT, "query", str(query_path)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "Research query was refused"
        raise RuntimeError(detail[:256])
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Research broker response is not JSON") from error
    if isinstance(response, dict):
        return (response,)
    if isinstance(response, list) and response and all(isinstance(item, dict) for item in response):
        return tuple(response)
    raise RuntimeError("Research broker response is not an object or non-empty list")


def _body(response: dict[str, object]) -> bytes:
    encoded = response.get("body", "")
    if not isinstance(encoded, str):
        raise RuntimeError("Research broker body is invalid")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise RuntimeError("Research broker body is not base64") from error


def _render(query: dict[str, object], response: dict[str, object]) -> str:
    kind = str(query["kind"])
    provenance = response.get("provenance", {})
    if not isinstance(provenance, dict):
        raise RuntimeError("Research provenance is invalid")
    body = _body(response)
    lines = [
        f"schema=osint.{kind}.v1",
        f"outcome={response.get('outcome', '')}",
        f"source_id={query['source_id']}",
        f"subject={query['subject']}",
        f"origin={provenance.get('origin', '')}",
        f"content_type={response.get('content_type', '')}",
        f"status={response.get('status', 0)}",
        f"body_sha256={hashlib.sha256(body).hexdigest()}",
        f"body_base64={base64.b64encode(body).decode('ascii')}",
        f"query_digest={provenance.get('query_digest', '')}",
        f"dns_chain={_json(provenance.get('dns_chain', []))}",
        f"redirect_chain={_json(provenance.get('redirect_chain', []))}",
    ]
    return "\n".join(lines) + "\n"


def _probe_command(name: str, arguments: tuple[str, ...]) -> None:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"missing OSINT executable: {name}")
    result = subprocess.run(
        [executable, *arguments],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if not (result.stdout or result.stderr) or result.returncode not in {0, 1, 2}:
        raise RuntimeError(f"OSINT executable probe failed: {name}")


def _probe_imports() -> None:
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            "import geopy; import piexif; from staticmap import StaticMap; print('imports-ok')",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or "imports-ok" not in result.stdout:
        raise RuntimeError("OSINT Python import probe failed")


def self_check(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("self-check input is not a regular file")
    _probe_command("dig", ("-v",))
    _probe_command("whois", ())
    _probe_command("sherlock", ("--version",))
    _probe_command("holehe", ("--help",))
    _probe_command("theHarvester", ("--help",))
    _probe_imports()
    print("osint-profile-self-check")


def main(arguments: list[str]) -> int:
    if arguments == ["--version"]:
        print(VERSION)
        return 0
    if len(arguments) == 2 and arguments[0] == "--self-check":
        self_check(Path(arguments[1]))
        return 0
    if len(arguments) != 2:
        return 2
    capability, input_path = arguments
    queries = _queries(Path(input_path), capability)
    responses = _research(Path(input_path))
    if len(queries) != len(responses):
        raise RuntimeError("Research broker response count is invalid")
    rendered = "".join(_render(query, response) for query, response in zip(queries, responses, strict=True))
    sys.stdout.write(rendered)
    return 0 if all(response.get("outcome") == "answered" for response in responses) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"osint tool refused: {error}", file=sys.stderr)
        raise SystemExit(1) from error
