"""Say which credentials this machine holds, without ever showing one.

`.env` and every overlay are gitignored, correctly — the values must never be tracked. The cost is
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

import sys
from pathlib import Path

import declared_secrets

SET, EMPTY, ABSENT = "set", "empty", "absent"

REPO_ROOT = declared_secrets.REPO_ROOT

# The template is committed and blank by construction. Listing it beside the real files invites the
# reading this command exists to prevent — a template mistaken for an inventory.
TEMPLATE_NAME = ".env.example"


def env_files(directory: Path) -> list[Path]:
    """Every env file in the directory, the default board first and the overlays after it."""
    default = directory / ".env"
    overlays = sorted(path for path in directory.glob(".env.*") if path.name != TEMPLATE_NAME)
    return ([default] if default.exists() else []) + overlays


def read_holdings(path: Path) -> dict[str, str]:
    """Which declared names that file holds — the verdict per name, never the value.

    A commented-out line holds nothing: that is the template's own idiom for "not set here", and
    reading it as a holding would report a credential that does not exist.
    """
    assigned: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        assigned[name.strip()] = SET if value.strip() else EMPTY
    return {name: assigned.get(name, ABSENT) for name in (*declared_secrets.SECRETS, *declared_secrets.NOT_SECRETS)}


def report(directory: Path) -> int:
    """Print the holdings per file. Non-zero when any value is empty rather than merely absent."""
    files = env_files(directory)
    if not files:
        print(f"no env file in {directory} — run `bash scripts/setup-board.sh`")
        return 1

    empties = 0
    for path in files:
        holdings = read_holdings(path)
        print(f"\n{path.name}")
        for name, verdict in sorted(holdings.items()):
            if verdict == ABSENT:
                continue
            empties += verdict == EMPTY
            marker = "  " if verdict == SET else "!!"
            print(f"  {marker} {name}: {verdict}")
        if all(verdict == ABSENT for verdict in holdings.values()):
            print("     nothing declared — it carries only values this repository does not name")

    print(
        f"\n{len(files)} env file(s). Absent is ordinary — an overlay carries only its own board's values."
        + (f"\n{empties} empty value(s): delete the line rather than blanking it." if empties else "")
    )
    return 1 if empties else 0


if __name__ == "__main__":
    sys.exit(report(REPO_ROOT))
