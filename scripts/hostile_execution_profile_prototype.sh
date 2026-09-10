#!/usr/bin/env bash
# PROTOTYPE — throwaway runtime probe for issue 247. Never source this into production.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
image=incypher-hostile-profile-prototype:current
cgroup_parent=incypher-profile-prototype
cgroup_source=/sys/fs/cgroup/system.slice/$cgroup_parent

cd "$repo_root"
python3 scripts/runtime.py verify
docker build --quiet --tag "$image" . >/dev/null

printf 'client=%s server=%s kernel=%s\n' \
  "$(docker version --format '{{.Client.Version}}')" \
  "$(docker version --format '{{.Server.Version}}')" \
  "$(docker info --format '{{.KernelVersion}}')"

printf '\nrootless namespace baseline\n'
set +e
docker run --rm --user 20000:20000 --entrypoint /usr/bin/bwrap "$image" \
  --unshare-user --uid 0 --gid 0 --unshare-pid --unshare-net \
  --ro-bind / / /bin/true
rootless_status=$?
set -e
printf 'rootless_status=%s (non-zero is the observed baseline)\n' "$rootless_status"

# Docker creates this dedicated parent before resolving it as the next container's bind source.
docker run --rm --cgroup-parent "$cgroup_parent" --entrypoint /bin/true "$image"

docker run --rm --interactive \
  --env PROBE_CREDENTIAL=prototype-secret-never-visible \
  --cap-add SYS_ADMIN \
  --cap-add NET_ADMIN \
  --security-opt seccomp=unconfined \
  --security-opt systempaths=unconfined \
  --security-opt apparmor=unconfined \
  --cgroup-parent "$cgroup_parent" \
  --cgroupns private \
  --mount "type=bind,source=$cgroup_source,target=/run/cgroup-parent" \
  --entrypoint /bin/bash "$image" -s <<'CONTAINER'
set -euo pipefail

mkdir -p /run/control /run/broker /run/attempt-work /run/sibling-work /root/.codex
printf 'control secret\n' >/run/control/credential
printf 'sibling secret\n' >/run/sibling-work/secret
printf 'auth secret\n' >/root/.codex/auth.json
printf 'own work\n' >/run/attempt-work/own.txt

# Dedicated cgroup-v2 subtree: trusted control and the hostile Attempt are siblings.
container_cgroup=$(find /run/cgroup-parent -mindepth 1 -maxdepth 1 -type d | head -n 1)
mkdir "$container_cgroup/control" "$container_cgroup/attempt"
echo $$ >"$container_cgroup/control/cgroup.procs"
echo '+cpu +io +memory +pids' >"$container_cgroup/cgroup.subtree_control"
echo '20000 100000' >"$container_cgroup/attempt/cpu.max"
echo 33554432 >"$container_cgroup/attempt/memory.max"
echo 0 >"$container_cgroup/attempt/memory.swap.max"
echo 16 >"$container_cgroup/attempt/pids.max"

# The writable Attempt filesystem has a hard byte ceiling and no executable/set-id devices.
mount -t tmpfs -o size=8M,nosuid,nodev,noexec tmpfs /run/attempt-work
printf 'own work\n' >/run/attempt-work/own.txt
python3 - /run/attempt-work/network_probe.py <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(
    """import socket
import sys

if sys.argv[1] == "public":
    sock = socket.socket()
    sock.settimeout(0.2)
    raise SystemExit(0 if sock.connect_ex(("1.1.1.1", 443)) != 0 else 1)

sock = socket.socket(socket.AF_UNIX)
sock.connect("/run/target.sock")
sock.sendall(b"ping")
raise SystemExit(0 if sock.recv(4) == b"pong" else 1)
""",
    encoding="utf-8",
)
PY

# Export a deny filter. Bubblewrap installs it only after it finishes privileged setup.
python3 - /run/control/attempt-seccomp.bpf <<'PY'
import ctypes
import os
import sys

allow = 0x7FFF0000
errno_eperm = 0x00050001
lib = ctypes.CDLL("libseccomp.so.2")
lib.seccomp_init.argtypes = [ctypes.c_uint32]
lib.seccomp_init.restype = ctypes.c_void_p
lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
lib.seccomp_rule_add.restype = ctypes.c_int
lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.seccomp_export_bpf.restype = ctypes.c_int
lib.seccomp_release.argtypes = [ctypes.c_void_p]

ctx = lib.seccomp_init(allow)
if not ctx:
    raise SystemExit("seccomp_init failed")
for name in (
    "acct", "add_key", "bpf", "delete_module", "finit_module", "fsconfig",
    "fsmount", "fsopen", "fspick", "init_module", "keyctl", "kexec_file_load",
    "kexec_load", "mount", "mount_setattr", "move_mount", "open_by_handle_at",
    "pivot_root", "quotactl", "reboot", "request_key", "setns", "swapon",
    "swapoff", "umount2", "unshare", "userfaultfd",
):
    number = lib.seccomp_syscall_resolve_name(name.encode())
    if number >= 0 and lib.seccomp_rule_add(ctx, errno_eperm, number, 0) != 0:
        raise SystemExit(f"cannot add seccomp rule for {name}")
