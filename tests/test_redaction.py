"""Redaction is exact values and nothing else, and the two failures it must not trade between.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) settles both sides.
Under-redacting leaks a run that gets zipped and pasted into an issue. Over-redacting silently eats
an Observation, which is the worse failure precisely because nothing detects it — so there are no
patterns and no heuristics here, only values the Solver was handed and the two encodings a verbose
HTTP call actually emits.
"""

import base64
import urllib.parse

import declared_secrets
import pytest
from solver.redaction import Redactor

TOKEN = "sk-ant-oat01-" + "z" * 24  # gitleaks:allow - shaped like the real thing, opens nothing


def test_an_exact_value_is_replaced_by_a_marker_naming_the_variable():
    """The marker names the variable and never the value: a post-mortem needs to know *which*
    credential reached an Observation, and knowing that reveals nothing about it."""
    redactor = Redactor({"CTFD_API_TOKEN": TOKEN})

    assert redactor.redact(f"Authorization: Token {TOKEN}") == b"Authorization: Token [redacted:CTFD_API_TOKEN]"


@pytest.mark.parametrize(
    "encode",
    [
        lambda value: base64.b64encode(value.encode()).decode(),
        lambda value: base64.urlsafe_b64encode(value.encode()).decode(),
        lambda value: urllib.parse.quote(value, safe=""),
        lambda value: urllib.parse.quote_plus(value),
    ],
    ids=["base64", "base64-urlsafe", "url-encoded", "url-encoded-plus"],
)
def test_the_encoded_forms_a_verbose_http_call_emits_are_covered(encode):
    """`curl -v` is the standing case: the value never appears on the wire in the form we hold it."""
    redactor = Redactor({"TEAM_KEY": TOKEN})

    assert TOKEN not in redactor.redact(f"POST /x -d {encode(TOKEN)}").decode()


@pytest.mark.parametrize("prefix", ["", "a", "ab", "user:", "solver:"], ids=lambda p: f"after {p!r}")
@pytest.mark.parametrize("suffix", ["", "\n", ":extra"], ids=lambda s: f"before {s!r}")
def test_a_secret_inside_a_larger_base64_blob_is_still_covered(prefix, suffix):
    """The bug this test exists for. Base64 encodes three bytes at a time, so `b64encode(token)`
    appears inside `b64encode("user:" + token)` only when the prefix length divides by three — and
    a Basic-auth header, the case this module's docstring cites, is one where it does not.
    """
    redactor = Redactor({"CTFD_API_TOKEN": TOKEN})
    blob = base64.b64encode(f"{prefix}{TOKEN}{suffix}".encode()).decode()

    survived = redactor.redact(blob).decode().replace("[redacted:CTFD_API_TOKEN]", "")

    neighbours = _encoded(prefix) + _encoded(suffix)
    assert len(survived) <= len(neighbours) + 4, (
        f"what may survive is the neighbours' own encoding and up to two characters at each "
        f"boundary, and {survived!r} is more than that"
    )


def _encoded(text: str) -> str:
    return base64.b64encode(text.encode()).decode().rstrip("=")


def test_an_empty_value_redacts_nothing():
    """`docs/credentials.md` names the trap: an empty value is not an unset one, and `--env-file`
    exports it. A redactor that took `""` as a value would replace the gap between every character
    in the stream — an eaten run rather than a leaked one."""
    redactor = Redactor({"OPENAI_API_KEY": "", "TEAM_KEY": "   "})

    assert redactor.redact("nothing here is a secret") == b"nothing here is a secret"


def test_a_value_shaped_like_a_credential_is_left_alone_unless_it_is_one():
    """The no-heuristics rule, stated as a test. A key-shaped string the Solver was never handed is
    challenge output, and eating it is the failure nothing detects."""
    redactor = Redactor({"CTFD_API_TOKEN": TOKEN})
    challenge_output = "found in the dump: sk-ant-api03-" + "q" * 24  # gitleaks:allow

    assert redactor.redact(challenge_output) == challenge_output.encode()


def test_the_longest_value_wins_where_two_overlap():
    """Two declared values can share a prefix — a key and the same key inside a URL. Redacting the
    shorter one first would leave the tail of the longer one in the clear."""
    redactor = Redactor({"SHORT": "abc123", "LONG": "abc123def456"})

    assert redactor.redact("abc123def456") == b"[redacted:LONG]"


def test_undecodable_bytes_survive_redaction():
    """An Observation is whatever a command wrote, and a binary carve writes bytes that are not
    text. Redacting must not be the step that destroys them."""
    redactor = Redactor({"TEAM_KEY": TOKEN})

    assert redactor.redact(b"\x89PNG\r\n\x1a\n\xff\xfe") == b"\x89PNG\r\n\x1a\n\xff\xfe"


def test_the_declared_set_is_read_from_an_environment_the_solver_was_handed():
    """The redactor is built from the declared names, so a credential added to the set is covered
    without anyone remembering to wire it up here."""
    redactor = Redactor.for_declared_secrets({"CTFD_API_TOKEN": TOKEN, "CTFD_URL": "https://board.example"})

    redacted = redactor.redact(f"GET https://board.example/api Authorization: Token {TOKEN}").decode()

    assert TOKEN not in redacted
    assert "https://board.example" in redacted, "the board URL is the one variable a record must keep readable"


def test_the_redactor_covers_every_secret_the_template_declares():
    """ADR-0010's requirement, aimed at the thing that actually redacts.

    `tests/test_declared_secrets.py` binds the template to the declared *set*; this binds it to the
    redactor built from that set, which is the object a leaked Run would have gone through. The two
    are one import apart today and were two lists before `solver/credentials.py` existed.
    """
    declared = declared_secrets.template_declarations(declared_secrets.REPO_ROOT / ".env.example")
    handed = {name: f"planted-value-for-{name}" for name in declared}

    redacted = Redactor.for_declared_secrets(handed).redact(" ".join(handed.values())).decode()

    for name in declared - set(declared_secrets.NOT_SECRETS):
        assert handed[name] not in redacted, f"{name} is declared in the template and reaches a record in the clear"
