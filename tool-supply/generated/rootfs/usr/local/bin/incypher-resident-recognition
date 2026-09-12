# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check) tesseract --version >/dev/null 2>&1; ZXingReader -help >/dev/null 2>&1; printf 'component-admission\n'; exit 0 ;;
esac
capability=${1:-}; input=${2:-}; test -d "$input"; test ! -L "$input"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
decode_images() {
  find "$input" -maxdepth 1 -type f | LC_ALL=C sort | while IFS= read -r source; do
    name=${source##*/}; destination="$work/${name%.b64}"
    case "$source" in *.b64) base64 -d "$source" > "$destination" ;; *) cp "$source" "$destination" ;; esac
  done
}
decode_images
case "$capability" in
  recognition.ocr)
    printf 'schema=resident.recognition.ocr.v1\ntext-begin\n'
    for image in "$work"/*; do tesseract "$image" stdout 2>/dev/null; done
    printf 'text-end\n'
    ;;
  recognition.barcode)
    printf 'schema=resident.recognition.barcode.v1\nvalues-begin\n'
    for image in "$work"/*; do
      test "$(identify-im7.q16 -format '%[fx:w*h]' "$image" 2>/dev/null)" -le 1000000
      ZXingReader "$image" | mawk -F '"' '/^Text:/ {print $2; exit}'
    done
    printf 'values-end\n'
    ;;
  *) exit 64 ;;
esac
