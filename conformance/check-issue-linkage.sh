# Read a pull request against the issue it came from, and assert the three things the Organisation
# promises about that pair: the acceptance boxes are ticked or explained, the drift block is
# present, and the linked issue carries a category and a state. Over-delivery is annotated and
# never blocked.
#
#   sh conformance/check-issue-linkage.sh <case>
#
# The `<case>` is a directory of facts already read off GitHub — the pull-request body, and one
# body and one label list per linked issue — laid out by `resolve-issue-linkage.sh` beside this
# file:
#
#   <case>/pull-request-body.md
#   <case>/pull-request-author            the login that opened it
#   <case>/issues/<number>.md
#   <case>/issues/<number>.labels         one label per line
#
# Resolution and judgement are two scripts because only one of them can be exercised offline
# (ADR-0035). Everything below reads that directory and nothing else, which is what lets the
# fixtures in the management hub drive every failing path — the same trade that keeps the
# conformance checker's input a tree.
#
# Through the interpreter, always, and never `./`: this file reaches the repository it is served
# from through the Contents API, which writes a plain blob and has no way to set the executable
# bit. Same as the two scripts beside it.
#
# It prints one line per rule, and the failing line names the rule first, so a caller can tell
# *which* rule went red. Exit 0 when the pair conforms, 1 when it does not, 2 when the arguments
# are wrong — a usage mistake is not a conformance finding.
set -eu