fd = os.open(sys.argv[1], os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o640)
try:
    if lib.seccomp_export_bpf(ctx, fd) != 0:
        raise SystemExit("seccomp_export_bpf failed")
finally:
    os.close(fd)
    lib.seccomp_release(ctx)
PY
chgrp 1000 /run/control/attempt-seccomp.bpf

# A prototype-only set-id `unshare` stands in for the production fixed-policy launcher.
# Only the Supervisor identity can execute it, and it is absent from the hostile mount view.
cp /usr/bin/unshare /run/control/attempt-launcher
chown root:1000 /run/control/attempt-launcher
chmod 4750 /run/control/attempt-launcher

# A separate broker proves pathname-UNIX capability access and kernel peer identity.
chown 30000:30000 /run/broker
setpriv --reuid 30000 --regid 30000 --clear-groups \
  python3 - /run/broker/target.sock /run/broker/peer.txt <<'PY' &
import os
import socket
import struct
import sys

sock = socket.socket(socket.AF_UNIX)
sock.bind(sys.argv[1])
os.chmod(sys.argv[1], 0o666)
sock.listen(1)
sock.settimeout(5)
conn, _ = sock.accept()
conn.settimeout(5)
pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
with open(sys.argv[2], "w", encoding="ascii") as receipt:
    receipt.write(f"pid={pid} uid={uid} gid={gid}\n")
if conn.recv(16) == b"ping":
    conn.sendall(b"pong")
conn.close()
sock.close()
PY
broker_pid=$!
while [[ ! -S /run/broker/target.sock ]]; do sleep 0.01; done

mkfifo /run/control/go
chown 1000:1000 /run/control/go

# The long-lived Supervisor identity holds no capability. Its one child blocks before the
# prototype launcher, so trusted control can place the whole future tree in the Attempt cgroup.
setpriv --reuid 1000 --regid 1000 --clear-groups /bin/sh -c '
  read -r go </run/control/go
  exec 9</run/control/attempt-seccomp.bpf
  exec /run/control/attempt-launcher --mount --pid --net --uts --ipc --fork --kill-child=KILL --mount-proc \
    /usr/bin/setpriv --reuid 0 --regid 0 --clear-groups /usr/bin/bwrap \
      --die-with-parent --new-session \
      --ro-bind /usr /usr \
      --symlink usr/bin /bin \
      --symlink usr/sbin /sbin \
      --symlink usr/lib /lib \
      --dir /etc --dir /etc/ssl \
      --ro-bind /etc/ssl/certs /etc/ssl/certs \
      --proc /proc --dev /dev \
      --bind /run/attempt-work /work \
      --dir /run --ro-bind /run/broker/target.sock /run/target.sock \
      --tmpfs /tmp --chmod 1777 /tmp --dir /home --dir /home/attempt \
      --chdir /work --clearenv \
      --setenv HOME /home/attempt --setenv PATH /usr/local/bin:/usr/bin:/bin \
      --seccomp 9 \
      /usr/bin/setpriv --reuid 20000 --regid 20000 --clear-groups \
        --bounding-set=-all --no-new-privs /bin/bash -c '\''
          set -u
          pass=0
          fail=0
          record() { label=$1; shift; if "$@"; then printf "PASS %s\\n" "$label" >>/work/result.txt; pass=$((pass+1)); else printf "FAIL %s\\n" "$label" >>/work/result.txt; fail=$((fail+1)); fi; }
          record uid test "$(id -u)" = 20000
          record capabilities grep -Eq "^CapBnd:[[:space:]]*0+$" /proc/self/status
          record no_new_privs grep -Eq "^NoNewPrivs:[[:space:]]*1$" /proc/self/status
          record seccomp grep -Eq "^Seccomp:[[:space:]]*2$" /proc/self/status
          record own_work test -r /work/own.txt
          record work_write /bin/bash -c "printf writable >/work/written.txt"
          record root_read_only /bin/bash -c "! touch /usr/escape 2>/dev/null"
          record control_hidden test ! -e /run/control
          record canonical_state_hidden test ! -e /state
          record solver_code_hidden test ! -e /opt/solver
          record sibling_hidden test ! -e /run/sibling-work
          record auth_hidden test ! -e /root/.codex/auth.json
          record credential_env_absent test -z "${PROBE_CREDENTIAL+x}"
          record outer_proc_hidden /bin/bash -c "! tr \\\"\\\\0\\\" \\\"\\\\n\\\" </proc/1/environ 2>/dev/null | grep -q prototype-secret"
          record namespace_syscalls_denied /bin/bash -c "! unshare --user --map-root-user true 2>/dev/null"
          record mount_denied /bin/bash -c "mkdir -p /tmp/m && ! mount -t tmpfs tmpfs /tmp/m 2>/dev/null"
          record public_network_denied python3 /work/network_probe.py public
          record target_broker python3 /work/network_probe.py broker
          record pid_namespace /bin/bash -c "test $(find /proc -maxdepth 1 -type d -regex \\\"/proc/[0-9]+\\\" | wc -l) -lt 10"
          printf "SUMMARY pass=%s fail=%s\\n" "$pass" "$fail" >>/work/result.txt
          touch /work/ready
          sleep 60 &
          wait
        '\''
