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
    ecosystem: str = "apt"


@dataclass(frozen=True)
class FileLock:
    source: str
    destination: str
    sha256: str
    mode: str


@dataclass(frozen=True)
class SourceLock:
    uri: str
    file: str


@dataclass(frozen=True)
class LicenseLock:
    authority: str
    file: str


@dataclass(frozen=True)
class FixtureLock:
    fixture_id: str
    argv: tuple[str, ...]
    input_file: str
    expected_stdout_sha256: str
    timeout_seconds: int
    capability_stdout_sha256: dict[str, str] | None = None
    capability_argv: dict[str, tuple[str, ...]] | None = None
    capability_input_files: dict[str, tuple[str, ...]] | None = None
    capability_expected_facts: dict[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class ComponentLock:
    component_id: str
    capability_ids: tuple[str, ...]
    version: str
    license_expression: str
    license_classification: str
    source: SourceLock
    license: LicenseLock
    entrypoint: str
    interpreter: str | None
    version_argv: tuple[str, ...]
    fixture: FixtureLock
    platforms: tuple[str, ...]
    packages: tuple[PackageLock, ...]
    files: tuple[FileLock, ...]
    profile_id: str
    capability_policies: dict[str, dict[str, object]] | None = None

    def inventory_record(self) -> dict[str, object]:
        record = asdict(self)
        record["platforms"] = list(self.platforms)
        record["capability_ids"] = list(self.capability_ids)
        record["packages"] = [
            {key: value for key, value in asdict(package).items() if key != "ecosystem" or value != "apt"}
            for package in self.packages
        ]
        record["files"] = [asdict(declared_file) for declared_file in self.files]
        record["version_argv"] = list(self.version_argv)
        if self.interpreter is None:
            record.pop("interpreter")
        record["fixture"]["argv"] = list(self.fixture.argv)
        if self.fixture.capability_stdout_sha256 is None:
            record["fixture"].pop("capability_stdout_sha256")
        for name in ("capability_argv", "capability_input_files", "capability_expected_facts"):
            value = getattr(self.fixture, name)
            if value is None:
                record["fixture"].pop(name)
            else:
                record["fixture"][name] = {key: list(items) for key, items in value.items()}
        if self.capability_policies is None:
            record.pop("capability_policies")
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
            {
                f"{package.name}={re.sub(r'\+b\d+$', '*', package.version)}"
                for component in self.components
                for package in component.packages
                if package.ecosystem == "apt"
            }
        )
        return ("\n".join(packages) + ("\n" if packages else "")).encode()


