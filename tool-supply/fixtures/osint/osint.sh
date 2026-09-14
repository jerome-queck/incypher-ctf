#!/bin/dash
set -eu

case "${1:-}" in
  --version)
    printf '1.0.0\n'
    exit 0
    ;;
  --self-check)
    test -n "${2:-}"
    exec /usr/bin/python3 /opt/solver/tool-supply/osint/osint.py --self-check "$2"
    ;;
esac

capability=${1:-}
input=${2:-}
test -n "$capability"
test -n "$input"
exec /usr/bin/python3 /opt/solver/tool-supply/osint/osint.py "$capability" "$input"
