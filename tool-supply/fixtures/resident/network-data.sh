# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check)
    curl --version >/dev/null; openssl version >/dev/null; sqlite3 --version >/dev/null
    printf 'component-admission\n'; exit 0 ;;
esac
capability=${1:-}; input=${2:-}; test -n "$input"
case "$capability" in
  network.http)
    test -n "${INCYPHER_TARGET_SOCKET:-}"; test -f "$input"; test ! -L "$input"
    method=$(jq -er 'if (keys|sort)==["method","path"] and (.method|type)=="string" and (.method|test("^(GET|HEAD)$")) and (.path|type)=="string" and (.path|test("^/[^[:space:]]{0,1023}$")) then .method else error("invalid") end' "$input")
    path=$(jq -er '.path' "$input")
    printf 'schema=resident.network.http.v1\nbody-begin\n'
    /usr/bin/python3 /target-client.py http "$method" "$path"
    printf '\nbody-end\n'
    ;;
  network.tcp)
    test -n "${INCYPHER_TARGET_SOCKET:-}"; test -f "$input"; test ! -L "$input"
    body=$(jq -er 'if keys==["body"] and (.body|type)=="string" and (.body|length)<=4096 then .body else error("invalid") end' "$input")
    printf 'schema=resident.network.tcp.v1\nbody='
    /usr/bin/python3 /target-client.py tcp "$body"
    printf '\n'
    ;;
  data.sqlite)
    test -d "$input"; test ! -L "$input"
    python3 - "$input" <<'PY'
import base64
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile

root = pathlib.Path(sys.argv[1])
request = json.loads((root / "request.json").read_text())
if set(request) != {"database", "query"}:
    raise ValueError("invalid SQLite request")
name, query = request["database"], request["query"]
if not isinstance(name, str) or pathlib.PurePath(name).name != name:
    raise ValueError("invalid database name")
if not isinstance(query, str) or len(query) > 4096 or not query.lstrip().lower().startswith("select ") or ";" in query:
    raise ValueError("only one bounded SELECT is allowed")
source = root / name
before = hashlib.sha256(source.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory() as temporary:
    database = pathlib.Path(temporary) / "input.sqlite"
    if source.suffix == ".b64":
        database.write_bytes(base64.b64decode(source.read_text().strip(), validate=True))
    else:
        database.write_bytes(source.read_bytes())
    with sqlite3.connect(f"file:{database}?immutable=1", uri=True) as connection:
        rows = connection.execute(query).fetchmany(256)
if hashlib.sha256(source.read_bytes()).hexdigest() != before:
    raise ValueError("input changed")
print("schema=resident.data.sqlite.v1")
print("rows=" + json.dumps(rows, separators=(",", ":"), sort_keys=True))
PY
    ;;
  *) exit 64 ;;
esac
