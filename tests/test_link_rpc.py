"""Local-file RPC tests with mocked helpers; no Bluetooth, sudo or radio."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from btdos import link, supervisor
from btdos.config import Plan
from btdos.evidence import verify_bundle


TARGET = "02:00:00:00:00:01"


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.state = {}
        self.guard = Mock()
        self.helper = self.stack.enter_context(patch.object(link, "request_le_parameters",
            return_value=dict(status="requested_unverified", actual_parameters_verified=False)))
        self.popen = self.stack.enter_context(patch.object(link.subprocess, "Popen",
            side_effect=AssertionError("unexpected subprocess")))
        self.plan = Plan(case="ble-cycle", adapter="hci0", target=TARGET, ble_interval_ms=7.5, cycles=2)

    def request(self, sequence=1, request_id=None, **changes):
        value = dict(schema_version=1, sequence=sequence,
                     request_id=request_id if request_id is not None else f"{sequence:032x}")
        value.update(changes)
        path = self.root / f"link-request-{sequence}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def serve(self):
        return link.serve_parameter_requests(self.plan, self.root, self.state, self.guard)

    def test_empty_folder_returns_without_guard_or_helper(self):
        self.assertIsNone(self.serve())
        self.guard.assert_not_called()
        self.helper.assert_not_called()
        self.popen.assert_not_called()

    def test_exact_response_shape_uses_only_plan_arguments(self):
        path = self.request()
        self.serve()
        response = json.loads((self.root / "link-response-1.json").read_text(encoding="utf-8"))
        self.assertEqual(set(response), {"schema_version", "request_id", "sequence", "result"})
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(response["request_id"], "1".zfill(32))
        self.assertEqual(response["sequence"], 1)
        self.assertEqual(response["result"], self.helper.return_value)
        self.helper.assert_called_once_with("hci0", TARGET, 7.5, guard=self.guard)
        self.assertTrue(path.is_file())
        self.assertEqual(self.state["last_sequence"], 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_helper_failure_is_returned_without_claiming_success(self):
        self.request()
        self.helper.return_value = dict(status="request_failed", cleanup_status="unconfirmed")
        self.serve()
        result = json.loads((self.root / "link-response-1.json").read_text())["result"]
        self.assertEqual(result["status"], "request_failed")
        self.assertEqual(result["cleanup_status"], "unconfirmed")

    def test_consumed_unchanged_files_are_not_reexecuted_and_cycle_sequence_is_bounded(self):
        self.request()
        self.serve()
        self.serve()
        self.assertEqual(self.helper.call_count, 1)
        self.request(2)
        self.serve()
        self.serve()
        self.assertEqual(self.helper.call_count, 2)
        self.request(3)
        with self.assertRaisesRegex(RuntimeError, "connection budget"):
            self.serve()
        self.assertEqual(self.helper.call_count, 2)

    def test_noncycle_case_allows_only_one_request(self):
        self.plan.case = "ble-read"
        self.request()
        self.serve()
        self.request(2)
        with self.assertRaisesRegex(RuntimeError, "connection budget"):
            self.serve()
        self.helper.assert_called_once()

    def test_disabled_or_nonble_plan_rejects_requests(self):
        self.request()
        for case, interval in (("br-sdp", 7.5), ("ble-cycle", None)):
            with self.subTest(case=case, interval=interval):
                self.plan.case, self.plan.ble_interval_ms = case, interval
                with self.assertRaisesRegex(RuntimeError, "not enabled"):
                    self.serve()
        self.helper.assert_not_called()

    def test_strict_schema_rejects_unknown_fields_wrong_types_and_duplicate_keys(self):
        invalid = [
            dict(schema_version=True, sequence=1, request_id="a"*32),
            dict(schema_version=2, sequence=1, request_id="a"*32),
            dict(schema_version=1, sequence=True, request_id="a"*32),
            dict(schema_version=1, sequence=1.0, request_id="a"*32),
            dict(schema_version=1, sequence=1, request_id="A"*32),
            dict(schema_version=1, sequence=1, request_id="a"*31),
            dict(schema_version=1, sequence=1, request_id=1),
            dict(schema_version=1, sequence=1), [], None,
        ]
        invalid += [dict(schema_version=1, sequence=1, request_id="a"*32, **{field: "INJECTED"})
                    for field in ("target", "adapter", "argv", "path", "interval_ms")]
        path = self.root / "link-request-1.json"
        for value in invalid:
            with self.subTest(value=value):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    self.serve()
        path.write_text('{"schema_version":1,"sequence":1,"sequence":1,"request_id":"' + "a"*32 + '"}')
        with self.assertRaisesRegex(RuntimeError, "invalid link RPC JSON"):
            self.serve()
        self.helper.assert_not_called()

    def test_malformed_utf8_json_and_overlong_request_are_rejected(self):
        path = self.root / "link-request-1.json"
        for raw in (b"\xff", b"{", b" "*2049):
            with self.subTest(raw_length=len(raw)):
                path.write_bytes(raw)
                with self.assertRaises(RuntimeError):
                    self.serve()
        self.helper.assert_not_called()

    def test_directory_request_is_rejected(self):
        path = self.root / "link-request-1.json"
        path.mkdir()
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            self.serve()
        self.helper.assert_not_called()

    def test_symlink_lstat_is_rejected_before_open_on_every_platform(self):
        path = self.root / "link-request-1.json"
        with patch.object(Path, "lstat", return_value=Mock(st_mode=stat.S_IFLNK, st_size=0)), \
                patch.object(link.os, "open") as opening, self.assertRaisesRegex(RuntimeError, "regular file"):
            link._read_rpc_request(path)
        opening.assert_not_called()

    def test_real_symlink_request_is_rejected(self):
        path = self.root / "link-request-1.json"
        source = self.root / "source.json"
        source.write_text(json.dumps(dict(schema_version=1, sequence=1, request_id="a"*32)))
        try:
            path.symlink_to(source)
        except OSError:
            self.skipTest("symlinks require platform support/privileges")
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            self.serve()
        self.helper.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_fifo_request_is_rejected_without_blocking(self):
        os.mkfifo(self.root / "link-request-1.json")
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            self.serve()
        self.helper.assert_not_called()

    def test_filename_gap_extra_queue_and_sequence_mismatch_are_rejected(self):
        path = self.request(2)
        with self.assertRaisesRegex(RuntimeError, "strictly sequential"):
            self.serve()
        self.request(1)
        with self.assertRaisesRegex(RuntimeError, "strictly sequential"):
            self.serve()
        path.unlink()
        (self.root / "link-request-1.json").rename(self.root / "link-request-01.json")
        with self.assertRaisesRegex(RuntimeError, "filename"):
            self.serve()
        (self.root / "link-request-01.json").unlink()
        self.request(1).write_text(json.dumps(dict(schema_version=1, sequence=2, request_id="a"*32)))
        with self.assertRaisesRegex(RuntimeError, "match filename"):
            self.serve()
        self.helper.assert_not_called()

    def test_changed_or_removed_consumed_request_is_not_replayed(self):
        path = self.request()
        self.serve()
        original = path.read_bytes()
        path.write_bytes(original+b" ")
        with self.assertRaisesRegex(RuntimeError, "modified or replayed"):
            self.serve()
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, "removed"):
            self.serve()
        self.helper.assert_called_once()

    def test_request_id_reuse_in_another_cycle_is_rejected(self):
        self.request(request_id="a"*32)
        self.serve()
        self.request(2, request_id="a"*32)
        with self.assertRaisesRegex(RuntimeError, "request_id was replayed"):
            self.serve()
        self.helper.assert_called_once()

    def test_existing_response_cannot_be_overwritten_or_trigger_helper(self):
        self.request()
        response = self.root / "link-response-1.json"
        response.write_bytes(b"ORIGINAL")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.serve()
        self.assertEqual(response.read_bytes(), b"ORIGINAL")
        self.helper.assert_not_called()

    def test_guard_exception_propagates_and_no_response_is_published(self):
        self.request()
        error = InterruptedError("STOP fixture")
        self.helper.side_effect = error
        with self.assertRaises(InterruptedError) as raised:
            self.serve()
        self.assertIs(raised.exception, error)
        self.assertFalse((self.root / "link-response-1.json").exists())
        self.assertEqual(self.state["last_sequence"], 1)
        self.serve()
        self.helper.assert_called_once()

    def test_guard_failure_before_or_after_helper_prevents_publication(self):
        self.request()
        for outcomes, helper_calls in (([InterruptedError("STOP")], 0), ([None, TimeoutError("budget")], 1)):
            with self.subTest(outcomes=outcomes):
                self.state.clear()
                self.helper.reset_mock()
                self.guard.side_effect = outcomes
                with self.assertRaises((InterruptedError, TimeoutError)):
                    self.serve()
                self.assertEqual(self.helper.call_count, helper_calls)
                self.assertFalse((self.root / "link-response-1.json").exists())

    def test_atomic_response_publication_exclusive_and_temp_cleanup(self):
        response = dict(schema_version=1, sequence=1, request_id="a"*32, result={})
        publish_name = "rename" if os.name == "nt" else "link"
        original_publish = getattr(os, publish_name)
        def publish(source, destination):
            self.assertFalse(Path(destination).exists())
            self.assertEqual(json.loads(Path(source).read_text()), response)
            return original_publish(source, destination)
        with patch.object(link.os, publish_name, side_effect=publish):
            link._publish_rpc_response(self.root, 1, response)
        self.assertEqual(json.loads((self.root / "link-response-1.json").read_text()), response)
        with self.assertRaises(FileExistsError):
            link._publish_rpc_response(self.root, 1, dict(replacement=True))
        self.assertEqual(json.loads((self.root / "link-response-1.json").read_text()), response)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_publish_failure_or_oversized_response_does_not_leave_temp(self):
        publish_name = "rename" if os.name == "nt" else "link"
        with patch.object(link.os, publish_name, side_effect=OSError("fixture full disk")), self.assertRaises(OSError):
            link._publish_rpc_response(self.root, 1, {})
        with self.assertRaisesRegex(RuntimeError, "512 KiB"):
            link._publish_rpc_response(self.root, 1, {"large": "x"*(512*1024)})
        self.assertFalse((self.root / "link-response-1.json").exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])


class SupervisorRpcTests(unittest.TestCase):
    def test_worker_exit_between_lookups_stops_rpc_without_update_and_keeps_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Plan(case="ble-cycle", adapter="hci0", target=TARGET, ble_interval_ms=7.5,
                        cycles=1, baseline=0, recovery=0, output_root=directory)
            capture = Mock(name="SYNTHETIC capture")
            capture.name = "SYNTHETIC"
            capture.alive.return_value = True
            capture.stop.return_value = dict(name="SYNTHETIC", cleanup_confirmed=True)
            worker = Mock(returncode=0)
            worker.poll.return_value = None
            def spawn(argv, **_kwargs):
                folder = Path(argv[argv.index("--plan")+1]).parent
                (folder / "link-request-1.json").write_text(json.dumps(
                    dict(schema_version=1, sequence=1, request_id="a"*32)))
                return worker
            def command(argv, deadline, timeout, guard):
                guard()
                worker.poll.return_value = 0
                return dict(status="completed", returncode=0, stderr="", cleanup_status="direct_child_reaped",
                            stdout=f"Connections:\n< LE {TARGET} handle 42 state 1 lm CENTRAL\n")
            with patch.object(supervisor, "inspect_plan_adapter", return_value={}), \
                    patch.object(supervisor, "adapters", return_value=[]), \
                    patch.object(supervisor, "diagnose", return_value={}), \
                    patch.object(supervisor, "build_captures", return_value=[capture]), \
                    patch.object(supervisor, "export_index", return_value={}), \
                    patch.object(supervisor.subprocess, "Popen", side_effect=spawn) as popen, \
                    patch.object(link, "_absolute_tool", side_effect=lambda _name, expected: expected), \
                    patch.object(link, "_command", side_effect=command) as commands:
                folder, status = supervisor._run_locked(plan)
            self.assertEqual(status, "failed")
            self.assertIn("worker exited during parameter request", json.loads((folder / "result.json").read_text(encoding="utf-8"))["error"])
            self.assertEqual(commands.call_count, 2)
            self.assertTrue(all(call.args[0][-1] == "con" for call in commands.call_args_list))
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertFalse((folder / "link-response-1.json").exists())
            self.assertTrue(verify_bundle(folder)["ok"])


if __name__ == "__main__":
    unittest.main()
