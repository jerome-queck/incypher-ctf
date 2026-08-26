"""Triage extracts and does not predict, and every test here is a way of holding it to that.

The measurement behind the design is the reason it needs holding: a model asked for difficulty from
a description alone correlates near zero, and a coarser question does not rescue it — 37.75% over
three tiers, with **83% of Hard mislabelled Easy** (ADR-0006). So what is on trial is the *order*:
what the Board states wins, `solves` orders what is left, and the model is asked about the
remainder and nothing else — with the manifest in front of it and no way to act on it.
"""

import datetime as dt
import json

import pytest
from solver.board import Board
from solver.codex import Child, Credential, Invocation, asking
from solver.intake import OVER_THE_CAP, Attachment, Sighting
from solver.instance import Terms
from solver.record import Recorder
from solver.redaction import Redactor
from solver.triage import EXTRACTED, FLOOR, JUDGED, SOLVES, UNJUDGED, render, triage, unasked


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


def sighting(challenge_id=1, *, description="", solves=0, files=(), category="forensics", **fields):
    return Sighting(
        challenge_id=challenge_id,
        name=f"challenge-{challenge_id}",
        category=category,
        challenge_type=fields.pop("challenge_type", "standard"),
        value=fields.pop("value", 500),
        solves=solves,
        position=fields.pop("position", 0),
        description=description,
        attempts=0,
        max_attempts=None,
        solved=False,
        terms=Terms(challenge_id=challenge_id, challenge_type="standard"),
        attachments=tuple(files),
        **fields,
    )


def held(name="cover.png", nbytes=18, path=None):
    return Attachment(f"files/aa/{name}", name, path or f"/state/{name}", nbytes)


def tiers(judgements) -> dict:
    return {one.challenge_id: (one.tier, one.provenance) for one in judgements}


def records(recorder) -> list[dict]:
    lines = (json.loads(line) for line in recorder.stream_path.read_text().splitlines())
    return [line for line in lines if line["record"] == "triage"]


def transcribing():
    """A judge that answers nothing and keeps the prompt, so a test can say what it was shown."""
    seen = []

    def judge(prompt: str) -> str:
        seen.append(prompt)
        return ""

    return judge, seen


@pytest.mark.parametrize(
    "prose",
    [
        "**Difficulty:** Hard\n\nOpen the archive.",
        "Difficulty: hard",
        "**Difficulty**: HARD",
        "Some prose first. Difficulty - Hard. More prose.",
    ],
)
def test_a_stated_difficulty_is_extracted_however_the_board_marks_it_up(recorder, prose):
    """22 of 25 sampled Brunner descriptions carried one. It is not a guess about the Challenge; it
    is the Challenge's author saying so, and the markup around it is a convention rather than a
    field in any API."""
    judged = triage([sighting(1, description=prose, solves=40)], recorder=recorder)

    assert tiers(judged) == {1: (3, EXTRACTED)}
    assert judged[0].stated.lower() == "hard"


def test_a_stated_word_the_table_cannot_read_falls_through_and_is_still_recorded(recorder):
    """A Board with its own scale is a Board we cannot read, not one to force onto a number nobody
    stated. Keeping the word is how the table gains a row instead of the Challenge gaining a wrong
    Tier."""
    judge, _ = transcribing()

    judged = triage([sighting(1, description="Difficulty: nightmare")], recorder=recorder, judge=judge)

    assert judged[0].provenance == UNJUDGED
    assert judged[0].stated == "nightmare"


def test_where_nothing_is_stated_solves_carry_the_ordering(recorder):
    """The Board's own population has already attempted these, and how many got through is the one
    measurement of difficulty available for free. Most-solved is cheapest."""
    board = [sighting(one, solves=solves) for one, solves in ((1, 90), (2, 40), (3, 12), (4, 1))]

    judged = triage(board, recorder=recorder)

    assert tiers(judged) == {1: (1, SOLVES), 2: (2, SOLVES), 3: (3, SOLVES), 4: (4, SOLVES)}


def test_zero_solves_carry_no_ordering_at_all_and_go_to_the_judge(recorder):
    """At Run start every Challenge on a fresh Board has none, and a quantile over zeros would hand
    the whole Board one Tier and call it evidence."""
    judge, seen = transcribing()
    board = [sighting(1, solves=0), sighting(2, solves=7), sighting(3, solves=2)]

    triage(board, recorder=recorder, judge=judge)

    assert [f"id {one} " in seen[0] for one in (1, 2, 3)] == [True, False, False]


