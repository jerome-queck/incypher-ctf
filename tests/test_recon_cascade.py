"""The cascade that opens every Attempt, before any model is invoked.

What is on trial is that the cascade is **total** — over file types it has never met, over tools
that are not installed, over an artefact large enough to eat the Run — and that every one of those
cases arrives as an Observation rather than as silence or an exception. The two facts that shape it
are [#15](https://github.com/jerome-queck/incypher-ctf/issues/15)'s live census: 52 of 52 Brunner
attachments are `.zip`, so extension dispatch has one branch, and 22 of 74 Challenges ship no file
at all, so a cascade that needs an artefact to say anything is a cascade that says nothing about
three Challenges in ten.
"""

import hashlib
import json
import re
import zipfile
from itertools import chain
from pathlib import Path

import pytest
from solver import shell as shell_module
from solver.attempt_executor import AttemptResult, ResourceOutcome
from solver.recon import BRANCHES, DISPATCH, OD, PICTURES, STRINGS, Limits, recon
from solver.record import SOURCE_BOARD, SOURCE_SOLVER, Recorder
from solver.redaction import Redactor

BRUNNER = r"brunner{[^}]*}"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00\x00\x00\rIHDR" + bytes(24)
# Deterministic bytes that `file` has no opinion about, which is the case the floor exists for.
UNRECOGNISABLE = b"".join(hashlib.sha256(bytes([step % 256])).digest() for step in range(400))


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


def scout(recorder, description="", artefacts=(), *, wrappers=(BRUNNER,), limits=None):
    return recon(
        description,
        artefacts,
        flag_wrappers=wrappers,
        recorder=recorder,
        attempt_id="attempt-1",
        limits=limits or Limits(),
    )


class ExecutorResult:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class MimeExecutor:
    def __init__(self):
        self.requests = []

    def start(self, request):
        self.requests.append(request)
        return ExecutorResult(
            AttemptResult(
                envelope_id="envelope-1",
                generation_id=request.generation_id,
                outcome=ResourceOutcome.EXITED,
                exit_code=0,
                output=b"image/png\n",
                observed={},
                cleanup_complete=True,
            )
        )


class ResidentMimeRuntime:
    def __init__(self):
        self.requests = []

    def invoke_resident(self, **request):
        self.requests.append(request)
        return type(
            "Result",
            (),
            {"exit_code": 0, "output": b"schema=resident.recon.mime.v1\nmime=image/png\n"},
        )()


def commands(result, subject=None) -> list[str]:
    return [probe.command for probe in result.probes if subject in (None, probe.subject)]


def output_for(result, tool: str, subject=None) -> str:
    return next(probe.shown for probe in result.probes if probe.tool == tool and subject in (None, probe.subject))


def tools(result, subject=None) -> list[str]:
    return [probe.tool for probe in result.probes if subject in (None, probe.subject)]


def records(recorder) -> list[dict]:
    return [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]


def sources(recorder, subject: str) -> set[str]:
    ends = (one for one in records(recorder) if one["record"] == "step-end")
    return {one["source"] for one in ends if subject in one["command_raw"]}


def test_the_mime_type_decides_the_branch_and_the_extension_never_does(tmp_path, recorder):
    """A `.txt` that is really a PNG is handled as a PNG. Extension dispatch is what the census
    rules out anyway — every Brunner attachment is a `.zip`, so the types live one layer inside."""
    mislabelled = tmp_path / "notes.txt"
    mislabelled.write_bytes(PNG)

    result = scout(recorder, artefacts=[mislabelled])

    assert output_for(result, "file").strip() == "image/png"
    assert "exiftool" in tools(result, subject="notes.txt")


def test_mime_dispatch_is_the_one_legacy_shell_step_routed_through_the_attempt_executor(tmp_path, recorder):
    artefact = tmp_path / "notes.txt"
    artefact.write_bytes(PNG)
    executor = MimeExecutor()

    result = recon(
        "",
        [artefact],
        flag_wrappers=(BRUNNER,),
        recorder=recorder,
        attempt_id="attempt-1",
        generation_id="generation-000001",
        executor=executor,
    )

    assert output_for(result, "file").strip() == "image/png"
    assert len(executor.requests) == 1
    dispatched = executor.requests[0]
    assert dispatched.generation_id == "generation-000001"
    assert dispatched.attempt_id == "attempt-1"
    assert dispatched.argv == (*DISPATCH, "notes.txt")
    assert dispatched.workspace == tmp_path


