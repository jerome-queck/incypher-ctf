"""Refresh byte-addressed apt provenance for an existing package closure."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def package_fields(name: str, version: str, *, allow_platform_variant: bool = False) -> dict[str, str]:
    result = subprocess.run(
        ["apt-cache", "show", name if allow_platform_variant else f"{name}={version}"],
        check=True,
        capture_output=True,
        text=True,
    )
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(": ")
        if separator:
            fields.setdefault(key, value)
    required = {"Filename", "SHA256", "Size", "Installed-Size", "Version"}
    if not required.issubset(fields):
        raise ValueError(f"apt metadata incomplete for {name}={version}")
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("closure", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"))
    arguments = parser.parse_args()
    document = json.loads(arguments.closure.read_text())
    if arguments.platform is not None:
        document["platform"] = arguments.platform
    for package in document["packages"]:
        fields = package_fields(
            package["name"], package["version"], allow_platform_variant=arguments.platform is not None
        )
        package.update(
            {
                "archive_sha256": fields["SHA256"],
                "archive_size_bytes": int(fields["Size"]),
                "installed_size_kib": int(fields["Installed-Size"]),
                "source": "https://http.kali.org/kali/" + fields["Filename"],
                "version": fields["Version"],
            }
        )
    (arguments.output or arguments.closure).write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
