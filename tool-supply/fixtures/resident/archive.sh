# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check)
    7z i >/dev/null; cpio --version >/dev/null; unsquashfs -h >/dev/null 2>&1
    printf 'component-admission\n'; exit 0 ;;
esac
capability=${1:-}; input=${2:-}; test -n "$input"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
copy_inputs() {
  mkdir "$work/inputs"
  if [ -d "$input" ]; then cp -R "$input"/. "$work/inputs"/; else cp "$input" "$work/inputs"/; fi
  find "$work/inputs" -type l -exec false {} +
  find "$work/inputs" -type f -name '*.b64' -print | while IFS= read -r encoded; do
    decoded=${encoded%.b64}; base64 -d "$encoded" > "$decoded"; rm "$encoded"
  done
}
bounded_facts() {
  root=$1
  test -z "$(find "$root" ! -type f ! -type d -print -quit)"
  bytes=$(find "$root" -type f -exec wc -c {} + | mawk 'END {print $1+0}')
  test "$bytes" -le 16777216
  find "$root" -type f -print | LC_ALL=C sort | while IFS= read -r file; do
    relative=${file#"$root"/}
    printf 'file=%s sha256=%s strings=' "$relative" "$(sha256sum "$file" | cut -d' ' -f1)"
    strings -n 4 "$file" | head -n 8 | tr '\n' '|'; printf '\n'
  done
}
archive_extract() {
  copy_inputs; mkdir "$work/extracted"
  password=password
  if [ -f "$work/inputs/request.json" ]; then password=$(jq -er '.password | select(type == "string" and length <= 128)' "$work/inputs/request.json"); fi
  find "$work/inputs" -maxdepth 1 -type f | LC_ALL=C sort | while IFS= read -r archive; do
    name=${archive##*/}; output="$work/extracted/$name"; mkdir "$output"
    case "$name" in
      request.json|*.part2.rar) rmdir "$output" ;;
      *.part1.rar|*.7z|*.zip|*.rar) 7z x -bd -y "-p$password" "-o$output" "$archive" >/dev/null ;;
      *.cpio) (cd "$output" && cpio -id < "$archive" 2>/dev/null) ;;
      *.bz2) bzip2 -dc "$archive" > "$output/payload" ;;
      *.xz) xz -dc "$archive" > "$output/payload" ;;
      *.zst) zstd -q -dc "$archive" > "$output/payload" ;;
      *) rmdir "$output" ;;
    esac
  done
  test ! -e "$work/extracted/escape"
  printf 'schema=resident.archive.extract.v1\n'; bounded_facts "$work/extracted"
}
rootfs_extract() {
  copy_inputs
  image=$(find "$work/inputs" -type f -name '*.sqfs' -print -quit); test -n "$image"
  unsquashfs -quiet -d "$work/rootfs" "$image" >/dev/null
  printf 'schema=resident.firmware.rootfs.v1\n'; bounded_facts "$work/rootfs"
}
case "$capability" in
  archive.extract) archive_extract ;;
  firmware.rootfs) rootfs_extract ;;
  *) exit 64 ;;
esac