def test_a_board_whose_solve_counts_are_all_the_same_has_ranked_nothing(recorder):
    """`solves` earns its place by ordering. Where it orders nothing, handing the set the *cheapest*
    Tier would be the mistake `FLOOR` exists to refuse — unknown read as easy."""
    judge, seen = transcribing()
    board = [sighting(1, solves=5), sighting(2, solves=5), sighting(3, solves=5)]

    judged = triage(board, recorder=recorder, judge=judge)

    assert [f"id {one} " in seen[0] for one in (1, 2, 3)] == [True, True, True]
    assert tiers(judged) == {one: (FLOOR, UNJUDGED) for one in (1, 2, 3)}


def test_the_judge_is_asked_once_about_the_remainder_and_never_about_the_rest(recorder):
    """One prompt over the whole remainder, so it is one invocation — and so the model is doing the
    only thing this judgement is for, which is ordering them against each other."""
    judge, seen = transcribing()
    board = [
        sighting(1, description="**Difficulty:** Easy"),
        sighting(2, solves=5),
        sighting(3),
        sighting(4),
        sighting(5, solves=1),
    ]

    triage(board, recorder=recorder, judge=judge)

    assert len(seen) == 1
    assert [f"id {one} " in seen[0] for one in (1, 2, 3, 4, 5)] == [False, False, True, True, False]


def test_the_judge_is_never_asked_at_all_where_nothing_is_left(recorder):
    judge, seen = transcribing()

    triage([sighting(1, description="Difficulty: easy")], recorder=recorder, judge=judge)

    assert seen == []


def test_the_judge_sees_the_manifest_and_never_a_files_contents(recorder, tmp_path):
    """Opening a file here would be Triage doing the Attempt's job with none of the Attempt's
    budget, over every Challenge on the Board."""
    secret = tmp_path / "cover.png"
    secret.write_bytes(b"the-bytes-inside-the-artefact")
    judge, seen = transcribing()

    triage([sighting(1, files=[held("cover.png", 29, secret)])], recorder=recorder, judge=judge)

    assert "cover.png (29 bytes)" in seen[0]
    assert "the-bytes-inside-the-artefact" not in seen[0]


def test_an_attachment_we_do_not_hold_is_named_as_one_rather_than_hidden(recorder):
    """A file over the fetch cap is a fact every later judgement about that Challenge is made
    without, so the judge is told the Challenge has one rather than that it has none."""
    judge, seen = transcribing()
    over_the_cap = Attachment("files/aa/huge.7z", "huge.7z", outcome=OVER_THE_CAP, detail="over the cap")

    triage([sighting(1, files=[over_the_cap])], recorder=recorder, judge=judge)

    assert "files: none" in seen[0]
    assert "listed but not held: huge.7z" in seen[0]


def test_a_challenge_the_judge_never_answers_for_keeps_the_floor_and_says_so(recorder):
    """A Run where every judged Tier is really the floor is a Run whose judge never worked, and that
    has to be visible rather than unanimous."""
    judged = triage([sighting(1), sighting(2)], recorder=recorder, judge=lambda _prompt: "1 4")

    assert tiers(judged) == {1: (4, JUDGED), 2: (FLOOR, UNJUDGED)}


@pytest.mark.parametrize("answer", ["2: 3", "- id 2 → tier 3", "Challenge 2 is tier 3", "2 3"])
def test_the_judges_tier_is_read_out_of_whatever_prose_it_answered_in(recorder, answer):
    """Nothing is inferred from the model's words: a line is searched for an id we asked about and a
    digit in range, and a line carrying neither is skipped."""
    assert tiers(triage([sighting(2)], recorder=recorder, judge=lambda _prompt: answer)) == {2: (3, JUDGED)}


def test_a_tier_outside_the_scale_is_not_a_tier(recorder):
    """The model naming 9 is the model answering a question that was not asked."""
    judged = triage([sighting(1)], recorder=recorder, judge=lambda _prompt: "1 9")

    assert judged[0].provenance == UNJUDGED


def test_triage_never_touches_the_board(recorder):
    """It deploys no Instance and submits nothing — the point of a Tier is to be cheap enough to
    recompute over the whole Board every time the Board moves."""

    def refuses(_request):
        raise AssertionError("Triage reached the Board")

    Board("https://board.example", "not-a-real-token", refuses)

    assert triage([sighting(1), sighting(2, solves=3)], recorder=recorder, judge=unasked)


