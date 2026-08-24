"""Reading an env file — the one place that decides what a line means.

Two callers parsed this format independently, line for line the same, and both carried the same two
faults: `export NAME=value` became a variable called `export NAME`, and `NAME=""` became two
characters of content. In the credential reporter those are the failure
[#61](https://github.com/jerome-queck/incypher-ctf/issues/61) exists to prevent, once in each
direction — a secret we hold reported absent, and one we do not hold reported set. In
`ctfd_probe.load_env` the first of them would have started the Solver without a credential that was
sitting in the file.

Matching parsers were never the goal; one parser is. What a line means is decidable offline with no
board and no network, which is what makes it worth a seam of its own
([#22](https://github.com/jerome-queck/incypher-ctf/issues/22)).
"""

import re

# `export` is a shell keyword rather than part of the name — `docs/credentials.md` sources overlays
# with `. ./.env.incypher`, so it is idiomatic in exactly the files this reads.
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(value: str) -> str:
    """Strip one matched pair of surrounding quotes, and nothing else.

    An unmatched quote stays in the value: guessing past what the file says is how a parser invents
    a credential. The pair that matters most is the empty one — `NAME=""` is a variable that exists
    and holds nothing, which is the trap `docs/credentials.md` names.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def assignments(text: str) -> dict[str, str]:
    """Every live assignment in the file, name to value, later lines winning as a shell would.

    A commented line assigns nothing — that is the template's own idiom for "not set here", and
    reading it as an assignment would report a credential that does not exist. An inline `#` is
    **kept** in the value, deliberately: a shell sourcing the file would treat it as a comment while
    `docker run --env-file` keeps it, and since ADR-0008 injects with `--env-file`, taking the
    narrower reading would call a value the container really receives empty.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if match := _ASSIGNMENT.match(line):
            found[match.group(1)] = _unquote(match.group(2).strip())
    return found
