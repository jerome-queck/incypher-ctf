# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check)
    file --version >/dev/null; xxd -h >/dev/null 2>&1 || true; git --version >/dev/null
    printf 'component-admission\n'; exit 0 ;;
esac
capability=${1:-}; input=${2:-}
test -n "$input"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
case "$capability" in
  recon.mime)
    test -f "$input"; test ! -L "$input"
    printf 'schema=resident.recon.mime.v1\nmime=%s\n' "$(file -b --mime-type "$input")"
    ;;
  recon.bytes)
    test -f "$input"; test ! -L "$input"
    printf 'schema=resident.recon.bytes.v1\nhex='
    xxd -p -l 4096 "$input" | tr -d '\n'
    printf '\nstrings='; strings -n 4 "$input" | head -n 32 | tr '\n' '|'; printf '\n'
    ;;
  recon.repository)
    export GIT_AUTHOR_NAME=resident GIT_AUTHOR_EMAIL=resident@localhost
    export GIT_COMMITTER_NAME=resident GIT_COMMITTER_EMAIL=resident@localhost
    if [ -d "$input/.git" ]; then repository=$input
    elif [ -f "$input" ]; then
      bundle=$input
      case "$input" in *.b64) bundle=$work/repository.bundle; base64 -d "$input" > "$bundle" ;; esac
      git clone -q "$bundle" "$work/repository"; repository=$work/repository
    else exit 65
    fi
    printf 'schema=resident.recon.repository.v1\nhistory-begin\n'
    git -C "$repository" log -p --all --format= --no-ext-diff --no-textconv | head -c 32768
    printf 'history-end\n'
    ;;
  *) exit 64 ;;
esac
