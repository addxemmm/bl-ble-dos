"""Supervision tests use injected capture doubles and never start workloads."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from btdos.config import Plan
from btdos.evidence import EvidenceBundle, verify_bundle
from btdos.supervisor import _run_locked, adapter_lock, create_demo, worker_statistics


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plan(self):
        return Plan.from_dict({
            "case": "br-sdp", "adapter": "hci0", "target": "02:00:00:00:00:01",
            "local_address": "02:00:00:00:00:02", "baseline": 0, "recovery": 0,
            "output_root": str(self.root / "runs"),
        })

    def result(self, path):
        return json.loads((path / "result.json").read_text(encoding="utf-8"))

    def fake_capture(self):
        capture = Mock()
        capture.name = "fixture-hci"
        capture.alive.return_value = True
        capture.stop.return_value = {
            "name": "fixture-hci", "status": "not_ready", "file": None,
            "capture_initialized": False, "target_packets_verified": False,
            "cleanup_confirmed": True, "forced": False,
        }
        return capture

    def run_with_capture(self, capture, on_update=None):
        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={"fixture": True}), patch("btdos.supervisor.build_captures", return_value=[capture]), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen") as popen:
            path, status = _run_locked(self.plan(), on_update=on_update)
        popen.assert_not_called()
        self.assertEqual(verify_bundle(path), {"ok": True, "errors": []})
        self.assertFalse((path / "worker.jsonl").exists())
        capture.stop.assert_called_once()
        return path, status

    def test_capture_start_failure_prevents_workload_and_seals_partial_evidence(self):
        capture = self.fake_capture()
        capture.start.side_effect = RuntimeError("fixture capture start failed")
        path, status = self.run_with_capture(capture)
        self.assertEqual(status, "failed")
        result = self.result(path)
        self.assertEqual(result["status"], "failed")
        self.assertIn("fixture capture start failed", result["error"])
        self.assertEqual(result["packet_verification"], "manual_required")
        capture.wait_ready.assert_not_called()

    def test_capture_readiness_failure_prevents_workload(self):
        capture = self.fake_capture()
        capture.wait_ready.side_effect = RuntimeError("fixture no capture header")
        path, status = self.run_with_capture(capture)
        self.assertEqual(status, "failed")
        self.assertIn("fixture no capture header", self.result(path)["error"])

    def test_capture_exit_before_baseline_prevents_workload(self):
        capture = self.fake_capture()
        capture.alive.return_value = False
        path, status = self.run_with_capture(capture)
        self.assertEqual(status, "failed")
        self.assertTrue(self.result(path)["error"])

    def test_stop_request_before_start_prevents_capture_and_workload(self):
        capture = self.fake_capture()

        def request_stop(phase, folder, elapsed):
            if phase == "CAPTURE_START":
                (folder / "STOP").touch()

        path, status = self.run_with_capture(capture, on_update=request_stop)
        self.assertEqual(status, "interrupted")
        self.assertTrue((path / "STOP").exists())
        capture.start.assert_not_called()

    def test_worker_exit_codes_preserve_execution_status_not_compliance_verdict(self):
        for code, expected in ((0, "completed"), (1, "worker_failed"), (2, "needs_continuation")):
            with self.subTest(code=code):
                capture = self.fake_capture()
                worker = Mock()
                worker.poll.return_value = code
                worker.returncode = code
                phases = []
                with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={"fixture": True}), patch("btdos.supervisor.build_captures", return_value=[capture]), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker) as popen:
                    path, status = _run_locked(self.plan(), on_update=lambda phase, *_: phases.append(phase))
                popen.assert_called_once()
                args, kwargs = popen.call_args
                self.assertIn("btdos.worker", args[0])
                self.assertFalse(kwargs.get("shell", False))
                self.assertEqual(status, expected)
                self.assertEqual(self.result(path)["status"], expected)
                self.assertEqual(self.result(path)["packet_verification"], "manual_required")
                self.assertIn("RECOVERY", phases)
                self.assertEqual(phases[-1], "FINALIZE")
                self.assertTrue(verify_bundle(path)["ok"])

    def test_capture_death_during_run_stops_active_worker(self):
        capture = self.fake_capture()
        # Readiness guard, pre-worker guard, then first guard with worker running.
        capture.alive.side_effect = [True, True, False]
        worker = Mock()
        worker.poll.return_value = None

        def confirm_worker_stopped(proc):
            proc.poll.return_value = 130
            proc.returncode = 130
            return False

        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=[capture]), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker) as popen, patch("btdos.supervisor.stop_worker", side_effect=confirm_worker_stopped) as stop:
            path, status = _run_locked(self.plan())
        self.assertEqual(status, "failed")
        stop.assert_called_once_with(worker)
        capture.stop.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["stdout"].closed)
        self.assertTrue(verify_bundle(path)["ok"])

    def test_worker_stop_failure_still_closes_logs_and_all_captures(self):
        captures = [self.fake_capture(), self.fake_capture()]
        captures[1].name = "fixture-nrf"
        worker = Mock()
        worker.poll.return_value = 0
        worker.returncode = 0
        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=captures), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker) as popen, patch("btdos.supervisor.stop_worker", side_effect=OSError("fixture worker stop failed")):
            path, status = _run_locked(self.plan())
        self.assertEqual(status, "cleanup_forced")
        self.assertTrue(popen.call_args.kwargs["stdout"].closed)
        for capture in captures:
            capture.stop.assert_called_once()
        result = self.result(path)
        self.assertTrue(result["worker_forced"])
        self.assertTrue(any("fixture worker stop failed" in item for item in result["cleanup_errors"]))
        self.assertTrue(verify_bundle(path)["ok"])

    def test_worker_log_close_failure_still_closes_all_captures(self):
        captures = [self.fake_capture(), self.fake_capture()]
        worker = Mock()
        worker.poll.return_value = 0
        worker.returncode = 0
        worker_log = Mock()
        worker_log.close.side_effect = OSError("fixture worker log close failed")
        original_open = Path.open

        def fixture_open(path, *args, **kwargs):
            if path.name == "worker.jsonl" and args and args[0] == "xb":
                return worker_log
            return original_open(path, *args, **kwargs)

        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=captures), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker), patch.object(Path, "open", new=fixture_open):
            path, status = _run_locked(self.plan())
        self.assertEqual(status, "cleanup_failed")
        worker_log.close.assert_called_once()
        for capture in captures:
            capture.stop.assert_called_once()
        self.assertTrue(any("fixture worker log close failed" in item for item in self.result(path)["cleanup_errors"]))
        self.assertTrue(verify_bundle(path)["ok"])

    def test_capture_cleanup_failure_does_not_skip_other_capture(self):
        captures = [self.fake_capture(), self.fake_capture()]
        captures[1].name = "fixture-nrf"
        captures[1].stop.side_effect = OSError("fixture capture cleanup failed")
        worker = Mock()
        worker.poll.return_value = 0
        worker.returncode = 0
        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=captures), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker):
            path, status = _run_locked(self.plan())
        self.assertEqual(status, "cleanup_unconfirmed")
        for capture in captures:
            capture.stop.assert_called_once()
        self.assertTrue(self.result(path)["cleanup_errors"])
        self.assertFalse(verify_bundle(path)["ok"])
        self.assertTrue((path / "UNSEALED.json").exists())
        self.assertFalse((path / "manifest.json").exists())

    def test_unconfirmed_capture_exit_is_not_completed_or_sealed(self):
        capture = self.fake_capture()
        capture.stop.return_value["cleanup_confirmed"] = False
        capture.stop.return_value["remaining_pids"] = [12345]
        worker = Mock()
        worker.poll.return_value = 0
        worker.returncode = 0
        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=[capture]), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker):
            path, status = _run_locked(self.plan())
        self.assertEqual(status, "cleanup_unconfirmed")
        self.assertEqual(self.result(path)["status"], "cleanup_unconfirmed")
        self.assertTrue((path / "report.md").exists())
        self.assertTrue((path / "UNSEALED.json").exists())
        self.assertFalse((path / "manifest.json").exists())
        self.assertFalse(verify_bundle(path)["ok"])

    def test_forced_but_confirmed_capture_exit_has_noncompleted_status(self):
        capture = self.fake_capture()
        capture.stop.return_value["forced"] = True
        worker = Mock()
        worker.poll.return_value = 0
        worker.returncode = 0
        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=[capture]), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker):
            path, status = _run_locked(self.plan())
        self.assertEqual(status, "cleanup_forced")
        self.assertTrue(verify_bundle(path)["ok"])

    def test_active_worker_stop_failure_does_not_seal_bundle(self):
        capture = self.fake_capture()
        capture.alive.side_effect = [True, True, False]
        worker = Mock()
        worker.poll.return_value = None
        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=[capture]), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker), patch("btdos.supervisor.stop_worker", side_effect=OSError("fixture active worker stop failed")):
            path, status = _run_locked(self.plan())
        capture.stop.assert_called_once()
        self.assertEqual(status, "cleanup_unconfirmed")
        self.assertFalse((path / "manifest.json").exists())
        self.assertTrue((path / "UNSEALED.json").exists())

    def test_log_write_failure_still_closes_all_captures_and_marks_unsealed(self):
        captures = [self.fake_capture(), self.fake_capture()]
        captures[1].name = "fixture-nrf"
        worker = Mock()
        worker.poll.return_value = 0
        worker.returncode = 0
        original_emit = EvidenceBundle.emit

        def fail_on_cleanup_event(bundle, event, **data):
            if event == "phase" and data.get("state") == "CAPTURE_STOP":
                raise OSError("fixture evidence disk failure")
            return original_emit(bundle, event, **data)

        with patch("btdos.supervisor.inspect_plan_adapter", return_value={"adapter": "hci0"}), patch("btdos.supervisor.diagnose", return_value={}), patch("btdos.supervisor.build_captures", return_value=captures), patch("btdos.supervisor.export_index", return_value={"status": "not_available_or_empty"}), patch("btdos.supervisor.subprocess.Popen", return_value=worker) as popen, patch.object(EvidenceBundle, "emit", new=fail_on_cleanup_event):
            with self.assertRaises(RuntimeError):
                _run_locked(self.plan())
        for capture in captures:
            capture.stop.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["stdout"].closed)
        runs = list((self.root / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertTrue((runs[0] / "UNSEALED.json").exists())
        self.assertFalse((runs[0] / "manifest.json").exists())
        self.assertFalse(verify_bundle(runs[0])["ok"])

    @unittest.skipUnless(sys.platform == "linux", "flock locking is implemented on Linux")
    def test_adapter_lock_conflicts_then_releases_without_unlinking_inode(self):
        with patch("btdos.supervisor.tempfile.gettempdir", return_value=str(self.root)):
            with adapter_lock("hci0"):
                lock = self.root / f"btdos-{os.getuid()}-hci0.lock"
                inode = lock.stat().st_ino
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(RuntimeError):
                    with adapter_lock("hci0"):
                        self.fail("second lock acquired for same adapter")
            self.assertTrue(lock.exists())
            with adapter_lock("hci0"):
                self.assertEqual(lock.stat().st_ino, inode)

    def test_demo_is_pure_synthetic_and_never_opens_hardware(self):
        with patch("btdos.supervisor.build_captures") as capture, patch("btdos.supervisor.inspect_plan_adapter") as inspect, patch("btdos.supervisor.diagnose") as doctor, patch("btdos.supervisor.subprocess.Popen") as popen, patch("socket.socket") as socket:
            path = create_demo(self.root / "demo")
        for function in (capture, inspect, doctor, popen, socket):
            function.assert_not_called()
        result = self.result(path)
        self.assertTrue(result["simulation"])
        self.assertEqual(result["status"], "simulation_completed")
        self.assertEqual(result["packet_verification"], "simulation_no_wire_evidence")
        self.assertEqual(result["captures"], [])
        self.assertFalse(any(path.rglob("*.pcap")))
        self.assertFalse(any(path.rglob("*.pcapng")))
        self.assertFalse(any(path.rglob("*.btsnoop")))
        events = [json.loads(line) for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(row["simulation"] for row in events))
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertIn("SIMULATION", report)
        self.assertIn("not_provided", report)
        self.assertEqual(verify_bundle(path), {"ok": True, "errors": []})

    def test_worker_statistics_ignores_truncated_json_lines(self):
        path = self.root / "worker.jsonl"
        rows = [json.dumps({"event": "sdp_attempt"}), "incomplete {", json.dumps({"event": "sdp_response"}), json.dumps({"event": "sdp_attempt"})]
        path.write_text("\n".join(rows), encoding="utf-8")
        stats = worker_statistics(path)
        self.assertEqual(stats["events"], {"sdp_attempt": 2, "sdp_response": 1})
        self.assertEqual(stats["last_worker_event"], {"event": "sdp_attempt"})


if __name__ == "__main__":
    unittest.main()
