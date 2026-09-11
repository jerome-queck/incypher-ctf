"""One reading of an env file, because two readings disagreed.

`ctfd_probe.load_env` and the credential reporter each parsed this format, line for line the same,
and both were wrong in the same two ways — which is the argument for one parser rather than for
carefully matching parsers. Verified against the versions this replaces: `export TEAM_KEY=zzz` was
read as a variable named `export TEAM_KEY`, so the reporter called a held secret **absent** and the
Solver would have started without it; and `TOKEN=""` was read as two characters of content, so the
reporter called an empty credential **set**. Both are the failure #61 exists to prevent, one in each
direction.
"""

import env_file
import pytest


def test_a_plain_assignment():
    assert env_file.assignments("CTFD_URL=https://b.example\n") == {"CTFD_URL": "https://b.example"}


@pytest.mark.parametrize("line", ["export TEAM_KEY=zzz", "  export   TEAM_KEY=zzz", "export TEAM_KEY = zzz"])
def test_export_is_a_shell_prefix_and_not_part_of_the_name(line: str):
    """A hand-written `.env` may use shell-style export without changing the variable name."""
    assert env_file.assignments(line) == {"TEAM_KEY": "zzz"}


@pytest.mark.parametrize("quoted", ['"zzz"', "'zzz'"])
def test_one_matched_pair_of_quotes_is_a_wrapper_and_not_content(quoted: str):
    assert env_file.assignments(f"TEAM_KEY={quoted}") == {"TEAM_KEY": "zzz"}


@pytest.mark.parametrize("quoted", ['""', "''"])
def test_empty_quotes_are_empty(quoted: str):
    """The mirror of the export bug: counted as content, this reports a credential we do not hold."""
    assert env_file.assignments(f"TEAM_KEY={quoted}") == {"TEAM_KEY": ""}


def test_an_unmatched_quote_is_content():
    """Guessing past what the file actually says is how a parser invents a value."""
    assert env_file.assignments('TEAM_KEY="zzz') == {"TEAM_KEY": '"zzz'}


def test_comments_and_blanks_and_lines_without_an_assignment_are_skipped():
    text = "\n# TEAM_KEY=commented\n\nnot an assignment\nCTFD_URL=https://b.example\n"

    assert env_file.assignments(text) == {"CTFD_URL": "https://b.example"}


def test_an_inline_hash_stays_in_the_value():
    """Deliberate, and the reason is that the readers disagree: a shell sourcing the file treats it
    as a comment, while `docker run --env-file` — how ADR-0008 injects — keeps it. Taking the
    narrower reading would report a value the container will actually receive as empty."""
    assert env_file.assignments("TEAM_KEY= # note") == {"TEAM_KEY": "# note"}


def test_the_last_assignment_wins():
    """What a shell does, and what `--env-file` does."""
    assert env_file.assignments("TEAM_KEY=first\nTEAM_KEY=second\n") == {"TEAM_KEY": "second"}
