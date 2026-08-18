# The Organisation's conformance checker: read a repository tree and assert that it still matches
# the conventions every repository here agrees to. Read-only, and it asks nothing of the network —
# the tree in front of it and the manifest beside it are the whole input, which is what lets the
# fixtures in the management hub exercise its failing paths.
#
#   sh conformance/check-conformance.sh <tree> [repository]
#
# `repository` is the `owner/name` the tree belongs to, and the only thing it decides is whether
# this tree is the one the mirrored documents *live* in rather than a copy of them (ADR-0034).
# Omitted means "not that repository", which is the answer for every fixture and every caller but
# one.
#
# Through the interpreter, always, and never `./`: this file reaches the repository it is served
# from through the Contents API, which writes a plain blob and has no way to set the executable
# bit — the same platform limit that forced `bin/seed-templates` to exist for the `CLAUDE.md`
# symlink. A caller that relied on the bit would work in a clone and fail here.
#
# It prints one line per rule, and the failing line names the rule first, so a caller can tell
# *which* rule went red rather than only that something did. Exit 0 when the tree conforms, 1 when
# it does not, 2 when the arguments are wrong — a usage mistake is not a conformance finding.
set -eu

# Spelled out rather than `${1:?…}`, which exits 1 in some shells and 2 in others — and 1 is the
# status this script reserves for a tree that genuinely does not conform. A caller that could not
# tell a missing argument from a violation would report the wrong thing.
if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  printf 'usage: sh conformance/check-conformance.sh <tree> [repository]\n' >&2
  exit 2
fi

tree=$1
repository=${2:-}

[ -d "$tree" ] || {
  printf 'No such tree: %s\n' "$tree" >&2
  exit 2
}

# The manifest ships beside this script and is read from beside it, never from the tree being
# checked — a repository that could edit the contract it is held to is not being held to one.
manifest=$(dirname -- "$0")/manifest
tab=$(printf '\t')

[ -f "$manifest" ] || {
  printf 'No manifest beside the checker: %s\n' "$manifest" >&2
  exit 2
}

# Rules are named, not numbered: the name is what a failing run prints, what the fixture runner
# asserts against, and what someone greps for. A renamed rule is therefore a visible change.
map_rule='map-covers-top-level-directories'
presence_rule='documents-every-repository-owes-are-present'
mirrored_rule='mirrored-files-match-the-organisations-copy'
skeleton_rule='skeleton-files-keep-their-required-headings'
ceiling_rule='documents-stay-under-their-line-ceiling'
adr_filename_rule='decision-records-are-named-for-their-number'
adr_number_rule='no-two-decision-records-share-a-number'
supersession_rule='supersession-is-recorded-in-both-records'

# The repository the mirrored documents are authored in, declared by the manifest rather than
# written here: the generator knows which tree it read them out of, and a name repeated in two
# files is a name that eventually disagrees with itself.
originals_repository=$(awk -F'\t' '$1 == "originals" {print $2}' "$manifest")

# Whether this tree is that repository rather than one holding copies. Two rules turn on it, so it
# is answered once: a rule that decided it for itself is a rule that can drift from the other.
# Omitted means "not that repository", which is the answer for every fixture and every caller but
# one.
checking_the_originals=
if [ -n "$repository" ] && [ "$repository" = "$originals_repository" ]; then
  checking_the_originals=yes
fi

failures=0

fail() {
  rule=$1
  detail=$2
  printf 'FAIL  %s  %s\n' "$rule" "$detail"
  failures=$((failures + 1))
}