def test_production_mime_dispatch_uses_the_locked_resident_capability(tmp_path, recorder):
    artefact = tmp_path / "notes.txt"
    artefact.write_bytes(PNG)
    runtime = ResidentMimeRuntime()

    result = recon(
        "",
        [artefact],
        flag_wrappers=(BRUNNER,),
        recorder=recorder,
        attempt_id="attempt-1",
        generation_id="generation-000001",
        executor=MimeExecutor(),
        tool_runtime=runtime,
    )

    assert output_for(result, "file").splitlines() == ["schema=resident.recon.mime.v1", "mime=image/png"]
    assert "exiftool" in tools(result, subject="notes.txt")
    assert runtime.requests == [
        {
            "generation_id": "generation-000001",
            "attempt_id": "attempt-1",
            "step_id": "attempt-1:step-3",
            "workspace": tmp_path,
            "capability_id": "recon.mime",
            "input_path": artefact,
        }
    ]
    assert next(probe.command for probe in result.probes if probe.tool == "file") == "recon.mime notes.txt"


def test_a_type_the_cascade_has_never_met_still_gets_the_whole_floor(tmp_path, recorder):
    """Unknown is the default branch, not a failure branch: the floor is what makes the cascade
    total over file types rather than total over the ones we anticipated."""
    unheard_of = tmp_path / "artefact.q3x"
    unheard_of.write_bytes(UNRECOGNISABLE)

    ran = scout(recorder, artefacts=[unheard_of])

    assert set(tools(ran, subject="artefact.q3x")) == {"file", "strings", "entropy", "od", "flag-scan"}


def test_both_ends_are_dumped_only_where_they_are_different_bytes(tmp_path, recorder):
    """`od` is asked for head and tail because a header names a format and a footer often carries
    the appended thing — but on a small artefact those are one dump, and a second copy of it costs
    the model context for nothing."""
    small = tmp_path / "small.bin"
    small.write_bytes(b"\x7fELF" + bytes(64))
    large = tmp_path / "large.bin"
    large.write_bytes(b"\x7fELF" + bytes(4000))

    assert tools(scout(recorder, artefacts=[small]), subject="small.bin").count("od") == 1
    assert tools(scout(recorder, artefacts=[large]), subject="large.bin").count("od") == 2


def test_a_challenge_that_ships_no_file_is_still_reconnoitred(recorder):
    """22 of 74 Brunner Challenges ship no attachment. A cascade that needs one says nothing at
    all about three Challenges in ten."""
    result = scout(recorder, "Connect to the service and read the notice board.")

    assert "notice board" in result.block()
    assert len(result.probes) >= 1


def test_the_description_is_read_for_the_boards_wrapper(recorder):
    """A password, a second download host, or the Flag itself lives only in prose."""
    result = scout(recorder, "Warm-up: brunner{prose_is_an_artefact} was left in the notes.")

    assert "brunner{prose_is_an_artefact}" in output_for(result, "flag-scan")


def test_the_description_and_its_scan_are_recorded_as_the_boards_own_statement(tmp_path, recorder):
    """Both probes over the prose read bytes the Board handed us; every probe over an artefact
    reads bytes off the working directory. The record carries which, because what authorises a Flag
    submission is what the Solver's own work produced (ADR-0019)."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"nothing here\n")

    scout(recorder, "Flag format: brunner{like_this}", artefacts=[artefact])

    assert sources(recorder, "the description") == {SOURCE_BOARD}
    assert sources(recorder, str(artefact)) == {SOURCE_SOLVER}


def test_the_wrapper_comes_from_the_board_and_not_from_the_code(tmp_path, recorder):
    """Each Board's wrapper is its own — `flag{…}` at IN-CYPHER, `brunner{.*}` at Brunner. A
    cascade holding one of them finds nothing at the next event."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"nothing here but flag{another_boards_wrapper}\n")

    board = scout(recorder, artefacts=[artefact], wrappers=(r"flag\{[^}]*\}",))
    elsewhere = scout(recorder, artefacts=[artefact], wrappers=(BRUNNER,))

    found = output_for(board, "flag-scan", subject="note.txt")
    missed = output_for(elsewhere, "flag-scan", subject="note.txt")

    assert "flag{another_boards_wrapper}" in found
    assert "flag{another_boards_wrapper}" not in missed


