"""The Attempt prompt carries facts and rules, and never a word about how the Attempt is going.

Two things are on trial. The first is #71's derivation rule, whose structural half is already
enforced and cannot say *why*: a transformation the model performs in its head lands in no
Observation, so a candidate from one can only ever be submitted `unverified`. The second is the
prohibitions — they exist in a Board's prose and nowhere in its API, so the only route by which one
reaches the thing that could break it is this prompt.
"""

import datetime as dt
from dataclasses import replace
from pathlib import Path

from solver import prompt
from solver.carry import SECTIONS, Boundary
from solver.instance import Lease, Terms
from solver.intake import Attachment, Sighting
from solver.profile import Rules
from solver.prompt import APPROACH, DERIVATION, compose

RULES = Rules(
    event="somewhere",
    url="https://board.example",
    flag_wrappers=(r"brunner\{[^}]{1,256}\}",),
    window_seconds=5.5 * 3600,
    prohibitions=("no broad automated enumeration — an immediate ban", "no sandbagging"),
)

TERMS = Terms(challenge_id=7, challenge_type="dynamic_iac", timeout=600)

CHALLENGE = Sighting(
    challenge_id=7,
    name="the locked room",
    category="forensics",
    challenge_type="dynamic_iac",
    value=300,
    solves=4,
    position=0,
    description="A door that only opens twice.",
    attempts=0,
    max_attempts=0,
    solved=False,
    terms=TERMS,
    attachments=(Attachment("files/aa/door.zip", "door.zip", Path("/state/runs/x/intake/7/door.zip"), 512),),
)


def prompt_for(**overrides):
    settings = {
        "challenge": CHALLENGE,
        "rules": RULES,
        "boundary": Boundary(),
        "recon": "[recon] file door.zip → Zip archive",
        "workdir": Path("/state/work/brunnerctf-2026-global/7"),
        "budget_s": 600,
        **overrides,
    }
    return compose(**settings)


def test_the_derivation_rule_reaches_the_model_verbatim():
    """It was written, reviewed and taken back out of `solver/flag.py` because a constant nothing
    reads is dead weight. This is the place that reads it."""
    assert DERIVATION in prompt_for()
    assert "run that transformation as a command" in DERIVATION


def test_every_prohibition_this_boards_rules_impose_is_named():
    text = prompt_for()

    assert "no broad automated enumeration — an immediate ban" in text
    assert "no sandbagging" in text


def test_a_board_whose_rules_forbid_nothing_says_so_rather_than_showing_an_empty_list():
    text = prompt_for(rules=Rules(event="e", url="u", flag_wrappers=("f",), window_seconds=1))

    assert prompt.NO_PROHIBITIONS in text


def test_every_flag_wrapper_is_the_boards_own_and_never_hardcoded():
    """Every shape, not just the primary one — a model told only the first would read a Flag in the
    second as not a Flag at all."""
    text = prompt_for(rules=replace(RULES, flag_wrappers=(*RULES.flag_wrappers, r"FLAG-[0-9a-f]{1,64}")))

    assert all(one in text for one in (*RULES.flag_wrappers, r"FLAG-[0-9a-f]{1,64}"))


def test_submitting_is_not_the_models():
    """ADR-0014: Codex never touches the Board. It holds no token and not even the Board's address,
    so a model that tried would spend Steps failing at it."""
    assert "Do not try to submit it" in prompt_for()


def test_all_five_things_that_cross_a_boundary_are_carried_and_no_sixth():
    text = prompt_for()

    for heading in SECTIONS:
        assert f"## {heading}" in text


def test_the_boards_own_facts_are_carried_rather_than_restated_in_the_header():
    """A fact the Board gave us is not ours to forget, and a header sentence is not something
    `solver/carry.py` can carry into the next turn."""
    facts = prompt_for().split(f"## {SECTIONS[0]}")[1]

    assert "the locked room" in facts
    assert "A door that only opens twice." in facts
    assert "door.zip" in facts


def test_an_instance_is_given_as_an_address_and_an_expiry():
    until = dt.datetime(2026, 9, 22, 13, 0, tzinfo=dt.timezone.utc)
    text = prompt_for(lease=Lease(7, "nc target.example 31337", until, TERMS))

    assert "nc target.example 31337" in text
    assert until.isoformat() in text


def test_the_one_line_the_model_writes_is_asked_for_by_its_marker():
    """Read from a marker rather than inferred from prose, so a label is something the model chose
    to write and never a sentence a parser picked out of a paragraph."""
    assert APPROACH in prompt_for()


def test_nothing_tells_the_model_how_it_is_doing():
    """ADR-0005 puts the stall call outside the solving model on the measurement that a model's own
    progress report is unreliable, and telling one mid-Attempt that it appears stuck is close to an
    ideal prompt for inducing *impossible*."""
    text = prompt_for().lower()

    for prod in ("stuck", "stalled", "giving up", "impossible", "you have tried", "running out of time"):
        assert prod not in text


def test_the_budget_is_stated_once_as_a_size_and_not_as_a_countdown():
    text = prompt_for(budget_s=900)

    assert "15 minute(s)" in text
    assert text.count("minute(s)") == 1
