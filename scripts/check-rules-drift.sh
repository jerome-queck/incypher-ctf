# shellcheck shell=sh
#
# Re-fetch a competition source and diff its rendered text against the committed baseline.
#
#   sh scripts/check-rules-drift.sh docs/competitions/<baseline>.txt [--update]
#
# Competition sources are mutable. A red-line change we did not see — a new ban on automation, a
# changed flag format, a new connection path — is the disqualification failure mode, and nobody
# reliably re-reads a page they have already read. This turns that re-read into a diff.
#
# Run it before every event, and again mid-event on a multi-day CTF.
#
# The baseline's own header says where to fetch and which span of the page matters, so this script
# hardcodes neither event nor source kind. Exits 0 unchanged, 1 on drift, 2 when it could not check.
set -eu

if [ $# -lt 1 ]; then
  printf 'usage: sh scripts/check-rules-drift.sh <baseline.txt> [--update]\n' >&2
  exit 2
fi

baseline=$1
update=${2:-}
[ -f "$baseline" ] || { printf 'no such baseline: %s\n' "$baseline" >&2; exit 2; }

header_value() { sed -n "s/^$1:[[:space:]]*//p" "$baseline" | head -n1; }
url=$(header_value Source)
from=$(header_value From)
to=$(header_value To)
links=$(header_value Links)
# SC2015's hazard is a `C` that runs after a true `A` and a false `B` in something meant as
# if-then-else. There is no then-branch here at all: `C` runs exactly when a header is missing.
# shellcheck disable=SC2015
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

# Rendered text, not markup: a theme rebuild changes every tag and no claim, and pages carry
# per-request data inside <script> that would make raw markup differ on every fetch.
FROM=$from TO=$to LINKS=$links SOURCE=$url python3 - "$work/page.html" > "$work/fetched.txt" <<'PY'
import html, os, re, sys
from urllib.parse import urljoin

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
    sys.exit(f"could not locate the source span ({os.environ['FROM']!r} .. {os.environ['TO']!r}) "
             f"— the page structure changed, which is itself drift worth reading")
print('\n'.join(lines[start:end + 1]))

if os.environ['LINKS'].lower() == 'yes':
    raw_start = markup.find(os.environ['FROM'])
    raw_end = markup.find(os.environ['TO'], raw_start)
    if raw_start < 0 or raw_end < raw_start:
        sys.exit('could not locate the source span in markup for link capture')
    print('Link targets')
    for anchor in re.finditer(r'(?is)<a\b([^>]*)>(.*?)</a>', markup):
        if not raw_start <= anchor.start() <= raw_end:
            continue
        href = re.search(r'''(?is)\bhref\s*=\s*(["'])(.*?)\1''', anchor.group(1))
        if not href:
            continue
        label = html.unescape(re.sub(r'<[^>]+>', ' ', anchor.group(2)))
        label = re.sub(r'\s+', ' ', label).strip()
        print(f"{label} -> {urljoin(os.environ['SOURCE'], html.unescape(href.group(2)))}")
PY

sed "1,${separator_line}d" "$baseline" > "$work/baseline.txt"

if diff -q "$work/baseline.txt" "$work/fetched.txt" >/dev/null; then
  printf 'source unchanged: %s\n' "$url"
  exit 0
fi

printf '\nSOURCE DRIFT — %s\n\n' "$url" >&2
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