def test_every_wrapper_the_board_states_is_scanned_for_and_none_eats_another(tmp_path, recorder):
    """The trap `solver/wrapper.py` exists for, end to end. The second shape's match starts *earlier*
    in these bytes, so a single `a|b` alternation would consume the `brunner{` after it and answer
    with the decoy alone — worse than scanning for the primary shape by itself."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"noise FLAG-abcbrunner{the_real_flag} more\n")

    second = r"FLAG-[0-9a-f]{1,64}"

    result = scout(recorder, artefacts=[artefact], wrappers=(BRUNNER, second))
    joined = scout(recorder, artefacts=[artefact], wrappers=(f"{BRUNNER}|{second}",))

    found = output_for(result, "flag-scan", subject="note.txt")
    assert "brunner{the_real_flag}" in found
    assert "FLAG-abc" in found
    # The route a single pattern leaves open, and what it costs — this is the whole reason the
    # profile states a list.
    assert "brunner{the_real_flag}" not in output_for(joined, "flag-scan", subject="note.txt")


def test_a_second_wrapper_costs_no_second_step_and_no_second_read(tmp_path, recorder):
    """The cascade shares one deadline, so a re-read per pattern would spend a later artefact's
    budget on bytes this one has already seen."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"nothing here\n")

    one = scout(recorder, artefacts=[artefact], wrappers=(BRUNNER,))
    two = scout(recorder, artefacts=[artefact], wrappers=(BRUNNER, r"FLAG-[0-9a-f]{1,64}"))

    assert tools(one) == tools(two)


def test_a_zip_is_listed_by_the_one_branch_the_census_justifies(tmp_path, recorder):
    """52 of 52 Brunner attachments are `.zip`, so this is the branch that carries the event."""
    archive = tmp_path / "challenge.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        writing.writestr("inner/secret.png", PNG)

    result = scout(recorder, artefacts=[archive])

    assert "inner/secret.png" in output_for(result, "unzip")


def test_a_tool_that_is_not_installed_becomes_the_observation(tmp_path, recorder, monkeypatch):
    """Never silence — which would leave the model to narrate what it thinks happened."""
    monkeypatch.setenv("PATH", "")
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"brunner{still_recorded}\n")

    result = scout(recorder, artefacts=[artefact])

    assert all(probe.shown for probe in result.probes)
    assert any("did not run" in probe.shown for probe in result.probes)
    # The in-process floor needs no binary, so a stripped PATH costs the tools and not the facts.
    assert "brunner{still_recorded}" in output_for(result, "flag-scan", subject="note.txt")


def test_a_tool_with_nothing_to_say_says_that(tmp_path, recorder):
    """`strings` finds nothing in a 36-byte PNG. An empty body under a command reads to a model as
    a command that never ran, which is the same silence read from the other end."""
    tiny = tmp_path / "tiny.png"
    tiny.write_bytes(PNG)

    assert "no output" in output_for(scout(recorder, artefacts=[tiny]), "strings", "tiny.png")


def test_a_command_that_will_not_stop_is_killed_and_says_so(tmp_path, recorder, monkeypatch):
    monkeypatch.setitem(BRANCHES, "text/plain", (("python3", "-c", "import time; time.sleep(30)"),))
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    result = scout(recorder, artefacts=[artefact], limits=Limits(command_seconds=0.3))

    killed = next(probe for probe in result.probes if probe.tool == "python3")
    assert killed.exit_code is None
    assert "killed" in killed.shown


