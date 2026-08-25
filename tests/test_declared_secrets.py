"""The declared set is one list, and the template cannot be counted two ways.

[ADR-0010](../docs/adr/0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)
makes the redactor's coverage a failing check rather than a leaked run — but only as strongly as
the phrase *"every secret variable in `.env.example`"* is unambiguous, and it is not. Grepping
`^[A-Z_]+=` finds four names; counting the commented-out overlay-only keys finds six; and neither
is the answer, because `CTFD_URL` is a variable and not a secret. These tests bind the template to
the one list that settles it.
"""

import declared_secrets
import pytest

TEMPLATE = declared_secrets.REPO_ROOT / ".env.example"


def test_every_name_the_template_declares_is_classified():
    """The drift this exists to catch: a credential added to the template and nowhere else.

    Subset rather than equality, and in this direction only — the declared set may cover *more*
    than the template does, because over-redacting costs a mangled Observation while
    under-redacting costs a leaked run.
    """
    unclassified = declared_secrets.template_declarations(TEMPLATE) - (
        set(declared_secrets.SECRETS) | set(declared_secrets.NOT_SECRETS)
    )

    assert not unclassified, f"declared in .env.example but neither secret nor not: {sorted(unclassified)}"


def test_a_name_is_never_both_a_secret_and_not_one():
    assert not set(declared_secrets.SECRETS) & set(declared_secrets.NOT_SECRETS)


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"])
def test_the_commented_out_overlay_keys_are_found(name: str):
    """The whole bug. Both are commented out in the template because an empty value is not an unset
    one, and the obvious parser therefore misses exactly the two keys that cost money."""
    assert name in declared_secrets.template_declarations(TEMPLATE)
    assert name in declared_secrets.SECRETS


def test_prose_naming_a_variable_is_not_a_declaration(tmp_path):
    """The template explains itself in comments, and several of them name variables. Only a
    commented-out assignment is a declaration; a sentence mentioning a name is not."""
    template = tmp_path / ".env.example"
    template.write_text(
        "# Use ANTHROPIC_AUTH_TOKEN when it calls the Messages API directly.\n"
        "# `--with-access-token` is Enterprise-only, so CODEX_ACCESS_TOKEN goes nowhere.\n"
        "# REAL_DECLARATION=\n"
        "ALSO_REAL=\n"
    )

    assert declared_secrets.template_declarations(template) == {"REAL_DECLARATION", "ALSO_REAL"}


def test_the_url_is_declared_and_is_not_a_secret():
    """`CTFD_URL` decides which competition the Solver enters, so it is the one value that must
    stay *readable* in a record — redacting it would hide the guard against playing the wrong
    board."""
    assert "CTFD_URL" in declared_secrets.NOT_SECRETS
    assert "CTFD_URL" not in declared_secrets.SECRETS


def test_a_credential_named_only_in_prose_is_still_covered():
    """`ANTHROPIC_AUTH_TOKEN` is the standing case for the subset rule, and the template-to-set test
    above cannot guard it: the template names it in a sentence rather than declaring it, so deleting
    it from `SECRETS` would break nothing. The redactor may cover more than the template; this is
    what stops "more" from quietly becoming "the same"."""
    assert "ANTHROPIC_AUTH_TOKEN" in declared_secrets.SECRETS
    assert "ANTHROPIC_AUTH_TOKEN" not in declared_secrets.template_declarations(TEMPLATE)
