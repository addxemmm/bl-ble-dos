"""Black-box CLI checks stay offline; no scan, connect or live run is invoked."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import btdos
from btdos import cli as app_cli
from btdos.discovery import ScanResult


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, env_overrides=None):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1", TERM="dumb")
        env.update(env_overrides or {})
        package_root = str(Path(btdos.__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "btdos", *map(str, args)], cwd=self.root,
            stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
            errors="replace", timeout=20, env=env,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_help_and_version(self):
        help_result = self.cli("--help")
        self.assert_success(help_result)
        self.assertIn("doctor", help_result.stdout)
        self.assertIn("demo", help_result.stdout)
        self.assertIn("verify", help_result.stdout)
        version = self.cli("--version")
        self.assert_success(version)
        self.assertEqual(version.stdout.strip(), btdos.__version__)

    @unittest.skipUnless(sys.platform == "win32", "Regression for Windows redirected cp1252 streams")
    def test_windows_redirected_cp1252_help_is_reconfigured_to_utf8(self):
        result = self.cli("--help", env_overrides={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})
        self.assert_success(result)
        self.assertIn("有界可用性测试", result.stdout)
        self.assertNotIn("\ufffd", result.stdout)
        self.assertNotIn("UnicodeEncodeError", result.stderr)

    def test_no_arguments_and_explicit_wizard_do_not_hang_without_tty(self):
        for args in ((), ("wizard",)):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assert_success(result)
                self.assertIn("usage:", result.stdout.lower())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_doctor_json_is_machine_readable_without_live_operation(self):
        result = self.cli("doctor", "--json")
        self.assert_success(result)
        value = json.loads(result.stdout)
        self.assertEqual(value["schema_version"], 1)
        self.assertIsInstance(value["ready_for_live"], bool)
        self.assertIsInstance(value["tools"], dict)
        self.assertIsInstance(value["adapters"], list)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_demo_verify_report_and_tamper_failure(self):
        output = self.root / "demo bundles"
        result = self.cli("demo", "--output", output)
        self.assert_success(result)
        runs = list(output.iterdir())
        self.assertEqual(len(runs), 1)
        bundle = runs[0]
        value = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(value["status"], "simulation_completed")
        self.assertTrue(value["simulation"])
        self.assertEqual(value["captures"], [])
        self.assertFalse(list(bundle.rglob("*.pcap")))
        self.assertFalse(list(bundle.rglob("*.btsnoop")))
        verified = self.cli("verify", bundle)
        self.assert_success(verified)
        self.assertEqual(json.loads(verified.stdout), {"ok": True, "errors": []})
        snapshot = {path.name: path.read_bytes() for path in bundle.iterdir() if path.is_file()}
        report = self.cli("report", bundle)
        self.assert_success(report)
        self.assertIn("SIMULATION", report.stdout)
        self.assertEqual(snapshot, {path.name: path.read_bytes() for path in bundle.iterdir() if path.is_file()})
        (bundle / "report.md").write_text("modified", encoding="utf-8")
        failed = self.cli("verify", bundle)
        self.assertEqual(failed.returncode, 1)
        self.assertFalse(json.loads(failed.stdout)["ok"])
        report = self.cli("report", bundle)
        self.assertEqual(report.returncode, 1)
        self.assertIn("integrity mismatch", report.stderr)

    def test_invalid_plan_fails_before_hardware_or_output_creation(self):
        plan = self.root / "invalid-plan.json"
        plan.write_text(json.dumps({"case": "br-sdp"}), encoding="utf-8")
        result = self.cli("run", "--plan", plan, "--confirm", "02:00:00:00:00:01")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [plan])

    def test_target_confirmation_mismatch_fails_before_hardware(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({
            "case": "br-sdp", "adapter": "hci0", "target": "02:00:00:00:00:01",
            "local_address": "02:00:00:00:00:02", "output_root": str(self.root / "runs"),
        }), encoding="utf-8")
        result = self.cli("run", "--plan", plan, "--confirm", "02:00:00:00:00:03")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / "runs").exists())

    def test_missing_bundle_verify_is_structured_failure(self):
        result = self.cli("verify", self.root / "missing")
        self.assertEqual(result.returncode, 1)
        value = json.loads(result.stdout)
        self.assertFalse(value["ok"])
        self.assertTrue(value["errors"])
        self.assertNotIn("Traceback", result.stderr)

    def test_scan_json_preserves_snapshot_cleanup_and_interrupted_exit_code(self):
        args = app_cli.parser().parse_args(["scan", "--transport", "bredr", "--adapter", "hci1", "--seconds", "3", "--json"])
        for status, expected in (("completed", 0), ("interrupted", 130), ("failed", 1)):
            with self.subTest(status=status):
                result = ScanResult("bredr", "hci1", "2026-01-01T00:00:00Z", 3, [],
                                    status=status, cleanup_status="released" if status == "completed" else "unconfirmed",
                                    warnings=["SYNTHETIC scan cleanup warning"])
                with patch("btdos.discovery.scan_devices", return_value=result) as scan, patch("builtins.print") as output:
                    self.assertEqual(app_cli.discover(args), expected)
                scan.assert_called_once_with("bredr", adapter="hci1", seconds=3)
                output.assert_called_once()
                value = json.loads(output.call_args.args[0])
                self.assertEqual(value["status"], status)
                self.assertEqual(value["requested_transport"], "bredr")
                self.assertEqual(value["cleanup_status"], result.cleanup_status)
                self.assertEqual(value["warnings"], result.warnings)


if __name__ == "__main__":
    unittest.main()
