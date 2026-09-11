"""One fixed syscall ceiling shared by strict preflight and production Attempt workers."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


DENIED_SYSCALLS = (
    "acct",
    "add_key",
    "bpf",
    "delete_module",
    "finit_module",
    "fsconfig",
    "fsmount",
    "fsopen",
    "fspick",
    "init_module",
    "keyctl",
    "kexec_file_load",
    "kexec_load",
    "mount",
    "mount_setattr",
    "move_mount",
    "open_by_handle_at",
    "pivot_root",
    "quotactl",
    "reboot",
    "request_key",
    "setns",
    "swapon",
    "swapoff",
    "umount2",
    "unshare",
    "userfaultfd",
)


def write_seccomp_policy(path: Path) -> None:
    allow = 0x7FFF0000
    errno_eperm = 0x00050001
    library = ctypes.CDLL("libseccomp.so.2")
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.seccomp_export_bpf.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(allow)
    if not context:
        raise RuntimeError("seccomp_init failed")
    try:
        for name in DENIED_SYSCALLS:
            number = library.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and library.seccomp_rule_add(context, errno_eperm, number, 0) != 0:
                raise RuntimeError(f"cannot add seccomp rule for {name}")
        descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            if library.seccomp_export_bpf(context, descriptor) != 0:
                raise RuntimeError("seccomp_export_bpf failed")
        finally:
            os.close(descriptor)
    finally:
        library.seccomp_release(context)


__all__ = ["DENIED_SYSCALLS", "write_seccomp_policy"]
