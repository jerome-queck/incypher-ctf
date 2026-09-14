from types import SimpleNamespace

from scripts import qualify_crypto_profile as crypto

from scripts.qualify_crypto_profile import measurement


def test_measurement_builds_merge_base_and_binds_provenance(monkeypatch) -> None:
    monkeypatch.setattr("scripts.qualify_crypto_profile.image_identity", lambda image: "sha256:" + image * 64)
    monkeypatch.setattr("scripts.qualify_crypto_profile.unpacked_size", lambda image: {"a": 10, "b": 25}[image])
    monkeypatch.setattr("scripts.qualify_crypto_profile.image_platform", lambda _image: "linux/arm64")
    monkeypatch.setattr("scripts.qualify_crypto_profile.qualified_baseline_commit", lambda requested: "1" * 40)
    monkeypatch.setattr("scripts.qualify_crypto_profile.baseline_image", lambda commit, platform: "a")
    monkeypatch.setattr(
        "scripts.qualify_crypto_profile.git_object",
        lambda commit, suffix: "2" * 40 if suffix == "^{tree}" else "Dockerfile",
    )
    monkeypatch.setattr("scripts.qualify_crypto_profile.git_blob", lambda commit, path: b"Dockerfile")

    result = measurement("b")

    assert result["baseline_image_digest"] == "sha256:" + "a" * 64
    assert result["candidate_image_digest"] == "sha256:" + "b" * 64
    assert result["delta_bytes"] == 15
    assert result["baseline_source_commit"] == "1" * 40
    assert result["baseline_source_tree_digest"] == "2" * 40
    assert result["baseline_platform"] == "linux/arm64"


def test_baseline_image_retains_tag_and_detached_checkout_until_merge(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setattr(crypto, "new_retained_directory", lambda *_: tmp_path / "baseline-1")
    monkeypatch.setattr(crypto, "command", lambda *arguments: calls.append(arguments) or "")
    monkeypatch.setattr(
        crypto.subprocess,
        "run",
        lambda arguments, **_options: calls.append(tuple(arguments)) or SimpleNamespace(returncode=0),
    )

    tag = crypto.baseline_image("1" * 40, "linux/arm64")
    assert tag.startswith("incypher-crypto-baseline-baseline-1")

    assert any(command[:3] == ("git", "worktree", "add") for command in calls)
    assert any(command[:3] == ("docker", "build", "--platform") for command in calls)
    assert not any(command[:3] == ("docker", "image", "rm") for command in calls)
    assert not any(command[:3] == ("git", "worktree", "remove") for command in calls)
