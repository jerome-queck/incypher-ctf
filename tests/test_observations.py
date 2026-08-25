"""An Observation is stored whole and shown short, and the two must never be confused.

Two rules from [ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) meet
here and look contradictory until the reader is named. The *record* keeps the body whole, in a file
beside the stream, so the JSONL line stays small and fixed-size. The *model* is shown head and tail
with the middle elided behind a visible marker — silent truncation is a lie it would then reason
over.
"""

from solver.observation import digest_of, elide


def test_a_short_observation_is_shown_exactly_as_it_arrived():
    assert elide(b"total 4\n-rw-r--r-- 1 root root 12 flag.txt\n", limit=200) == (
        "total 4\n-rw-r--r-- 1 root root 12 flag.txt\n"
    )


def test_an_over_long_observation_keeps_its_head_and_its_tail():
    """Both ends carry the signal — a tool names itself at the top and reports its verdict at the
    bottom — so a head-only truncation loses the half that says what happened."""
    body = b"HEAD-MATTERS\n" + b"x" * 5_000 + b"\nTAIL-MATTERS"

    shown = elide(body, limit=400)

    assert shown.startswith("HEAD-MATTERS\n")
    assert shown.endswith("\nTAIL-MATTERS")


def test_the_elision_is_visible_and_says_how_much_is_missing():
    """The whole point. A model reasoning over a truncated listing must be able to see that it is
    truncated, or it concludes the file it was looking for is not there."""
    body = b"a" * 10_000

    shown = elide(body, limit=1_000)

    assert "elided" in shown
    assert "9000" in shown or "9,000" in shown


def test_what_is_shown_stays_within_the_limit_plus_its_marker():
    shown = elide(b"z" * 100_000, limit=1_000)

    assert len(shown) < 1_200


def test_undecodable_bytes_are_shown_rather_than_raising():
    """A carve or a core dump is not text. The model gets a lossy rendering of it; the body file
    keeps the bytes."""
    assert "PNG" in elide(b"\x89PNG\r\n\x1a\n\xff\xfe", limit=200)


def test_the_digest_is_over_the_bytes_it_is_given():
    """Novelty is a rule over digests, so the digest has to be a function of content alone — and
    the caller hands it the *redacted* bytes, which is what stops it being an oracle."""
    assert digest_of(b"same") == digest_of(b"same")
    assert digest_of(b"same") != digest_of(b"different")
    assert len(digest_of(b"same")) == 64
