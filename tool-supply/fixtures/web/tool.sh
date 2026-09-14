# shellcheck shell=sh
set -eu
exec /usr/bin/python3 /opt/solver/tool-supply/web/tool.py "$@"
