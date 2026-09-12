# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check) identify-im7.q16 -version >/dev/null; pdfinfo -v >/dev/null 2>&1; printf 'component-admission\n'; exit 0 ;;
esac
capability=${1:-}; input=${2:-}; test -n "$input"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
materialize() {
  source=$1; destination=$2
  case "$source" in *.b64) base64 -d "$source" > "$destination" ;; *) cp "$source" "$destination" ;; esac
}
case "$capability" in
  image.inspect)
    if [ -d "$input" ]; then source=$(find "$input" -maxdepth 1 -type f -print -quit); else source=$input; fi
    test -f "$source"; test ! -L "$source"; materialize "$source" "$work/image"
    printf 'schema=resident.image.inspect.v1\ndimensions=%s\n' "$(identify-im7.q16 -format '%wx%h' "$work/image" 2>/dev/null)"
    printf 'comment=%s\n' "$(exiftool -s3 -Comment "$work/image")"
    ;;
  document.pdf)
    test -d "$input"; test ! -L "$input"; printf 'schema=resident.document.pdf.v1\n'
    find "$input" -maxdepth 1 -type f | LC_ALL=C sort | while IFS= read -r source; do
      name=${source##*/}; destination="$work/${name%.b64}"; materialize "$source" "$destination"
      printf 'document=%s pages=%s\ntext-begin\n' "${name%.b64}" "$(pdfinfo "$destination" | mawk '/^Pages:/ {print $2}')"
      text=$(pdftotext -layout "$destination" - 2>/dev/null || true)
      compact=$(printf '%s' "$text" | tr -d '[:space:]')
      if [ -n "$compact" ]; then printf '%s\n' "$text"
      else
        pdftoppm -png -r 150 "$destination" "$work/page" >/dev/null 2>&1
        for page in "$work"/page-*.png; do tesseract "$page" stdout 2>/dev/null; done
        rm -f "$work"/page-*.png
      fi
      printf 'text-end\n'
    done
    ;;
  *) exit 64 ;;
esac
