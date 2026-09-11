"""Say which credentials this machine holds, without ever showing one.

`.env` is gitignored, correctly — the values must never be tracked. Legacy overlays are reported
as ambiguous authority that must be migrated, never composed with the active file. The cost is
that the repository cannot answer *"do we have an account on that board?"*, and
[#59](https://github.com/jerome-queck/incypher-ctf/issues/59) is what that costs: a session read the
repository, found nothing saying an IN-CYPHER account existed, and concluded there was none. There
was one, populated, the whole time. Silence is not absence, and this command is the difference.

It reports **set / empty / absent** per declared name, and the middle one is why it exists rather
than being a line in a document. `docs/credentials.md` already names the trap: an empty value is not
an unset one — it occupies its slot in a client's credential search and authenticates with nothing,
and `docker run --env-file` exports it, so the container fails while the file still looks right.

Run it as `python3 scripts/credentials_held.py`.
"""

import enum
import sys
from pathlib import Path

import declared_secrets
import env_file
from solver.env_file import environment_sources


class Holding(enum.Enum):
    """What a file says about one declared name.

    Three states rather than a boolean, because **empty is not absent** and the two want opposite
    fixes: fill this in, versus delete the line, since an empty value shadows a credential that
    would otherwise be found. A type rather than three strings so that the distinction survives
    rendering — bare strings compare `is` only while nothing has formatted them.
    """

    SET = "set"
    EMPTY = "empty"
    ABSENT = "absent"


SET, EMPTY, ABSENT = Holding.SET, Holding.EMPTY, Holding.ABSENT


def env_files(directory: Path) -> list[Path]:
    """The active env file followed by legacy files that must be diagnosed and removed."""
    return list(environment_sources(directory))


def read_holdings(path: Path) -> dict[str, Holding]:
    """Which declared names that file holds — the verdict per name, never the value.

    A commented-out line holds nothing: that is the template's own idiom for "not set here", and
    reading it as a holding would report a credential that does not exist.
    """
    assigned = {
        name: Holding.SET if value.strip() else Holding.EMPTY
        for name, value in env_file.assignments(path.read_text()).items()
    }
    return {
        name: assigned.get(name, Holding.ABSENT) for name in (*declared_secrets.SECRETS, *declared_secrets.NOT_SECRETS)
    }


def render(path: Path, holdings: dict[str, Holding]) -> list[str]:
    """The lines describing one file — every declared name, in all three states.

    Absent names are named too, on one line rather than a column each. Omitting them was the first
    version and it recreated #59 in miniature: a reader who does not already know the declared set
    cannot tell a name that is absent from one nobody asked about, which is the silence this command
    exists to break.
    """
    present = [(name, holding) for name, holding in sorted(holdings.items()) if holding is not Holding.ABSENT]
    absent = [name for name, holding in sorted(holdings.items()) if holding is Holding.ABSENT]
    lines = [path.name] + [
        f"  {'  ' if holding is Holding.SET else '!!'} {name}: {holding.value}" for name, holding in present
    ]
    if absent:
        lines.append(f"     absent: {', '.join(absent)}")
    return lines


def report(directory: Path) -> int:
    """Print holdings; fail on an empty value or any legacy environment authority."""
    files = env_files(directory)
    if not files:
        # Not a failure. A fresh clone holds nothing, which is the correct answer to the question
        # this command asks — and exiting non-zero would make any pre-flight caller read a clean
        # checkout as broken.
        print(f"no env file in {directory} — run `bash scripts/setup-board.sh` to create one")
        return 0

    empties = 0
    legacy = [path.name for path in files if path.name != ".env"]
    for path in files:
        holdings = read_holdings(path)
        empties += sum(holding is Holding.EMPTY for holding in holdings.values())
        print("\n" + "\n".join(render(path, holdings)))

    print(f"\n{len(files)} env file(s). Absent is ordinary for optional active-board values.")
    if legacy:
        print(f"ambiguous authority: migrate and remove {', '.join(legacy)}")
    if empties:
        print(f"{empties} empty value(s): delete the line rather than blanking it.")
    return 1 if empties or legacy else 0


if __name__ == "__main__":
    sys.exit(report(declared_secrets.REPO_ROOT))