' &
attempt_pid=$!
echo "$attempt_pid" >"$container_cgroup/attempt/cgroup.procs"
printf 'go\n' >/run/control/go

for _ in $(seq 1 500); do
  [[ -e /run/attempt-work/ready ]] && break
  sleep 0.01
done
test -e /run/attempt-work/ready
cat /run/attempt-work/result.txt
wait "$broker_pid"
printf 'broker_peer=%s' "$(cat /run/broker/peer.txt)"

tree_before=$(wc -l <"$container_cgroup/attempt/cgroup.procs")
echo 1 >"$container_cgroup/attempt/cgroup.kill"
set +e
wait "$attempt_pid"
attempt_status=$?
set -e
tree_after=$(wc -l <"$container_cgroup/attempt/cgroup.procs")
printf 'process_tree_before=%s after=%s launcher_exit=%s\n' "$tree_before" "$tree_after" "$attempt_status"

# The same per-Attempt domain enforces aggregate resources over every descendant.
set +e
sh -c 'sleep 0.2; exec python3 -c "x=bytearray(80*1024*1024); print(len(x))"' &
resource_pid=$!
echo "$resource_pid" >"$container_cgroup/attempt/cgroup.procs"
wait "$resource_pid"
memory_status=$?
set -e
printf 'memory_status=%s events=%s\n' "$memory_status" "$(tr '\n' ',' <"$container_cgroup/attempt/memory.events")"

echo 6 >"$container_cgroup/attempt/pids.max"
set +e
sh -c 'sleep 0.2; for i in 1 2 3 4 5 6 7 8; do sleep 1 & done; wait' &
resource_pid=$!
echo "$resource_pid" >"$container_cgroup/attempt/cgroup.procs"
wait "$resource_pid"
pids_status=$?
set -e
printf 'pids_status=%s events=%s\n' "$pids_status" "$(tr '\n' ',' <"$container_cgroup/attempt/pids.events")"
while [[ $(cat "$container_cgroup/attempt/pids.current") -ne 0 ]]; do sleep 0.05; done
echo 16 >"$container_cgroup/attempt/pids.max"

set +e
sh -c 'sleep 0.2; exec timeout 1 yes' >/dev/null &
resource_pid=$!
echo "$resource_pid" >"$container_cgroup/attempt/cgroup.procs"
wait "$resource_pid"
cpu_status=$?
set -e
printf 'cpu_status=%s stat=%s\n' "$cpu_status" "$(tr '\n' ',' <"$container_cgroup/attempt/cpu.stat")"

sh -c 'sleep 0.2; exec dd if=/dev/zero of=/tmp/io-first.bin bs=1M count=8 oflag=direct status=none' &
resource_pid=$!
echo "$resource_pid" >"$container_cgroup/attempt/cgroup.procs"
wait "$resource_pid"
io_device=$(awk 'NR==1 { print $1 }' "$container_cgroup/attempt/io.stat")
echo "$io_device wbps=1048576" >"$container_cgroup/attempt/io.max"
io_start=$(date +%s%N)
sh -c 'sleep 0.2; exec dd if=/dev/zero of=/tmp/io-limited.bin bs=1M count=4 oflag=direct status=none' &
resource_pid=$!
echo "$resource_pid" >"$container_cgroup/attempt/cgroup.procs"
wait "$resource_pid"
io_end=$(date +%s%N)
python3 -c "print('io_limited_seconds=%.3f device=$io_device' % (($io_end-$io_start)/1e9))"
rm -f /tmp/io-first.bin /tmp/io-limited.bin

set +e
dd if=/dev/zero of=/run/attempt-work/overfill bs=1M count=12 status=none
disk_status=$?
set -e
printf 'disk_status=%s bytes_written=%s\n' "$disk_status" "$(stat -c %s /run/attempt-work/overfill)"

printf 'cgroup_limits cpu=%s memory=%s pids=%s swap=%s\n' \
  "$(cat "$container_cgroup/attempt/cpu.max")" \
  "$(cat "$container_cgroup/attempt/memory.max")" \
  "$(cat "$container_cgroup/attempt/pids.max")" \
  "$(cat "$container_cgroup/attempt/memory.swap.max")"

rm -f /run/attempt-work/overfill
umount /run/attempt-work
CONTAINER
