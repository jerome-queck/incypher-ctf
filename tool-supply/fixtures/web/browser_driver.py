#!/usr/bin/python3
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

READ_BLOCK_BYTES = 64 * 1024
MAX_NETWORK_EVENTS = 128
MAX_NETWORK_BYTES = 256 * 1024
MAX_BROWSER_PROCESSES = 256
MAX_BROWSER_CPU_SECONDS = 60
MAX_BROWSER_FILESYSTEM_BYTES = 64 * 1024 * 1024


def origin(value):
    default = 443 if value["scheme"] == "https" else 80
    suffix = "" if value["port"] == default else f":{value['port']}"
    return f"{value['scheme']}://{value['host']}{suffix}"


def _bounded_file_digest(path: Path, byte_limit: int) -> tuple[str, int]:
    """Hash a download without ever retaining its contents as one buffer."""

    if byte_limit < 0:
        raise ValueError("browser download byte bound is invalid")
    if path.stat().st_size > byte_limit:
        raise ValueError("browser download exceeds its byte bound")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(min(READ_BLOCK_BYTES, byte_limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > byte_limit:
                raise ValueError("browser download exceeds its byte bound")
            digest.update(chunk)
    return digest.hexdigest(), size


def _bounded_chromium_executable(byte_limit: int, *, cpu_seconds: int = MAX_BROWSER_CPU_SECONDS) -> str:
    """Launch Chromium with inherited file, process, and CPU limits."""

    if byte_limit <= 0:
        raise ValueError("browser download byte bound is invalid")
    if isinstance(cpu_seconds, bool) or not isinstance(cpu_seconds, int) or cpu_seconds <= 0:
        raise ValueError("browser CPU bound is invalid")
    directory = Path(os.environ.get("TMPDIR", "/tmp"))
    wrapper = directory / "incypher-bounded-chromium.py"
    wrapper.write_text(
        "#!/usr/bin/python3\n"
        "import os\n"
        "import resource\n"
        "import sys\n"
        f"resource.setrlimit(resource.RLIMIT_FSIZE, ({byte_limit}, {byte_limit}))\n"
        f"resource.setrlimit(resource.RLIMIT_NPROC, ({MAX_BROWSER_PROCESSES}, {MAX_BROWSER_PROCESSES}))\n"
        f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds}))\n"
        "os.execv('/usr/bin/chromium', ['/usr/bin/chromium', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def _browser_file_limit(response_byte_limit: int) -> int:
    """Reserve bounded Chromium profile space while retaining the tighter response limit."""

    if response_byte_limit <= 0 or response_byte_limit > MAX_BROWSER_FILESYSTEM_BYTES:
        raise ValueError("response byte bound exceeds browser filesystem bound")
    return MAX_BROWSER_FILESYSTEM_BYTES


def _record_network_event(
    network: list[tuple[str, str, int, int]],
    item: tuple[str, str, int, int],
    observed_bytes: int,
    byte_limit: int,
) -> tuple[int, bool]:
    item_bytes = len(item[0].encode()) + len(item[1].encode()) + 32
    if len(network) >= MAX_NETWORK_EVENTS or observed_bytes + item_bytes > min(MAX_NETWORK_BYTES, byte_limit):
        return observed_bytes, False
    network.append(item)
    return observed_bytes + item_bytes, True


def main():
    request = json.loads(sys.stdin.buffer.readline())
    with tempfile.TemporaryDirectory(prefix="target-browser-driver-") as private_home:
        os.environ["HOME"] = private_home
        os.environ["TMPDIR"] = private_home
        result = browse(request)
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def browse(request):
    declared = request["origin"]
    admitted = request["admitted_subresources"]
    origins = [declared, *admitted]
    allowed = {(item["scheme"], item["host"], item["port"]) for item in origins}
    resolver = ",".join(
        [*(f"MAP {item['host']} {item['address']}" for item in origins), "MAP * ~NOTFOUND", "EXCLUDE localhost"]
    )
    timeout = request["limits"]["timeout_ms"]
    cpu_seconds = max(1, min(MAX_BROWSER_CPU_SECONDS, (timeout + 999) // 1000))
    byte_limit = request["limits"]["response_bytes"]
    network = []
    network_bytes = 0
    network_overflow = False
    downloads = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=_bounded_chromium_executable(_browser_file_limit(byte_limit), cpu_seconds=cpu_seconds),
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-domain-reliability",
                "--disable-features=MediaRouter,OptimizationHints,AutofillServerCommunication",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-default-browser-check",
                "--no-first-run",
                f"--host-resolver-rules={resolver}",
            ],
        )
        context = browser.new_context(accept_downloads=True, service_workers="block")
        page = context.new_page()

        def route(handler):
            nonlocal network_bytes, network_overflow
            parsed = urlsplit(handler.request.url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if (parsed.scheme, parsed.hostname, port) in allowed:
                handler.continue_()
            else:
                network_bytes, recorded = _record_network_event(
                    network,
                    (handler.request.method, handler.request.url, 0, 0),
                    network_bytes,
                    byte_limit,
                )
                if not recorded:
                    network_overflow = True
                handler.abort("blockedbyclient")

        def response(value):
            nonlocal network_bytes, network_overflow
            content_length = value.headers.get("content-length", "0")
            size = int(content_length) if content_length.isdigit() else 0
            item = (value.request.method, value.url, value.status, min(size, byte_limit))
            network_bytes, recorded = _record_network_event(network, item, network_bytes, byte_limit)
            if not recorded:
                network_overflow = True
                return

        context.route("**/*", route)
        context.on("response", response)
        page.goto(origin(declared) + request["path"], wait_until="domcontentloaded", timeout=timeout)
        if network_overflow:
            raise ValueError("browser network observations exceed their bound")
        if request["wait_selector"]:
            page.wait_for_selector(request["wait_selector"], timeout=timeout)
            if network_overflow:
                raise ValueError("browser network observations exceed their bound")
        if request["download_selector"]:
            with page.expect_download(timeout=timeout) as awaited:
                page.click(request["download_selector"])
            download = awaited.value
            digest, size = _bounded_file_digest(Path(download.path()), byte_limit)
            downloads.append((download.suggested_filename, digest, size))
            if network_overflow:
                raise ValueError("browser network observations exceed their bound")
        dom = page.content()
        if len(dom.encode()) > byte_limit:
            raise ValueError("browser DOM exceeds its byte bound")
        local_storage = page.evaluate("Object.entries(localStorage)")
        session_storage = page.evaluate("Object.entries(sessionStorage)")
        context.close()
        browser.close()
    return {
        "dom": dom,
        "network": network,
        "local_storage": local_storage,
        "session_storage": session_storage,
        "downloads": downloads,
    }


if __name__ == "__main__":
    raise SystemExit(main())