# `MAP.md` is the manifest, and this is the one rule that holds it to its own claim: a repository
# grows a top-level directory and the map is supposed to grow a row in the same pull request.
# Nothing enforced that, so the map drifted quietly and a reader who trusted it was misled — worse
# than having no map at all (`CODING_STANDARDS.md` §4).
#
# A row rather than a mention, because prose is not navigation: the table is what a reader steers
# by, and a directory named in a sentence somewhere in the file leaves the table wrong. A row that
# points deeper — `docs/adr/` for `docs/` — counts, since that is the entry point a reader wants.
check_map_covers_top_level_directories() {
  map="$tree/MAP.md"

  if [ ! -f "$map" ]; then
    fail "$map_rule" 'MAP.md is missing, so no directory has a row'
    return
  fi

  rows=$(grep '^[[:space:]]*|' "$map" || true)

  # Both globs, because `.github/` is a top-level directory like any other and is the one most
  # likely to be forgotten. `.` and `..` always match the second glob; a directory with no visible
  # entries leaves the first glob unexpanded, and the literal is not a directory, so it falls out.
  for path in "$tree"/* "$tree"/.*; do
    [ -d "$path" ] || continue
    name=${path##*/}
    case $name in
      . | .. | .git) continue ;;
    esac

    # `grep -F`, so a name carrying a regular-expression character — `.github` again — is matched
    # as the literal it is rather than as a pattern that would also match `xgithub`. The backtick
    # and the trailing slash are what make it a path rather than a word: `docs` in a sentence is
    # not a row, and `docs/` written as a path is. Any cell of the row counts — which column a map
    # puts the path in is the map's business, and the rule is about coverage, not layout.
    printf '%s\n' "$rows" | grep -qF "\`$name/" ||
      fail "$map_rule" "$name/ has no row in MAP.md"
  done
}

# The manifest's records of one class, as lines. A record is `class<tab>path<tab>…`, and the
# `heading` records that follow a `skeleton` one are read separately, in the order they appear —
# that order is the requirement.
manifest_records() {
  awk -F'\t' -v class="$1" '$1 == class' "$manifest"
}

# The shim the generator carries too, and for the same reason in reverse: this script ships to
# `Jerome-Group/.github` on its own and can source nothing. `sha256sum` is what a runner has,
# `shasum` is what macOS has.
file_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi | cut -d' ' -f1
}

# The documents every repository in the Organisation carries, held to being there at all. Every
# other rule here binds a file only where the file is present, which left the Organisation with a
# rule set every repository runs and no rule that a repository *has* the documents that rule set
# is about: all three repositories that predate the seed were missing two of the `docs/agents/`
# documents their own `AGENTS.md` points at, and every rule was green about it (#112).
#
# Which documents those are is the seed's answer rather than this rule's. The manifest records one
# as `required` exactly when the *private* template seeds it, so the four health files owned only
# by a public repository are never asked for and a private repository still passes carrying
# exactly what it was seeded with (ADR-0038).
#
# The failure names where the document comes from, the way the mirrored failure does: the path
# beside the record is the file's home in the Organisation's own tree, which is what somebody
# restoring it needs — `CONTRIBUTING.md` is published from `dot-github/CONTRIBUTING.md` and
# `AGENTS.md` written at `templates/base/AGENTS.md`.
#
# Skipped in the repository the documents are authored in, where those two paths are where the
# files actually live and the destination path is a layout that tree does not have (ADR-0034).
# What this rule catches is a *copy* going missing where nobody chose it; in the hub, dropping a
# document is dropping it from the seed, which changes the manifest `bin/test-conformance`
# regenerates and compares on every run.
check_documents_every_repository_owes_are_present() {
  [ -z "$checking_the_originals" ] || return 0

  records=$(manifest_records required)

  while IFS="$tab" read -r _ path origin; do
    [ -n "$path" ] || continue

    authored_at="$origin in $originals_repository"

    [ -f "$tree/$path" ] ||
      fail "$presence_rule" \
        "$path is missing — every repository carries it; it is seeded from $authored_at"
  done <<EOF
$records
EOF
}

