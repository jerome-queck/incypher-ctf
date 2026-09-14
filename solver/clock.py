"""One production clock, selected and owned at Boot."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from solver.event_store_storage import canonical_bytes
from solver.profile import Rules

PROFILE_ENV = "INCYPHER_QUALIFICATION_CLOCK"
SIGNATURE_ENV = "INCYPHER_QUALIFICATION_CLOCK_SIGNATURE"
PUBLIC_KEY_ENV = "INCYPHER_EVALUATOR_PUBLIC_KEY"
IMAGE_ENV = "INCYPHER_STRICT_IMAGE"
MANIFEST_IMAGE_ENV = "INCYPHER_IMAGE_MANIFEST"
TRUSTED_KEY_DIGEST = "672b8a4f435628a41eb590ee563c9e395cbebfbc766accf20efc2e028d302c5b"  # gitleaks:allow


class Clock(Protocol):
    def now(self) -> dt.datetime: ...

    def monotonic(self) -> float: ...

    def wall_time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def wall_time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class SignedQualificationClock:
    """Accelerated clock admitted only by a trusted exact-image control."""

    def __init__(
        self,
        opened_at: dt.datetime,
        rate: int,
        *,
        monotonic=time.monotonic,
        initial_elapsed_seconds: float = 0.0,
    ) -> None:
        self._opened_at, self._rate, self._monotonic = opened_at, rate, monotonic
        self._initial_elapsed_seconds = initial_elapsed_seconds
        self._origin = monotonic()

    def now(self) -> dt.datetime:
        elapsed = (self._initial_elapsed_seconds + self._monotonic() - self._origin) * self._rate
        return self._opened_at + dt.timedelta(seconds=elapsed)

    def monotonic(self) -> float:
        return self.now().timestamp()

    def wall_time(self) -> float:
        return self.now().timestamp()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds / self._rate)


@dataclass(frozen=True)
class QualificationConfiguration:
    clock: Clock
    rules: Rules
    seed: str


def qualification_from_environment(environ: Mapping[str, str]) -> QualificationConfiguration | None:
    paths = tuple(environ.get(name, "").strip() for name in (PROFILE_ENV, SIGNATURE_ENV))
    if not any(paths):
        return None
    if not all(paths) or not environ.get(PUBLIC_KEY_ENV, "").strip():
        raise ValueError("qualification clock, signature and evaluator key must be supplied together")
    profile_path, signature_path = map(Path, paths)
    public_path = Path(environ[PUBLIC_KEY_ENV])
    public = public_path.read_bytes()
    if hashlib.sha256(public).hexdigest() != TRUSTED_KEY_DIGEST:
        raise ValueError("qualification clock evaluator key is untrusted")
    verified = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-inkey",
            str(public_path),
            "-in",
            str(profile_path),
            "-sigfile",
            str(signature_path),
        ],
        capture_output=True,
        check=False,
    )
    if verified.returncode:
        raise ValueError("qualification clock signature is invalid")
    raw = profile_path.read_bytes()
    document = json.loads(raw)
    if raw != canonical_bytes(document) + b"\n":
        raise ValueError("qualification clock is not canonical")
    if set(document) != {
        "schema_version",
        "kind",
        "image_digest",
        "anchor_unix_seconds",
        "opened_at",
        "rate",
        "rules",
        "seed",
    }:
        raise ValueError("qualification clock shape is invalid")
    if document["schema_version"] != 1 or document["kind"] != "exact-image-qualification-clock":
        raise ValueError("qualification clock contract is invalid")
    expected_image = environ.get(MANIFEST_IMAGE_ENV, "") or environ.get(IMAGE_ENV, "")
    if document["image_digest"] != expected_image:
        raise ValueError("qualification clock names another exact image")
    try:
        opened_at = dt.datetime.fromisoformat(document["opened_at"])
    except (TypeError, ValueError):
        raise ValueError("qualification clock opening is invalid") from None
    anchor = document["anchor_unix_seconds"]
    if (
        opened_at.tzinfo is None
        or type(anchor) not in {int, float}
        or not math.isfinite(float(anchor))
        or anchor <= 0
        or not isinstance(document["rate"], int)
        or not 1 <= document["rate"] <= 3600
    ):
        raise ValueError("qualification clock rate is invalid")
    rules = document["rules"]
    required = {"event", "url", "flag_wrappers", "window_seconds", "prohibitions", "requires", "web_search"}
    if not isinstance(rules, dict) or set(rules) != required:
        raise ValueError("qualification Board Rules shape is invalid")
    if rules["url"] != environ.get("CTFD_URL", ""):
        raise ValueError("qualification Board URL disagrees with Boot")
    if not str(rules["url"]).startswith("http://host.docker.internal:"):
        raise ValueError("qualification Board is not the isolated host endpoint")
    try:
        selected_rules = Rules(
            event=str(rules["event"]),
            url=str(rules["url"]),
            flag_wrappers=tuple(str(value) for value in rules["flag_wrappers"]),
            window_seconds=int(rules["window_seconds"]),
            prohibitions=tuple(str(value) for value in rules["prohibitions"]),
            requires=tuple(str(value) for value in rules["requires"]),
            web_search=bool(rules["web_search"]),
        )
    except (TypeError, ValueError):
        raise ValueError("qualification Board Rules are invalid") from None
    if selected_rules.window_seconds <= 0 or not selected_rules.flag_wrappers:
        raise ValueError("qualification Board Rules are invalid")
    if document["seed"] not in {"", "final-interval-v1"}:
        raise ValueError("qualification seed is unsupported")
    return QualificationConfiguration(
        SignedQualificationClock(
            opened_at,
            document["rate"],
            initial_elapsed_seconds=max(0.0, time.time() - float(anchor)),
        ),
        selected_rules,
        str(document["seed"]),
    )


def from_environment(environ: Mapping[str, str]) -> Clock:
    selected = qualification_from_environment(environ)
    return selected.clock if selected is not None else SystemClock()