# Spelled out rather than `${1:?…}`, which exits 1 in some shells and 2 in others — and 1 is the
# status this script reserves for a pull request that genuinely breaks a rule.
if [ $# -ne 1 ]; then
  printf 'usage: sh conformance/check-issue-linkage.sh <case>\n' >&2
  exit 2
fi

case_dir=$1
body="$case_dir/pull-request-body.md"

[ -d "$case_dir" ] || {
  printf 'No such case: %s\n' "$case_dir" >&2
  exit 2
}

[ -f "$body" ] || {
  printf 'No pull-request body in the case: %s\n' "$body" >&2
  exit 2
}

# Rules are named, not numbered: the name is what a failing run prints, what the fixture runner
# asserts against, and what someone greps for. A renamed rule is therefore a visible change.
linked_rule='the-pull-request-links-an-issue'
boxes_rule='acceptance-boxes-are-ticked-or-explained'
drift_rule='the-drift-block-is-present'
labels_rule='the-linked-issue-carries-a-category-and-a-state'
over_delivery_rule='over-delivery-is-annotated-never-blocked'

# The label vocabulary, written here rather than read from `terraform/labels.json`, which lives in
# the management hub and is not served with this script. That makes it a second copy of a closed
# set (ADR-0028), so `bin/test-conformance` holds the two equal — a label added to Terraform and
# forgotten here would otherwise be created everywhere and recognised nowhere.
#
# The categories are the three form names (ADR-0027). A `wayfinder:<type>` counts as a category
# too and is matched by prefix, because that is the rule `docs/agents/triage-labels.md` states:
# the wayfinding axis is a category, not a third axis, so the rule has no exemption clause.
states='needs-triage needs-info ready-for-agent ready-for-human wontfix'
categories='task decision bug'
wayfinder_prefix='wayfinder:'

# Authors whose pull requests cannot carry a linked issue, because they cannot open one.
# Dependabot's pull requests auto-merge (ADR-0010), so every rule below would hold every dependency
# update in the Organisation on a required check that no human could satisfy — the merge does not
# go red, it sits pending forever. The same list, for the same reason, as `check-trailers.sh`.
#
# Matched on the login GitHub gives the bot, which a human account does not wear. Both spellings,
# because GitHub has two: GraphQL's `Bot` node calls it `dependabot` and REST's `user.login` calls
# it `dependabot[bot]`, and the same bot answering to two names is the sort of thing a check
# written against one API and read against the other gets silently wrong. The resolver beside this
# file asks GraphQL, so the first is the live answer — the second is there so that a caller who
# built the case from REST is not quietly unprotected.
bot_authors='dependabot dependabot[bot]'

# A case that names no author is a human one. That is every fixture but one, and it is what the
# resolver wrote before this rule existed, so the absent file is the ordinary answer rather than a
# missing input.
author=$(cat "$case_dir/pull-request-author" 2>/dev/null || true)

for bot in $bot_authors; do
  if [ "$author" = "$bot" ]; then
    # Reported as skipped, never as `ok` — the convention `check-conformance.sh` keeps for the one
    # rule it can decline to run. "Asked and answered" and "not asked" are different facts, and a
    # run that printed the same word for both would let a rule quietly stop running without the
    # output changing.
    for rule in "$linked_rule" "$boxes_rule" "$drift_rule" "$labels_rule" "$over_delivery_rule"; do
      printf 'skip  %s  %s opened this; a bot cannot open an issue to link\n' "$rule" "$author"
    done
    exit 0
  fi
done

failures=0

fail() {
  rule=$1
  detail=$2
  printf 'FAIL  %s  %s\n' "$rule" "$detail"
  failures=$((failures + 1))
}

# The pull-request body as one line per statement. Every rule below asks whether the body *says*
# something, and the answer must not depend on where the author's editor wrapped: the not-doing
# line in `docs/agents/acceptance-criteria.md` is a sentence long and wraps in every real example
# of it. So a blank line ends a block and a wrapped continuation does not, and a leading `>` is
# dropped, because the documented shape is a blockquote and the quote marker is presentation.
#
# A drift-block marker also ends the block it follows, because the drift block is a list of
# statements and nothing obliges an author to put blank lines between them — joined, two
# over-deliveries would arrive as one annotation and a shortfall would arrive glued to the verdict
# above it. Matched with `index(…) == 1` rather than as a regular expression: `⬜` and `➕` are
# multi-byte, and a bracket expression over them is a set of bytes wherever the locale is C.
pull_request_blocks() {
  awk '
    function starts_a_statement(text) {
      return index(text, "⬜") == 1 || index(text, "➕") == 1 ||
        text ~ /^\*\*(Not doing|Not delivered|#[0-9])/
    }
    {
      sub(/\r$/, "")
      line = $0
      sub(/^[[:space:]]*>[[:space:]]?/, "", line)
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
    }
    line == "" { if (block != "") print block; block = ""; next }
    starts_a_statement(line) && block != "" { print block; block = "" }
    { block = (block == "" ? line : block " " line) }
    END { if (block != "") print block }
  ' "$body"
}

blocks=$(pull_request_blocks)

# Text reduced to what two writers would agree on: case, the Markdown that decorates a criterion,
# the typography an editor substitutes, and runs of whitespace, all removed. A criterion quoted in
# a pull request is quoted *as Markdown* — the author wraps it in bold and keeps its backticks —
# so comparing the raw strings would reject the correctly-written line and accept nothing.
#
# The curly quotes are replaced one at a time rather than through a bracket expression, because a
# bracket expression over multi-byte characters is a set of *bytes* wherever the locale is C, and
# replacing one byte of a three-byte character mangles the line instead of normalising it.
normalised() {
  printf '%s' "$1" |
    sed 's/“/"/g; s/”/"/g' |
    sed "s/‘/'/g; s/’/'/g" |
    tr 'A-Z' 'a-z' |
    tr -d '`*_"' |
    tr '\t' ' ' |
    tr -s ' ' |
    sed 's/^ //; s/ $//'
}

# A block that names this issue, as a test rather than a `case` pattern: `#1` is a prefix of `#12`,
# and a glob has no way to say "and no digit after it". A pull request closing a low-numbered issue
# whose body happened to mention a longer one would otherwise be read against the wrong ticket, and
# would pass every rule on the strength of somebody else's report.
#
# Only the trailing side needs anchoring. The `#` anchors the leading side already: `#23` is not a
# substring of `#123`.
names_the_issue() {
  printf '%s\n' "$1" | grep -qE "#$2([^0-9]|\$)"
}

# The issues this pull request links, by number, taken from the case the resolver laid down. A
# case with no issue files is a pull request GitHub reports no closing reference for, which is the
# `linked_rule` failure below rather than an empty run.
linked_issues() {
  for issue in "$case_dir"/issues/*.md; do
    [ -f "$issue" ] || continue
    name=${issue##*/}
    printf '%s\n' "${name%.md}"
  done
}

# The checkboxes under the issue's **Acceptance criteria** heading, as they are written. The
# heading ends the search as well as starting it: the next heading of any level closes the
# section, so the **Blocked by** list that follows it on our own tickets is not read as criteria.
#
# Fenced blocks are skipped for the reason the conformance checker skips them — a ticket that
# quotes a checklist inside a fence is showing a shape, not agreeing to one.
acceptance_criteria() {
  awk '
    { sub(/\r$/, "") }
    /^[[:space:]]*```/ || /^[[:space:]]*~~~/ { fenced = 1 - fenced; next }
    fenced { next }
    /^#+[[:space:]]/ {
      inside = (tolower($0) ~ /^#+[[:space:]]+acceptance criteria[[:space:]]*$/)
      next
    }
    inside && /^[[:space:]]*[-*][[:space:]]+\[[ xX]\][[:space:]]/ { print }
  ' "$1"
}

# The blocks that could be declaring an exception. `Not doing` is the shape §2 of
# `docs/agents/acceptance-criteria.md` documents; `Not delivered` is the drift block's line for the
# same fact in §3, and it is accepted too. The two are separate obligations in prose and they
# carry identical substance — issue, criterion, reason — so a check that recognised only the first
# would go red on a pull request that had said the whole thing, in the Organisation's own idiom,
# in the paragraph immediately below. What is held here is the substance.
not_doing_blocks=$(printf '%s\n' "$blocks" | grep -iE 'not doing|not delivered' || true)

# The first eight words of a criterion, or the whole of it if it is shorter. A quote is frequently
# trimmed — the drift block quotes the clause that was not met rather than a criterion whose second
# half says "or …" — and a rule that demanded the whole string would reject the line that had
# explained the shortfall precisely.
#
# Words rather than a character count, because a count cuts *bytes*: criteria here carry em dashes
# and curly quotes routinely, and a cut through the middle of one produces a needle that matches
# nothing. Splitting on the spaces this text has already been collapsed onto cannot land inside a
# character.
criterion_opening() {
  printf '%s' "$1" | cut -d' ' -f1-8
}

# Issue, criterion and reason — all three, which is what makes the line readable by someone who
# has not opened the issue, and what stops `Not doing — see above` counting as an explanation.
#
# The reason is measured as what remains after the criterion, in words. A count rather than a
# pattern, because a reason has no shape; four is low enough that no genuine sentence trips it and
# high enough that a shrug does. The whole criterion is tried before its opening, so that a line
# quoting the criterion in full has its reason measured exactly; a line that trimmed the quote has
# the trimmed tail counted as part of its reason, which is the price of accepting the trim.
criterion_is_explained() {
  explaining=$1
  # Not `criterion`, which is the caller's loop variable: a function that quietly overwrote it
  # would work only for as long as the loop happened to reassign it before reading it again.
  wanted_text=$(normalised "$2")
  explained=no

  while IFS= read -r block; do
    [ -n "$block" ] || continue
    names_the_issue "$block" "$explaining" || continue

    said=$(normalised "$block")

    for wanted in "$wanted_text" "$(criterion_opening "$wanted_text")"; do
      case $said in
        *"$wanted"*) ;;
        *) continue ;;
      esac

      reason=${said##*"$wanted"}
      if [ "$(printf '%s' "$reason" | wc -w)" -ge 4 ]; then
        explained=yes
        break
      fi
    done
  done <<EOF
$not_doing_blocks
EOF

  [ "$explained" = yes ]
}

# Strict on the boxes and generous about the exception, which is the whole design (ADR-0029). A
# gate that accepted only ticked boxes would make a false tick the cheapest way past a red check,
# and the tick destroys the one thing the boxes exist to carry — the knowledge that a criterion
# could not be met.
check_acceptance_boxes_are_ticked_or_explained() {
  number=$1
  criteria=$(acceptance_criteria "$case_dir/issues/$number.md")

  while IFS= read -r criterion; do
    [ -n "$criterion" ] || continue

    # The marker is read from where a marker can be — after the bullet — rather than looked for
    # anywhere in the line. A criterion whose own text mentions `[x]`, which a ticket about
    # checkboxes will, would otherwise be read as ticked and never checked at all.
    # Named rather than captured, because the unticked marker is a space and a command
    # substitution strips it — the box would come back empty and read as neither.
    box=$(printf '%s' "$criterion" |
      sed -n 's/^[[:space:]]*[-*][[:space:]]*\[[xX]\].*/ticked/p
              s/^[[:space:]]*[-*][[:space:]]*\[ \].*/unticked/p')
    [ "$box" = unticked ] || continue

    # The text of the box without its marker: that is what a not-doing line quotes.
    text=$(printf '%s' "$criterion" | sed 's/^[[:space:]]*[-*][[:space:]]*\[[ xX]\][[:space:]]*//')

    criterion_is_explained "$number" "$text" ||
      fail "$boxes_rule" "#$number, \"$text\" is unticked and no not-doing line explains it"
  done <<EOF
$criteria
EOF
}

# The verdict line, which is the part of the drift block that cannot be omitted: it is what the
# Owner acts on without reading the rest, and it is present even when there is nothing to report
# (`docs/agents/acceptance-criteria.md` §3). `N of M criteria delivered` is the shape both the
# full form and the collapsed one carry, and a fixed shape is the point — a block that appears
# only when an agent judges it interesting cannot be read as evidence when it is missing.
check_the_drift_block_is_present() {
  number=$1

  printf '%s\n' "$blocks" | grep -E "#$number([^0-9]|\$)" |
    grep -qE '[0-9]+ of [0-9]+ criteria delivered' ||
    fail "$drift_rule" "no drift block in the pull-request body for #$number"
}

# One state and one category, exactly, which is the rule the vocabulary was declared with and
# until now only documented. The two halves are reported separately, because "untriaged" and
# "triaged onto two categories" are different mistakes with different fixes.
#
# Each membership test is an `if` rather than a `[ … ] && …`, so that the loop's status is not the
# status of its last comparison. Under `set -e` a final label that matched nothing would otherwise
# end the script mid-rule, silently, with everything so far reported as passing.
check_the_linked_issue_carries_a_category_and_a_state() {
  number=$1
  carried=$(cat "$case_dir/issues/$number.labels" 2>/dev/null || true)

  carried_states=0
  carried_categories=0

  while IFS= read -r label; do
    [ -n "$label" ] || continue

    for state in $states; do
      if [ "$label" = "$state" ]; then
        carried_states=$((carried_states + 1))
      fi
    done

    for category in $categories; do
      if [ "$label" = "$category" ]; then
        carried_categories=$((carried_categories + 1))
      fi
    done

    case $label in
      "$wayfinder_prefix"*) carried_categories=$((carried_categories + 1)) ;;
    esac
  done <<EOF
$carried
EOF

  [ "$carried_states" -eq 1 ] ||
    fail "$labels_rule" "#$number carries $carried_states state labels, not exactly one"

  [ "$carried_categories" -eq 1 ] ||
    fail "$labels_rule" "#$number carries $carried_categories category labels, not exactly one"
}

# An annotation, never a failure. Whether an extra was worth building is a judgement for the
# Owner, and a judgement call is a bad thing to make a merge gate — the response to over-delivery
# is a conversation, not a rejection (ADR-0029).
#
# `::notice` is the GitHub Actions workflow command for exactly this: it surfaces on the pull
# request without touching the job's status. `%` is escaped because the runner reads it as the
# start of an escape sequence; the block is already one line, so nothing else needs to be.
over_delivered=0

annotate_over_delivery() {
  mentions=$(printf '%s\n' "$blocks" | grep -i 'beyond the brief' || true)

  while IFS= read -r block; do
    [ -n "$block" ] || continue

    said=$(normalised "$block")
    case $said in
      *'nothing beyond the brief'*) continue ;;
    esac

    # A section that says "none" is an empty one, however it is punctuated. Reduced to its letters
    # so that `— none.`, `none` and `**None**` are the one answer they are meant to be; anything
    # with a sentence in it is something the Owner has not agreed to yet.
    said_after=$(printf '%s' "${said##*beyond the brief}" | tr -cd 'a-z')
    case $said_after in
      '' | none) continue ;;
    esac

    escaped=$(printf '%s' "$block" | sed 's/%/%25/g')
    printf '::notice title=Beyond the brief::%s\n' "$escaped"
    over_delivered=$((over_delivered + 1))
  done <<EOF
$mentions
EOF
}

# The whole run when the linkage itself is missing, because there is nothing else to check and the
# fix is one line in the body. Everything below depends on there being an issue, so this exits
# rather than falling through into three rules that would each report the absence again.
issues=$(linked_issues)

if [ -z "$issues" ]; then
  fail "$linked_rule" 'this pull request closes no issue'

  cat >&2 <<'RULE'

Work here starts as an issue and lands as a pull request that closes it — CONTRIBUTING.md, "How
work flows". The link is what lets this check read the pull request against what was agreed, and
GitHub only makes it when the body carries a closing keyword and the number:

    Closes #123

The pull-request template already has that line waiting under the summary. A reference without a
keyword — "see #123" — is a mention, not a link, and does not count.
RULE

  printf '\n1 conformance violation in %s.\n' "$case_dir" >&2
  exit 1
fi

while IFS= read -r number; do
  [ -n "$number" ] || continue

  check_acceptance_boxes_are_ticked_or_explained "$number"
  check_the_drift_block_is_present "$number"
  check_the_linked_issue_carries_a_category_and_a_state "$number"
done <<EOF
$issues
EOF

# Before the exit below, so that an over-delivery on a pull request that also broke a rule is
# still surfaced. The annotation is not a finding, and its presence says nothing about the status.
annotate_over_delivery

if [ "$failures" -gt 0 ]; then
  # The rule is taught where it fails, in full, for the reason ADR-0032 gives: the failure is the
  # one moment the rule is wanted, and a message that says "see the docs" spends it on an errand.
  printf '\n%d conformance violation(s) in %s.\n\n' "$failures" "$case_dir" >&2

  cat >&2 <<'RULE'
A pull request is read against the issue it came from. Three things have to hold.

Every acceptance box on the linked issue is ticked, or the body carries a not-doing line naming
the issue, the criterion and the reason:

    **Not doing — #40, "`bin/audit` asserts the ruleset's bypass list":** the provider does not
    read bypasses back, so there is nothing to assert against without a raw API call, which is
    its own ticket.

The drift block's own `Not delivered` line counts as one, when it carries all three. Quote the
clause you missed rather than the whole box if that is what you mean.

Never tick a box you did not deliver. The unmet criterion is usually the most valuable thing a
session produces, and a tick destroys it in the one place a reader would look (ADR-0029).

The drift block is in the body, leads with its verdict, and is present even when there is nothing
to report:

    **#40 — Audit the branch-protection baseline: 7 of 7 criteria delivered, nothing beyond the
    brief.**

And the linked issue carries exactly one state label and exactly one category label. The states
are needs-triage, needs-info, ready-for-agent, ready-for-human and wontfix; the categories are
task, decision, bug and any wayfinder:<type>.

The full rule is in docs/agents/acceptance-criteria.md and docs/agents/triage-labels.md.
RULE

  exit 1
fi

for rule in "$linked_rule" "$boxes_rule" "$drift_rule" "$labels_rule"; do
  printf 'ok    %s\n' "$rule"
done

if [ "$over_delivered" -gt 0 ]; then
  printf 'note  %s  %d thing(s) beyond the brief, annotated and not blocking\n' \
    "$over_delivery_rule" "$over_delivered"
else
  printf 'ok    %s\n' "$over_delivery_rule"
fi
