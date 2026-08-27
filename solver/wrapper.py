"""A Board's Flag wrappers, compiled — separately, and that is the whole reason this exists.

A Board states the shape of its Flags, and some Board will state more than one. The obvious way to
carry two shapes in one pattern is an alternation, and it is silently destructive: `finditer` takes
the leftmost match and moves past it, so an alternative that *starts earlier* consumes the bytes a
later one would have matched.

```
flag\\{[^}]{1,256}\\}|FLAG-[0-9a-f]{1,64}   over   b"noise FLAG-abcflag{the_real_flag} more"
  → FLAG-abcf                                      and the real Flag is gone, in silence
```

Worse than not adding the second shape at all — `flag\\{…\\}` alone finds that Flag. So a Board that
states two shapes states **two patterns**, every scan site runs each of them over the same bytes, and
no site is ever handed one pattern built out of several.

Order is the profile's, and the first entry is the Board's primary shape: it is matched first
everywhere, so it wins provenance where two shapes match the same bytes.

A leaf module. It imports nothing from `solver`, which is not tidiness — `solver/profile.py` reaches
`solver/flag.py` through `solver/intake.py`, so the rule cannot live in the profile without a cycle,
and its two readers are `solver/recon.py` and `solver/flag.py`. Two callers that would otherwise each
keep their own copy of a rule is `CODING_STANDARDS.md` §6's own test for a seam.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

# The wording both scan sites already use for a wrapper that will not compile, given one home so
# neither has to re-invent it. A Board that publishes a pattern we cannot compile is a fact about
# the Board, reported as such rather than raised at an Attempt that was about to start.
BROKEN = "the Board's Flag wrapper {wrapper!r} did not compile — {error}"


def compiled(wrappers: Sequence[str]) -> tuple[tuple[re.Pattern[bytes], ...], tuple[str, ...]]:
    """Every wrapper that compiles, in the Board profile's order, and a sentence for each that did not.

    Both are returned because both are facts a scan site has to report: one broken pattern is not a
    reason to stop scanning for the others, and a Board whose patterns all broke has to say so
    rather than answer *no match*.
    """
    matchers: list[re.Pattern[bytes]] = []
    broken: list[str] = []
    for wrapper in wrappers:
        try:
            matchers.append(re.compile(wrapper.encode()))
        except re.error as error:
            broken.append(BROKEN.format(wrapper=wrapper, error=error))
    return tuple(matchers), tuple(broken)


def found_in(text: bytes, matchers: Sequence[re.Pattern[bytes]]) -> Iterator[bytes]:
    """Every match of every wrapper in some bytes, primary shape first.

    One pass per matcher over bytes already in hand — never one matcher over the bytes read twice.
    An artefact scan shares the recon cascade's single deadline, so a re-read per pattern would
    spend a later artefact's budget on bytes this one has already seen.
    """
    for matcher in matchers:
        for match in matcher.finditer(text):
            yield match.group(0)