def test_a_command_that_stops_talking_without_exiting_is_killed_too(tmp_path, recorder, monkeypatch):
    """The other way a command hangs: end-of-output is not end-of-process, and a cascade that
    waits for the second one has no cap at all."""
    monkeypatch.setattr(shell_module, "REAP_GRACE_SECONDS", 0.2)
    monkeypatch.setitem(
        BRANCHES, "text/plain", (("python3", "-c", "import os, time; os.close(1); os.close(2); time.sleep(30)"),)
    )
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    result = scout(recorder, artefacts=[artefact])

    stuck = next(probe for probe in result.probes if probe.tool == "python3")
    assert stuck.exit_code is None
    assert "did not exit" in stuck.shown


def test_output_is_capped_where_it_is_captured_and_not_where_it_is_shown(tmp_path, recorder, monkeypatch):
    """A cap applied after a gigabyte is already in memory is a cap in name. What proves it is the
    stored body, which is written from what was captured."""
    monkeypatch.setitem(BRANCHES, "text/plain", (("python3", "-c", "print('a' * 500000)"),))
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    result = scout(recorder, artefacts=[artefact], limits=Limits(output_bytes=2048))

    capped = next(probe for probe in result.probes if probe.tool == "python3")
    body = next(
        record
        for record in records(recorder)
        if record["record"] == "step-end" and record["command_raw"] == capped.command
    )
    assert body["observation_bytes"] < 3000
    assert "capped" in capped.shown


def test_the_size_cap_is_enforced_rather_than_intended(tmp_path, recorder):
    """Brunner links a 6.15 GB archive from description prose. A floor that reads to the end of
    whatever it is handed is how one artefact consumes an Attempt."""
    huge = tmp_path / "huge.bin"
    huge.write_bytes(b"." * 200_000 + b"brunner{past_the_cap}\n")

    swept = scout(recorder, artefacts=[huge], limits=Limits(artefact_bytes=4096))
    scan = output_for(swept, "flag-scan", subject="huge.bin")

    assert "brunner{past_the_cap}" not in scan
    assert "4096" in scan


def test_the_scan_stops_on_the_clock_and_not_only_on_the_size_cap(tmp_path, recorder):
    """The size cap bounds a 6 GB artefact; nothing bounds a slow disk under it. A reading is a
    probe like any other, so the wall-clock reaches it too."""
    slow = tmp_path / "slow.bin"
    slow.write_bytes(b"." * (2 << 20) + b"brunner{after_the_clock}\n")

    scan = output_for(scout(recorder, artefacts=[slow], limits=Limits(command_seconds=0)), "flag-scan", "slow.bin")

    assert "brunner{after_the_clock}" not in scan
    assert "stopping after" in scan


def test_the_entropy_windows_reach_the_artefacts_tail(tmp_path, recorder):
    """An appended blob lives past everything a head-first sweep reaches — the same reason `od` is
    asked for both ends."""
    appended = tmp_path / "appended.bin"
    appended.write_bytes(bytes(400_000) + bytes(range(256)) * 16)

    windows = output_for(scout(recorder, artefacts=[appended]), "entropy", "appended.bin").splitlines()

    assert windows[-1].startswith(f"0x{appended.stat().st_size - 4096:08x}")
    assert windows[-1].endswith("8.000 bits/byte")


def test_one_enormous_match_does_not_become_the_whole_block(tmp_path, recorder):
    """`brunner{.*}` is a real Board's wrapper, and over binary input one match can be most of the
    artefact."""
    greedy = tmp_path / "greedy.bin"
    greedy.write_bytes(b"brunner{" + b"a" * 50_000 + b"}")

    scan = output_for(scout(recorder, artefacts=[greedy], wrappers=(r"brunner\{.*\}",)), "flag-scan", "greedy.bin")

    assert "1 match(es)" in scan
    assert len(scan) < 500


def test_a_stopped_probe_is_marked_as_stopped_in_the_block(tmp_path, recorder, monkeypatch):
    """A killed tool that reads like a clean one is the silence this cascade refuses, one layer up
    — the block is what a model actually reads."""
    monkeypatch.setitem(BRANCHES, "text/plain", (("python3", "-c", "import time; time.sleep(30)"),))
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    block = scout(recorder, artefacts=[artefact], limits=Limits(command_seconds=0.3)).block()

    assert "[stopped]" in block


