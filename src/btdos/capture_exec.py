"""Internal unprivileged exec shim: private PGID without losing the control TTY.

No preexec_fn, no shell, no passwords, no additional privilege. The supervisor
constructs the complete argv and verifies the group/session before group signals.
"""
import ctypes
import os
import signal
import sys


def main():
    if sys.platform != "linux" or len(sys.argv) < 4 or sys.argv[2] != "--":
        raise RuntimeError("internal Linux capture exec interface")
    parent = int(sys.argv[1])
    argv = sys.argv[3:]
    if not argv or not os.path.isabs(argv[0]):
        raise ValueError("capture executable must be absolute")
    if os.getppid() != parent:
        return 1
    libc = ctypes.CDLL(None)
    if libc.prctl(1, signal.SIGINT, 0, 0, 0) != 0:
        return 1
    os.setpgid(0, 0)
    if os.getppid() != parent:
        return 1
    os.execv(argv[0], argv)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
