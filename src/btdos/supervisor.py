"""Own capture/workload lifetimes; preserve partial evidence on every stop path."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .capture import build_captures
from .config import Plan
from .doctor import diagnose, inspect_plan_adapter
from .evidence import EvidenceBundle, write_json, utc
from . import __version__


@contextmanager
def adapter_lock(adapter):
    if sys.platform != "linux":
        raise RuntimeError("实时测试需要 Linux")
    import fcntl
    path = Path(tempfile.gettempdir()) / f"btdos-{os.getuid()}-{adapter}.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"{adapter} 已有本工具任务运行") from exc
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        # Keep the inode: unlinking a flock file allows parallel lock races.


def stop_worker(proc):
    if proc is None or proc.poll() is not None:
        return False
    forced = False
    for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return forced
        try:
            proc.wait(timeout=wait)
            return forced
        except subprocess.TimeoutExpired:
            forced = True
    return forced


def worker_statistics(path):
    counts = {}
    last = None
    if not Path(path).exists():
        return dict(events=counts, last_worker_event=None)
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("event", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
            last = event
    return dict(events=counts, last_worker_event=last,
                note="API/worker事件，非经目标关联核实的空口PDU数")


def export_index(folder):
    tshark = shutil.which("tshark")
    source = folder / "host-hci.btsnoop"
    if not tshark or not source.exists() or source.stat().st_size <= 16:
        return dict(status="not_available_or_empty", target_verification="manual_required")
    argv = [tshark, "-r", str(source), "-Y", "bthci_evt || btl2cap || btsdp || btatt",
            "-T", "fields", "-E", "header=y", "-E", "separator=,", "-E", "quote=d",
            "-e", "frame.number", "-e", "frame.time_epoch", "-e", "_ws.col.Protocol", "-e", "_ws.col.Info"]
    try:
        with (folder / "packet-index.csv").open("xb") as out, (folder / "tshark.log").open("xb") as err:
            proc = subprocess.run(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL, timeout=15)
        return dict(status="exported_unverified" if proc.returncode == 0 else "export_failed",
                    returncode=proc.returncode, target_verification="manual_required")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(status="export_failed", error=str(exc), target_verification="manual_required")


def run_plan(plan, on_update=None):
    plan.validate()
    if sys.platform != "linux":
        raise RuntimeError("实时运行需要 Linux；Windows 可开发、doctor、demo、report、verify")
    with adapter_lock(plan.adapter):
        return _run_locked(plan, on_update)


def _run_locked(plan, on_update=None):
    bundle = EvidenceBundle(Path(plan.output_root), plan.to_dict())
    captures, results = [], []
    worker = None
    worker_handle = None
    status, error, phase = "failed", None, "VALIDATE"
    started = time.monotonic()
    stop_path = bundle.path / "STOP"
    forced = False
    last_heartbeat = 0

    def state(value):
        nonlocal phase
        phase = value
        bundle.emit("phase", state=value)
        if on_update:
            on_update(value, bundle.path, time.monotonic()-started)

    def guard(check_capture=True):
        nonlocal last_heartbeat
        if stop_path.exists():
            raise InterruptedError("STOP 文件请求停止")
        now = time.monotonic()
        if now-started > plan.wall_budget:
            raise TimeoutError("supervisor 总墙钟预算到期")
        if check_capture and any(not item.alive() for item in captures):
            raise RuntimeError("必选采集进程中途退出，停止负载")
        if now-last_heartbeat > 1:
            if shutil.disk_usage(bundle.path).free < 32*1024*1024:
                raise RuntimeError("证据磁盘剩余空间不足32MiB")
            if sum(p.stat().st_size for p in bundle.path.iterdir() if p.is_file()) > 128*1024*1024:
                raise RuntimeError("证据包超过128MiB上限")
            last_heartbeat = now
        if on_update:
            on_update(phase, bundle.path, now-started)

    def observe(seconds):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            guard()
            time.sleep(min(0.1, max(0, end-time.monotonic())))

    try:
        state("VALIDATE")
        info = inspect_plan_adapter(plan)
        write_json(bundle.path / "environment.json", diagnose())
        bundle.emit("adapter_bound", **info)
        captures = build_captures(plan, bundle.path)
        state("CAPTURE_START")
        for capture in captures:
            guard(check_capture=False)
            capture.start()
            capture.wait_ready()
            bundle.emit("capture_initialized", name=capture.name,
                        note="有效文件头不等于目标包已捕获")
        guard()
        state("BASELINE")
        observe(plan.baseline)
        state("RUN")
        guard()
        worker_handle = (bundle.path / "worker.jsonl").open("xb")
        argv = [sys.executable, "-u", "-m", "btdos.worker", "--plan", str(bundle.path / "plan.json"),
                "--stop-file", str(stop_path), "--parent-pid", str(os.getpid())]
        worker = subprocess.Popen(argv, stdout=worker_handle, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, start_new_session=True)
        worker_started = time.monotonic()
        while worker.poll() is None:
            guard()
            if time.monotonic()-worker_started > plan.worker_budget:
                raise TimeoutError("worker 预算到期")
            time.sleep(0.1)
        bundle.emit("worker_finished", returncode=worker.returncode)
        status = "completed" if worker.returncode == 0 else "needs_continuation" if worker.returncode == 2 else "worker_failed"
        state("RECOVERY")
        observe(plan.recovery)
    except (KeyboardInterrupt, InterruptedError) as exc:
        status, error = "interrupted", str(exc) or "SIGINT"
    except Exception as exc:
        status, error = "failed", str(exc)
    finally:
        cleanup_errors = []
        # Cleanup must not depend on working logging, UI callbacks, or a healthy worker.
        try:
            forced = stop_worker(worker)
        except BaseException as exc:
            forced = True
            cleanup_errors.append(f"worker cleanup: {exc}")
        try:
            if worker_handle:
                worker_handle.close()
        except BaseException as exc:
            cleanup_errors.append(f"worker log close: {exc}")
        if status not in ("completed", "needs_continuation", "worker_failed"):
            try:
                time.sleep(min(2, plan.recovery))
            except KeyboardInterrupt:
                pass
        for capture in reversed(captures):
            try:
                results.append(capture.stop())
            except BaseException as exc:
                results.append(dict(name=capture.name, status="cleanup_failed", error=str(exc), cleanup_confirmed=False))
                cleanup_errors.append(f"{capture.name} cleanup: {exc}")
        if cleanup_errors:
            status = "cleanup_failed"
        try:
            worker_unconfirmed = worker is not None and worker.poll() is None
        except BaseException:
            worker_unconfirmed = True
        unconfirmed = worker_unconfirmed or any(not item.get("cleanup_confirmed", True) for item in results)
        if unconfirmed:
            status = "cleanup_unconfirmed"
        elif any(item.get("forced") for item in results) or forced:
            status = "cleanup_forced"
        try:
            state("CAPTURE_STOP")
            state("FINALIZE")
            export = (dict(status="not_run_cleanup_unconfirmed") if unconfirmed else export_index(bundle.path))
            bundle.emit("packet_index", **export)
            extra = dict(error=error, captures=list(reversed(results)),
                worker_forced=forced, cleanup_errors=cleanup_errors,
                worker_statistics=worker_statistics(bundle.path / "worker.jsonl"),
                packet_verification="manual_required", packet_index=export)
            if unconfirmed:
                from .report import render_report
                metadata = dict(schema_version=1, tool_version=__version__, simulation=False,
                                status=status, started=bundle.started, finished=utc(), **extra)
                write_json(bundle.path / "result.json", metadata)
                (bundle.path / "report.md").write_text(render_report(plan.to_dict(), metadata), encoding="utf-8")
                write_json(bundle.path / "UNSEALED.json", dict(status=status,
                           reason="采集写入者退出未确认；原始文件可能仍变化，未生成完整性清单"))
            else:
                bundle.finalize(status, extra)
        except BaseException as exc:
            # Never pretend the evidence sealed successfully after a disk or cleanup failure.
            try:
                write_json(bundle.path / "UNSEALED.json", dict(status="unsealed", error=str(exc)))
            except OSError:
                pass
            raise RuntimeError(f"证据未封存，请保留目录 {bundle.path}: {exc}") from exc
    return bundle.path, status


def create_demo(output_root):
    plan = Plan(case="br-sdp", adapter="hci0", target="02:00:00:00:00:01",
                local_address="02:00:00:00:00:02", output_root=str(output_root)).to_dict()
    bundle = EvidenceBundle(Path(output_root), plan, simulation=True)
    for phase in ("VALIDATE", "CAPTURE_START", "BASELINE", "RUN", "RECOVERY", "CAPTURE_STOP", "FINALIZE"):
        bundle.emit("phase", state=phase, synthetic=True)
    bundle.emit("synthetic_transaction", tid=1, note="非真实SDP响应，无原始抓包")
    bundle.finalize("simulation_completed", dict(captures=[], packet_verification="simulation_no_wire_evidence",
                    worker_statistics=dict(synthetic_transactions=1)))
    return bundle.path
