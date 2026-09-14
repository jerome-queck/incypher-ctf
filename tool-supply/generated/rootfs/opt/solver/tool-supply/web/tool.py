#!/usr/bin/python3
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "1.0.0"
TARGET_CLIENT = "/target-client.py"
CURATED_WORDLIST = Path("/opt/solver/tool-supply/web/seclists-routes-2026.2.txt")
MAX_DISCOVERY_PATHS = 128


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
    documents = []
    for candidate in paths:
        if not isinstance(candidate, str) or not candidate.startswith("/") or candidate.startswith("//"):
            raise ValueError("discovery path is invalid")
        documents.append(
            {
                "method": "GET",
                "path": candidate,
                "query": [],
                "body": {"kind": "raw", "content": ""},
                "headers": [],
                "response_headers": ["content-type"],
            }
        )
    results = _target("http-session", documents)
    found = [candidate for candidate, result in zip(paths, results, strict=True) if result.get("status") != 404]
    print("schema=web.discovery.v1")
    print("engine=target-broker-http-session+curated-wordlist")
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
