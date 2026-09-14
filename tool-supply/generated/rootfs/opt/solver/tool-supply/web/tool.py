#!/usr/bin/python3
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

VERSION = "1.0.0"
TARGET_CLIENT = "/target-client.py"
CURATED_WORDLIST = Path("/opt/solver/tool-supply/web/seclists-routes-2026.2.txt")
MAX_DISCOVERY_PATHS = 128


def _ffuf_probe(paths: list[str]) -> None:
    """Run the pinned ffuf binary against a bounded loopback fixture."""
    if not paths or len(paths) > MAX_DISCOVERY_PATHS:
        raise ValueError("ffuf probe paths are outside the admitted bound")
    if any(not path.startswith("/") or path.startswith("//") for path in paths):
        raise ValueError("ffuf probe path is invalid")
    expected_path = paths[0]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler hook
            status = 200 if self.path.split("?", 1)[0] == expected_path else 404
            body = b"ffuf-fixture\n" if status == 200 else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_arguments):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="incypher-ffuf-") as directory:
            root = Path(directory)
            wordlist = root / "words.txt"
            output = root / "results.json"
            wordlist.write_text("\n".join(path.lstrip("/") for path in paths) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "/usr/bin/ffuf",
                    "-u",
                    f"http://127.0.0.1:{server.server_port}/FUZZ",
                    "-w",
                    str(wordlist),
                    "-mc",
                    "200",
                    "-of",
                    "json",
                    "-o",
                    str(output),
                    "-s",
                    "-t",
                    "1",
                    "-timeout",
                    "1",
                    "-maxtime",
                    "10",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"ffuf fixture probe failed: {detail[:256]}")
            try:
                document = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeError("ffuf fixture output is not JSON") from error
            results = document.get("results") if isinstance(document, dict) else None
            if (
                not isinstance(results, list)
                or len(results) != 1
                or not isinstance(results[0], dict)
                or results[0].get("status") != 200
            ):
                raise RuntimeError("ffuf fixture did not produce one bounded match")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _target(command, documents):
    handle, path = tempfile.mkstemp(prefix="incypher-web-", suffix=".json")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(documents, stream, sort_keys=True, separators=(",", ":"))
        result = subprocess.run(
            ["/usr/bin/python3", TARGET_CLIENT, command, path],
            capture_output=True,
            check=False,
            timeout=45,
        )
    finally:
        os.unlink(path)
    if result.returncode != 0:
        raise RuntimeError("Target operation was refused")
    return json.loads(result.stdout)


def discovery(path):
    request = json.loads(Path(path).read_text())
    prefix = request.get("prefix", "/")
    if not isinstance(prefix, str) or not prefix.startswith("/") or prefix.startswith("//"):
        raise ValueError("discovery prefix is invalid")
    paths = []
    try:
        words = CURATED_WORDLIST.open(encoding="utf-8")
    except OSError as error:
        raise ValueError("curated discovery wordlist is unavailable") from error
    with words:
        for word in words:
            word = word.strip()
            if not word or word.startswith("#"):
                continue
            candidate = f"{prefix.rstrip('/')}/{word.lstrip('/')}"
            if candidate == "//" or not candidate.startswith("/"):
                raise ValueError("curated discovery path is invalid")
            if candidate not in paths:
                paths.append(candidate)
            if len(paths) > MAX_DISCOVERY_PATHS:
                raise ValueError("curated discovery wordlist exceeds its path bound")
    if not paths:
        raise ValueError("curated discovery wordlist is empty")
    result = _target("http-fuzz", {"paths": paths})
    if result.get("outcome") != "answered" or not isinstance(result.get("body"), str):
        raise RuntimeError("ffuf Target operation was refused")
    try:
        observation = json.loads(base64.b64decode(result["body"], validate=True))
    except (ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("ffuf Target observation is invalid") from error
    found = observation.get("found") if isinstance(observation, dict) else None
    if (
        not isinstance(found, list)
        or any(not isinstance(candidate, str) or candidate not in paths for candidate in found)
        or len(set(found)) != len(found)
    ):
        raise RuntimeError("ffuf Target observation is invalid")
    print("schema=web.discovery.v1")
    print("engine=ffuf@2.2.1+target-broker-http-session+curated-wordlist")
    print("ffuf=pass")
    for candidate in found:
        print(f"found={candidate}")


def browser(path):
    request = json.loads(Path(path).read_text())
    result = _target("browser", request)
    observation = result.get("browser", {})
    dom = str(observation.get("dom", ""))
    print("schema=web.browser.v1")
    print("engine=playwright@1.55.0+chromium@150.0.7871.181")
    print(f"dom_sha256={hashlib.sha256(dom.encode()).hexdigest()}")
    print(f"dom={dom}")
    print(f"network_events={len(observation.get('network', []))}")
    print(f"local_storage={json.dumps(observation.get('local_storage', []), separators=(',', ':'))}")
    print(f"downloads={json.dumps(observation.get('downloads', []), separators=(',', ':'))}")


def self_check(_path):
    _ffuf_probe(["/ffuf-fixture-hit", "/ffuf-fixture-miss"])
    probes = (
        (["/usr/bin/dpkg-query", "-W", "-f=${Version}", "ffuf"], "2.2.1-1"),
        (["/usr/bin/ffuf", "-V"], "ffuf version: 2.1.0-dev"),
        (["/usr/bin/chromium", "--version"], "Chromium 150.0.7871.181"),
        (
            [
                "/usr/bin/python3",
                "-c",
                "from playwright.sync_api import sync_playwright; "
                "p=sync_playwright().start(); "
                "b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']); "
                "b.close(); p.stop(); print('playwright-ok')",
            ],
            "playwright-ok",
        ),
    )
    for argv, expected in probes:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode != 0 or expected not in result.stdout:
            raise RuntimeError(f"Web component probe failed: {argv[0]}")
    print("web-profile-self-check")


def main():
    if sys.argv[1:] == ["--version"]:
        print(VERSION)
        return 0
    if len(sys.argv) != 3:
        return 2
    try:
        if sys.argv[1] == "--self-check":
            self_check(sys.argv[2])
        elif sys.argv[1] == "web.discovery":
            discovery(sys.argv[2])
        elif sys.argv[1] == "web.browser":
            browser(sys.argv[2])
        else:
            return 2
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"web tool refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
