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
from solver.prompt import APPROACH, DERIVATION, NAME_TAKEN, compose
from solver.stall import DECLARES_IMPOSSIBLE
from solver.staging import Staged

RULES = Rules(
    event="somewhere",
    url="https://board.example",
    flag_wrappers=(r"brunner\{[^}]{1,256}\}",),
    window_seconds=5.5 * 3600,
    prohibitions=("no broad automated enumeration — an immediate ban", "no sandbagging"),
)

TERMS = Terms(challenge_id=7, challenge_type="dynamic_iac", timeout=600)

# Where the model is standing, and — on `CHALLENGE` below — the path it must never be sent to:
# `Attachment.path` is Intake's copy inside the Run's own record, which is what #126 is about.
WORKDIR = Path("/state/work/brunnerctf-2026-global/7")

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
        "workdir": WORKDIR,
        "staged": (Staged("door.zip", 512, WORKDIR / "door.zip"),),
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


def test_a_held_file_is_named_where_the_model_is_standing():
    """The prompt says the working directory already holds this Challenge's files and then has to
    name them there: the model works in that directory, and a path anywhere else is one it has to
    be told twice about."""
    text = prompt_for()

    assert str(WORKDIR / "door.zip") in text
    assert "512 bytes as downloaded" in text


def test_nothing_in_the_prompt_names_a_path_inside_the_runs_own_record():
    """`CHALLENGE`'s attachment still carries Intake's copy at `/state/runs/x/intake/7/door.zip`,
    which is the fixture's whole job now. The Solver has no reason to send the model into the
    directory holding the stream its own stall is judged from — `solver/codex.py` already refuses
    to let the working directory *contain* that record, and this is the same rule from the other
    side."""
    assert "/state/runs" not in prompt_for()


def test_a_landing_that_could_not_take_the_boards_name_says_whose_name_it_is():
    """Since [#119](https://github.com/jerome-queck/incypher-ctf/issues/119) the Board's file lands
    beside a stranger under a name minted from its own digest. Recon opens onto the right one and
    the record says so — and a model that runs `ls`, sees the name the Board's prose uses, and
    opens it would still be reading the stranger."""
    landing = WORKDIR / "door.aa11bb22cc33.zip"

    text = prompt_for(staged=(Staged("door.zip", 512, landing),))

    assert str(landing) in text
    assert NAME_TAKEN.format(name="door.zip") in text
    # The tense is the load-bearing half, pinned the way `DERIVATION` is pinned above: a
    # present-tense claim about the directory is one the model's own `mv` falsifies next turn.
    assert "already had that name" in NAME_TAKEN


def test_a_landing_under_the_boards_own_name_says_nothing_about_a_taken_name():
    """The clause is the rare branch. Firing it on every Challenge would spend the opening frame
    explaining a thing that did not happen.

    The whole line is pinned rather than the clause's absence: asserting that some wording is *not*
    there is an assertion a reworded clause passes while firing on every Challenge.
    """
    lines = prompt_for().splitlines()

    listed = lines[lines.index("Files already downloaded for you:") + 1]

    assert listed == f"- {WORKDIR / 'door.zip'} (512 bytes as downloaded)"


def test_the_size_is_dated_rather_than_stated_as_the_file_now():
    """`nbytes` is what Intake downloaded, frozen when the Attempt staged its files — and this
    prompt is recomposed every turn over a directory the model has been working in. The bare
    `(512 bytes)` this replaces is a present-tense claim the model's own first `unzip` can break,
    and on a forensics Challenge a stated size is something a model reasons from.

    Counted rather than matched, so the number cannot appear undated anywhere: an assertion that the
    dated form is *present* is one that a second, bare mention alongside it would still pass.
    """
    text = prompt_for()

    assert text.count("512 bytes") == 1
    assert text.count("512 bytes as downloaded") == 1


def test_the_prompt_is_composed_from_what_it_is_handed_and_never_from_disk():
    """Nothing here touches the filesystem, and the fixture's paths do not exist. A size or an
    existence check read off disk would make the prompt a function of the working directory at the
    moment it was composed, which is a second source of truth for what an Attempt was handed."""
    absent = Path("/state/work/nowhere-at-all/7/door.zip")

    text = prompt_for(staged=(Staged("door.zip", 512, absent),))

    assert str(absent) in text


def test_the_model_is_told_the_sentence_that_declares_a_challenge_unwinnable():
    """The matcher waited for words nobody asked for, and on the first live Challenge that deserved
    it the model said the thing in its own words and was not heard
    ([#143](https://github.com/jerome-queck/incypher-ctf/issues/143)).

    `DECLARES_IMPOSSIBLE` is `solver/stall.py`'s, imported rather than restated, so the prompt and
    the matcher cannot drift into two different sentences.
    """
    assert DECLARES_IMPOSSIBLE in prompt_for()


def test_the_unwinnable_contract_is_about_the_challenge_and_never_about_the_attempt():
    """ADR-0005 keeps the stall call away from the solving model because telling one that it appears
    stuck is close to an ideal prompt for inducing *impossible*. This says what makes a Challenge
    unwinnable — no route that does not break the rules — and never asks how the turn is going."""
    said = prompt_for().lower()

    assert "no route to the Flag exists for anyone playing this Board".lower() in said
    assert "a challenge you have not cracked yet is one to keep working" in said
    for inducing in ("stuck", "give up", "how you are doing", "running out"):
        assert inducing not in said


def test_the_unwinnable_contract_says_the_solver_holds_the_credential_the_model_does_not():
    """The trigger's first wording asked what *we* do not hold, and the model answered about itself
    — correctly, since ADR-0014 denies it the Board credential on purpose. `compfest-2026-seg2`
    parked two Challenges the rest of the Board had solved 58 and 31 times on exactly that reading
    (#149), so the prompt now says whose the credential is rather than leaving it to be inferred."""
    said = prompt_for()

    assert "holds this Board's credential and submits the Flag on your behalf" in said
    assert "that boundary is deliberate and it is not the Challenge being closed" in said


def test_the_unwinnable_contract_names_each_thing_that_is_not_grounds():
    """One at a time rather than left to be inferred from the trigger. Each of these is a shape a
    live Attempt stopped on, or would have: `23-1` had no account for the service and `16-1` had
    neither the address nor the key, and both were work left to do."""
    said = prompt_for().lower()

    for not_grounds in (
        "to reach the board, to log in, or to hold an account anywhere",
        "a tool the image does not ship",
        "a host that will not answer",
        "a key or an address or an endpoint you have not found yet",
        "a cryptographic step you have not broken",
    ):
        assert not_grounds in said
