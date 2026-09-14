"""Bounded transform, emulation, protocol, and jail adapters."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path("/opt/solver/tool-supply/misc-protocols")
RUNTIME_MANIFEST = ROOT / "runtime.json"
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 4096
TARGET_CLIENT = "/target-client.py"


def load_request(path: str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("request exceeds the adapter input bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    return value


def run_process(
    arguments: list[str],
    *,
    timeout: float = 5.0,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    result = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        timeout=timeout,
        env=env,
        input=input_bytes,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"command failed: {arguments[0]}")
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise ValueError("adapter command output exceeds the output bound")
    return result.stdout


def _runtime_probe() -> None:
    manifest_path = ROOT / RUNTIME_MANIFEST.name
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("shared interpreter manifest is unavailable") from error
    if not isinstance(manifest, dict) or manifest.get("fixture_id") != "misc-protocols.shared-runtime-v1":
        raise RuntimeError("shared interpreter manifest identity is invalid")
    closure = manifest.get("closure")
    interpreters = manifest.get("interpreters")
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise RuntimeError("shared interpreter architecture is unsupported")
    if not isinstance(closure, dict) or set(closure) != {"amd64", "arm64"}:
        raise RuntimeError("shared interpreter manifest shape is invalid")
    selected_closure = closure.get(architecture)
    if (
        not isinstance(selected_closure, list)
        or not all(isinstance(item, str) for item in selected_closure)
        or len(set(selected_closure)) != len(selected_closure)
        or not isinstance(interpreters, list)
        or len(interpreters) != 2
    ):
        raise RuntimeError("shared interpreter manifest shape is invalid")
    expected_closure = {
        "amd64": {"dash=0.5.12-12", "python3=3.14.6-1"},
        "arm64": {"dash=0.5.12-12+b1", "python3=3.14.6-1"},
    }
    if set(selected_closure) != expected_closure[architecture]:
        raise RuntimeError("shared interpreter closure is not exact")
    seen_packages: set[str] = set()
    seen_paths: set[str] = set()
    for item in interpreters:
        if not isinstance(item, dict) or set(item) != {"package", "versions", "path", "argv", "stdout_sha256"}:
            raise RuntimeError("shared interpreter entry is invalid")
        package = item["package"]
        versions = item["versions"]
        path = item["path"]
        argv = item["argv"]
        expected_digest = item["stdout_sha256"]
        if (
            not isinstance(package, str)
            or package in seen_packages
            or not isinstance(versions, dict)
            or set(versions) != {"amd64", "arm64"}
            or any(not isinstance(value, str) for value in versions.values())
            or not isinstance(path, str)
            or path in seen_paths
            or not isinstance(argv, list)
            or not argv
            or not all(isinstance(argument, str) for argument in argv)
            or not isinstance(expected_digest, str)
        ):
            raise RuntimeError("shared interpreter entry values are invalid")
        seen_packages.add(package)
        seen_paths.add(path)
        version = versions[architecture]
        locked_version = (
            run_process(
                ["/usr/bin/dpkg-query", "-W", "-f=${Version}", package],
                env=_minimal_env(),
            )
            .decode("utf-8", "strict")
            .strip()
        )
        if locked_version != version:
            raise RuntimeError(f"unexpected shared interpreter package version: {package}")
        executable = Path(path)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"shared interpreter is not executable: {path}")
        output = run_process([path, *argv], env=_minimal_env())
        if hashlib.sha256(output).hexdigest() != expected_digest:
            raise RuntimeError(f"shared interpreter output digest mismatch: {path}")


def _minimal_env() -> dict[str, str]:
    return {
        "HOME": "/tmp/incypher-misc-home",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "TMPDIR": "/tmp",
    }


def transform(path: str) -> list[str]:
    request = load_request(path)
    if set(request) == {"operation", "value"}:
        if request["operation"] != "from_base64" or not isinstance(request["value"], str):
            raise ValueError("invalid base64 transform request")
    elif set(request) == {"operation", "source"}:
        if request["operation"] != "deobfuscate" or not isinstance(request["source"], str):
            raise ValueError("invalid deobfuscation request")
    else:
        raise ValueError("unknown transform request fields")
    result = json.loads(
        run_process(
            ["/usr/bin/node", str(ROOT / "transform.mjs")],
            timeout=10,
            env=_minimal_env(),
            input_bytes=json.dumps(request, sort_keys=True, separators=(",", ":")).encode(),
        ).decode("utf-8")
    )
    value = result.get("value")
    if not isinstance(value, str):
        raise ValueError("transform helper returned an invalid value")
    digest = hashlib.sha256(value.encode()).hexdigest()
    return [
        "schema=misc-protocols.transform.v1",
        "engine=cyberchef@11.4.0+webcrack@2.16.0",
        f"operation={result['operation']}",
        f"result-sha256={digest}",
        "result-json=" + json.dumps(value, ensure_ascii=True, separators=(",", ":")),
    ]


def emulate(path: str) -> list[str]:
    request = load_request(path)
    if set(request) != {"arch", "code_hex", "instruction_limit"}:
        raise ValueError("invalid emulation request fields")
    if request["arch"] != "x86" or not isinstance(request["code_hex"], str):
        raise ValueError("only bounded x86 blobs are admitted")
    try:
        code = bytes.fromhex(request["code_hex"])
    except ValueError as error:
        raise ValueError("emulation code is not hexadecimal") from error
    limit = request["instruction_limit"]
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 256:
        raise ValueError("instruction limit is outside the admitted bound")
    if not 1 <= len(code) <= 256:
        raise ValueError("emulation code is outside the admitted bound")
    # Keep the blob runner in-process but with no filesystem/network syscalls exposed by the
    # request. Qiling's BLOB loader maps only the supplied bytes and the instruction count is
    # the hard stop for malformed or looping samples.
    from qiling import Qiling
    from qiling.const import QL_ARCH, QL_OS, QL_VERBOSE

    qiling = Qiling(
        code=code,
        rootfs="/",
        ostype=QL_OS.BLOB,
        archtype=QL_ARCH.X86,
        profile={
            "CODE": {
                "entry_point": "0x1000000",
                "ram_size": "0x1000",
                "heap_size": "0x1000",
            }
        },
        verbose=QL_VERBOSE.DISABLED,
        console=False,
    )
    qiling.run(count=limit)
    value = int(qiling.arch.regs.eax)
    return [
        "schema=misc-protocols.emulate.v1",
        "engine=qiling@1.4.6",
        f"instructions={limit}",
        f"eax={value}",
        f"code-sha256={hashlib.sha256(code).hexdigest()}",
    ]


def _target_session(path: str) -> list[dict[str, Any]]:
    if not os.environ.get("INCYPHER_TARGET_SOCKET") or not os.environ.get("INCYPHER_TARGET_GENERATION"):
        raise RuntimeError("target broker credentials are absent")
    result = run_process(
        ["/usr/bin/python3", TARGET_CLIENT, "tcp-session", path],
        timeout=10,
        env={**os.environ, **_minimal_env()},
    )
    documents = json.loads(result.decode("utf-8"))
    if isinstance(documents, dict):
        documents = [documents]
    if not isinstance(documents, list) or not documents or len(documents) > 128:
        raise ValueError("target broker returned an invalid session document")
    if any(not isinstance(document, dict) for document in documents):
        raise ValueError("target broker returned an invalid exchange")
    return documents


def protocol(path: str, capability: str) -> list[str]:
    documents = _target_session(path)
    schema = {"protocol.relay": "misc-protocols.relay.v1"}[capability]
    facts = [f"schema={schema}", "engine=target-broker.tcp-session.v1"]
    for index, document in enumerate(documents):
        outcome = document.get("outcome")
        body = base64.b64decode(str(document.get("body", "")), validate=True)
        if len(body) > MAX_OUTPUT_BYTES:
            raise ValueError("target response exceeds the adapter output bound")
        facts.append(
            f"exchange={index} outcome={outcome} response-bytes={len(body)} "
            f"response-sha256={hashlib.sha256(body).hexdigest()}"
        )
        provenance = document.get("provenance", {})
        if isinstance(provenance, dict):
            facts.append(f"request-id={document.get('request_id', '')}")
            facts.append(f"resolved-address={provenance.get('resolved_address', '')}")
    return facts


def _jail_requests(path: str) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_dir():
        return [load_request(path)]
    entries = sorted(source.iterdir(), key=lambda item: item.name)
    if not entries or any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise ValueError("jail fixture directory must contain regular request files")
    return [load_request(str(entry)) for entry in entries]


def jail(path: str) -> list[str]:
    requests = _jail_requests(path)
    executions: list[tuple[str, str]] = []
    languages: set[str] = set()
    for request in requests:
        if set(request) != {"language", "source"}:
            raise ValueError("invalid jail request fields")
        language, source = request["language"], request["source"]
        if language not in {"javascript", "python", "dash"} or not isinstance(source, str):
            raise ValueError("unsupported jail language")
        if language in languages:
            raise ValueError("jail fixture directory repeats a language")
        languages.add(language)
        if not source or len(source.encode()) > MAX_SOURCE_BYTES or "\x00" in source:
            raise ValueError("jail source is outside the admitted bound")
        forbidden = ("import", "require", "socket", "subprocess", "open(", "eval(", "exec(", "process.")
        if any(token in source for token in forbidden):
            raise ValueError("jail source requests a forbidden capability")
        command = {
            "javascript": ["/usr/bin/node", "--no-addons", "--disallow-code-generation-from-strings"],
            "python": ["/usr/bin/python3", "-I", "-S"],
            "dash": ["/bin/dash"],
        }[language]
        with tempfile.TemporaryDirectory(prefix="incypher-jail-", dir="/tmp") as temporary:
            source_path = Path(temporary) / {"javascript": "main.js", "python": "main.py", "dash": "main.sh"}[language]
            source_path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [*command, str(source_path)],
                check=False,
                capture_output=True,
                timeout=3,
                cwd="/tmp",
                env=_minimal_env(),
            )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(detail or "jail program failed")
        if len(completed.stdout) > MAX_OUTPUT_BYTES:
            raise ValueError("jail output exceeds the output bound")
        output = completed.stdout.decode("utf-8", "strict").rstrip("\n")
        executions.append((language, output))
    result = [
        "schema=misc-protocols.jail.v1",
        "engine=bounded-jail-playbook@1.0.0",
    ]
    for language, output in executions:
        result.extend(
            (
                f"language={language}",
                "stdout-sha256=" + hashlib.sha256(output.encode()).hexdigest(),
                "stdout=" + json.dumps(output, ensure_ascii=True, separators=(",", ":")),
            )
        )
    return result


def self_check(path: str) -> int:
    version = run_process(["/usr/bin/node", "--version"], env=_minimal_env()).decode().strip()
    if version != "v24.19.0":
        raise RuntimeError(f"unexpected node version: {version}")
    _runtime_probe()
    run_process(["/usr/bin/python3", "-c", "from qiling import Qiling; assert Qiling"], env=_minimal_env())
    run_process(["/usr/bin/socat", "-V"], env=_minimal_env())
    transform(path)
    emulate(str(ROOT / "emulate.json"))
    for language in ("javascript", "python", "dash"):
        jail_path = ROOT / "jail" / f"jail-{language}.json"
        jail(str(jail_path))
    return 0


HANDLERS = {
    "misc.transform": transform,
    "misc.emulate": emulate,
    "protocol.relay": lambda path: protocol(path, "protocol.relay"),
    "jail.reason": jail,
}


def main(arguments: list[str]) -> int:
    if arguments and arguments[0] == "--self-check":
        return self_check(arguments[1] if len(arguments) > 1 else str(ROOT / "input.txt"))
    if len(arguments) != 2 or arguments[0] not in HANDLERS:
        return 64
    for line in HANDLERS[arguments[0]](arguments[1]):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
