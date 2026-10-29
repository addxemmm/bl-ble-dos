"""Fixed-argv capture adapters; no arbitrary privileged command interface."""
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time

from .doctor import command


def _process_snapshot():
    """Linux pid -> (ppid, starttime, state); starttime protects against pid reuse."""
    if sys.platform != "linux":
        return {}
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = path.read_text()
            rest = raw[raw.rfind(")")+2:].split()
            result[int(path.parent.name)] = (int(rest[1]), rest[19], rest[0])
        except (OSError, ValueError, IndexError):
            continue
    return result


def system_binary(name):
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"缺少系统工具 {name}")
    path = Path(found).resolve()
    if sys.platform == "linux":
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError(f"提权工具必须由root拥有且非组/其他用户可写: {path}")
        if not any(path.is_relative_to(base) for base in (Path("/usr/bin"), Path("/usr/sbin"), Path("/bin"), Path("/sbin"))):
            raise RuntimeError(f"不使用非系统路径提权: {path}")
    return str(path)


class CaptureProcess:
    def __init__(self, name, argv, raw_path, log_path, kind):
        self.name, self.argv = name, list(argv)
        self.raw_path, self.log_path = Path(raw_path), Path(log_path)
        self.kind = kind
        self.proc = None
        self.log_handle = None
        self.forced = False
        self.ready = False
        self.tracked = {}
        self.cleanup_confirmed = False
        self.last_track = 0
        self.wrapper = False
        self.parent_sid = None
        self.group_verified = False
        self.unknown_pids = []

    def track_descendants(self):
        if not self.proc or sys.platform != "linux":
            return
        snapshot = _process_snapshot()
        wanted = {self.proc.pid} | {pid for pid, start in self.tracked.items()
                                    if pid in snapshot and snapshot[pid][1] == start}
        changed = True
        while changed:
            added = {pid for pid, (ppid, _, _) in snapshot.items() if ppid in wanted}
            changed = not added.issubset(wanted)
            wanted.update(added)
        for pid in wanted:
            if pid in snapshot:
                self.tracked[pid] = snapshot[pid][1]
        self.last_track = time.monotonic()

    def remaining_descendants(self):
        snapshot = _process_snapshot()
        remaining, unknown = [], []
        for pid, start in self.tracked.items():
            info = snapshot.get(pid)
            if info is None:
                # Missing snapshot entry is not proof of exit: /proc may be restricted.
                try:
                    raw = Path(f"/proc/{pid}/stat").read_text()
                    rest = raw[raw.rfind(")")+2:].split()
                    info = (int(rest[1]), rest[19], rest[0])
                except FileNotFoundError:
                    if not Path("/proc/self/stat").exists():
                        unknown.append(pid)
                    continue
                except (OSError, ValueError, IndexError):
                    unknown.append(pid)
                    continue
            if info[1] == start and info[2] != "Z":
                remaining.append(pid)
        self.unknown_pids = unknown
        return remaining + unknown

    def start(self):
        if self.raw_path.exists() or self.raw_path.is_symlink():
            raise RuntimeError("采集输出已存在，拒绝覆盖")
        # User-owned private output avoids unreadable root-owned capture files.
        self.raw_path.touch(mode=0o600, exist_ok=False)
        self.log_handle = self.log_path.open("xb")
        try:
            argv = self.argv
            if sys.platform == "linux":
                self.wrapper = True
                self.parent_sid = os.getsid(0)
                argv = [sys.executable, "-m", "btdos.capture_exec", str(os.getpid()), "--", *self.argv]
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                         stdout=self.log_handle, stderr=subprocess.STDOUT,
                                         start_new_session=False)
        except BaseException:
            self.log_handle.close()
            raise

    def _header_ready(self):
        try:
            with self.raw_path.open("rb") as handle:
                header = handle.read(24)
            if self.kind == "btsnoop":
                return len(header) >= 16 and header[:8] == b"btsnoop\x00"
            return len(header) >= 24 and header[:4] in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d", b"\x0a\x0d\x0d\x0a")
        except OSError:
            return False

    def wait_ready(self, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} 提前退出，查看 {self.log_path.name}")
            if self._header_ready():
                if self.wrapper:
                    self.group_verified = (os.getpgid(self.proc.pid) == self.proc.pid and
                                           os.getsid(self.proc.pid) == self.parent_sid)
                    if not self.group_verified:
                        raise RuntimeError("采集进程组/控制TTY会话校验失败")
                self.track_descendants()
                self.ready = True
                return
            time.sleep(0.1)
        raise RuntimeError(f"{self.name} 未形成有效文件头；负载不启动")

    def alive(self):
        alive = self.proc is not None and self.proc.poll() is None
        if alive and time.monotonic()-self.last_track > 1:
            self.track_descendants()
        return alive

    def stop(self):
        self.track_descendants()
        valid_group = False
        if self.proc is not None and self.proc.poll() is None:
            try:
                # sudo/timeout forward INT to managed children; outer timeout bounds any orphan.
                if sys.platform == "linux" and self.wrapper:
                    valid_group = (os.getpgid(self.proc.pid) == self.proc.pid and
                                   os.getsid(self.proc.pid) == self.parent_sid)
                    if valid_group:
                        os.killpg(self.proc.pid, signal.SIGINT)
                    else:
                        self.proc.send_signal(signal.SIGINT)
                elif os.name == "posix":
                    os.killpg(self.proc.pid, signal.SIGINT)
                else:
                    self.proc.terminate()
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.forced = True
                try:
                    if sys.platform == "linux" and self.wrapper and not valid_group:
                        self.proc.terminate()
                    elif os.name == "posix":
                        os.killpg(self.proc.pid, signal.SIGTERM)
                    else:
                        self.proc.kill()
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
        if self.log_handle:
            self.log_handle.close()
        if sys.platform == "linux":
            end = time.monotonic()+2
            while self.remaining_descendants() and time.monotonic() < end:
                time.sleep(0.1)
            self.cleanup_confirmed = not self.remaining_descendants()
        else:
            self.cleanup_confirmed = self.proc is None or self.proc.poll() is not None
        return self.result()

    def result(self):
        size = self.raw_path.stat().st_size if self.raw_path.exists() else 0
        header_size = 16 if self.kind == "btsnoop" else 24
        return dict(name=self.name, file=self.raw_path.name, bytes=size,
                    status=("forced_stop_processes_exited" if self.forced and self.cleanup_confirmed else
                            "forced_stop_resource_unconfirmed" if self.forced else
                            "initialized_contains_data_unverified" if self.ready and size > header_size else
                            "initialized_no_packet_evidence" if self.ready else "not_ready"),
                    returncode=self.proc.poll() if self.proc else None,
                    capture_initialized=self.ready, target_packets_verified=False,
                    forced=self.forced, cleanup_confirmed=self.cleanup_confirmed,
                    group_verified=self.group_verified,
                    unknown_pids=self.unknown_pids,
                    remaining_pids=self.remaining_descendants() if sys.platform == "linux" else [])