# A mirrored file is a copy of an Organisation document that the next seed silently overwrites,
# so any difference is drift by definition and there is nothing to weigh: the edit is already
# lost, and the only useful thing the failure can do is say where the text actually lives
# (ADR-0031).
#
# Only files that are present are checked, which is the right reading of *this* rule and used to
# be the whole answer: a private repository carries none of the four public health files, and a
# hash check that failed on their absence would fail every private repository in the Organisation
# for holding exactly what it was seeded with. Whether a document should be there at all is asked
# above instead, by the rule that knows which documents every repository owes — so a missing one
# is reported once, as an absence, rather than twice (ADR-0038).
#
# The one tree this rule must not run against is the repository the originals live in, where the
# same file is the source rather than a copy. Held to a hash pinned beside the checker, that
# repository could never edit its own documents: the hash only moves when the hub is republished,
# which happens after a merge the red check would prevent. The manifest names the repository that
# owns them and the caller says which repository it is holding, so the two can be compared
# (ADR-0034).
check_mirrored_files_match_the_organisations_copy() {
  [ -z "$checking_the_originals" ] || return 0

  records=$(manifest_records mirrored)

  while IFS="$tab" read -r _ path expected; do
    [ -n "$path" ] || continue
    file="$tree/$path"
    [ -f "$file" ] || continue

    [ "$(file_hash "$file")" = "$expected" ] ||
      fail "$mirrored_rule" \
        "$path differs from the Organisation's copy — edit it in Jerome-Group/org, not here"
  done <<EOF
$records
EOF
}

