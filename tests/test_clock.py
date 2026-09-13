import datetime as dt
import hashlib
import subprocess
from pathlib import Path

import pytest

from solver.clock import IMAGE_ENV, PROFILE_ENV, PUBLIC_KEY_ENV, SIGNATURE_ENV, SignedQualificationClock
from solver.clock import SystemClock, from_environment, qualification_from_environment
from solver.event_store_storage import canonical_bytes


def _signed(tmp_path: Path):
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", private], check=True)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    document = {
        "image_digest": "sha256:" + "a" * 64,
        "kind": "exact-image-qualification-clock",
        "opened_at": "2026-09-22T01:00:00+00:00",
        "rate": 60,
        "rules": {
            "event": "controlled-final-interval",
            "flag_wrappers": [r"qualification\{[^}]+\}"],
            "prohibitions": [],
            "requires": [],
            "url": "http://host.docker.internal:38095",
            "web_search": False,
            "window_seconds": 3600,
        },
        "schema_version": 1,
        "seed": "final-interval-v1",
    }
    profile, signature = tmp_path / "clock.json", tmp_path / "clock.sig"
    profile.write_bytes(canonical_bytes(document) + b"\n")
    subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", private, "-in", profile, "-out", signature],
        check=True,
    )
    environ = {
        PROFILE_ENV: str(profile),
        SIGNATURE_ENV: str(signature),
        PUBLIC_KEY_ENV: str(public),
        IMAGE_ENV: document["image_digest"],
        "CTFD_URL": document["rules"]["url"],
    }
    return document, profile, public, environ


def test_system_clock_is_the_only_unsigned_default():
    assert isinstance(from_environment({}), SystemClock)
    with pytest.raises(ValueError, match="must be supplied together"):
        from_environment({PROFILE_ENV: "clock.json"})


def test_supervisor_refuses_unsigned_clock_before_opening_run_services(tmp_path, capsys):
    from solver import supervisor

    assert (
        supervisor.main(
            {"RUN_ID": "qualification", PROFILE_ENV: str(tmp_path / "clock.json")},
            state=tmp_path / "state",
            stay_quiescent=False,
        )
        == supervisor.REFUSED_EXIT
    )
    assert "must be supplied together" in capsys.readouterr().err
    assert not (tmp_path / "state" / "runs" / "qualification").exists()


def test_established_evaluator_may_select_accelerated_exact_image_clock(monkeypatch, tmp_path):
    _document, _profile, public, environ = _signed(tmp_path)
    monkeypatch.setattr("solver.clock.TRUSTED_KEY_DIGEST", hashlib.sha256(public.read_bytes()).hexdigest())
    clock = from_environment(environ)
    assert isinstance(clock, SignedQualificationClock)
    assert clock.now().tzinfo is not None
    assert qualification_from_environment(environ).rules.event == "controlled-final-interval"


def test_tampered_signed_clock_is_refused(monkeypatch, tmp_path):
    _document, profile, public, environ = _signed(tmp_path)
    monkeypatch.setattr("solver.clock.TRUSTED_KEY_DIGEST", hashlib.sha256(public.read_bytes()).hexdigest())
    profile.write_bytes(profile.read_bytes().replace(b'"rate":60', b'"rate":61'))
    with pytest.raises(ValueError, match="signature is invalid"):
        from_environment(environ)


def test_signed_board_url_must_match_boot(monkeypatch, tmp_path):
    _document, _profile, public, environ = _signed(tmp_path)
    monkeypatch.setattr("solver.clock.TRUSTED_KEY_DIGEST", hashlib.sha256(public.read_bytes()).hexdigest())
    environ["CTFD_URL"] = "https://scored.example"
    with pytest.raises(ValueError, match="URL disagrees"):
        qualification_from_environment(environ)


def test_acceleration_uses_one_wall_and_monotonic_timeline():
    ticks = iter((10.0, 10.5, 11.0, 11.5))
    opened = dt.datetime(2026, 9, 22, 1, tzinfo=dt.UTC)
    clock = SignedQualificationClock(opened, 60, monotonic=lambda: next(ticks))
    assert clock.now() == opened + dt.timedelta(seconds=30)
    assert clock.monotonic() == (opened + dt.timedelta(seconds=60)).timestamp()
    assert clock.wall_time() == (opened + dt.timedelta(seconds=90)).timestamp()
