"""Real Linux process tests; NO Bluetooth, sudo, capture tools or radio access.

Every byte is SYNTHETIC TEST DATA in a TemporaryDirectory, never field evidence.
The deliberately synthetic btsnoop-shaped files are not evidence bundles and
must not be copied into runs/ or used as packet verification.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import btdos
from btdos.capture import CaptureProcess
from btdos.config import Plan


# This fixture produces only a recognizable file header and conspicuous TEST
# markers, not real Bluetooth packets. All three writers share a private group.
SYNTHETIC_WRITER = r'''
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

root, role, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
running = True
child = None
def stop(*_):
    global running
    running = False
signal.signal(signal.SIGINT, signal.SIG_IGN if mode == "ignore-int" else stop)
signal.signal(signal.SIGTERM, stop)
signal.alarm(25)  # Independent fixture lifetime cap, even if the test fails.
stat = Path(f"/proc/{os.getpid()}/stat").read_text()
start = stat[stat.rfind(")") + 2:].split()[19]
marker = root / f"{role}.pid.json"
pending = marker.with_suffix(".pending")
pending.write_text(json.dumps({"pid": os.getpid(), "start": start}))
pending.replace(marker)
print("SYNTHETIC TEST ONLY", role, os.getpid(), flush=True)
if role != "grandchild":
    next_role = "child" if role == "root" else "grandchild"
    code = (root / "synthetic_writer_fixture.py").read_text()
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(root), next_role, mode])
raw = root / "SYNTHETIC-TEST-ONLY.btsnoop"
try:
    if role == "root":
        deadline = time.monotonic() + 8
        while not all((root / f"{r}.pid.json").exists() for r in ("child", "grandchild")):
            if not running or time.monotonic() > deadline:
                raise RuntimeError("synthetic descendants did not initialize")
            time.sleep(0.02)
        raw.write_bytes(b"btsnoop\x00" + struct.pack(">II", 1, 1002))
        (root / "synthetic-header.ready").touch()
    while running and not (root / "synthetic-header.ready").exists():
        time.sleep(0.02)
    with raw.open("ab", buffering=0) as output:
        while running:
            output.write(("SYNTHETIC TEST ONLY " + role + "\n").encode())
            time.sleep(0.03)
finally:
    if child is not None:
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
    print("SYNTHETIC TEST ONLY exited", role, flush=True)
'''


# Exercise the actual worker.main() parent-death setup. Replace ONLY its workload
# with a local sleep loop that deliberately does not poll stopped()/getppid(), so
# termination before the worker alarm proves the installed PDEATHSIG is active.
SYNTHETIC_WORKER = r'''
from pathlib import Path
import sys
import time
from btdos import cases, worker

plan, stop_file, parent_pid, ready = sys.argv[1:]
def synthetic_wait(_args):
    Path(ready).write_text("SYNTHETIC TEST ONLY: no Bluetooth workload\n")
    while True:
        time.sleep(0.03)
cases.br_sdp = synthetic_wait
sys.argv = ["btdos.worker", "--plan", plan, "--stop-file", stop_file,
            "--parent-pid", parent_pid]
raise SystemExit(worker.main())
'''


SYNTHETIC_PARENT = r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(sys.argv[1])
signal.alarm(20)
child = subprocess.Popen([sys.executable, "-u", str(root / "synthetic_worker.py"),
    str(root / "synthetic-plan.json"), str(root / "STOP"), str(os.getpid()),
    str(root / "synthetic-worker.ready")])
stat = Path(f"/proc/{child.pid}/stat").read_text()
start = stat[stat.rfind(")") + 2:].split()[19]
marker = root / "worker.pid.json"
pending = marker.with_suffix(".pending")
pending.write_text(json.dumps({"pid": child.pid, "start": start}))
pending.replace(marker)
while True:
    time.sleep(0.03)
'''


@unittest.skipUnless(sys.platform == "linux", "Real process signals and PDEATHSIG require Linux")
class ProcessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="btdos-SYNTHETIC-TEST-ONLY-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes = []
        self.handles = []
        self.addCleanup(self.cleanup_processes)

    @staticmethod
    def identity(pid):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            rest = raw[raw.rfind(")") + 2:].split()
            return rest[19], rest[0]
        except (OSError, IndexError):
            return None

    def running(self, record):
        identity = self.identity(record["pid"])
        return identity is not None and identity[0] == record["start"] and identity[1] != "Z"

    def wait_for(self, predicate, message, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.03)
        self.fail(message)

    def records(self):
        return [json.loads(path.read_text()) for path in self.root.glob("*.pid.json")]

    def cleanup_processes(self):
        # Capture's exec shim creates a private group in the existing session;
        # the PDEATHSIG fixture parent creates a private session. Never target
        # the test runner group, including a failure before shim setpgid().
        for proc in self.processes:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for record in self.records():
            if self.running(record):
                try:
                    os.kill(record["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for proc in self.processes:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        for handle in self.handles:
            handle.close()

    def start_writer(self, mode="normal"):
        fixture = self.root / "synthetic_writer_fixture.py"
        fixture.write_text(SYNTHETIC_WRITER, encoding="utf-8")
        capture = CaptureProcess(
            "SYNTHETIC-TEST-ONLY", [sys.executable, "-u", "-c", SYNTHETIC_WRITER, str(self.root), "root", mode],
            self.root / "SYNTHETIC-TEST-ONLY.btsnoop", self.root / "SYNTHETIC-TEST-ONLY.log", "btsnoop",
        )
        capture.start()
        self.processes.append(capture.proc)
        self.handles.append(capture.log_handle)
        capture.wait_ready(seconds=10)
        self.assertTrue(capture.group_verified)
        self.assertEqual(os.getpgid(capture.proc.pid), capture.proc.pid)
        self.assertEqual(os.getsid(capture.proc.pid), os.getsid(0))
        self.assertEqual(len(self.records()), 3)
        self.assertEqual(set(capture.tracked), {row["pid"] for row in self.records()})
        self.wait_for(lambda: capture.raw_path.stat().st_size > 16, "synthetic writers did not append test markers")
        return capture

    def assert_stopped_and_stable(self, capture, result):
        self.assertIsNotNone(capture.proc.poll())
        self.assertTrue(capture.log_handle.closed)
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(result["remaining_pids"], [])
        self.assertTrue(all(not self.running(row) for row in self.records()))
        snapshot = (capture.raw_path.read_bytes(), capture.raw_path.stat().st_mtime_ns)
        time.sleep(0.2)
        self.assertEqual(snapshot, (capture.raw_path.read_bytes(), capture.raw_path.stat().st_mtime_ns))
        self.assertFalse((self.root / "manifest.json").exists())
        self.assertFalse(result["target_packets_verified"])

    def test_real_capture_stop_reaps_process_tree_and_freezes_synthetic_file(self):
        capture = self.start_writer()
        result = capture.stop()
        self.assertFalse(result["forced"])
        self.assertEqual(capture.proc.returncode, 0)
        self.assert_stopped_and_stable(capture, result)

    def test_wrapper_exec_failure_is_cleaned_without_group_readiness(self):
        capture = CaptureProcess(
            "SYNTHETIC-TEST-ONLY", [str(self.root / "NONEXISTENT-SYNTHETIC-EXECUTABLE")],
            self.root / "SYNTHETIC-EXEC-FAILURE.btsnoop", self.root / "SYNTHETIC-EXEC-FAILURE.log", "btsnoop",
        )
        capture.start()
        self.processes.append(capture.proc)
        self.handles.append(capture.log_handle)
        with self.assertRaises(RuntimeError):
            capture.wait_ready(seconds=5)
        result = capture.stop()
        self.assertIsNotNone(capture.proc.poll())
        self.assertFalse(result["capture_initialized"])
        self.assertFalse(result["group_verified"])
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(result["remaining_pids"], [])
        self.assertEqual(capture.raw_path.read_bytes(), b"")
        self.assertTrue(capture.log_handle.closed)

    def test_ignored_sigint_requires_escalation_and_is_marked_forced(self):
        capture = self.start_writer(mode="ignore-int")
        started = time.monotonic()
        result = capture.stop()
        self.assertLess(time.monotonic() - started, 12)
        self.assertTrue(result["forced"])
        self.assertNotEqual(result["status"], "completed")
        self.assertIn("forced", result["status"])
        self.assert_stopped_and_stable(capture, result)

    def test_actual_worker_exits_on_parent_death_without_radio_workload(self):
        (self.root / "synthetic_worker.py").write_text(SYNTHETIC_WORKER, encoding="utf-8")
        parent_script = self.root / "synthetic_parent.py"
        parent_script.write_text(SYNTHETIC_PARENT, encoding="utf-8")
        plan = Plan.from_dict({
            "case": "br-sdp", "adapter": "hci0", "target": "02:00:00:00:00:01",
            "local_address": "02:00:00:00:00:02", "duration": 60,
            "baseline": 0, "recovery": 0, "output_root": str(self.root),
        })
        (self.root / "synthetic-plan.json").write_text(json.dumps(plan.to_dict()), encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        source = str(Path(btdos.__file__).resolve().parent.parent)
        env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
        log = (self.root / "SYNTHETIC-WORKER-ONLY.log").open("wb")
        self.handles.append(log)
        parent = subprocess.Popen(
            [sys.executable, "-u", str(parent_script), str(self.root)], env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.processes.append(parent)
        self.wait_for(lambda: (self.root / "synthetic-worker.ready").exists(), "synthetic worker did not enter local-only workload")
        record = json.loads((self.root / "worker.pid.json").read_text())
        self.assertTrue(self.running(record))
        # Terminate only the supervisor, NOT its process group. The real worker's
        # installed prctl(PDEATHSIG, SIGINT) must end the independent sleep loop.
        parent.terminate()
        parent.wait(timeout=3)
        self.wait_for(lambda: not self.running(record), "worker survived supervisor death despite PDEATHSIG", timeout=5)
        text = (self.root / "SYNTHETIC-WORKER-ONLY.log").read_text(encoding="utf-8")
        self.assertIn('"event": "worker_end"', text)
        self.assertIn('"status": "interrupted"', text)
        self.assertFalse((self.root / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