# A document's headings, in the order it carries them. Fenced blocks are skipped, because a shell
# comment or a Markdown sample inside one is not a heading a reader can navigate to. The direction
# that matters is the permissive one: counting a quoted heading would let it *satisfy* a
# requirement, so a document that had deleted the section, or moved it, would pass on the strength
# of quoting it. Documents here quote each other's shape constantly, so this is the ordinary case
# rather than the adversarial one — `required-heading-only-in-a-fence` and
# `reordering-hidden-in-a-fence` are the two fixtures that would otherwise pass.
#
# The `#` run is measured with `match`/`RLENGTH` rather than an interval expression, which not
# every awk on a runner supports: one to six `#` and a space is two to seven characters.
headings_of() {
  awk '
    /^[[:space:]]*```/ || /^[[:space:]]*~~~/ { fenced = 1 - fenced; next }
    fenced { next }
    /^#/ { if (match($0, /^#+ /) && RLENGTH >= 2 && RLENGTH <= 7) print }
  ' "$1"
}

# Present and in order, and nothing else: the required headings are consumed in sequence against
# the headings the file actually has, so anything added between them — a heading, a section, a
# whole chapter — is simply skipped over. Deleting a required heading or moving one behind
# another is the only way to fail. That permissiveness is the point (ADR-0031): a repository owns
# every word under these headings, and owes the Organisation only the shape.
check_required_headings() {
  checked=$1
  document=$2

  present=$(headings_of "$document")
  wanted=$(awk -F'\t' -v path="$checked" '$1 == "heading" && $2 == path {print $3}' "$manifest")

  # What is left of the file below the last heading matched. A required heading found only above
  # that point is present but out of order, which is why the two are asked separately — "missing"
  # and "moved" are different mistakes with different fixes.
  remaining=$present

  while IFS= read -r heading; do
    [ -n "$heading" ] || continue

    line=$(printf '%s\n' "$remaining" | grep -nFx -- "$heading" | head -n 1 | cut -d: -f1)

    if [ -n "$line" ]; then
      remaining=$(printf '%s\n' "$remaining" | tail -n +$((line + 1)))
    elif printf '%s\n' "$present" | grep -qFx -- "$heading"; then
      # One report per file. Every later heading is now out of order too, and a wall of them
      # would bury the one that moved.
      fail "$skeleton_rule" "$checked has \"$heading\" out of the order the Organisation requires"
      return
    else
      fail "$skeleton_rule" "$checked is missing the required heading \"$heading\""
      return
    fi
  done <<EOF
$wanted
EOF
}

# A ceiling rather than a target, set high enough that it fires on bloat and never on an ordinary
# edit — see the generator, which is where the number is declared.
check_line_ceiling() {
  checked=$1
  document=$2
  ceiling=$3

  [ "$ceiling" != '-' ] || return 0

  # `awk` rather than `wc -l`, which counts newlines: a file whose last line has none would come
  # in one under its real length, and the file one line over its ceiling is exactly the one
  # somebody would trim the newline off.
  lines=$(awk 'END { print NR }' "$document")
  [ "$lines" -le "$ceiling" ] ||
    fail "$ceiling_rule" "$checked is $lines lines, over its ceiling of $ceiling"
}

check_skeleton_files_keep_their_required_headings() {
  records=$(manifest_records skeleton)

  while IFS="$tab" read -r _ path ceiling; do
    [ -n "$path" ] || continue
    file="$tree/$path"
    [ -f "$file" ] || continue

    check_required_headings "$path" "$file"
    check_line_ceiling "$path" "$file" "$ceiling"
  done <<EOF
$records
EOF
}

# Seed-only files are named by the manifest and checked by nothing, which is a decision rather
# than an omission: a `.gitignore` or a starting ADR is a gift, not a contract (ADR-0031).

# The decision records of the tree, by filename. `docs/adr/` at the root and nowhere else: that is
# where every repository in this Organisation keeps them, and it is what `docs/agents/domain.md`
# tells an agent to read. A context-scoped set under `src/<context>/docs/adr/` is a layout nothing
# here has, and a rule that swept for it would be enforcing a shape on repositories that never
# chose one — see ADR-0033 for what happens on the day one does.
#
# Markdown only, because a record is Markdown; a `.keep` holding an empty directory open is not a
# record and neither is a stray note.
decision_records() {
  for record in "$tree"/docs/adr/*.md; do
    [ -f "$record" ] || continue
    printf '%s\n' "${record##*/}"
  done
}

# The number a record's filename claims, or nothing if the name does not claim one — which the
# filename rule has already reported, so the two later rules simply pass over it rather than
# guessing at a number and reporting the same file twice.
record_number() {
  printf '%s' "$1" | sed -n 's/^\([0-9][0-9][0-9][0-9]\)-.*/\1/p'
}

# All three record rules are structural, and a record's *body* is held to nothing: no required
# section, no required heading, no minimum. A record may be one paragraph, and requiring a section
# produces records carrying an empty one that reads "None", which passes and teaches nobody. The
# house style is prose guidance in `docs/agents/domain.md` instead (ADR-0033).

# `NNNN-hyphenated-title.md`, and the shape is the whole rule. It is checked because every
# reference in every record is `ADR-NNNN` and every link beside it is the filename: a record whose
# name does not carry its number is unreachable by both (ADR-0033).
#
# Four literal digit classes rather than an interval expression, and `LC_ALL=C` on the range, for
# the same portability reason the heading reader avoids intervals — this script runs on whatever
# `sh` and whatever `grep` a caller's runner has.
check_decision_records_are_named_for_their_number() {
  records=$(decision_records)

  while IFS= read -r name; do
    [ -n "$name" ] || continue

    printf '%s' "$name" |
      LC_ALL=C grep -qE '^[0-9][0-9][0-9][0-9]-[a-z0-9]+(-[a-z0-9]+)*\.md$' ||
      fail "$adr_filename_rule" \
        "docs/adr/$name is not named NNNN-hyphenated-title.md"
  done <<EOF
$records
EOF
}

# Uniqueness, and deliberately not contiguity (ADR-0033). A gap in the numbering costs a reader
# nothing, whereas a number used twice makes every `ADR-NNNN` in the repository ambiguous — and
# contiguity would fail a pull request that has done nothing wrong, merely because another one
# merged first.
#
# The failure names the number that is taken and the records that take it, because the fix is to
# renumber one of them and the author needs to know which two are in play.
check_no_two_decision_records_share_a_number() {
  records=$(decision_records)

  duplicates=$(printf '%s\n' "$records" | sed -n 's/^\([0-9][0-9][0-9][0-9]\)-.*/\1/p' |
    LC_ALL=C sort | uniq -d)

  while IFS= read -r number; do
    [ -n "$number" ] || continue

    sharing=$(printf '%s\n' "$records" | grep "^$number-" | paste -sd' ' -)
    fail "$adr_number_rule" "$number is taken twice in docs/adr/, by $sharing"
  done <<EOF
$duplicates
EOF
}

# A record that supersedes another requires the one it supersedes to point forward at it. The
# defect this closes is a navigation one and it is real: a reader searching a topic lands on the
# *earlier* record, which is the one that still reads as current, and nothing on the page says it
# has been overtaken (ADR-0033).
#
# A claim is a line that says `supersed…` and names `ADR-NNNN` on the same line. Line-scoped
# rather than file-scoped because records discuss supersession in the abstract — "a decision that
# no longer holds is superseded by a new record" — and a file-scoped rule would read every
# reference in such a record as a claim about it.
#
# The pointer back is any mention of `ADR-NNNN` anywhere in the superseded record: what the reader
# needs is the forward link, and where it sits and how it is worded is the record's business. That
# also makes the rule symmetric — the record carrying `Superseded by [ADR-NNNN]` states a claim of
# its own, and the pair satisfies it from the other side.
#
# A number no record here has is passed over rather than failed. It is a reference to another
# repository's record or to one not yet written, and this rule is about the link between two
# records that both exist, not about dangling references.
check_supersession_is_recorded_in_both_records() {
  records=$(decision_records)

  while IFS= read -r name; do
    [ -n "$name" ] || continue

    number=$(record_number "$name")
    [ -n "$number" ] || continue

    # One reference per line before any is read, because a claim may name two records — `tr` on
    # the characters that can precede one, then a `sed` that keeps only the lines that are one.
    # `grep -o` would say this in a word and is not POSIX; the whole point of this script is that
    # it runs on whatever `grep` a caller's runner has.
    claimed=$(grep -i 'supersed' "$tree/docs/adr/$name" | tr ' \t([<*_`' '\n\n\n\n\n\n\n\n' |
      sed -n 's/^ADR-\([0-9][0-9][0-9][0-9]\).*/\1/p' | LC_ALL=C sort -u || true)

    while IFS= read -r other; do
      [ -n "$other" ] || continue
      [ "$other" != "$number" ] || continue

      superseded=$(printf '%s\n' "$records" | grep "^$other-" | head -n 1)
      [ -n "$superseded" ] || continue

      grep -q "ADR-$number" "$tree/docs/adr/$superseded" ||
        fail "$supersession_rule" \
          "docs/adr/$name supersedes ADR-$other, which does not point forward to ADR-$number"
    done <<CLAIMED