def build_captures(plan, folder):
    timeout = system_binary("timeout")
    btmon = system_binary("btmon")
    budget = str(plan.wall_budget + 15) + "s"
    base = [timeout, "--signal=INT", "--kill-after=3s", budget, btmon,
            "-i", plan.adapter, "-w", str(folder / "host-hci.btsnoop")]
    if os.geteuid() != 0:
        sudo = system_binary("sudo")
        if command([sudo, "-n", "true"])["code"] != 0:
            raise RuntimeError("HCI采集需要权限：先在终端执行 sudo -v，再以普通用户启动工具")
        base = [sudo, "-n", "--", *base]
    captures = [CaptureProcess("hci", base, folder / "host-hci.btsnoop", folder / "capture-hci.log", "btsnoop")]
    if plan.capture == "hci+nrf":
        nrfutil = shutil.which("nrfutil")
        if not nrfutil:
            raise RuntimeError("未安装 nrfutil；可选择单dongle HCI模式")
        help_result = command([nrfutil, "ble-sniffer", "sniff", "--help"], timeout=8)
        help_text = help_result["stdout"] + help_result["stderr"]
        if help_result["code"] != 0 or not all(flag in help_text for flag in ("--port", "--follow", "--output-pcap-file")):
            raise RuntimeError("nrfutil ble-sniffer接口不匹配，未启动负载")
        argv = [timeout, "--signal=INT", "--kill-after=3s", budget,
                nrfutil, "ble-sniffer", "sniff", "--port", plan.nrf_port,
                "--follow", plan.target, "--output-pcap-file", str(folder / "air.pcap")]
        captures.append(CaptureProcess("nrf", argv, folder / "air.pcap", folder / "capture-nrf.log", "pcap"))
    return captures
