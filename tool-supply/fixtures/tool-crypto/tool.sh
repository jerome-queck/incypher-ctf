# shellcheck shell=sh
set -eu

case "${1:-}" in
  --version) printf '2.0.0\n' ;;
  --self-check) python3 /usr/local/libexec/incypher-tool-crypto.py --self-check ;;
  *) exec python3 /usr/local/libexec/incypher-tool-crypto.py "$@" ;;
esac
