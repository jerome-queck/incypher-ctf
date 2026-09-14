# shellcheck shell=sh
set -eu

case "${1:-}" in
  --version) printf '1.0.0\n' ;;
  --self-check) exec python3 /opt/solver/tool-supply/misc-protocols/tool.py --self-check "${2:-/opt/solver/tool-supply/misc-protocols/input.txt}" ;;
  *) exec python3 /opt/solver/tool-supply/misc-protocols/tool.py "$@" ;;
esac
