"""Capture initialization and cleanup tests never start capture programs."""

from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from btdos.capture import CaptureProcess, system_binary


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = CaptureProcess(
            "hci", ["btmon", "-i", "hci0"], self.root / "host-hci.btsnoop",
            self.root / "capture-hci.log", "btsnoop",
        )

    def test_start_failure_closes_log_and_remains_not_ready(self):
        with patch("btdos.capture.subprocess.Popen", side_effect=OSError("fixture start failure")) as popen:
            with self.assertRaises(OSError):
                self.capture.start()
        popen.assert_called_once()
        self.assertIsNone(self.capture.proc)
        self.assertTrue(self.capture.log_handle.closed)
        self.assertFalse(self.capture.alive())
        result = self.capture.stop()
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["target_packets_verified"])

    def test_existing_raw_file_is_never_overwritten(self):
        self.capture.raw_path.write_bytes(b"original")
        with patch("btdos.capture.subprocess.Popen") as popen:
            with self.assertRaises(RuntimeError):
                self.capture.start()
        popen.assert_not_called()
        self.assertEqual(self.capture.raw_path.read_bytes(), b"original")

    def test_popen_receives_fixed_argv_and_no_shell(self):
        process = Mock()
        process.poll.return_value = 0
        with patch("btdos.capture.subprocess.Popen", return_value=process) as popen:
            self.capture.start()
        self.addCleanup(self.capture.stop)
        args, kwargs = popen.call_args
        expected = ["btmon", "-i", "hci0"]
        if sys.platform == "linux":
            expected = [sys.executable, "-m", "btdos.capture_exec", str(os.getpid()), "--", *expected]
        self.assertEqual(args[0], expected)
        self.assertFalse(kwargs.get("shell", False))
        self.assertFalse(kwargs["start_new_session"])
        self.assertNotIn("preexec_fn", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_linux_start_preserves_session_and_uses_unprivileged_exec_wrapper(self):
        process = Mock()
        process.poll.return_value = 0
        with patch("btdos.capture.sys.platform", "linux"), patch("btdos.capture.os.getsid", return_value=777, create=True), patch("btdos.capture.subprocess.Popen", return_value=process) as popen:
            self.capture.start()
        self.addCleanup(self.capture.stop)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [sys.executable, "-m", "btdos.capture_exec", str(os.getpid()), "--", "btmon", "-i", "hci0"])
        self.assertEqual(self.capture.parent_sid, 777)
        self.assertTrue(self.capture.wrapper)
        self.assertFalse(kwargs["start_new_session"])
        self.assertNotIn("preexec_fn", kwargs)
        self.assertFalse(kwargs.get("shell", False))

    def test_unestablished_linux_group_uses_pid_signal_never_parent_group(self):
        self.capture.wrapper = True
        self.capture.parent_sid = 777
        self.capture.proc = Mock(pid=123456)
        self.capture.proc.poll.side_effect = [None, 0]
        with patch("btdos.capture.sys.platform", "linux"), patch.object(self.capture, "track_descendants"), patch.object(self.capture, "remaining_descendants", return_value=[]), patch("btdos.capture.os.getpgid", return_value=777, create=True), patch("btdos.capture.os.killpg", create=True) as kill_group:
            result = self.capture.stop()
        kill_group.assert_not_called()
        self.capture.proc.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertTrue(result["cleanup_confirmed"])

    def test_header_with_wrong_wrapper_session_is_not_ready(self):
        self.capture.wrapper = True
        self.capture.parent_sid = 777
        self.capture.proc = Mock(pid=123456)
        self.capture.proc.poll.return_value = None
        self.capture.raw_path.write_bytes(b"btsnoop\x00" + b"\x00" * 8)
        with patch("btdos.capture.os.getpgid", return_value=123456, create=True), patch("btdos.capture.os.getsid", return_value=888, create=True):
            with self.assertRaises(RuntimeError):
                self.capture.wait_ready(seconds=1)
        self.assertFalse(self.capture.ready)
        self.assertFalse(self.capture.group_verified)

    def test_early_capture_exit_never_becomes_ready(self):
        self.capture.proc = Mock()
        self.capture.proc.poll.return_value = 1
        with self.assertRaises(RuntimeError):
            self.capture.wait_ready(seconds=1)
        self.assertFalse(self.capture.ready)

    def test_absent_header_times_out_without_reporting_packet_evidence(self):
        self.capture.proc = Mock()
        self.capture.proc.poll.return_value = None
        with self.assertRaises(RuntimeError):
            self.capture.wait_ready(seconds=0)
        result = self.capture.result()
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["capture_initialized"])
        self.assertFalse(result["target_packets_verified"])

    def test_header_alone_is_not_packet_evidence(self):
        self.capture.raw_path.write_bytes(b"btsnoop\x00" + b"\x00" * 8)
        self.capture.proc = Mock()
        self.capture.proc.poll.return_value = None
        self.capture.wait_ready(seconds=1)
        result = self.capture.result()
        self.assertTrue(result["capture_initialized"])
        self.assertEqual(result["status"], "initialized_no_packet_evidence")
        self.assertFalse(result["target_packets_verified"])

    def test_data_presence_still_does_not_prove_target_packets(self):
        self.capture.raw_path.write_bytes(b"btsnoop\x00" + b"\x00" * 40)
        self.capture.proc = Mock()
        self.capture.proc.poll.return_value = None
        self.capture.wait_ready(seconds=1)
        result = self.capture.result()
        self.assertEqual(result["status"], "initialized_contains_data_unverified")
        self.assertFalse(result["target_packets_verified"])

    def test_forced_stop_is_not_reported_as_clean_capture(self):
        self.capture.forced = True
        self.assertEqual(self.capture.result()["status"], "forced_stop_resource_unconfirmed")

    def test_missing_system_capture_tool_fails_closed(self):
        with patch("btdos.capture.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                system_binary("btmon")

    def test_descendant_tracking_includes_grandchildren_but_not_other_processes(self):
        self.capture.proc = Mock(pid=100)
        snapshot = {
            100: (1, "1000", "S"), 101: (100, "1001", "S"),
            102: (101, "1002", "S"), 200: (1, "2000", "S"),
        }
        with patch("btdos.capture.sys.platform", "linux"), patch("btdos.capture._process_snapshot", return_value=snapshot):
            self.capture.track_descendants()
        self.assertEqual(self.capture.tracked, {100: "1000", 101: "1001", 102: "1002"})

    def test_pid_reuse_and_zombies_are_not_counted_as_surviving_writers(self):
        self.capture.tracked = {100: "1000", 101: "1001", 102: "1002"}
        snapshot = {
            100: (1, "1000", "Z"), 101: (1, "1001", "S"),
            102: (1, "9999", "S"),
        }
        with patch("btdos.capture._process_snapshot", return_value=snapshot):
            self.assertEqual(self.capture.remaining_descendants(), [101])

    def test_lingering_descendant_makes_cleanup_unconfirmed(self):
        self.capture.proc = Mock(pid=100)
        self.capture.proc.poll.return_value = 0
        with patch("btdos.capture.sys.platform", "linux"), patch.object(self.capture, "track_descendants"), patch.object(self.capture, "remaining_descendants", return_value=[101]), patch("btdos.capture.time.monotonic", side_effect=[0, 3]):
            result = self.capture.stop()
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["remaining_pids"], [101])

    def test_proc_permission_failure_is_unknown_not_confirmed_exit(self):
        self.capture.proc = Mock(pid=100)
        self.capture.proc.poll.return_value = 0
        self.capture.tracked = {101: "1001"}
        with patch("btdos.capture.sys.platform", "linux"), patch.object(self.capture, "track_descendants"), patch("btdos.capture._process_snapshot", return_value={}), patch("btdos.capture.Path.read_text", side_effect=PermissionError("fixture restricted proc")), patch("btdos.capture.time.monotonic", side_effect=[0, 3]):
            result = self.capture.stop()
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["remaining_pids"], [101])
        self.assertEqual(result["unknown_pids"], [101])

    def test_missing_pid_with_accessible_proc_is_confirmed_exit(self):
        self.capture.proc = Mock(pid=100)
        self.capture.proc.poll.return_value = 0
        self.capture.tracked = {101: "1001"}
        self.capture.forced = True

        def fixture_exists(path):
            return path.as_posix() == "/proc/self/stat"

        with patch("btdos.capture.sys.platform", "linux"), patch.object(self.capture, "track_descendants"), patch("btdos.capture._process_snapshot", return_value={}), patch("btdos.capture.Path.read_text", side_effect=FileNotFoundError("fixture exited pid")), patch("btdos.capture.Path.exists", new=fixture_exists):
            result = self.capture.stop()
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(result["remaining_pids"], [])
        self.assertEqual(result["unknown_pids"], [])
        self.assertEqual(result["status"], "forced_stop_processes_exited")

    def test_known_orphan_is_traversal_root_but_reused_pid_is_not(self):
        self.capture.proc = Mock(pid=100)
        self.capture.tracked = {101: "1001", 150: "old150"}
        snapshot = {
            101: (1, "1001", "S"), 102: (101, "1002", "S"),
            103: (102, "1003", "S"), 150: (1, "new150", "S"),
            151: (150, "new151", "S"), 200: (1, "other", "S"),
        }
        with patch("btdos.capture.sys.platform", "linux"), patch("btdos.capture._process_snapshot", return_value=snapshot):
            self.capture.track_descendants()
        self.assertEqual(self.capture.tracked, {101: "1001", 102: "1002", 103: "1003", 150: "old150"})


if __name__ == "__main__":
    unittest.main()
