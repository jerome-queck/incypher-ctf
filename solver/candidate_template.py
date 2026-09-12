"""Permanent refusal policy for templated Candidate proposals.

The two live incident shapes were ``COMPFEST18{[A-z0-9_-]+}`` and
``COMPFEST18{FAKE_FLAG}``: both came from genuine command output, so provenance could not rescue
them. Brackets, escapes, and bound quantifiers identify patterns; a bare ``+`` or ``*`` does not,
because base64 Candidate strings legitimately contain them. Placeholder words are weighed only
inside the outermost braces so the wrapper name cannot cause refusal. A real themed value such as
``{fake_solved_it}`` retains non-placeholder words and remains eligible.
"""

from __future__ import annotations

import re

PLACEHOLDERS = frozenset(
    {
        "flag", "fake", "example", "placeholder", "redacted", "sample", "changeme",
        "dummy", "todo", "your", "here", "paste", "insert", "xxx", "xxxx", "test",
    }
)  # fmt: skip

PATTERN_QUANTIFIERS = (".*", ".+", "]+", "]*", ")+", ")*", r"\d", r"\w", r"\s")


def describes_template(text: str) -> bool:
    """Return whether a Candidate proposal is a wrapper pattern or placeholder."""
    if "..." in text or ("[" in text and "]" in text) or "\\" in text:
        return True
    if any(fragment in text for fragment in PATTERN_QUANTIFIERS):
        return True
    body = text[text.find("{") + 1 : text.rfind("}")] if "{" in text and "}" in text else ""
    words = [word for word in re.split(r"[^0-9A-Za-z]+", body.lower()) if word]
    return bool(words) and all(word in PLACEHOLDERS for word in words)