$claimed
CLAIMED
  done <<EOF
$records
EOF
}

check_map_covers_top_level_directories
check_documents_every_repository_owes_are_present
check_mirrored_files_match_the_organisations_copy
check_skeleton_files_keep_their_required_headings
check_decision_records_are_named_for_their_number
check_no_two_decision_records_share_a_number
check_supersession_is_recorded_in_both_records

if [ "$failures" -gt 0 ]; then
  printf '\n%d conformance violation(s) in %s.\n' "$failures" "$tree" >&2
  exit 1
fi

# A skipped rule is reported as skipped, never as `ok`. The two mean different things — one is
# "asked and answered", the other is "not asked" — and a run that printed the same word for both
# would let a rule quietly stop running without the output changing.
for rule in "$map_rule" "$presence_rule" "$mirrored_rule" "$skeleton_rule" "$ceiling_rule" \
  "$adr_filename_rule" "$adr_number_rule" "$supersession_rule"; do
  if [ -z "$checking_the_originals" ]; then
    printf 'ok    %s\n' "$rule"
  elif [ "$rule" = "$mirrored_rule" ]; then
    printf 'skip  %s  %s holds these documents; it does not copy them\n' \
      "$rule" "$originals_repository"
  elif [ "$rule" = "$presence_rule" ]; then
    printf 'skip  %s  %s keeps these documents where it publishes them from\n' \
      "$rule" "$originals_repository"
  else
    printf 'ok    %s\n' "$rule"
  fi
done
