"""Assemble locked Tool-profile fragments into one deterministic image rootfs."""

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import tempfile
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import cast


FLOATING_VERSIONS = frozenset({"head", "latest", "main", "master", "rolling", "stable"})
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]*")
PACKAGE_PATTERN = re.compile(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
RANGE_PREFIX_PATTERN = re.compile(r"(?:[~^]|[<>]=?|==?)")
RANGE_SEGMENT_PATTERN = re.compile(r"(?:^|[.+:~_-])[xX](?:$|[.+:~_-])")


@dataclass(frozen=True)
class PackageLock:
    name: str
    version: str


@dataclass(frozen=True)
class FileLock:
    source: str
    destination: str
    sha256: str
    mode: str


@dataclass(frozen=True)
class ComponentLock:
    component_id: str
    version: str
    license_expression: str
    license_classification: str
    platforms: tuple[str, ...]
    packages: tuple[PackageLock, ...]
    files: tuple[FileLock, ...]
    profile_id: str

    def inventory_record(self) -> dict[str, object]:
        record = asdict(self)
        record["platforms"] = list(self.platforms)
        record["packages"] = [asdict(package) for package in self.packages]
        record["files"] = [asdict(declared_file) for declared_file in self.files]
        record["profiles"] = [record.pop("profile_id")]
        return record


@dataclass(frozen=True)
class FragmentLock:
    path: str
    profile_id: str
    sha256: str
    components: tuple[ComponentLock, ...]

    def digest_record(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class AssemblyPlan:
    fragments: tuple[FragmentLock, ...]
    components: tuple[ComponentLock, ...]
    lock_digest: str

    def inventory_bytes(self) -> bytes:
        return canonical_json(
            {"schema_version": 1, "components": [component.inventory_record() for component in self.components]}
        )

    def package_input(self) -> bytes:
        packages = sorted(
            {f"{package.name}={package.version}" for component in self.components for package in component.packages}
        )
        return ("\n".join(packages) + ("\n" if packages else "")).encode()


@dataclass(frozen=True)
class BuiltAssembly:
    inventory: bytes
    package_input: bytes
    rootfs_digest: str


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def object_with_keys(value: object, expected: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    if set(value) != expected:
        raise ValueError(f"{name} has unknown or missing fields")
    return cast(Mapping[str, object], value)


def exact_version(value: object, kind: str, identity: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.casefold() in FLOATING_VERSIONS
        or RANGE_PREFIX_PATTERN.match(value)
        or RANGE_SEGMENT_PATTERN.search(value)
        or "*" in value
        or "||" in value
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"floating {kind} version: {identity}={value}")
    return value


def parse_package(value: object, component_id: str) -> PackageLock:
    item = object_with_keys(value, {"name", "version"}, f"package in {component_id}")
    name = item["name"]
    if not isinstance(name, str) or not PACKAGE_PATTERN.fullmatch(name):
        raise ValueError(f"invalid package name: {name}")
    return PackageLock(name, exact_version(item["version"], "package", f"{component_id}/{name}"))


def parse_file(value: object, profile_id: str) -> FileLock:
    item = object_with_keys(value, {"source", "destination", "sha256", "mode"}, f"file in {profile_id}")
    source = item["source"]
    if not isinstance(source, str):
        raise ValueError(f"source must be a string: {source}")
    source_path = PurePosixPath(source)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise ValueError(f"source escapes Tool-supply tree: {source}")
    if not source_path.is_relative_to(PurePosixPath("fixtures", profile_id)):
        raise ValueError(f"fixture is not owned by profile {profile_id}: {source}")
    destination = item["destination"]
    destination_path = PurePosixPath(destination) if isinstance(destination, str) else PurePosixPath()
    if not isinstance(destination, str) or not destination_path.is_absolute() or ".." in destination_path.parts:
        raise ValueError(f"destination must be an absolute contained path: {destination}")
    sha256 = item["sha256"]
    if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
        raise ValueError(f"invalid source digest: {source}")
    mode = item["mode"]
    if not isinstance(mode, str) or not re.fullmatch(r"0[0-7]{3}", mode):
        raise ValueError(f"invalid file mode: {source}={mode}")
    return FileLock(source, destination, sha256, mode)


def parse_component(value: object, profile_id: str) -> ComponentLock:
    item = object_with_keys(
        value,
        {
            "component_id",
            "version",
            "license_expression",
            "license_classification",
            "platforms",
            "packages",
            "files",
        },
        f"component in {profile_id}",
    )
    component_id = item["component_id"]
    if not isinstance(component_id, str) or not ID_PATTERN.fullmatch(component_id):
        raise ValueError(f"invalid component_id: {component_id}")
    version = exact_version(item["version"], "component", component_id)
    license_expression = item["license_expression"]
    if not isinstance(license_expression, str) or not license_expression:
        raise ValueError(f"missing licence expression: {component_id}")
    classification = item["license_classification"]
    if classification != "free-redistributable":
        raise ValueError(f"unsupported licence classification: {component_id}={classification}")
    platforms = item["platforms"]
    if (
        not isinstance(platforms, list)
        or not platforms
        or any(platform not in {"amd64", "arm64"} for platform in platforms)
        or len(platforms) != len(set(platforms))
    ):
        raise ValueError(f"invalid platforms: {component_id}")
    packages = item["packages"]
    files = item["files"]
    if not isinstance(packages, list) or not isinstance(files, list):
        raise ValueError(f"packages and files must be lists: {component_id}")
    return ComponentLock(
        component_id=component_id,
        version=version,
        license_expression=license_expression,
        license_classification=cast(str, classification),
        platforms=tuple(sorted(cast(list[str], platforms))),
        packages=tuple(
            sorted((parse_package(package, component_id) for package in packages), key=lambda row: row.name)
        ),
        files=tuple(
            sorted((parse_file(declared_file, profile_id) for declared_file in files), key=lambda row: row.destination)
        ),
        profile_id=profile_id,
    )


def read_fragment(path: Path, source: Path) -> FragmentLock:
    content = path.read_bytes()
    relative = path.relative_to(source).as_posix()
    document = object_with_keys(json.loads(content), {"schema_version", "profile_id", "components"}, relative)
    if document["schema_version"] != 1:
        raise ValueError(f"unsupported lock schema_version: {relative}={document['schema_version']}")
    profile_id = document["profile_id"]
    if not isinstance(profile_id, str) or not ID_PATTERN.fullmatch(profile_id):
        raise ValueError(f"invalid profile_id: {profile_id}")
    components = document["components"]
    if not isinstance(components, list) or not components:
        raise ValueError(f"profile has no components: {profile_id}")
    return FragmentLock(
        relative,
        profile_id,
        digest_bytes(content),
        tuple(parse_component(component, profile_id) for component in components),
    )


def plan_assembly(source: Path) -> AssemblyPlan:
    lock_paths = sorted((source / "locks").glob("*.json"))
    if not lock_paths:
        raise ValueError("no Tool-supply locks found")
    fragments = tuple(read_fragment(path, source) for path in lock_paths)
    profiles: set[str] = set()
    component_ids: set[str] = set()
    destinations: set[str] = set()
    package_versions: dict[str, set[str]] = {}
    components: list[ComponentLock] = []
    for fragment in fragments:
        if fragment.profile_id in profiles:
            raise ValueError(f"duplicate profile_id: {fragment.profile_id}")
        profiles.add(fragment.profile_id)
        for component in fragment.components:
            if component.component_id in component_ids:
                raise ValueError(f"duplicate component_id: {component.component_id}")
            component_ids.add(component.component_id)
            for package in component.packages:
                versions = package_versions.setdefault(package.name, set())
                versions.add(package.version)
                if len(versions) > 1:
                    raise ValueError(f"conflicting package versions: {package.name}={','.join(sorted(versions))}")
            for declared_file in component.files:
                if declared_file.destination in destinations:
                    raise ValueError(f"duplicate image destination: {declared_file.destination}")
                destinations.add(declared_file.destination)
            components.append(component)
    digest_records = [fragment.digest_record() for fragment in fragments]
    return AssemblyPlan(
        fragments,
        tuple(sorted(components, key=lambda row: row.component_id)),
        digest_bytes(canonical_json(digest_records)),
    )


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        mode = f"{path.stat().st_mode & 0o777:04o}".encode()
        digest.update(relative + b"\0" + mode + b"\0" + path.read_bytes())
    return digest.hexdigest()


def build_assembly(plan: AssemblyPlan, source: Path, output: Path) -> BuiltAssembly:
    rootfs = output / "rootfs"
    rootfs.mkdir(parents=True)
    source_root = source.resolve()
    for component in plan.components:
        for declared_file in component.files:
            source_file = (source / declared_file.source).resolve()
            if not source_file.is_relative_to(source_root):
                raise ValueError(f"source escapes Tool-supply tree: {declared_file.source}")
            if digest_bytes(source_file.read_bytes()) != declared_file.sha256:
                raise ValueError(f"source digest mismatch: {declared_file.source}")
            destination = PurePosixPath(declared_file.destination)
            assembled = rootfs.joinpath(*destination.parts[1:])
            assembled.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, assembled)
            os.chmod(assembled, int(declared_file.mode, 8))
    inventory = plan.inventory_bytes()
    package_input = plan.package_input()
    (output / "inventory.json").write_bytes(inventory)
    (output / "apt-packages.txt").write_bytes(package_input)
    return BuiltAssembly(inventory, package_input, tree_digest(rootfs))


def validate_output_path(source: Path, output: Path) -> None:
    output_root = output.resolve()
    inputs = (source.resolve(), (source / "locks").resolve(), (source / "fixtures").resolve())
    if any(input_path.is_relative_to(output_root) for input_path in inputs):
        raise ValueError("output overlaps Tool-supply inputs")


def exchange_directories(left: Path, right: Path) -> None:
    """Atomically swap two published directory names on supported build hosts."""

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        exchange = libc.renamex_np
        exchange.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = exchange(os.fsencode(left), os.fsencode(right), 2)  # RENAME_SWAP
    elif sys.platform.startswith("linux"):
        exchange = libc.renameat2
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = exchange(-100, os.fsencode(left), -100, os.fsencode(right), 2)  # RENAME_EXCHANGE
    else:
        raise OSError(f"atomic directory exchange is unsupported on {sys.platform}")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def publish(source: Path, output: Path) -> None:
    validate_output_path(source, output)
    plan = plan_assembly(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tool-supply-", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        first_path = temporary_root / "first"
        second_path = temporary_root / "second"
        first_path.mkdir()
        second_path.mkdir()
        first = build_assembly(plan, source, first_path)
        second = build_assembly(plan, source, second_path)
        if first != second:
            raise ValueError("two clean assemblies produced different inventories")
        inventory_digest = digest_bytes(first.inventory)
        receipt = {
            "schema_version": 1,
            "receipt_type": "tool-supply-fragments",
            "lock_digest": plan.lock_digest,
            "fragment_digests": [fragment.digest_record() for fragment in plan.fragments],
            "assembly_inventory_digest": inventory_digest,
            "rootfs_digest": first.rootfs_digest,
            "reproducibility": {
                "assemblies": 2,
                "comparison": "identical",
                "first_inventory_digest": inventory_digest,
                "second_inventory_digest": inventory_digest,
            },
        }
        (first_path / "receipt.json").write_bytes(canonical_json(receipt))
        if output.exists():
            exchange_directories(first_path, output)
        else:
            first_path.rename(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("tool-supply"))
    parser.add_argument("--output", type=Path, default=Path("tool-supply/generated"))
    arguments = parser.parse_args()
    try:
        publish(arguments.source, arguments.output)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