@dataclass(frozen=True)
class BuiltAssembly:
    inventory: bytes
    package_input: bytes
    rootfs_digest: str
    capabilities: bytes | None = None


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
    if not isinstance(value, Mapping) or set(value) not in ({"name", "version"}, {"name", "version", "ecosystem"}):
        raise ValueError(f"package in {component_id} has unknown or missing fields")
    item = cast(Mapping[str, object], value)
    name = item["name"]
    if not isinstance(name, str) or not PACKAGE_PATTERN.fullmatch(name):
        raise ValueError(f"invalid package name: {name}")
    ecosystem = item.get("ecosystem", "apt")
    if ecosystem not in {"apt", "conda", "pypi", "npm"}:
        raise ValueError(f"invalid package ecosystem: {component_id}/{name}")
    return PackageLock(name, exact_version(item["version"], "package", f"{component_id}/{name}"), str(ecosystem))


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
    required_fields = {
        "component_id",
        "capability_ids",
        "version",
        "license_expression",
        "license_classification",
        "source",
        "license",
        "entrypoint",
        "version_argv",
        "fixture",
        "platforms",
        "packages",
        "files",
    }
    optional_fields = {"interpreter", "capability_policies"}
    if (
        not isinstance(value, Mapping)
        or not required_fields <= set(value)
        or not set(value) - required_fields <= optional_fields
    ):
        raise ValueError(f"component in {profile_id} has unknown or missing fields")
    item = cast(Mapping[str, object], value)
    component_id = item["component_id"]
    if not isinstance(component_id, str) or not ID_PATTERN.fullmatch(component_id):
        raise ValueError(f"invalid component_id: {component_id}")
    capability_ids = item["capability_ids"]
    if (
        not isinstance(capability_ids, list)
        or not capability_ids
        or any(not isinstance(value, str) or not ID_PATTERN.fullmatch(value) for value in capability_ids)
        or len(capability_ids) != len(set(capability_ids))
    ):
        raise ValueError(f"invalid capability_ids: {component_id}")
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
    parsed_files = tuple(
        sorted((parse_file(declared_file, profile_id) for declared_file in files), key=lambda row: row.destination)
    )
    files_by_source = {declared.source: declared for declared in parsed_files}
    files_by_destination = {declared.destination: declared for declared in parsed_files}
    source_record = object_with_keys(item["source"], {"uri", "file"}, f"source in {component_id}")
    source_uri = source_record["uri"]
    source_file = source_record["file"]
    if not isinstance(source_uri, str) or not source_uri.startswith("repo:"):
        raise ValueError(f"source URI is not immutable repository evidence: {component_id}")
    if not isinstance(source_file, str) or source_file not in files_by_source:
        raise ValueError(f"source file is not in the locked component closure: {component_id}")
    license_record = object_with_keys(item["license"], {"authority", "file"}, f"license in {component_id}")
    license_authority = license_record["authority"]
    license_file = license_record["file"]
    if not isinstance(license_authority, str) or not license_authority:
        raise ValueError(f"missing licence authority: {component_id}")
    if not isinstance(license_file, str) or license_file not in files_by_source:
        raise ValueError(f"licence file is not in the locked component closure: {component_id}")
    entrypoint = item["entrypoint"]
    if not isinstance(entrypoint, str) or entrypoint not in files_by_destination:
        raise ValueError(f"entrypoint is not in the locked component closure: {component_id}")
    interpreter = item.get("interpreter")
    if interpreter is not None and interpreter != "/bin/dash":
        raise ValueError(f"unsupported component interpreter: {component_id}={interpreter}")
    if interpreter is None and not files_by_destination[entrypoint].mode.endswith(("5", "7")):
        raise ValueError(f"entrypoint is not executable: {component_id}")
    if interpreter is not None and files_by_destination[entrypoint].mode != "0444":
        raise ValueError(f"interpreted entrypoint must be read-only: {component_id}")
    version_argv = item["version_argv"]
    if not isinstance(version_argv, list) or not version_argv or any(not isinstance(arg, str) for arg in version_argv):
        raise ValueError(f"invalid version argv: {component_id}")
    fixture_fields = {"fixture_id", "argv", "input_file", "expected_stdout_sha256", "timeout_seconds"}
    capability_fixture_fields = {
        "capability_stdout_sha256",
        "capability_argv",
        "capability_input_files",
        "capability_expected_facts",
    }
    fixture_value = item["fixture"]
    if (
        not isinstance(fixture_value, Mapping)
        or not fixture_fields <= set(fixture_value)
        or not set(fixture_value) - fixture_fields <= capability_fixture_fields
    ):
        raise ValueError(f"fixture in {component_id} has unknown or missing fields")
    fixture_record = cast(Mapping[str, object], fixture_value)
    fixture_id = fixture_record["fixture_id"]
    fixture_argv = fixture_record["argv"]
    input_file = fixture_record["input_file"]
    expected_stdout = fixture_record["expected_stdout_sha256"]
    timeout_seconds = fixture_record["timeout_seconds"]
    capability_stdout = fixture_record.get("capability_stdout_sha256")
    capability_argv = fixture_record.get("capability_argv")
    capability_input_files = fixture_record.get("capability_input_files")
    capability_expected_facts = fixture_record.get("capability_expected_facts")
    if not isinstance(fixture_id, str) or not ID_PATTERN.fullmatch(fixture_id):
        raise ValueError(f"invalid fixture_id: {component_id}")
    if not isinstance(fixture_argv, list) or any(not isinstance(arg, str) for arg in fixture_argv):
        raise ValueError(f"invalid fixture argv: {component_id}")
    if not isinstance(input_file, str) or input_file not in files_by_source:
        raise ValueError(f"fixture input is not in the locked component closure: {component_id}")
    input_destination = files_by_source[input_file].destination
    if input_destination not in fixture_argv:
        raise ValueError(f"fixture argv does not consume its locked input: {component_id}")
    if not isinstance(expected_stdout, str) or not SHA256_PATTERN.fullmatch(expected_stdout):
        raise ValueError(f"invalid fixture expected output: {component_id}")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise ValueError(f"invalid fixture timeout: {component_id}")
    if capability_stdout is not None and (
        not isinstance(capability_stdout, Mapping)
        or set(capability_stdout) != set(capability_ids)
        or any(
            not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value) for value in capability_stdout.values()
        )
    ):
        raise ValueError(f"invalid capability fixture outputs: {component_id}")
    capability_maps = (capability_argv, capability_input_files, capability_expected_facts)
    if any(value is not None for value in capability_maps):
        if any(not isinstance(value, Mapping) or set(value) != set(capability_ids) for value in capability_maps):
            raise ValueError(f"incomplete capability fixtures: {component_id}")
        assert isinstance(capability_argv, Mapping)
        assert isinstance(capability_input_files, Mapping)
        assert isinstance(capability_expected_facts, Mapping)
        for capability_id in capability_ids:
            argv = capability_argv[capability_id]
            sources = capability_input_files[capability_id]
            facts = capability_expected_facts[capability_id]
            if (
                not isinstance(argv, list)
                or len(argv) != 1
                or not isinstance(argv[0], str)
                or not argv[0].startswith("/")
            ):
                raise ValueError(f"invalid capability argv: {component_id}/{capability_id}")
            if (
                not isinstance(sources, list)
                or not sources
                or any(not isinstance(source, str) or source not in files_by_source for source in sources)
            ):
                raise ValueError(f"invalid capability inputs: {component_id}/{capability_id}")
            root = PurePosixPath(argv[0])
            if any(not PurePosixPath(files_by_source[source].destination).is_relative_to(root) for source in sources):
                raise ValueError(f"capability input escapes its argument: {component_id}/{capability_id}")
            if (
                not isinstance(facts, list)
                or not facts
                or any(not isinstance(fact, str) or not fact or len(fact.encode()) > 1024 for fact in facts)
            ):
                raise ValueError(f"invalid capability facts: {component_id}/{capability_id}")
    policies_value = item.get("capability_policies")
    policies: dict[str, dict[str, object]] | None = None
    if policies_value is not None:
        if not isinstance(policies_value, Mapping) or set(policies_value) != set(capability_ids):
            raise ValueError(f"incomplete capability policies: {component_id}")
        policy_fields = {
            "argv",
            "input_kind",
            "max_input_bytes",
            "max_output_bytes",
            "cpu_seconds",
            "memory_bytes",
            "filesystem_bytes",
            "pids",
            "wall_seconds",
            "network",
            "output_schema",
        }
        policies = {}
        for capability_id in capability_ids:
            policy = object_with_keys(
                policies_value[capability_id], policy_fields, f"policy in {component_id}/{capability_id}"
            )
            command = (
                [interpreter, entrypoint, capability_id, "{input}"]
                if interpreter
                else [entrypoint, capability_id, "{input}"]
            )
            if policy["argv"] != command:
                raise ValueError(f"capability policy has an unsafe argv template: {component_id}/{capability_id}")
            if policy["input_kind"] not in {"file", "directory", "file-or-directory"}:
                raise ValueError(f"capability policy has an invalid input kind: {component_id}/{capability_id}")
            if policy["network"] not in {"deny", "target-broker", "research-broker"}:
                raise ValueError(f"capability policy has an invalid network class: {component_id}/{capability_id}")
            schema_prefix = "resident." if profile_id == "resident" else f"{profile_id}."
            if not isinstance(policy["output_schema"], str) or not policy["output_schema"].startswith(schema_prefix):
                raise ValueError(f"capability policy has an invalid output schema: {component_id}/{capability_id}")
            for limit in (
                "max_input_bytes",
                "max_output_bytes",
                "cpu_seconds",
                "memory_bytes",
                "filesystem_bytes",
                "pids",
                "wall_seconds",
            ):
                if not isinstance(policy[limit], int) or isinstance(policy[limit], bool) or policy[limit] <= 0:
                    raise ValueError(f"capability policy has an invalid limit: {component_id}/{capability_id}/{limit}")
            policies[capability_id] = dict(policy)
    return ComponentLock(
        component_id=component_id,
        capability_ids=tuple(sorted(capability_ids)),
        version=version,
        license_expression=license_expression,
        license_classification=cast(str, classification),
        source=SourceLock(cast(str, source_uri), cast(str, source_file)),
        license=LicenseLock(cast(str, license_authority), cast(str, license_file)),
        entrypoint=entrypoint,
        interpreter=cast(str | None, interpreter),
        version_argv=tuple(cast(list[str], version_argv)),
        fixture=FixtureLock(
            fixture_id=fixture_id,
            argv=tuple(cast(list[str], fixture_argv)),
            input_file=input_file,
            expected_stdout_sha256=expected_stdout,
            timeout_seconds=timeout_seconds,
            capability_stdout_sha256=dict(sorted(capability_stdout.items()))
            if isinstance(capability_stdout, Mapping)
            else None,
            capability_argv={key: tuple(value) for key, value in sorted(capability_argv.items())}
            if isinstance(capability_argv, Mapping)
            else None,
            capability_input_files={key: tuple(value) for key, value in sorted(capability_input_files.items())}
            if isinstance(capability_input_files, Mapping)
            else None,
            capability_expected_facts={key: tuple(value) for key, value in sorted(capability_expected_facts.items())}
            if isinstance(capability_expected_facts, Mapping)
            else None,
        ),
        platforms=tuple(sorted(cast(list[str], platforms))),
        packages=tuple(
            sorted((parse_package(package, component_id) for package in packages), key=lambda row: row.name)
        ),
        files=parsed_files,
        profile_id=profile_id,
        capability_policies=policies,
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


def capability_catalogue(source: Path, plan: AssemblyPlan) -> bytes | None:
    """Validate the queryable required/triggered catalogue against locked components."""

    path = source / "capabilities.json"
    if not path.exists():
        return None
    document = object_with_keys(json.loads(path.read_bytes()), {"schema_version", "capabilities"}, "capabilities")
    if document["schema_version"] != 1 or not isinstance(document["capabilities"], list):
        raise ValueError("unsupported capability catalogue")
    component_ids = {component.component_id for component in plan.components}
    seen: set[str] = set()
    required_fields = {
        "capability_id",
        "requirement",
        "component_ids",
        "profiles",
        "installed",
        "proved",
        "enabled",
        "authority",
        "reason",
        "promotion_rule",
    }
    for value in document["capabilities"]:
        if (
            not isinstance(value, Mapping)
            or not required_fields <= set(value)
            or set(value) - required_fields > {"candidates"}
        ):
            raise ValueError("capability row has unknown or missing fields")
        capability_id = value["capability_id"]
        if not isinstance(capability_id, str) or not ID_PATTERN.fullmatch(capability_id) or capability_id in seen:
            raise ValueError(f"invalid capability row: {capability_id}")
        seen.add(capability_id)
        components = value["component_ids"]
        profiles = value["profiles"]
        if (
            value["requirement"] not in {"required", "triggered"}
            or not isinstance(components, list)
            or any(not isinstance(item, str) or item not in component_ids for item in components)
            or not isinstance(profiles, list)
            or any(not isinstance(item, str) or not ID_PATTERN.fullmatch(item) for item in profiles)
            or value["authority"] not in {"tool-handle", "target-handle", "research-handle", "none"}
            or any(not isinstance(value[name], bool) for name in ("installed", "proved", "enabled"))
            or any(not isinstance(value[name], str) for name in ("reason", "promotion_rule"))
        ):
            raise ValueError(f"invalid capability row: {capability_id}")
        if value["requirement"] == "required":
            if not components or not value["installed"] or not value["proved"] or not value["enabled"]:
                raise ValueError(f"unresolved required capability: {capability_id}")
            if value["reason"] or value["promotion_rule"] or value["authority"] == "none":
                raise ValueError(f"invalid required capability state: {capability_id}")
        elif (
            components
            or value["installed"]
            or value["proved"]
            or value["enabled"]
            or not value["reason"]
            or not value["promotion_rule"]
        ):
            raise ValueError(f"invalid triggered capability state: {capability_id}")
        candidates = value.get("candidates")
        if candidates is not None and (
            not isinstance(candidates, list)
            or not candidates
            or any(not isinstance(item, str) or "@" not in item for item in candidates)
        ):
            raise ValueError(f"capability candidates are not exact: {capability_id}")
    return canonical_json(document)


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
    capabilities = capability_catalogue(source, plan)
    if capabilities is not None:
        (output / "capabilities.json").write_bytes(capabilities)
    return BuiltAssembly(inventory, package_input, tree_digest(rootfs), capabilities)


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
        if first.capabilities is not None:
            receipt["capability_catalogue_digest"] = digest_bytes(first.capabilities)
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
