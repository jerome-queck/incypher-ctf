#!/usr/bin/python3
"""Bounded OSINT adapter: every live or recorded query crosses Research."""

from __future__ import annotations

import base64
import asyncio
from contextlib import contextmanager, redirect_stdout
from concurrent.futures import Future
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


VERSION = "1.0.0"
RESEARCH_CLIENT = "/research-client.py"
FUNCTIONAL_FIXTURE_ID = "osint-functional-fixture-v1"
FUNCTIONAL_FIXTURE = Path(__file__).with_name("functional.json")
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


@contextmanager
def _socket_import_fence():
    original_socket = socket.socket

    class DeniedSocket(original_socket):
        def __new__(cls, *_args, **_options):
            raise OSError("OSINT adapters use only Research")

    socket.socket = DeniedSocket
    try:
        yield
    finally:
        socket.socket = original_socket


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
    tool, inference = _tool_inference(query, response, body)
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
        f"tool={tool}",
        f"tool_inference={_json(inference)}",
    ]
    return "\n".join(lines) + "\n"


def _tool_inference(
    query: dict[str, object], response: dict[str, object], body: bytes
) -> tuple[str, dict[str, object]]:
    kind = query["kind"]
    if response.get("outcome") != "answered":
        return "none", {}
    if kind == "dns":
        document = json.loads(body)
        addresses = document.get("addresses", []) if isinstance(document, dict) else []
        return "research-dns", {"addresses": addresses if isinstance(addresses, list) else []}
    if kind == "identity":
        return "sherlock", _sherlock_inference(str(query["subject"]), response, body)
    if kind == "email":
        return "holehe", _holehe_inference(str(query["subject"]), response, body)
    if kind == "domain":
        if query.get("source_id") == "rdap":
            return "research-rdap", _rdap_inference(body)
        return "theharvester", _theharvester_inference(str(query["subject"]), body)
    if kind == "geo":
        return "geopy", _geopy_inference(str(query["subject"]), body)
    raise RuntimeError("OSINT capability has no production adapter")


def _sherlock_inference(subject: str, response: dict[str, object], body: bytes) -> dict[str, object]:
    with _socket_import_fence():
        import requests
        from sherlock_project import sherlock as sherlock_module
        from sherlock_project.notify import QueryNotify

    broker_response = requests.Response()
    broker_response.status_code = int(response.get("status", 0))
    broker_response._content = body
    broker_response.encoding = "utf-8"
    broker_response.url = "https://broker.invalid/user"

    class BrokerSession:
        def __init__(self, **_options):
            pass

        def request(self, *_args, **_options):
            future: Future[requests.Response] = Future()
            future.set_result(broker_response)
            return future

        get = head = post = put = request

    original = sherlock_module.SherlockFuturesSession
    sherlock_module.SherlockFuturesSession = BrokerSession
    try:
        result = sherlock_module.sherlock(
            subject,
            {
                "Research": {
                    "errorType": "status_code",
                    "errorCode": [404],
                    "url": "https://broker.invalid/users/{}",
                    "urlMain": "https://broker.invalid/",
                }
            },
            QueryNotify(),
            timeout=1,
        )["Research"]["status"]
    finally:
        sherlock_module.SherlockFuturesSession = original
    return {"status": result.status.value, "claimed": result.status.value == "Claimed"}


def _holehe_inference(subject: str, response: dict[str, object], body: bytes) -> dict[str, object]:
    import httpx
    from holehe.modules.cms.gravatar import gravatar

    async def analyse() -> dict[str, object]:
        observations: list[dict[str, object]] = []

        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                int(response.get("status", 0)),
                request=request,
                content=body,
                headers={"content-type": str(response.get("content_type", "application/json"))},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(answer), timeout=1) as client:
            await gravatar(subject, client, observations)
        if len(observations) != 1:
            raise RuntimeError("Holehe returned an invalid observation")
        observation = observations[0]
        return {
            "exists": bool(observation.get("exists")),
            "rate_limited": bool(observation.get("rateLimit")),
            "service": str(observation.get("name", "")),
        }

    return asyncio.run(analyse())


