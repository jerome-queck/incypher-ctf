import json
from pathlib import Path

from solver.isolation import (
    STRICT_CONTROLS,
    STRICT_PROFILE_DIGEST,
    STRICT_PROFILE_ID,
    STRICT_RUNTIME_PIN,
    IsolationReceipt,
)
from solver.tool_supply_probe import observe
from solver.tool_supply_receipt import create_receipt, validate_receipt


REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPLY = REPO_ROOT / "tool-supply"
IMAGE_CONFIG = "sha256:" + "a" * 64
IMAGE_MANIFEST = "sha256:" + "b" * 64


def admitted(_environment) -> IsolationReceipt:
    return IsolationReceipt(
        profile_id=STRICT_PROFILE_ID,
        profile_digest=STRICT_PROFILE_DIGEST,
        image_id=IMAGE_MANIFEST,
        runtime_pin=tuple(STRICT_RUNTIME_PIN.items()),
        outer_capabilities="empty",
        checks=tuple((control, "pass") for control in STRICT_CONTROLS),
        broker_peer_uid=20000,
        processes_before_kill=3,
        processes_after_kill=0,
        owned_residue=(),
    )


def test_locked_entrypoint_produces_the_semantic_receipt_after_strict_admission() -> None:
    generated = SUPPLY / "generated"
    observation = observe(
        "fixture.identity",
        inventory_path=generated / "inventory.json",
        image_root=generated / "rootfs",
        environ={
            "INCYPHER_STRICT_IMAGE": IMAGE_CONFIG,
            "INCYPHER_TOOL_IMAGE_MANIFEST": IMAGE_MANIFEST,
            "INCYPHER_TOOL_IMAGE_CONFIG": IMAGE_CONFIG,
            "INCYPHER_TOOL_PLATFORM": "linux/arm64",
        },
        preflight=admitted,
    )

    assert observation["semantic_fixture"] == {
        "entrypoint": "/usr/local/bin/incypher-fixture-tool",
        "entrypoint_sha256": "25a5402ecb519028e94fdc17ccd43aa9308a31a400a24fae51ccf62e659615e0",
        "observed_version": "1.0.0",
        "exit_code": 0,
        "stdout_sha256": "2067e3727fec495781f370c84a7d82111db1cc77c3b52ccfdcb4d2d71d02e650",
        "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "outcome": "pass",
    }
    receipt = create_receipt(
        observation,
        lock_fragment=(SUPPLY / "locks" / "resident-fixture.json").read_bytes(),
        inventory=(generated / "inventory.json").read_bytes(),
        supply_receipt=(generated / "receipt.json").read_bytes(),
        source_root=SUPPLY,
    )
    validate_receipt(receipt, expected_image_manifest_digest=IMAGE_MANIFEST)
    assert json.dumps(receipt).find(str(REPO_ROOT)) == -1