def test_the_category_changes_nothing_because_there_is_no_table_keyed_by_one(recorder):
    """`strength(category)` is absent by decision rather than stubbed: Category is an open string
    read from the Board, so a table keyed by name has no entry for the categories that will actually
    be scored (`CONTEXT.md`, *Tier*)."""
    web, unheard_of = sighting(1, solves=5, category="web"), sighting(2, solves=5, category="(Practice) misc")
    board = [web, unheard_of, sighting(3, solves=1, category="crypto")]

    judged = triage(board, recorder=recorder)

    assert judged[0].tier == judged[1].tier


def test_every_tier_is_printed_and_recorded_with_its_provenance(recorder):
    """The provenance is the point of the record rather than a decoration on it: an extracted Tier,
    an inferred one and a judged one are three different qualities of evidence."""
    board = [sighting(1, description="Difficulty: Easy"), sighting(2, solves=3), sighting(3), sighting(4, solves=1)]

    judged = triage(board, recorder=recorder, judge=lambda _prompt: "3 4")

    printed = render(judged)
    assert [EXTRACTED, SOLVES, JUDGED, SOLVES] == [line.split("·")[1].strip() for line in printed.splitlines()]
    assert [one["provenance"] for one in records(recorder)[0]["tiers"]] == [EXTRACTED, SOLVES, JUDGED, SOLVES]


def test_running_it_twice_over_an_unmoved_board_reaches_the_same_tiers(recorder):
    """One callable thing rather than a pipeline stage: it runs at Run start, again over what is
    unsolved, and over whatever arrives in between, and it holds nothing between calls."""
    board = [sighting(1, description="Difficulty: Easy"), sighting(2, solves=3), sighting(3, solves=1)]

    assert tiers(triage(board, recorder=recorder)) == tiers(triage(board, recorder=recorder))


class Answering(Child):
    """A `codex exec` that never ran, handing back the bytes it would have written."""

    def __init__(self, wrote: bytes) -> None:
        self._blocks = [wrote, b""]

    def read(self, _budget: float) -> bytes | None:
        return self._blocks.pop(0) if self._blocks else b""

    def stop(self) -> None:
        raise AssertionError("the judge was killed rather than answered")

    def close(self) -> tuple[int | None, bytes]:
        return 0, b""


def test_the_judge_is_asked_with_nothing_it_can_act_on(recorder, tmp_path):
    """The CLI keeps its shell — ADR-0014 measured that there is no door marked *just answer* — so
    what keeps this judgement off the Board is what the invocation withholds: a read-only sandbox,
    no network, and an environment carrying no token and not even the Board's URL."""
    said = (
        '{"type":"item.completed","item":{"id":"a","type":"agent_message","text":"1 4"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}\n'
    ).encode()
    spawned = {}

    def launch(argv, workdir, environment, prompt):
        spawned.update(argv=list(argv), workdir=workdir, environment=dict(environment), prompt=prompt.decode())
        return Answering(said)

    judge = asking(
        Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "codex"),
        recorder=recorder,
        workdir=tmp_path / "scratch",
        launch=launch,
        now=lambda: dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc),
    )
    judged = triage([sighting(1)], recorder=recorder, judge=judge)

    assert tiers(judged) == {1: (4, JUDGED)}
    assert spawned["argv"][spawned["argv"].index("--sandbox") + 1] == "read-only"
    assert "sandbox_workspace_write.network_access=false" in spawned["argv"]
    assert Invocation().sandbox == "workspace-write"  # an Attempt's, and deliberately not this
    assert not {"CTFD_URL", "CTFD_API_TOKEN"} & set(spawned["environment"])
    assert "id 1 " in spawned["prompt"]


def test_what_the_judge_says_lands_in_the_channel_flag_verification_never_sweeps(recorder, tmp_path):
    """A Tier is a judgement and everything the model says on the way to one is a Claim. Anything
    the judge said reaching `observations/` would be a string a Flag could later be swept out of."""
    said = (
        '{"type":"item.completed","item":{"id":"a","type":"agent_message",'
        '"text":"1 2 — this looks like brunner{not-a-real-flag}"}}\n'
    ).encode()

    judge = asking(
        Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "codex"),
        recorder=recorder,
        workdir=tmp_path / "scratch",
        launch=lambda *_args: Answering(said),
        now=lambda: dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc),
    )
    triage([sighting(1)], recorder=recorder, judge=judge)

    swept = b"".join(path.read_bytes() for path in (recorder.run_dir / "observations").glob("*"))
    assert b"not-a-real-flag" not in swept
    assert any(b"not-a-real-flag" in path.read_bytes() for path in (recorder.run_dir / "claims").glob("*"))
