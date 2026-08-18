# Assert that no commit in a range added a credential.
#
#   sh conformance/check-secrets.sh <range>     e.g. sh …/check-secrets.sh origin/main..HEAD
#
# It exists because "never commit a token" was, until now, a sentence in `AGENTS.md` with nothing
# behind it on a private repository. Public repositories get GitHub's own secret scanning and push
# protection free through the Public baseline; on private ones both are paid, the Organisation is
# not licensing them, and so a scanner in this workflow is the only control there is (ADR-0036).
#
# **It fires after the push.** By the time this fails, the credential has already been sent to
# GitHub's servers and is in a log, an API response and any clone taken since. Rotating it is
# therefore not the tidy-up after the fix — it *is* the fix, and rewriting the branch without
# rotating leaves a live credential in someone else's hands. That is precisely what the paid push
# protection would have stopped, and accepting this limitation is what buying it would have cost.
#
# The detection itself is gitleaks, pinned and installed by `install-gitleaks.sh` beside this
# file: the rules are maintained by people who watch credential formats for a living, which is not
# a thing this Organisation should be hand-writing regexes for.
#
# Through the interpreter, always, and never `./` — the Contents API that writes this file into the
# hub commits a plain blob and cannot set the executable bit. Same as the checks beside it.
#
# Read-only. Findings are printed redacted, because this output lands in an Actions log that a
# public repository shows the world, and a check that reprinted the secret would publish it a
# second time. Exits 1 on a finding, 2 when it cannot run.
set -eu

# Spelled out rather than `${1:?…}`, which exits 1 in some shells and 2 in others — and 1 is the
# status this script reserves for a commit that genuinely carries a credential, the same split
# every check beside it makes.
if [ $# -ne 1 ]; then
  printf 'usage: sh conformance/check-secrets.sh <range>\n' >&2
  exit 2
fi

range=$1

# `$GITLEAKS` first, so a caller that installed the pinned build into a directory of its own can
# name it without putting it on `PATH`. An absent scanner is a usage error, never a pass: a check
# that quietly exits 0 because its tool is missing is the silent green this whole effort exists to
# remove.
gitleaks=${GITLEAKS:-gitleaks}

command -v "$gitleaks" >/dev/null 2>&1 || {
  printf 'No gitleaks on PATH and none named by $GITLEAKS.\n' >&2
  printf 'Install the pinned build first: sh conformance/install-gitleaks.sh <directory>\n' >&2
  exit 2
}

# The range is resolved here rather than left to the scanner, because the scanner's answer to a
# range it cannot resolve is `no leaks found` and exit 0 — it logs git's error and scans zero
# commits. A check that reports a mistyped base as a clean branch is worse than no check, so an
# unresolvable range is a usage error and says so.
git rev-list "$range" >/dev/null 2>&1 || {
  printf 'Cannot resolve %s in this repository.\n' "$range" >&2
  printf 'Nothing was scanned, so nothing about it is known.\n' >&2
  exit 2
}

report=$(mktemp)

remove_the_report() {
  rm -f "$report"
}
trap remove_the_report EXIT INT TERM

# `git` rather than `dir`, so what is scanned is exactly the commits the range names — the pull
# request's own history and not the tree it happens to produce. A credential added and then
# deleted in a later commit of the same branch is still on GitHub's servers, and a scan of the
# final tree would report the branch as clean.
#
# `--redact` and `-v` together: verbose is what prints a finding at all, and redact is what keeps
# the secret out of the line it prints. The report beside it is not for the reader — it is how the
# two ways of exiting non-zero are told apart below.
if "$gitleaks" git . --log-opts="$range" --no-banner --no-color --redact -v \
  --report-format json --report-path "$report"; then
  printf 'ok    no credential in %s\n' "$range"
  exit 0
fi

# gitleaks exits 1 for a finding *and* for its own failures — an unresolvable range, a repository
# it cannot read. Taken at face value, a mistyped base would print the whole burned-credential
# response and send someone rotating a token nobody committed. So the report decides: findings in
# it means a credential, an empty one means the scan never happened.
if ! grep -q '"RuleID"' "$report" 2>/dev/null; then
  printf '\ngitleaks could not scan %s, and reported no findings.\n' "$range" >&2
  printf 'This is the scanner failing, not a credential. Check the range resolves here.\n' >&2
  exit 2
fi

# The whole response, not a pointer to it. A contributor reading this has a burned credential and
# minutes that matter; a failure that says "see SECURITY.md" spends them on an errand (ADR-0032).
cat >&2 <<'RULE'

A credential was added by a commit in this pull request.

Treat it as burned. This check runs after the push, so it is already on GitHub's servers, in the
Actions log, and in every clone taken since — a force-push removes it from the branch and from
nowhere else.

  1. Rotate or revoke it now, at whatever issued it. This is the fix; the rest is tidying up.
  2. Take it out of the code. Read it from the environment, or from a repository secret.
  3. Rewrite the branch so the commit no longer carries it, and force-push.

If this is not a credential — a fixture, a documented example, a string that merely looks like
one — say so where it lives, on the line itself:

    token = "…"  # gitleaks:allow

That is an assertion that the value is worthless, so make it worthless: a planted test credential
belongs to nobody and opens nothing.

Why this check exists after the push rather than before it is in ADR-0036, and where to report a
credential you found rather than wrote is in SECURITY.md.
RULE

exit 1
