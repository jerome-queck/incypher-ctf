# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check) ps --version >/dev/null; printf 'component-admission\n'; exit 0 ;;
esac
test "${1:-}" = process.inspect
input=${2:-}; test -f "$input"; test ! -L "$input"
python3 - "$input" <<'PY'
import json
import pathlib
import sys

allowed = {"Name", "Pid", "PPid", "Threads", "Uid", "VmPeak", "VmSize"}
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
if set(request) != {"fields"} or not isinstance(request["fields"], list):
    raise ValueError("invalid process request")
fields = request["fields"]
if not fields or len(fields) != len(set(fields)) or any(field not in allowed for field in fields):
    raise ValueError("unsupported process field")
status = {}
for line in pathlib.Path("/proc/self/status").read_text().splitlines():
    if ":" in line:
        name, value = line.split(":", 1)
        status[name] = value.strip()
print("schema=resident.process.inspect.v1")
for field in fields:
    print(f"{field}={status[field]}")
PY
