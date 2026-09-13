import json
from pathlib import Path

import pytest

from scripts.verify_sage_closure import verify


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    prefix = tmp_path / "sage"
    metadata = prefix / "conda-meta"
    metadata.mkdir(parents=True)
    installed = {
        "name": "sagelib",
        "version": "10.9",
        "build": "held_0",
        "license": "GPL-2.0-or-later",
        "sha256": "1" * 64,
        "url": "https://conda.example/sagelib.conda",
        "size": 123,
    }
    (metadata / "sagelib.json").write_text(json.dumps(installed))
    locked = {
        "packages": [
            {
                **installed,
                "build_string": installed["build"],
            }
        ]
    }
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps(locked))
    explicit = tmp_path / "lock.explicit"
    explicit.write_text(f"@EXPLICIT\n{installed['url']}#{installed['sha256']}\n")
    return prefix, lock, explicit


def test_verifier_binds_installed_source_licence_and_bytes(tmp_path: Path) -> None:
    prefix, lock, explicit = fixture(tmp_path)

    verify(prefix, lock, explicit)


@pytest.mark.parametrize(
    "field,replacement", [("license", "MIT"), ("sha256", "0" * 64), ("url", "https://evil.invalid/x"), ("size", 1)]
)
def test_verifier_rejects_tampered_locked_metadata(tmp_path: Path, field: str, replacement: object) -> None:
    prefix, lock, explicit = fixture(tmp_path)
    document = json.loads(lock.read_text())
    document["packages"][0][field] = replacement
    lock.write_text(json.dumps(document))

    with pytest.raises(ValueError):
        verify(prefix, lock, explicit)
