import pytest

from scripts.lock_sage_closure import lock_document


def package(**changes):
    value = {
        "name": "sagelib",
        "version": "10.9",
        "build_string": "py314_0",
        "license": "GPL-2.0-or-later",
        "sha256": "a" * 64,
        "size": 10,
        "url": "https://conda.example/sagelib.conda",
    }
    value.update(changes)
    return value


def test_lock_keeps_exact_source_licence_and_platform() -> None:
    lock = lock_document([package()], "linux-aarch64")

    assert lock["requested"] == "sagelib=10.9"
    assert lock["platform"] == "linux/arm64"
    assert lock["packages"] == [package()]


@pytest.mark.parametrize("field", ["license", "sha256", "url"])
def test_lock_refuses_incomplete_provenance(field: str) -> None:
    with pytest.raises(ValueError, match="lacks licence"):
        lock_document([package(**{field: ""})], "linux-64")
