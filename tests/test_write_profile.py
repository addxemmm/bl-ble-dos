"""Fixture-only write profile validation and UI/CLI integration; no radio calls."""
from io import StringIO
import asyncio
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
from btdos import cli, ui
from btdos import worker
from btdos.config import Plan, TEST_SERVICE_UUID, TEST_WRITE_UUID, WRITE_BUDGET, budget_defaults


def fixture(**changes):
    values = dict(case="ble-write-command", adapter="hci0", target="02:00:00:00:00:01",
                  service_uuid=TEST_SERVICE_UUID, characteristic=TEST_WRITE_UUID,
                  write_test_confirmed=True, **WRITE_BUDGET)
    values.update(changes)
    return values


class WriteProfileTests(unittest.TestCase):
    def test_fixture_profile_roundtrip_and_original_defaults(self):
        plan = Plan.from_dict(fixture())
        self.assertEqual(Plan.from_dict(plan.to_dict()), plan)
        self.assertEqual(plan.worker_budget, 60)
        self.assertEqual(budget_defaults()["rate"], 10)
        self.assertEqual(Plan(case="ble-cycle", adapter="hci0", target=plan.target).rate, 10)

    def test_requires_exact_fixture_ids_and_explicit_boolean_confirmation(self):
        for changes in ({"service_uuid": None}, {"service_uuid": TEST_WRITE_UUID},
                        {"service_uuid": TEST_SERVICE_UUID.upper()},
                        {"characteristic": "00002a00-0000-1000-8000-00805f9b34fb"},
                        {"characteristic": "0x0012"}, {"write_test_confirmed": False},
                        {"write_test_confirmed": 1}, {"write_test_confirmed": "true"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Plan.from_dict(fixture(**changes))

    def test_write_limits_and_no_arbitrary_payload(self):
        for changes in ({"rate": 100.1, "approved_rate": 101}, {"duration": 10.1},
                        {"max_ops": 1001}, {"max_ops": True}, {"rate": True},
                        {"rate": math.nan}, {"payload": "anything"}, {"rate": 100, "approved_rate": 99}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Plan.from_dict(fixture(**changes))

    def test_old_cases_do_not_inherit_fixture_fields_or_write_limits(self):
        for case in ("br-sdp", "br-channel", "ble-read", "ble-cycle"):
            values = dict(case=case, adapter="hci0", target="02:00:00:00:00:01",
                          local_address="02:00:00:00:00:02", characteristic="0x0012")
            Plan.from_dict(values)
            for changes in ({"rate": 21, "approved_rate": 100}, {"max_ops": 401},
                            {"service_uuid": TEST_SERVICE_UUID}, {"write_test_confirmed": True}):
                with self.subTest(case=case, changes=changes), self.assertRaises(ValueError):
                    Plan.from_dict(dict(values, **changes))

    def test_ble_interval_is_optional_quantized_finite_and_budgeted(self):
        for value in (7.5, 8.75, 50):
            plan = Plan.from_dict(fixture(ble_interval_ms=value))
            self.assertEqual(plan.worker_budget, 80)
        for value in (True, False, "7.5", [], {}, math.nan, math.inf, -math.inf, 7.4, 8, 51.25, 10 ** 1000):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                Plan.from_dict(fixture(ble_interval_ms=value))
        for case in ("ble-read", "ble-cycle"):
            values = dict(case=case, adapter="hci0", target="02:00:00:00:00:01", characteristic="0x0012")
            base = Plan.from_dict(values)
            requested = Plan.from_dict(dict(values, ble_interval_ms=7.5))
            extra = 20 * base.cycles if case == "ble-cycle" else 20
            self.assertEqual(requested.worker_budget, base.worker_budget + extra)
        with self.assertRaises(ValueError):
            Plan.from_dict(dict(case="br-sdp", adapter="hci0", target="02:00:00:00:00:01",
                                local_address="02:00:00:00:00:02", ble_interval_ms=7.5))

    def test_write_worker_budget_contains_connected_probe_windows(self):
        for baseline, recovery in ((0, 0), (5, 10), (60, 120)):
            for interval in (None, 7.5):
                plan = Plan.from_dict(fixture(baseline=baseline, recovery=recovery, ble_interval_ms=interval))
                self.assertEqual(plan.worker_budget, 10 + baseline + recovery + 35 + (20 if interval else 0))
                self.assertEqual(plan.wall_budget, math.ceil(baseline + plan.worker_budget + recovery + 30))
        for cycles in (1, 10):
            values = dict(case="ble-cycle", adapter="hci0", target=fixture()["target"], cycles=cycles)
            old = Plan.from_dict(values)
            requested = Plan.from_dict(dict(values, ble_interval_ms=7.5))
            self.assertEqual(requested.worker_budget, old.worker_budget + 20 * cycles)

    def test_cli_run_loads_new_json_profile_without_radio(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.json"
            path.write_text(json.dumps(fixture(ble_interval_ms=7.5)), encoding="utf-8")
            with patch.object(ui, "execute", return_value=0) as execute:
                self.assertEqual(cli.main(["run", "--plan", str(path), "--confirm", fixture()["target"]]), 0)
            self.assertEqual(execute.call_args.args[0].case, "ble-write-command")
            self.assertEqual(execute.call_args.args[0].ble_interval_ms, 7.5)

    def test_worker_dispatch_forwards_fixture_and_optional_interval(self):
        plan = Plan.from_dict(fixture(ble_interval_ms=7.5))
        libc = Mock()
        libc.prctl.return_value = 0
        with patch.object(worker.sys, "platform", "linux"), \
                patch.object(worker.sys, "argv", ["worker", "--plan", "SYNTHETIC", "--stop-file", "SYNTHETIC-RUN/STOP", "--parent-pid", "123"]), \
                patch.object(worker, "load_plan", return_value=plan), \
                patch.object(worker.os, "getppid", return_value=123), \
                patch.object(worker.ctypes, "CDLL", return_value=libc), \
                patch.object(worker.signal, "signal"), \
                patch.object(worker.signal, "SIGALRM", 14, create=True), \
                patch.object(worker.signal, "alarm", create=True) as alarm, \
                patch.object(worker.cases, "stopped"), patch.object(worker.cases, "log"), \
                patch.object(asyncio, "run"), \
                patch.object(worker.cases, "ble_write_command", new_callable=Mock, create=True) as dispatch:
            self.assertEqual(worker.main(), 0)
        args = dispatch.call_args.args[0]
        self.assertEqual(args.service_uuid, TEST_SERVICE_UUID)
        self.assertEqual(args.char, TEST_WRITE_UUID)
        self.assertTrue(args.write_test_confirmed)
        self.assertEqual(args.ble_interval_ms, 7.5)
        self.assertEqual((args.baseline, args.recovery), (5, 10))
        self.assertEqual(args.parameter_rpc_dir, str(Path("SYNTHETIC-RUN")))
        self.assertNotIn("parameter_rpc_dir", plan.to_dict())
        self.assertEqual(alarm.call_args_list[0].args, (80,))

    def test_ui_fixed_fixture_confirmation_and_write_default(self):
        draft = ui.WizardDraft(case="ble-write-command", target=ui.TargetSelection(fixture()["target"]))
        output = StringIO()
        with patch.object(ui, "console", Console(file=output)), patch.object(ui, "ask_yes", return_value=True) as confirm, \
                patch.object(ui, "ask_choice", return_value="1"), patch.object(ui, "probe_device") as probe:
            ui._parameters(draft)
            plan = draft.to_plan()
            ui.console.print(ui.review_panel(plan))
        self.assertEqual(plan.service_uuid, TEST_SERVICE_UUID)
        self.assertEqual(plan.characteristic, TEST_WRITE_UUID)
        self.assertTrue(plan.write_test_confirmed)
        self.assertEqual((plan.rate, plan.approved_rate, plan.duration, plan.max_ops), (100, 100, 10, 1000))
        self.assertFalse(confirm.call_args.kwargs["default"])
        self.assertIn("非只读", output.getvalue())
        self.assertIn("write-without-response", output.getvalue())
        self.assertIn("末值回读", output.getvalue())
        self.assertIn("无响应不等于目标确认", output.getvalue())
        probe.assert_not_called()

    def test_ui_declining_clears_existing_confirmation(self):
        draft = ui.WizardDraft(case="ble-write-command", target=ui.TargetSelection(fixture()["target"]),
                               service_uuid=TEST_SERVICE_UUID, characteristic=TEST_WRITE_UUID, write_test_confirmed=True)
        with patch.object(ui, "console", Console(file=StringIO())), patch.object(ui, "ask_yes", return_value=False):
            with self.assertRaises(ui.Back):
                ui._service_parameters(draft)
        self.assertFalse(draft.write_test_confirmed)
        with self.assertRaises(ValueError):
            draft.to_plan()

    def test_ui_case_switch_resets_budget_and_fixture_confirmation(self):
        draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(fixture()["target"]), characteristic="0x0012")
        info = {"adapters": [{"adapter": "hci0", "address": "02:00:00:00:00:02"}]}
        with patch.object(ui, "console", Console(file=StringIO())), patch.object(ui, "ask_choice", side_effect=["5", "1", "3", "1"]):
            ui._case_adapter(draft, info)
            self.assertEqual(draft.settings["rate"], 100)
            self.assertFalse(draft.write_test_confirmed)
            draft.service_uuid, draft.characteristic, draft.write_test_confirmed = TEST_SERVICE_UUID, TEST_WRITE_UUID, True
            ui._case_adapter(draft, info)
        self.assertEqual(draft.settings["rate"], 10)
        self.assertFalse(draft.write_test_confirmed)
        self.assertIsNone(draft.service_uuid)
        self.assertIsNone(draft.characteristic)


if __name__ == "__main__":
    unittest.main()
