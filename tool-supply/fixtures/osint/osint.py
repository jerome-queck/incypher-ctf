#!/usr/bin/python3
"""Bounded OSINT adapter: every live or recorded query crosses Research."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


VERSION = "1.0.0"
RESEARCH_CLIENT = "/research-client.py"
FUNCTIONAL_FIXTURE_ID = "osint-functional-fixture-v1"
FUNCTIONAL_FIXTURE = Path(__file__).with_name("functional.json")
LOCAL_FIXTURE_ENV = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
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


def _run_probe(arguments: list[str], marker: str, *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, **(env or {})},
    )
    output = result.stdout + result.stderr
    if result.returncode not in {0, 1, 2} or marker not in output:
        raise RuntimeError(f"OSINT functional probe failed: {arguments[0]}: {output.strip()[:512]}")
    return output


def _run_python_probe(source: str, marker: str, *, env: dict[str, str] | None = None) -> None:
    _run_probe(["/usr/bin/python3", "-c", source], marker, env=env)


class _FixtureHTTPHandler(BaseHTTPRequestHandler):
    def do_CONNECT(self):  # noqa: N802 - BaseHTTPRequestHandler hook
        self.send_error(502, "fixture proxy does not tunnel TLS")

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler hook
        if self.path.endswith(".png"):
            from PIL import Image

            stream = io.BytesIO()
            Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(stream, format="PNG")
            body = stream.getvalue()
            content_type = "image/png"
        else:
            body = b"fixture-user fixture@example.test fixture.example\n"
            content_type = "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler hook
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format, *_arguments):
        return


@contextmanager
def _http_fixture():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _WhoisFixtureHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(1)
        self.request.recv(256)
        self.request.sendall(b"Domain Name: fixture.example\nRegistrar: fixture-registry\n")


@contextmanager
def _whois_fixture():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _WhoisFixtureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _DnsFixtureHandler(socketserver.BaseRequestHandler):
    def handle(self):
        query, endpoint = self.request
        if len(query) < 17 or query[4:6] != b"\x00\x01":
            return
        end = 12
        while end < len(query) and query[end]:
            end += query[end] + 1
        question_end = end + 5
        if question_end > len(query):
            return
        response = (
            query[:2]
            + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
            + query[12:question_end]
            + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
            + socket.inet_aton("192.0.2.1")
        )
        endpoint.sendto(response, self.client_address)


@contextmanager
def _dns_fixture():
    server = socketserver.ThreadingUDPServer(("127.0.0.1", 0), _DnsFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _sherlock_probe(server: ThreadingHTTPServer) -> None:
    with tempfile.TemporaryDirectory(prefix="incypher-sherlock-") as directory:
        root = Path(directory)
        sites = root / "sites.json"
        sites.write_text(
            json.dumps(
                {
                    "Fixture": {
                        "errorMsg": [404],
                        "errorType": "status_code",
                        "regexCheck": "",
                        "url": f"http://127.0.0.1:{server.server_port}/users/{{}}",
                        "urlMain": f"http://127.0.0.1:{server.server_port}/",
                        "username_claimed": "fixture-user",
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        output_text = _run_probe(
            [
                "/usr/bin/sherlock",
                "fixture-user",
                "--json",
                str(sites),
                "--site",
                "Fixture",
                "--remote",
                "--timeout",
                "1",
                "--verbose",
                "--print-all",
                "--print-found",
                "--no-color",
                "--ignore-exclusions",
                "--no-txt",
            ],
            "Fixture: http://127.0.0.1",
            env=LOCAL_FIXTURE_ENV,
        )
        if "fixture-user" not in output_text:
            raise RuntimeError("Sherlock fixture result is missing the queried username")


def _holehe_probe() -> None:
    _run_python_probe(
        """
import httpx
import trio
from holehe.modules.social_media.snapchat import snapchat

def answer(request):
    if request.method == 'GET':
        return httpx.Response(200, request=request, text='data-xsrf="token" data-web-client-id="client"')
    return httpx.Response(200, request=request, json={'hasSnapchat': True})

async def probe():
    observations = []
    transport = httpx.MockTransport(answer)
    async with httpx.AsyncClient(transport=transport, timeout=1) as client:
        await snapchat('fixture@example.test', client, observations)
    assert len(observations) == 1 and observations[0]['exists'] is True
    print('holehe-fixture')

trio.run(probe)
""",
        "holehe-fixture",
    )


def _theharvester_probe() -> None:
    _run_python_probe(
        """
import asyncio
from theHarvester.discovery.crtsh import AsyncFetcher, SearchCrtsh

async def fixture_fetch(_urls, **_options):
    return [[{'name_value': '*.fixture.example'}, {'name_value': 'api.fixture.example'}]]

async def probe():
    AsyncFetcher.fetch_all = fixture_fetch
    search = SearchCrtsh('fixture.example')
    await search.process()
    assert sorted(await search.get_hostnames()) == ['api.fixture.example', 'fixture.example']
    print('theharvester-fixture')

asyncio.run(probe())
""",
        "theharvester-fixture",
    )


def _whois_probe(server: socketserver.ThreadingTCPServer) -> None:
    _run_probe(
        [
            "/usr/bin/whois",
            "-h",
            "127.0.0.1",
            "-p",
            str(server.server_address[1]),
            "fixture.example",
        ],
        "Domain Name: fixture.example",
    )


def _dig_probe(server: socketserver.ThreadingUDPServer) -> None:
    _run_probe(
        [
            "/usr/bin/dig",
            "@127.0.0.1",
            "-p",
            str(server.server_address[1]),
            "fixture.example",
            "A",
            "+short",
        ],
        "192.0.2.1",
    )


def _library_probes(server: ThreadingHTTPServer) -> None:
    _run_python_probe(
        "from geopy.distance import geodesic; assert geodesic((1.3521, 103.8198), (1.3522, 103.8199)).meters > 0; print('geopy-fixture')",
        "geopy-fixture",
    )
    _run_python_probe(
        "import piexif; payload = piexif.dump({'0th': {piexif.ImageIFD.Make: b'InCypher'}}); assert payload.startswith(b'Exif'); print('piexif-fixture')",
        "piexif-fixture",
    )
    _run_python_probe(
        """
from staticmap import CircleMarker, StaticMap

mapping = StaticMap(64, 64, url_template='http://127.0.0.1:%d/{z}/{x}/{y}.png')
mapping.add_marker(CircleMarker((103.8198, 1.3521), 'red', 8))
image = mapping.render(zoom=2, center=(103.8198, 1.3521))
assert image.width == 64 and image.height == 64
print('staticmap-fixture')
"""
        % server.server_port,
        "staticmap-fixture",
        env=LOCAL_FIXTURE_ENV,
    )


def self_check(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("self-check input is not a regular file")
    if not FUNCTIONAL_FIXTURE.is_file() or FUNCTIONAL_FIXTURE_ID not in FUNCTIONAL_FIXTURE.read_text(encoding="utf-8"):
        raise RuntimeError("OSINT functional fixture is unavailable")
    with _http_fixture() as http_server, _whois_fixture() as whois_server, _dns_fixture() as dns_server:
        _sherlock_probe(http_server)
        _holehe_probe()
        _theharvester_probe()
        _whois_probe(whois_server)
        _dig_probe(dns_server)
        _library_probes(http_server)
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
