# Read a live pull request and the issues it closes, and write them into a case directory for
# `check-issue-linkage.sh` beside this file to judge.
#
#   sh conformance/resolve-issue-linkage.sh <owner/repo> <pull-request-number> <case>
#
# It writes, and writes nothing else:
#
#   <case>/pull-request-body.md
#   <case>/pull-request-author            the login that opened it
#   <case>/issues/<number>.md
#   <case>/issues/<number>.labels         one label per line
#
# Split from the check for one reason: this half cannot be exercised without GitHub and the other
# half must be exercised without it (ADR-0035). Everything that decides whether a pull request
# conforms lives next door, where fixtures can drive it; everything here is fetching, and the
# whole of it is one query.
#
# GraphQL rather than the REST issue timeline, because `closingIssuesReferences` is GitHub's own
# answer to "what does this pull request close" — it is what the merge button acts on. Deriving it
# by grepping the body for a keyword would be a second implementation of a rule GitHub already
# owns, and would disagree with the sidebar the moment somebody linked an issue by hand.
#
# Through the interpreter, always, and never `./` — the Contents API that writes this file into
# the hub commits a plain blob and cannot set the executable bit. Same as the two scripts beside
# it.
#
# Needs `gh` signed in, or `GH_TOKEN` set, with read access to issues and pull requests. Exit 2
# when the arguments are wrong or the query fails: this script makes no conformance findings, so
# it has no use for the status that reports one.
set -eu

if [ $# -ne 3 ]; then
  printf 'usage: sh conformance/resolve-issue-linkage.sh <owner/repo> <pull-request-number> <case>\n' >&2
  exit 2
fi

repository=$1
number=$2
case_dir=$3

owner=${repository%%/*}
name=${repository#*/}

if [ -z "$owner" ] || [ -z "$name" ] || [ "$owner" = "$repository" ]; then
  printf 'Not an owner/repo: %s\n' "$repository" >&2
  exit 2
fi

# The issue directory is emptied rather than added to. A runner gets a fresh workspace and would
# never notice, but a second local run against a reused case would leave the previous pull
# request's issues sitting there — and the checker reads every file it finds as a linked issue, so
# the answer would be about two pull requests at once. Only the directory this script writes is
# removed; the case directory itself may be somewhere the caller cares about.
rm -rf "$case_dir/issues"
mkdir -p "$case_dir/issues"

# `first: 20` on the references and `first: 50` on the labels: both are ceilings no pull request
# here approaches, and a page-walking loop for a case that does not exist would be more code than
# the check it feeds. A pull request that ever exceeded them would silently check less than it
# should rather than fail, which is why that is ADR-0035's first revisit clause.
linkage=$(gh api graphql \
  -f owner="$owner" -f name="$name" -F number="$number" \
  -f query='
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          body
          author { login }
          closingIssuesReferences(first: 20) {
            nodes {
              number
              body
              labels(first: 50) { nodes { name } }
            }
          }
        }
      }
    }') || {
  printf 'Could not read %s#%s. The token needs read access to issues and pull requests.\n' \
    "$repository" "$number" >&2
  exit 2
}

# A pull request that is not there comes back as a null rather than as an error, and a null body
# written to disk is a case the checker would judge on its merits — it would report a missing
# drift block for a pull request nobody could read. Caught here, where the cause is still known.
printf '%s' "$linkage" | jq -e '.data.repository.pullRequest != null' >/dev/null || {
  printf 'No pull request %s#%s.\n' "$repository" "$number" >&2
  exit 2
}

printf '%s' "$linkage" |
  jq -r '.data.repository.pullRequest.body // ""' >"$case_dir/pull-request-body.md"

# Who opened it, because a bot cannot open an issue and the check has to know that before it holds
# a pull request to one. `author` is null for a deleted account, which is written out as the empty
# string and is on no exemption list — the right answer for a pull request nobody can be asked
# about.
printf '%s' "$linkage" |
  jq -r '.data.repository.pullRequest.author.login // ""' >"$case_dir/pull-request-author"

# One file per issue, named for its number, because that is how the checker finds them: the
# absence of any is what it reads as "this pull request closes no issue".
printf '%s' "$linkage" |
  jq -r '.data.repository.pullRequest.closingIssuesReferences.nodes[].number' |
  while IFS= read -r issue; do
    [ -n "$issue" ] || continue

    printf '%s' "$linkage" |
      jq -r --argjson issue "$issue" \
        '.data.repository.pullRequest.closingIssuesReferences.nodes[]
         | select(.number == $issue) | .body // ""' >"$case_dir/issues/$issue.md"

    printf '%s' "$linkage" |
      jq -r --argjson issue "$issue" \
        '.data.repository.pullRequest.closingIssuesReferences.nodes[]
         | select(.number == $issue) | .labels.nodes[].name' >"$case_dir/issues/$issue.labels"
  done

printf 'ok    resolved %s#%s into %s\n' "$repository" "$number" "$case_dir"
