from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from solver import target_broker_browser_worker


REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_FIXTURE = REPO_ROOT / "tool-supply" / "fixtures" / "web"


def _load_browser_driver():
    playwright = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = object()
    playwright.sync_api = sync_api
    sys.modules["playwright"] = playwright
    sys.modules["playwright.sync_api"] = sync_api
    spec = importlib.util.spec_from_file_location("web_browser_driver", WEB_FIXTURE / "browser_driver.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_browser_worker_collects_stdout_and_stderr_with_a_hard_bound() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('o' * 8192); sys.stderr.write('e' * 8192)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    stdout, stderr, limited = target_broker_browser_worker._bounded_communicate(
        process,
        b"",
        stdout_limit=1024,
        stderr_limit=512,
    )

    assert limited is True
    assert len(stdout) <= 1024
    assert len(stderr) <= 512
    assert process.poll() is not None


def test_browser_download_digest_rejects_before_reading_an_oversized_file(tmp_path: Path) -> None:
    driver = _load_browser_driver()
    path = tmp_path / "download.bin"
    path.write_bytes(b"x" * 9)

    with pytest.raises(ValueError, match="download exceeds its byte bound"):
        driver._bounded_file_digest(path, 8)


def test_browser_download_digest_streams_a_bounded_file(tmp_path: Path) -> None:
    driver = _load_browser_driver()
    path = tmp_path / "download.bin"
    path.write_bytes(b"held-download")

    digest, size = driver._bounded_file_digest(path, 1024)

    assert digest == "9d3a75f4d6359d9ea48c5f72e6d833650a1fc013e49d2cf9d77e192a228b5b92"
    assert size == len(b"held-download")


def test_browser_uses_an_inherited_file_limit_for_chromium_downloads(tmp_path: Path, monkeypatch) -> None:
    driver = _load_browser_driver()
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    executable = Path(driver._bounded_chromium_executable(8192))

    assert "resource.setrlimit(resource.RLIMIT_FSIZE, (8192, 8192))" in executable.read_text()


def test_browser_chromium_inherits_process_and_cpu_limits(tmp_path: Path, monkeypatch) -> None:
    driver = _load_browser_driver()
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    executable = Path(driver._bounded_chromium_executable(8192, cpu_seconds=61))

    source = executable.read_text()
    assert "resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))" in source
    assert "resource.setrlimit(resource.RLIMIT_CPU, (61, 61))" in source


def test_browser_filesystem_limit_leaves_bounded_chromium_working_space() -> None:
    driver = _load_browser_driver()

    assert driver._browser_file_limit(1024 * 1024) == 64 * 1024 * 1024
    with pytest.raises(ValueError, match="response byte bound exceeds browser filesystem bound"):
        driver._browser_file_limit(64 * 1024 * 1024 + 1)


def test_browser_network_observation_has_count_and_aggregate_bounds() -> None:
    driver = _load_browser_driver()
    network = []
    observed = 0

    for index in range(driver.MAX_NETWORK_EVENTS):
        observed, recorded = driver._record_network_event(network, ("GET", f"/{index}", 200, 1), observed, 1024 * 1024)
        assert recorded

    observed, recorded = driver._record_network_event(network, ("GET", "/overflow", 200, 1), observed, 1024 * 1024)

    assert recorded is False
    assert len(network) == driver.MAX_NETWORK_EVENTS

    aggregate = []
    observed = 0
    observed, recorded = driver._record_network_event(aggregate, ("GET", "/first", 200, 1), observed, 64)
    assert recorded
    _observed, recorded = driver._record_network_event(aggregate, ("GET", "/second", 200, 1), observed, 64)
    assert recorded is False


def test_browser_routes_all_context_pages_through_the_origin_allowlist() -> None:
    source = (WEB_FIXTURE / "browser_driver.py").read_text()

    assert 'context.route("**/*", route)' in source
    assert 'context.on("response", response)' in source


def test_web_discovery_consumes_the_curated_wordlist_and_names_the_broker(tmp_path: Path, monkeypatch, capsys) -> None:
    spec = importlib.util.spec_from_file_location("web_tool", WEB_FIXTURE / "tool.py")
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    wordlist = tmp_path / "routes.txt"
    wordlist.write_text("missing\nhidden-route\n")
    monkeypatch.setattr(tool, "CURATED_WORDLIST", wordlist)
    requests = []

    def target(command, documents):
        requests.extend(documents)
        return [{"status": 404}, {"status": 200}]

    monkeypatch.setattr(tool, "_target", target)
    request = tmp_path / "discovery.json"
    request.write_text(json.dumps({"prefix": "/"}))

    tool.discovery(request)

    assert [document["path"] for document in requests] == ["/missing", "/hidden-route"]
    output = capsys.readouterr().out
    assert "engine=target-broker-http-session+curated-wordlist" in output
    assert "engine=ffuf" not in output
    assert "found=/hidden-route" in output
