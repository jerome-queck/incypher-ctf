# Put the pinned secret scanner where the caller can run it, and prove on the way that it is the
# binary we pinned rather than whatever is at that URL today.
#
#   sh conformance/install-gitleaks.sh <directory>
#
# Served from `Jerome-Group/.github` beside the check that uses it, because the central workflow
# runs inside every repository in the Organisation and can reach nothing in the management hub —
# a `bin/` script there would be invisible to all of them (ADR-0036). The hub's own
# `bin/test-conformance` runs this same file out of its working tree, so the version CI scans
# with and the version a fixture is asserted against cannot come apart.
#
# Through the interpreter, always, and never `./`: this file reaches the repository it is served
# from through the Contents API, which commits a plain blob and cannot set the executable bit —
# the same platform limit the checker beside it records (ADR-0030).
#
# Idempotent. A directory already holding the pinned version is left alone, so a runner that
# cached it and the Owner's toolchain on the RAID0 both cost nothing on the second run.
set -eu

if [ $# -ne 1 ]; then
  printf 'usage: sh conformance/install-gitleaks.sh <directory>\n' >&2
  exit 2
fi

directory=$1

# The pin, and the two platforms this Organisation actually runs on: an `ubuntu-latest` runner and
# the Mac mini the Organisation is administered from (ADR-0021). A third platform is an error
# rather than an unverified download — a checksum nobody wrote down is a download nobody pinned.
#
# Bumping the version means bumping both digests, which is deliberate: they come from the release's
# own `checksums.txt`, and taking one without the other would leave the platform nobody tested
# verifying against a hash from a different build.
version='8.30.1'
darwin_arm64_asset="gitleaks_${version}_darwin_arm64.tar.gz"
darwin_arm64_sha256='b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5'
linux_x64_asset="gitleaks_${version}_linux_x64.tar.gz"
linux_x64_sha256='551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64)
    asset=$darwin_arm64_asset
    sha256=$darwin_arm64_sha256
    ;;
  Linux/x86_64)
    asset=$linux_x64_asset
    sha256=$linux_x64_sha256
    ;;
  *)
    printf 'No pinned gitleaks build for %s/%s.\n' "$(uname -s)" "$(uname -m)" >&2
    printf 'Add its asset and its digest from the v%s release checksums, or run this\n' "$version" >&2
    printf 'somewhere the Organisation already pins a build for.\n' >&2
    exit 2
    ;;
esac

# Already the pinned version, so there is nothing to fetch. Any other version is replaced rather
# than kept: a directory holding an older gitleaks would scan with rules this file does not name.
if [ -x "$directory/gitleaks" ] &&
  [ "$("$directory/gitleaks" version 2>/dev/null)" = "$version" ]; then
  printf 'ok    gitleaks %s is already in %s\n' "$version" "$directory"
  exit 0
fi

# `sha256sum` is what a runner has, `shasum` is what macOS has — the shim the checker beside this
# one carries, for the same reason: a file served from `Jerome-Group/.github` can source nothing.
file_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi | cut -d' ' -f1
}

download=$(mktemp -d)

remove_the_download() {
  rm -rf "$download"
}
trap remove_the_download EXIT INT TERM

printf 'Fetching gitleaks %s for %s/%s\n' "$version" "$(uname -s)" "$(uname -m)"

curl -fsSL -o "$download/$asset" \
  "https://github.com/gitleaks/gitleaks/releases/download/v$version/$asset"

actual=$(file_hash "$download/$asset")

# Before extraction, not after: an archive is only worth unpacking once it is the archive we
# pinned, and a tarball that is not it has no business being written into the caller's directory.
if [ "$actual" != "$sha256" ]; then
  printf '\n%s does not hash to the pinned digest.\n' "$asset" >&2
  printf '  expected %s\n  got      %s\n\n' "$sha256" "$actual" >&2
  printf 'Either the release was replaced or the download was tampered with. Do not install it.\n' >&2
  exit 1
fi

mkdir -p "$directory"
tar -xzf "$download/$asset" -C "$directory" gitleaks
chmod +x "$directory/gitleaks"

printf 'ok    gitleaks %s installed into %s\n' "$version" "$directory"