def _theharvester_inference(subject: str, body: bytes) -> dict[str, object]:
    previous_home = os.environ.get("HOME")
    with TemporaryDirectory(prefix="theharvester-home-") as home:
        config = Path(home) / ".theHarvester"
        config.mkdir()
        (config / "proxies.yaml").write_text("http:\nsocks5:\n", encoding="ascii")
        os.environ["HOME"] = home
        try:
            with redirect_stdout(io.StringIO()):
                from theHarvester.discovery.crtsh import AsyncFetcher, SearchCrtsh
        finally:
            if previous_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous_home

    with redirect_stdout(io.StringIO()):
        try:
            document = json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError("theHarvester response is not JSON") from error
        if not isinstance(document, list) or not all(isinstance(item, dict) for item in document):
            raise RuntimeError("theHarvester response is not a record list")

        async def fixture_fetch(_urls, **_options):
            return [document]

        async def analyse() -> dict[str, object]:
            original = AsyncFetcher.fetch_all
            AsyncFetcher.fetch_all = fixture_fetch
            try:
                search = SearchCrtsh(subject)
                await search.process()
                return {"hostnames": sorted(await search.get_hostnames())}
            finally:
                AsyncFetcher.fetch_all = original

        return asyncio.run(analyse())


def _rdap_inference(body: bytes) -> dict[str, object]:
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError("RDAP response is not JSON") from error
    if not isinstance(document, dict):
        raise RuntimeError("RDAP response is not an object")
    nameservers = document.get("nameservers", [])
    if not isinstance(nameservers, list):
        nameservers = []
    return {
        "handle": str(document.get("handle", "")),
        "ldh_name": str(document.get("ldhName", "")),
        "nameservers": sorted(
            str(item["ldhName"])
            for item in nameservers
            if isinstance(item, dict) and isinstance(item.get("ldhName"), str)
        ),
        "status": sorted(str(value) for value in document.get("status", []) if isinstance(value, str))
        if isinstance(document.get("status", []), list)
        else [],
    }


def _geopy_inference(subject: str, body: bytes) -> dict[str, object]:
    with _socket_import_fence():
        from geopy.point import Point

    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, list) and document and isinstance(document[0], dict):
        point = Point(float(document[0]["lat"]), float(document[0]["lon"]))
    elif isinstance(document, dict) and {"latitude", "longitude"} <= document.keys():
        point = Point(float(document["latitude"]), float(document["longitude"]))
    else:
        point = Point(subject)
    return {"latitude": point.latitude, "longitude": point.longitude}


def self_check(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("self-check input is not a regular file")
    if not FUNCTIONAL_FIXTURE.is_file() or FUNCTIONAL_FIXTURE_ID not in FUNCTIONAL_FIXTURE.read_text(encoding="utf-8"):
        raise RuntimeError("OSINT functional fixture is unavailable")
    examples = {
        "identity": (
            {"status": 200},
            b'{"login":"fixture-user"}',
            {"claimed": True, "status": "Claimed"},
        ),
        "email": (
            {"status": 200, "content_type": "application/json"},
            b'{"entry":[{"displayName":"Fixture User","profileUrl":"https://example.test/u"}]}',
            {"exists": True, "rate_limited": False, "service": "gravatar"},
        ),
        "domain": (
            {"status": 200},
            b'[{"name_value":"*.fixture.example"},{"name_value":"api.fixture.example"}]',
            {"hostnames": ["api.fixture.example", "fixture.example"]},
        ),
        "geo": (
            {"status": 200},
            b'[{"lat":"1.3521","lon":"103.8198"}]',
            {"latitude": 1.3521, "longitude": 103.8198},
        ),
    }
    subjects = {
        "identity": "fixture-user",
        "email": "fixture@example.test",
        "domain": "fixture.example",
        "geo": "1.3521,103.8198",
    }
    for kind, (response, body, expected) in examples.items():
        tool, observed = _tool_inference(
            {"kind": kind, "source_id": "crtsh" if kind == "domain" else kind, "subject": subjects[kind]},
            {"outcome": "answered", **response},
            body,
        )
        if tool == "none" or observed != expected:
            raise RuntimeError(f"{kind} production adapter self-check failed")
    rdap_tool, rdap = _tool_inference(
        {"kind": "domain", "source_id": "rdap", "subject": "fixture.example"},
        {"outcome": "answered", "status": 200},
        b'{"handle":"FIXTURE","ldhName":"fixture.example","nameservers":[],"status":["active"]}',
    )
    if rdap_tool != "research-rdap" or rdap["ldh_name"] != "fixture.example":
        raise RuntimeError("domain RDAP adapter self-check failed")
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
