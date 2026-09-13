"""Typed boundary objects for Crypto profile qualification evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

PROFILE_SIZE_BUDGET = 4 * 1024**3


@dataclass(frozen=True)
class CryptoComponent:
    component_id: str
    version: str
    capability_ids: list[str]
    packages: list[dict[str, object]]
    source: dict[str, object]
    license_expression: str
    license_classification: str
    license_authority: dict[str, object]

    @classmethod
    def from_inventory(cls, inventory: Mapping[str, Any], profile_id: str) -> CryptoComponent:
        matches = [item for item in inventory["components"] if profile_id in item["profiles"]]
        if len(matches) != 1:
            raise ValueError("Crypto profile catalogue must contain one component")
        item = matches[0]
        return cls(
            component_id=item["component_id"],
            version=item["version"],
            capability_ids=item["capability_ids"],
            packages=item["packages"],
            source=item["source"],
            license_expression=item["license_expression"],
            license_classification=item["license_classification"],
            license_authority=item["license"],
        )


@dataclass(frozen=True)
class QualifiedCryptoSupply:
    component_id: str
    profile_id: str
    image_digest: str
    size_bytes: int

    @classmethod
    def from_receipt(cls, receipt: Mapping[str, Any]) -> QualifiedCryptoSupply:
        image = receipt.get("image")
        solve = receipt.get("handle_solve")
        isolation = receipt.get("isolation")
        sbom = receipt.get("sbom")
        if not isinstance(image, Mapping) or not isinstance(image.get("manifest_digest"), str):
            raise ValueError("Crypto proof has no image binding")
        if not isinstance(solve, Mapping) or solve.get("receipt_type") != "resident-handle-solve":
            raise ValueError("Crypto Solve proof did not pass")
        if not isinstance(isolation, Mapping) or not isolation.get("checks"):
            raise ValueError("Crypto Isolation proof did not pass")
        files = sbom.get("files") if isinstance(sbom, Mapping) else None
        if not isinstance(files, list) or not files:
            raise ValueError("Crypto Supply proof has no size evidence")
        return cls(
            component_id=str(receipt.get("component_id")),
            profile_id=str(receipt.get("profile_id")),
            image_digest=image["manifest_digest"],
            size_bytes=sum(int(item["size"]) for item in files),
        )


@dataclass(frozen=True)
class CryptoProfileSize:
    baseline_image_digest: str
    baseline_size_bytes: int
    baseline_source_commit: str
    baseline_source_tree_digest: str
    baseline_dockerfile_sha256: str
    baseline_platform: str
    candidate_image_digest: str
    candidate_size_bytes: int
    delta_bytes: int
    budget_bytes: int
    method: str

    @classmethod
    def from_measurement(cls, measurement: Mapping[str, Any], candidate_digest: str) -> CryptoProfileSize:
        required = {
            "baseline_image_digest",
            "baseline_size_bytes",
            "baseline_source_commit",
            "baseline_source_tree_digest",
            "baseline_dockerfile_sha256",
            "baseline_platform",
            "candidate_image_digest",
            "candidate_size_bytes",
            "delta_bytes",
            "budget_bytes",
            "method",
        }
        if set(measurement) != required:
            raise ValueError("Crypto profile size evidence has unknown or missing fields")
        baseline = int(measurement["baseline_size_bytes"])
        candidate = int(measurement["candidate_size_bytes"])
        delta = int(measurement["delta_bytes"])
        budget = int(measurement["budget_bytes"])
        if measurement["candidate_image_digest"] != candidate_digest:
            raise ValueError("Crypto profile size evidence names another candidate")
        if baseline < 0 or candidate < baseline or delta != candidate - baseline:
            raise ValueError("Crypto profile size evidence has invalid arithmetic")
        if budget != PROFILE_SIZE_BUDGET or delta > budget:
            raise ValueError("Crypto profile exceeds ADR-0057's 4 GiB delta budget")
        if not re.fullmatch(r"[0-9a-f]{40,64}", str(measurement["baseline_source_commit"])):
            raise ValueError("Crypto profile baseline has no exact source commit")
        if not re.fullmatch(r"[0-9a-f]{40,64}", str(measurement["baseline_source_tree_digest"])):
            raise ValueError("Crypto profile baseline has no exact source tree")
        if not re.fullmatch(r"[0-9a-f]{64}", str(measurement["baseline_dockerfile_sha256"])):
            raise ValueError("Crypto profile baseline has no Dockerfile binding")
        if measurement["baseline_platform"] not in {"linux/amd64", "linux/arm64"}:
            raise ValueError("Crypto profile baseline has an unsupported platform")
        if measurement["method"] != "docker-build-git-merge-base-history-unpacked-layer-sum-v1":
            raise ValueError("Crypto profile size evidence uses an unknown measurement")
        return cls(
            baseline_image_digest=str(measurement["baseline_image_digest"]),
            baseline_size_bytes=baseline,
            baseline_source_commit=str(measurement["baseline_source_commit"]),
            baseline_source_tree_digest=str(measurement["baseline_source_tree_digest"]),
            baseline_dockerfile_sha256=str(measurement["baseline_dockerfile_sha256"]),
            baseline_platform=str(measurement["baseline_platform"]),
            candidate_image_digest=str(measurement["candidate_image_digest"]),
            candidate_size_bytes=candidate,
            delta_bytes=delta,
            budget_bytes=budget,
            method=str(measurement["method"]),
        )


__all__ = ["CryptoComponent", "CryptoProfileSize", "PROFILE_SIZE_BUDGET", "QualifiedCryptoSupply"]