def test_a_spent_cascade_budget_is_written_down_rather_than_skipped(tmp_path, recorder):
    """Wall-clock is a hard cap on the cascade and not only on one command — and a probe that did
    not run is a fact about the Attempt, so it is recorded like any other."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    result = scout(recorder, "a description", artefacts=[artefact], limits=Limits(cascade_seconds=0))

    assert result.probes
    assert all("not run" in probe.shown for probe in result.probes)


def test_every_probe_is_a_step_pair_numbered_from_the_start_of_the_attempt(tmp_path, recorder):
    """Recon is the Attempt's opening move, so its Steps are the Attempt's first Steps."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    result = scout(recorder, "a description", artefacts=[artefact])

    written = records(recorder)
    assert [record["record"] for record in written] == ["step-begin", "step-end"] * len(result.probes)
    assert [record["step_index"] for record in written if record["record"] == "step-end"] == list(
        range(1, len(result.probes) + 1)
    )


def test_entropy_is_measured_over_windows_rather_than_guessed(tmp_path, recorder):
    """The floor's one measurement that says "this is compressed or encrypted" without a tool."""
    flat = tmp_path / "flat.bin"
    flat.write_bytes(bytes(100_000))
    mixed = tmp_path / "mixed.bin"
    mixed.write_bytes(bytes(range(256)) * 400)

    assert "0.000" in output_for(scout(recorder, artefacts=[flat]), "entropy")
    assert "8.000" in output_for(scout(recorder, artefacts=[mixed]), "entropy")


def test_no_model_is_invoked_before_the_cascade_has_run(tmp_path, recorder):
    """The rabbit-hole this exists to prevent: the first action frames the whole run."""
    artefact = tmp_path / "note.txt"
    artefact.write_bytes(b"ordinary text\n")

    scout(recorder, "a description", artefacts=[artefact])

    ends = [record for record in records(recorder) if record["record"] == "step-end"]
    assert {record["model"] for record in ends} == {""}
    assert {record["tokens_in"] + record["tokens_out"] for record in ends} == {0}


def test_the_cascade_only_reaches_for_tools_the_image_installs():
    """A recon step that types a binary the `Dockerfile` never installs is a failure at 14:00 on
    competition day, and nothing else in this suite would catch it — the machine running the tests
    has a package manager and the image does not."""
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    named = set(re.findall(r"[\w.+-]+", dockerfile.read_text()))

    for command in (DISPATCH, STRINGS, OD, *chain.from_iterable(BRANCHES.values())):
        assert command[0] in named, f"{command[0]} is invoked by the cascade and installed by nothing"


def test_an_artefact_that_is_a_picture_is_named_as_one(recorder, tmp_path):
    """The cascade already dispatches on `file -b --mime-type`, so which artefacts are pictures is
    a fact it has computed and thrown away. Naming it is what lets the Attempt attach them
    (`solver/codex.py`), and it is read off the tool's answer rather than off the extension for
    the same reason every other branch is."""
    picture = tmp_path / "cipher.png"
    picture.write_bytes(PNG)

    found = scout(recorder, artefacts=[picture])

    assert found.pictures == (picture,)


def test_an_artefact_that_only_looks_like_a_picture_is_not_one(recorder, tmp_path):
    """A `.png` that is really a zip is not attached, and a zip named anything is not either. The
    whole point of dispatching on content is that the name is never the answer."""
    liar = tmp_path / "cipher.png"
    with zipfile.ZipFile(liar, "w") as archive:
        archive.writestr("inside.txt", "brunner{not_a_picture}")

    found = scout(recorder, artefacts=[liar])

    assert found.pictures == ()


def test_the_pictures_attached_are_capped_however_many_a_challenge_ships(recorder, tmp_path):
    """Every attached picture is spent context on a 5.5-hour clock, and a Challenge shipping a
    directory of frames would spend an Attempt's whole frame on them before the model read a word."""
    pictures = []
    for number in range(PICTURES + 3):
        picture = tmp_path / f"frame-{number:02d}.png"
        picture.write_bytes(PNG)
        pictures.append(picture)

    found = scout(recorder, artefacts=pictures)

    assert found.pictures == tuple(pictures[:PICTURES])
