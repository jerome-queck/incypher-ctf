from pathlib import Path
from types import SimpleNamespace

from scripts.qualify_final_interval_runtime import default_entrypoint_command, prepare_strict_cgroup_command


def test_exact_image_qualification_uses_default_supervisor_entrypoint(monkeypatch):
    seen = {}

    def command(image_id, **options):
        seen.update(image_id=image_id, options=options)
        return ["docker", "run", "--restart", "unless-stopped", image_id]

    monkeypatch.setattr("scripts.qualify_final_interval_runtime.strict_runtime.container_command", command)
    binding = SimpleNamespace(
        image_id="sha256:" + "a" * 64,
        image_manifest_digest="sha256:" + "b" * 64,
        image_config_digest="sha256:" + "c" * 64,
        platform="linux/arm64",
    )
    result = default_entrypoint_command(binding, Path("qualification.env"), Path("state"))

    assert "--entrypoint" not in result
    assert result[-1] == binding.image_id
    assert result[-3:-1] == ["--name", "incypher-final-interval-qualification"]
    assert seen["options"]["preflight_only"] is False
    assert seen["options"]["binding"] is binding


def test_exact_image_qualification_prepares_strict_cgroup():
    binding = SimpleNamespace(image_id="sha256:" + "a" * 64)

    assert prepare_strict_cgroup_command(binding) == [
        "docker",
        "run",
        "--rm",
        "--cgroup-parent",
        "incypher-v2-strict",
        "--entrypoint",
        "/bin/true",
        binding.image_id,
    ]
