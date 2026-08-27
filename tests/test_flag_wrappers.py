"""A Board may state more than one Flag shape, and what that must never collapse into.

What is on trial is one refusal: the wrappers are never joined into a single alternation. The repo
tells anyone meeting an unfamiliar Board that a wrapper it has never seen *costs a config value*
(`solver/recon.py`, `solver/flag.py`), and with one pattern the only way to spend that config value
on two shapes is `a|b` — which loses Flags the first shape alone would have found. These tests are
that trap, held open.
"""

import re

import pytest
from solver.wrapper import compiled, found_in

BOARD = r"flag\{[^}]{1,256}\}"
SECOND = r"FLAG-[0-9a-f]{1,64}"

# The second shape's match starts *earlier* than the first's, which is the whole hazard: a leftmost
# match consumes the `flag{` that follows it.
OVERLAPPING = b"noise FLAG-abcflag{the_real_flag} more"


def test_an_alternation_would_eat_the_flag_and_that_is_why_there_is_a_list():
    """The measurement this module exists for, kept as a test so nobody re-introduces the join.

    Not a test of our code — a test of `re`, deliberately. It is the reason the interface takes a
    sequence, and a reader who wonders why a plain `"|".join(...)` would not do reads this.
    """
    joined = re.compile((BOARD + "|" + SECOND).encode())

    assert joined.findall(OVERLAPPING) == [b"FLAG-abcf"], "if this changed, re-read this module"
    assert re.compile(BOARD.encode()).findall(OVERLAPPING) == [b"flag{the_real_flag}"]


def test_every_wrapper_is_matched_over_the_same_bytes_and_none_eats_another():
    matchers, broken = compiled([BOARD, SECOND])

    assert broken == ()
    assert list(found_in(OVERLAPPING, matchers)) == [b"flag{the_real_flag}", b"FLAG-abcf"]


def test_the_first_wrapper_is_the_primary_shape_and_is_matched_first():
    """Order is the profile's, and it decides provenance where two shapes match the same bytes."""
    both = b"flag{inner} and FLAG-abc"

    primary, _ = compiled([BOARD, SECOND])
    reversed_order, _ = compiled([SECOND, BOARD])

    assert list(found_in(both, primary))[0] == b"flag{inner}"
    assert list(found_in(both, reversed_order))[0] == b"FLAG-abc"


def test_one_wrapper_that_does_not_compile_costs_only_itself():
    """A Board that publishes a pattern we cannot compile is a fact about the Board. Scanning stops
    for that shape and for no other — a Run that gave up on every wrapper because one was malformed
    would be a Run that found nothing for a reason nobody chose."""
    matchers, broken = compiled([BOARD, "flag{[", SECOND])

    assert [one.pattern for one in matchers] == [BOARD.encode(), SECOND.encode()]
    assert len(broken) == 1
    assert "flag{[" in broken[0] and "did not compile" in broken[0]


def test_a_wrapper_that_matches_nothing_is_not_an_error():
    matchers, broken = compiled([BOARD])

    assert broken == ()
    assert list(found_in(b"nothing here at all", matchers)) == []


@pytest.mark.parametrize("stated", [[], ()])
def test_no_wrapper_at_all_compiles_to_nothing_rather_than_raising(stated):
    """The refusal belongs at the boot check, which reads the profile and can name the file. This
    is a compile step and answers about what it was handed."""
    assert compiled(stated) == ((), ())
