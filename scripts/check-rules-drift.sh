# shellcheck shell=sh
#
# Re-fetch a competition's rules page and diff it against the committed baseline.
#
#   sh scripts/check-rules-drift.sh docs/competitions/<event>.rules.txt [--update]
#
# Competition rules are mutable and say so. A red-line change we did not see — a new ban on
# automation, a changed flag format — is the disqualification failure mode, and nobody re-reads a
# rules page they have already read. This turns that re-read into a diff.
#
# Run it before every event, and again mid-event on a multi-day CTF.
#
# The baseline's own header says where to fetch and which span of the page is the rules, so this
# script hardcodes no event. Exits 0 unchanged, 1 on drift, 2 when it could not check.
set -eu

if [ $# -lt 1 ]; then
  printf 'usage: sh scripts/check-rules-drift.sh <baseline.rules.txt> [--update]\n' >&2
  exit 2
fi

baseline=$1
update=${2:-}
[ -f "$baseline" ] || { printf 'no such baseline: %s\n' "$baseline" >&2; exit 2; }

header_value() { sed -n "s/^$1:[[:space:]]*//p" "$baseline" | head -n1; }
url=$(header_value Source)
from=$(header_value From)
to=$(header_value To)
[ -n "$url" ] && [ -n "$from" ] && [ -n "$to" ] || {
  printf '%s is missing a Source/From/To header line.\n' "$baseline" >&2; exit 2; }

separator_line=$(grep -n '^-\{20,\}$' "$baseline" | head -n1 | cut -d: -f1)
[ -n "$separator_line" ] || { printf '%s has no header separator.\n' "$baseline" >&2; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# CTFd sits behind a WAF that 403s an unfamiliar agent, so this asks as a browser would.
curl -sSf --max-time 30 \
  -A 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36' \
  "$url" -o "$work/page.html" || { printf 'could not fetch %s\n' "$url" >&2; exit 2; }

# Rendered text, not markup: a theme rebuild changes every tag and no rule, and the page carries a
# per-request CSRF nonce inside <script> that would make raw markup differ on every fetch.
FROM=$from TO=$to python3 - "$work/page.html" > "$work/fetched.txt" <<'PY'
import html, os, re, sys

markup = open(sys.argv[1], encoding='utf-8', errors='replace').read()
markup = re.sub(r'(?is)<(script|style|svg)\b.*?</\1>', ' ', markup)
markup = re.sub(r'(?i)<br\s*/?>', '\n', markup)
markup = re.sub(r'(?i)</(p|div|li|h[1-6]|tr|section)>', '\n', markup)
markup = re.sub(r'(?i)<li[^>]*>', '\n- ', markup)
text = html.unescape(re.sub(r'<[^>]+>', ' ', markup))
text = re.sub(r'[ \t\r\f\v]+', ' ', text)

lines = [line.strip() for line in text.splitlines()]
lines = [line for line in lines if line]

start = next((i for i, l in enumerate(lines) if l == os.environ['FROM']), None)
end = next((i for i, l in enumerate(lines) if l.startswith(os.environ['TO'])), None)
if start is None or end is None or end < start:
    sys.exit(f"could not locate the rules span ({os.environ['FROM']!r} .. {os.environ['TO']!r}) "
             f"— the page structure changed, which is itself drift worth reading")
print('\n'.join(lines[start:end + 1]))
PY

sed "1,${separator_line}d" "$baseline" > "$work/baseline.txt"

if diff -q "$work/baseline.txt" "$work/fetched.txt" >/dev/null; then
  printf 'rules unchanged: %s\n' "$url"
  exit 0
fi

printf '\nRULES DRIFT — %s\n\n' "$url" >&2
diff -u "$work/baseline.txt" "$work/fetched.txt" >&2 || true

if [ "$update" = "--update" ]; then
  { sed -n "1,${separator_line}p" "$baseline"; cat "$work/fetched.txt"; } > "$work/merged.txt"
  stamp_sgt=$(TZ=Asia/Singapore date '+%Y-%m-%d %H:%M SGT')
  stamp_utc=$(date -u '+%Y-%m-%dT%H:%MZ')
  sed "s|^Fetched:.*|Fetched:   ${stamp_sgt} (${stamp_utc})|" "$work/merged.txt" > "$baseline"
  printf '\nbaseline updated — commit it, and say in the PR what changed.\n' >&2
else
  printf '\nRe-read the diff above before playing. Accept it with --update.\n' >&2
fi
exit 1
