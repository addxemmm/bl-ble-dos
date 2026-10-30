"""Scripted Rich UI tests: synthetic metadata, no bus, subprocess, or radio."""

from contextlib import contextmanager, ExitStack
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from btdos import ui
from btdos.discovery import Device, ScanResult
from btdos.probe import Characteristic, ProbeResult, Service, parse_sdp_xml


TARGET_A = "02:00:00:00:00:01"
TARGET_B = "02:00:00:00:00:02"
TARGET_C = "02:00:00:00:00:03"


def device(address, name="Same synthetic name"):
    return Device("/org/bluez/hci0/dev_" + address.replace(":", "_"), address, name,
                  address_type="public", rssi=-42, paired=False)


def snapshot(*devices, status="completed"):
    return ScanResult("le", "hci0", "2026-01-01T00:00:00Z", 5, list(devices), status=status)


class UiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.output = StringIO()
        self.console = Console(file=self.output, width=100, force_terminal=False, color_system=None)
        self.stack.enter_context(patch.object(ui, "console", self.console))
        self.stack.enter_context(patch.object(ui.sys, "platform", "linux"))
        self.probe = self.stack.enter_context(patch.object(ui, "probe_device", side_effect=AssertionError("unexpected probe")))
        self.scan = self.stack.enter_context(patch.object(ui, "scan_devices", side_effect=AssertionError("unexpected scan")))
        self.execute = self.stack.enter_context(patch.object(ui, "execute", return_value=0))
        self.permission = self.stack.enter_context(patch.object(ui, "_ensure_capture_permission", return_value=True))
        self.stack.enter_context(patch.object(ui, "run_plan", side_effect=AssertionError("unexpected workload")))
        self.stack.enter_context(patch.object(ui, "command", side_effect=AssertionError("unexpected command")))
        self.stack.enter_context(patch.object(ui.subprocess, "run", side_effect=AssertionError("unexpected subprocess")))
        self.info = {
            "platform": "Synthetic Linux", "python": "3.14", "bluetooth_service": "active",
            "adapters": [
                {"adapter": "hci0", "address": "02:00:00:00:00:10", "name": "Synthetic A"},
                {"adapter": "hci1", "address": "02:00:00:00:00:11", "name": "Synthetic B"},
            ],
            "sudo_noninteractive": False, "nrf_ports": [], "ready_for_live": True,
            "tools": {"nrfutil": None},
        }
        self.diagnose = self.stack.enter_context(patch.object(ui, "diagnose", return_value=self.info))

    @contextmanager
    def prompts(self, values):
        pending = list(values)
        observed = []

        def answer(label, **kwargs):
            observed.append((str(label), kwargs))
            if not pending:
                raise AssertionError(f"Unexpected extra prompt: {label}")
            value = pending.pop(0)
            if isinstance(value, BaseException):
                raise value
            if value is None:
                self.assertIsNotNone(kwargs.get("default"), f"No default for prompt: {label}")
                return str(kwargs["default"])
            return value

        with patch.object(ui.Prompt, "ask", side_effect=answer):
            yield observed
        self.assertEqual(pending, [], "Not all scripted answers were consumed")

    def allow_scans(self, *results):
        self.scan.side_effect = list(results)

    @staticmethod
    def br_to_review():
        # Environment, case/controller, manual target, UUID/preset, evidence.
        return [None, None, None, "m", TARGET_A, "m", "0x110B", "1", "1", "n", "n", None]

    def test_text_navigation_and_eof_are_explicit_exceptions(self):
        for value, expected in (("b", ui.Back), ("BACK", ui.Back), ("q", ui.Cancel), ("cancel", ui.Cancel), (EOFError(), ui.Cancel)):
            with self.subTest(value=value), self.prompts([value]):
                with self.assertRaises(expected):
                    ui.ask_text("Synthetic input")

    def test_choices_reprompt_and_normalize_without_running_anything(self):
        with self.prompts(["invalid", " M "]):
            self.assertEqual(ui.ask_text("Synthetic choice", choices=("s", "m")), "m")
        self.scan.assert_not_called()
        self.execute.assert_not_called()

    def test_numbers_reprompt_nan_infinity_out_of_range_and_fractional_integer(self):
        with self.prompts(["NaN", "inf", "-1", "21", "1.25"]):
            self.assertEqual(ui.ask_number("Rate", 1, 0.1, 20), 1.25)
        with self.prompts(["1.5", "NaN", "0", "3"]):
            self.assertEqual(ui.ask_number("Count", 1, 1, 10, integer=True), 3)

    def test_manual_target_is_canonical_and_never_scans(self):
        with self.prompts(["m", "02:aa:bb:cc:dd:ee"]):
            selected = ui.pick_target("bredr", "hci1")
        self.assertEqual(selected.address, "02:AA:BB:CC:DD:EE")
        self.assertEqual(selected.source, "manual")
        self.assertEqual(selected.requested_transport, "bredr")
        self.scan.assert_not_called()

    def test_keep_current_target_preserves_object_without_scanning(self):
        current = ui.TargetSelection(TARGET_A, "Synthetic remembered target")
        with self.prompts(["k"]):
            self.assertIs(ui.pick_target("le", "hci0", current), current)
        self.scan.assert_not_called()

    def test_same_name_devices_keep_stable_number_to_address_mapping(self):
        self.allow_scans(snapshot(device(TARGET_A), device(TARGET_B)))
        with self.prompts(["s", "2"]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, TARGET_B)
        self.assertEqual(selected.source, "scan_observation")
        self.scan.assert_called_once()
        args, kwargs = self.scan.call_args
        self.assertEqual(args, ("le",))
        self.assertEqual(kwargs["adapter"], "hci0")
        self.assertEqual(kwargs["seconds"], 5)
        self.assertTrue(callable(kwargs["on_progress"]))

    def test_filter_renumbers_visible_devices_and_selects_correct_address(self):
        self.allow_scans(snapshot(device(TARGET_A), device(TARGET_B)))
        with self.prompts(["s", "f", TARGET_B, "1"]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, TARGET_B)
        self.scan.assert_called_once()

    def test_second_page_uses_displayed_global_number(self):
        devices = [device(f"02:00:00:00:00:{i:02X}") for i in range(1, 11)]
        self.allow_scans(snapshot(*devices))
        with self.prompts(["s", "n", "9"]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, devices[8].address)
        self.scan.assert_called_once()

    def test_explicit_refresh_scans_exactly_once_more(self):
        self.allow_scans(snapshot(device(TARGET_A)), snapshot(device(TARGET_B), device(TARGET_C)))
        with self.prompts(["s", "r", "2"]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, TARGET_C)
        self.assertEqual(self.scan.call_count, 2)

    def test_scan_duration_change_does_not_scan_until_explicit_refresh(self):
        self.allow_scans(snapshot(device(TARGET_A)))
        with self.prompts(["s", "t", "NaN", "16", "3", "q"]), self.assertRaises(ui.Cancel):
            ui.pick_target("le", "hci0")
        self.scan.assert_called_once()
        self.scan.reset_mock()
        self.allow_scans(snapshot(device(TARGET_A)), snapshot(device(TARGET_B)))
        with self.prompts(["s", "t", "3", "r", "1"]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, TARGET_B)
        self.assertEqual(self.scan.call_count, 2)
        self.assertEqual(self.scan.call_args.kwargs["seconds"], 3)

    def test_manual_from_results_does_not_scan_again(self):
        self.allow_scans(snapshot(device(TARGET_A)))
        with self.prompts(["s", "m", TARGET_B]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, TARGET_B)
        self.assertEqual(selected.source, "manual")
        self.scan.assert_called_once()

    def test_empty_results_can_refresh_or_return_to_manual_selection(self):
        self.allow_scans(snapshot(), snapshot(device(TARGET_A)))
        with self.prompts(["s", "r", "1"]):
            self.assertEqual(ui.pick_target("le", "hci0").address, TARGET_A)
        self.assertEqual(self.scan.call_count, 2)
        self.scan.reset_mock()
        self.allow_scans(snapshot())
        with self.prompts(["s", "b", "m", TARGET_B]):
            self.assertEqual(ui.pick_target("le", "hci0").address, TARGET_B)
        self.scan.assert_called_once()

    def test_failed_scan_allows_retry_manual_or_back_without_auto_selection(self):
        for outcome in (RuntimeError("Synthetic scan failed"), snapshot(status="failed")):
            with self.subTest(outcome=outcome):
                self.scan.reset_mock()
                self.allow_scans(outcome)
                with self.prompts(["s", "m", TARGET_A]):
                    self.assertEqual(ui.pick_target("le", "hci0").address, TARGET_A)
                self.scan.assert_called_once()
        self.scan.reset_mock()
        self.allow_scans(RuntimeError("Synthetic retry"), snapshot(device(TARGET_B)))
        with self.prompts(["s", "s", "1"]):
            self.assertEqual(ui.pick_target("le", "hci0").address, TARGET_B)
        self.assertEqual(self.scan.call_count, 2)

    def test_back_and_cancel_in_target_selector_do_not_execute(self):
        with self.prompts(["b"]), self.assertRaises(ui.Back):
            ui.pick_target("le", "hci0")
        self.allow_scans(snapshot(device(TARGET_A)))
        with self.prompts(["s", "q"]), self.assertRaises(ui.Cancel):
            ui.pick_target("le", "hci0")
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_interrupted_unconfirmed_scan_shows_warning_without_selecting_candidates(self):
        interrupted = snapshot(device(TARGET_A), status="interrupted")
        interrupted.cleanup_status = "unconfirmed"
        interrupted.warnings = ["SYNTHETIC cleanup was not confirmed"]
        self.allow_scans(interrupted)
        with self.prompts(["s", "m", TARGET_B]):
            selected = ui.pick_target("le", "hci0")
        self.assertEqual(selected.address, TARGET_B)
        self.assertEqual(selected.source, "manual")
        self.assertIn("interrupted", self.output.getvalue())
        self.assertIn("unconfirmed", self.output.getvalue())
        self.assertIn("SYNTHETIC cleanup was not confirmed", self.output.getvalue())
        self.scan.assert_called_once()
        self.execute.assert_not_called()

    def test_invalid_custom_numbers_do_not_discard_selected_target(self):
        draft = ui.WizardDraft()
        script = [None, None, None, "m", TARGET_A, "m", "0x110B", "3", "NaN", "0", "1",
                  "inf", "1", "bad", "10", "1.5", "400", None, None,
                  "1", "n", "n", None, "q"]
        with self.prompts(script):
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_A)
        self.assertEqual(draft.settings["rate"], 1)
        self.assertEqual(draft.settings["duration"], 10)
        self.execute.assert_not_called()
        self.permission.assert_not_called()
        self.scan.assert_not_called()

    def test_back_navigation_keeps_target_and_parameter_defaults(self):
        draft = ui.WizardDraft()
        script = self.br_to_review()[:8] + ["b", "b", "k", "k", "1", "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_A)
        self.assertEqual(sum(label == "目标 MAC" for label, _ in observed), 1)
        self.assertEqual(draft.sdp_uuid16, 0x110B)
        self.execute.assert_not_called()
        self.scan.assert_not_called()

    def test_review_target_edit_and_cancel_never_request_permission_or_execute(self):
        draft = ui.WizardDraft(target=ui.TargetSelection(TARGET_A), characteristic="0x0012",
                               probe_result=ProbeResult(TARGET_A, "hci0", "bredr"))
        with self.prompts(self.br_to_review() + ["2", "m", TARGET_B, "q"]) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_B)
        self.assertIsNone(draft.probe_result)
        self.assertIsNone(draft.sdp_uuid16)
        self.assertIsNone(draft.characteristic)
        self.assertEqual(sum(label == "目标服务与参数来源" for label, _ in observed), 2)
        with self.assertRaises(ValueError):
            draft.to_plan()
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_review_parameter_edit_retains_target_and_returns_to_review(self):
        draft = ui.WizardDraft()
        with self.prompts(self.br_to_review() + ["3", "k", "2", "q"]):
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_A)
        self.assertEqual(draft.settings["duration"], 5)
        self.assertEqual(draft.settings["rate"], 1)
        self.assertEqual(draft.settings["approved_rate"], 1)
        self.assertEqual(draft.settings["max_ops"], 5)
        self.assertEqual(draft.preset, "quick")
        self.execute.assert_not_called()

    def test_incorrect_final_mac_does_not_request_permission_or_execute(self):
        draft = ui.WizardDraft()
        with self.prompts(self.br_to_review() + ["r", "NOT-A-MAC", "q"]):
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.permission.assert_not_called()
        self.execute.assert_not_called()

    def test_correct_final_mac_is_required_before_permission_and_execute(self):
        order = Mock()
        order.attach_mock(self.permission, "permission")
        order.attach_mock(self.execute, "execute")
        with self.prompts(self.br_to_review() + ["r", TARGET_A]):
            self.assertEqual(ui.run_wizard(self.info), 0)
        self.assertEqual([call[0] for call in order.mock_calls], ["permission", "execute"])
        self.assertEqual(self.execute.call_args.kwargs, {"wait_for_target": True, "allow_retry": True})
        plan = self.execute.call_args.args[0]
        self.assertEqual(plan.target, TARGET_A)
        self.assertEqual(plan.adapter, "hci0")
        self.assertEqual((plan.rate, plan.approved_rate, plan.duration, plan.max_ops),
                         (10, 10, 10, 100))

    def test_retry_preserves_draft_but_requires_fresh_run_mac_and_permission(self):
        draft = ui.WizardDraft()
        self.execute.side_effect = [ui.RETRY, 0]
        order = Mock()
        order.attach_mock(self.permission, "permission")
        order.attach_mock(self.execute, "execute")
        script = self.br_to_review() + ["r", TARGET_A, "r", "WRONG", "r", TARGET_A]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual([call[0] for call in order.mock_calls],
                         ["permission", "execute", "permission", "execute"])
        self.assertEqual(self.execute.call_count, 2)
        first, second = [call.args[0] for call in self.execute.call_args_list]
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertIsNot(first, second)
        self.assertEqual(draft.target.address, TARGET_A)
        self.assertEqual(draft.sdp_uuid16, 0x110B)
        confirms = [label for label, _ in observed if label.startswith("输入完整目标MAC确认")]
        self.assertEqual(len(confirms), 3)
        self.probe.assert_not_called()
        self.scan.assert_not_called()

    def test_retry_then_quit_never_launches_second_run(self):
        self.execute.return_value = ui.RETRY
        with self.prompts(self.br_to_review() + ["r", TARGET_A, "q"]):
            self.assertEqual(ui.run_wizard(self.info), 0)
        self.execute.assert_called_once()
        self.permission.assert_called_once()

    def test_default_preset_restores_stronger_budget_after_quick(self):
        draft = ui.WizardDraft(case="ble-cycle")
        with patch.object(ui, "_service_parameters"), self.prompts(["2", "1"]):
            ui._parameters(draft)
            self.assertEqual((draft.settings["rate"], draft.settings["approved_rate"],
                              draft.settings["duration"], draft.settings["max_ops"]), (1, 1, 5, 5))
            ui._parameters(draft)
        self.assertEqual((draft.settings["rate"], draft.settings["approved_rate"],
                          draft.settings["duration"], draft.settings["max_ops"]), (10, 10, 10, 100))
        self.assertEqual(draft.preset, "default")
        self.assertIn("≤100次", self.output.getvalue())
        self.scan.assert_not_called()
        self.probe.assert_not_called()
        self.execute.assert_not_called()

    def test_changed_adapter_or_transport_clears_old_target(self):
        for case_choice, controller in (("3", "2"), ("1", "1")):
            with self.subTest(case_choice=case_choice, controller=controller):
                draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(TARGET_A), characteristic="0x0012",
                                       sdp_uuid16=0x110B, probe_result=ProbeResult(TARGET_A, "hci0", "le"))
                with self.prompts([case_choice, controller]):
                    ui._case_adapter(draft, self.info)
                self.assertIsNone(draft.target)
                self.assertIsNone(draft.characteristic)
                self.assertIsNone(draft.sdp_uuid16)
                self.assertIsNone(draft.probe_result)
        self.execute.assert_not_called()
        self.scan.assert_not_called()

    def test_ble_read_target_edit_clears_characteristic_and_reconfirms_parameters(self):
        draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(TARGET_A), characteristic="0x0012")
        script = [None, None, None, "k", "m", None, "1", "1", "n", "n", None,
                  "2", "m", TARGET_B, "m", "0x0030", "1", "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        character_defaults = [kwargs["default"] for label, kwargs in observed if label == "特征 UUID / 0xHANDLE"]
        self.assertEqual(character_defaults, ["0x0012", None])
        self.assertEqual(draft.target.address, TARGET_B)
        self.assertEqual(draft.characteristic, "0x0030")
        self.execute.assert_not_called()

    def test_cycle_target_change_cannot_reuse_previous_read_characteristic(self):
        draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(TARGET_A), characteristic="0x0012")
        script = [None, "4", None, "m", TARGET_B, "k", "1", "1", "n", "n", None,
                  "1", "3", None, "k", "m", "0x0030", "1", "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        character_defaults = [kwargs["default"] for label, kwargs in observed if label == "特征 UUID / 0xHANDLE"]
        self.assertEqual(character_defaults, [None])
        self.assertEqual(draft.case, "ble-read")
        self.assertEqual(draft.target.address, TARGET_B)
        self.assertEqual(draft.characteristic, "0x0030")
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    @staticmethod
    def service_draft(case="br-sdp", **kwargs):
        return ui.WizardDraft(case=case, target=ui.TargetSelection(TARGET_A),
                              local_address="02:00:00:00:00:10", **kwargs)

    @staticmethod
    def sdp_result(**kwargs):
        service = Service("0000110c-0000-1000-8000-00805f9b34fb", "Synthetic service", 0x110C,
                          record_handle=0x10002, psm=25, channel=7, selectable=True)
        return ProbeResult(TARGET_A, "hci0", "bredr", services=[service], cleanup_status="process_exited", **kwargs)

    def test_probe_confirmation_defaults_no_and_declining_does_not_call_backend(self):
        draft = self.service_draft()
        with self.prompts(["p", None, "m", "0x110B"]) as observed:
            ui._service_parameters(draft)
        confirmations = [(label, kwargs) for label, kwargs in observed if label.startswith("确认现在")]
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(confirmations[0][1]["default"], "n")
        self.probe.assert_not_called()
        self.scan.assert_not_called()
        self.execute.assert_not_called()
        self.permission.assert_not_called()
        self.assertIsNone(draft.probe_result)

    def test_confirmed_probe_calls_backend_once_without_capture_or_workload(self):
        result = self.sdp_result()
        self.probe.side_effect = [result]
        draft = self.service_draft()
        with self.prompts(["p", "y", "s", "1"]):
            ui._service_parameters(draft)
        self.probe.assert_called_once_with("bredr", TARGET_A, adapter="hci0", local_address="02:00:00:00:00:10")
        self.assertIs(draft.probe_result, result)
        self.assertEqual(draft.sdp_uuid16, 0x110C)
        self.scan.assert_not_called()
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_sdp_selection_uses_service_class_not_record_psm_or_protocol_uuid(self):
        xml = """<record>
        <attribute id="0x0000"><uint32 value="0x00010002"/></attribute>
        <attribute id="0x0001"><sequence><uuid value="0x110c"/></sequence></attribute>
        <attribute id="0x0004"><sequence>
          <sequence><uuid value="0x0100"/><uint16 value="25"/></sequence>
          <sequence><uuid value="0x0003"/><uint8 value="7"/></sequence>
        </sequence></attribute>
        <attribute id="0x0100"><text value="SYNTHETIC service class"/></attribute>
        </record>"""
        services = parse_sdp_xml(xml)
        result = ProbeResult(TARGET_A, "hci0", "bredr", services=services, cleanup_status="released")
        draft = self.service_draft(probe_result=result)
        with self.prompts(["s", "1"]) as observed:
            ui._service_parameters(draft)
        self.assertEqual(draft.sdp_uuid16, 0x110C)
        choices = next(kwargs["options"] for label, kwargs in observed if label == "选择本次请求的服务")
        choices = [(key, label) for key, label in choices if key.isdigit()]
        self.assertEqual(len(choices), 1)
        self.assertIn("0x110C", choices[0][1])
        self.assertIn("SYNTHETIC", choices[0][1])
        self.assertNotIn(draft.sdp_uuid16, (0x10002, 25, 7, 0x0100, 0x0003))
        self.probe.assert_not_called()

    def test_completed_probe_requires_confirmed_cleanup_before_selection(self):
        for cleanup in ("released", "disconnected", "closed", "process_exited"):
            with self.subTest(cleanup=cleanup):
                result = self.sdp_result()
                result.cleanup_status = cleanup
                draft = self.service_draft(probe_result=result)
                with self.prompts(["s", "1"]):
                    ui._service_parameters(draft)
                self.assertEqual(draft.sdp_uuid16, 0x110C)
        self.probe.assert_not_called()

    def test_failed_empty_and_unconfirmed_probe_never_offer_automatic_selection(self):
        outcomes = [self.sdp_result(status="failed"), self.sdp_result(status="interrupted"),
                    self.sdp_result(status="no_results"), self.sdp_result()]
        outcomes[-1].cleanup_status = "unconfirmed"
        empty = self.sdp_result()
        empty.services = []
        outcomes.append(empty)
        for result in outcomes:
            with self.subTest(status=result.status, cleanup=result.cleanup_status, empty=not result.services):
                draft = self.service_draft(probe_result=result)
                with self.prompts(["s", "m", "0x110B"]) as observed:
                    ui._service_parameters(draft)
                menus = [kwargs["options"] for label, kwargs in observed if label == "目标服务与参数来源"]
                self.assertTrue(all("s" not in dict(menu) for menu in menus))
                self.assertEqual(draft.sdp_uuid16, 0x110B)
                self.assertIs(draft.probe_result, result)
        self.probe.assert_not_called()
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_gatt_selection_filters_write_only_and_duplicate_uuids_and_requires_oem_confirmation(self):
        service = "00001800-0000-1000-8000-00805f9b34fb"
        unique = "00002a00-0000-1000-8000-00805f9b34fb"
        duplicate = "00002a01-0000-1000-8000-00805f9b34fb"
        write_only = "00002a02-0000-1000-8000-00805f9b34fb"
        chars = [Characteristic(service, unique, 1, ["read"], "Unique readable", True),
                 Characteristic(service, duplicate, 2, ["read"], "Duplicate A", True),
                 Characteristic(service, duplicate.upper(), 3, ["read"], "Duplicate B", True),
                 Characteristic(service, write_only, 4, ["write"], "Write only", False)]
        result = ProbeResult(TARGET_A, "hci0", "le", characteristics=chars, cleanup_status="disconnected")
        draft = self.service_draft("ble-read", probe_result=result)
        with self.prompts(["s", "1", None, "q"]) as observed, self.assertRaises(ui.Cancel):
            ui._service_parameters(draft)
        self.assertIsNone(draft.characteristic)
        choices = next(kwargs["options"] for label, kwargs in observed if label == "选择本次请求的只读特征")
        choices = [(key, label) for key, label in choices if key.isdigit()]
        self.assertEqual(len(choices), 1)
        self.assertIn(unique, choices[0][1])
        confirmation = next(kwargs for label, kwargs in observed if label.startswith("已由厂家"))
        self.assertEqual(confirmation["default"], "n")
        with self.prompts(["s", "1", "y"]):
            ui._service_parameters(draft)
        self.assertEqual(draft.characteristic, unique)
        self.assertIn("重复UUID", self.output.getvalue())
        self.probe.assert_not_called()
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_budget_edit_preserves_service_snapshot_without_reprobing(self):
        result = self.sdp_result()
        self.probe.side_effect = [result]
        draft = ui.WizardDraft()
        script = [None, None, None, "m", TARGET_A, "p", "y", "s", "1", "1", "1", "n", "n", None,
                  "3", "k", "2", "q"]
        with self.prompts(script):
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertIs(draft.probe_result, result)
        self.assertEqual(draft.sdp_uuid16, 0x110C)
        self.assertEqual(draft.settings["max_ops"], 5)
        self.assertEqual(draft.preset, "quick")
        self.probe.assert_called_once()
        self.scan.assert_not_called()
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_probe_snapshot_identity_must_match_target_adapter_and_transport(self):
        result = self.sdp_result()
        draft = self.service_draft(probe_result=result)
        self.assertTrue(ui._probe_matches(draft))
        for attribute, value in (("target", TARGET_B), ("adapter", "hci1"), ("transport", "le")):
            with self.subTest(attribute=attribute):
                old = getattr(result, attribute)
                setattr(result, attribute, value)
                self.assertFalse(ui._probe_matches(draft))
                with self.prompts(["m", "0x110B"]) as observed:
                    ui._service_parameters(draft)
                self.assertNotIn("s", dict(observed[0][1]["options"]))
                setattr(result, attribute, old)
        self.probe.assert_not_called()

    def test_control_character_input_is_reprompted_not_accepted(self):
        for control in ("\x1b[D", "\x00", "\x7f", "\n", "\t"):
            with self.subTest(control=repr(control)), self.prompts(["bad" + control + "value", "clean"]):
                self.assertEqual(ui.ask_text("Value"), "clean")
        self.assertIn("输入含控制字符", self.output.getvalue())
        self.probe.assert_not_called()
        self.execute.assert_not_called()

    def test_arrow_menu_options_have_descriptive_labels_and_final_mac_has_no_history(self):
        with self.prompts(self.br_to_review() + ["r", TARGET_A]) as observed:
            self.assertEqual(ui.run_wizard(self.info), 0)
        menus = [(label, kwargs["options"]) for label, kwargs in observed if kwargs.get("options")]
        self.assertGreaterEqual(len(menus), 8)
        for label, options in menus:
            with self.subTest(label=label):
                self.assertTrue(all(isinstance(text, str) and len(text.strip()) > 1 and text != key for key, text in options))
        confirmation = next(kwargs for label, kwargs in observed if label.startswith("输入完整目标MAC确认"))
        self.assertIs(confirmation["history"], False)
        self.probe.assert_not_called()

    def test_probe_panel_displays_unknown_characteristic_handle_without_crashing(self):
        char = Characteristic("00001800-0000-1000-8000-00805f9b34fb", "00002a00-0000-1000-8000-00805f9b34fb",
                              None, ["read"], "Handle unavailable", True)
        ui.probe_panel(ProbeResult(TARGET_A, "hci0", "le", characteristics=[char], cleanup_status="disconnected"))
        self.assertIn("Handle unavailable", self.output.getvalue())
        self.assertIn(TARGET_A, self.output.getvalue())
        self.probe.assert_not_called()

    def test_service_catalog_paging_and_filtering_reuse_frozen_snapshot(self):
        services = [Service(f"0000{0x1100+i:04x}-0000-1000-8000-00805f9b34fb", f"Service {i}",
                            0x1100+i, selectable=True) for i in range(10)]
        result = ProbeResult(TARGET_A, "hci0", "bredr", services=services, cleanup_status="released")
        draft = self.service_draft(probe_result=result)
        with self.prompts(["s", "n", "2"]):
            ui._service_parameters(draft)
        self.assertEqual(draft.sdp_uuid16, 0x1109)
        with self.prompts(["s", "f", "0x1106", "1"]):
            ui._service_parameters(draft)
        self.assertEqual(draft.sdp_uuid16, 0x1106)
        self.assertIs(draft.probe_result, result)
        self.probe.assert_not_called()
        self.scan.assert_not_called()

    def test_br_target_change_requires_fresh_service_selection_before_review(self):
        draft = ui.WizardDraft()
        script = self.br_to_review() + ["2", "m", TARGET_B, "m", "0x111E", "1", "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_B)
        self.assertEqual(draft.sdp_uuid16, 0x111E)
        defaults = [kwargs["default"] for label, kwargs in observed if label == "SDP服务UUID16（核对服务清单）"]
        self.assertEqual(defaults, [None, None])
        self.assertEqual(draft.to_plan().sdp_uuid16, 0x111E)
        self.execute.assert_not_called()
        self.permission.assert_not_called()
        self.probe.assert_not_called()

    def test_home_returns_after_demo_and_diagnostics_instead_of_exiting(self):
        with patch.object(ui, "demo") as demo, patch.object(ui, "show_doctor") as show, self.prompts(["1", "2", "0"]):
            self.assertEqual(ui.wizard(), 0)
        demo.assert_called_once_with("runs")
        self.diagnose.assert_called_once()
        show.assert_called_once_with(self.info)
        self.execute.assert_not_called()
        self.scan.assert_not_called()

    def test_home_reuses_draft_after_cancelled_wizard(self):
        with patch.object(ui, "run_wizard", return_value=0) as run, self.prompts(["3", "3", "0"]):
            self.assertEqual(ui.wizard(), 0)
        self.assertEqual(run.call_count, 2)
        self.assertIs(run.call_args_list[0].kwargs["draft"], run.call_args_list[1].kwargs["draft"])

    def test_rich_and_escape_names_render_as_literal_single_line_at_both_widths(self):
        name = "[bold]SYNTHETIC[/bold]\x1b[2J\r\nq FAKE MENU\x07"
        devices = [device(TARGET_A, name), device(TARGET_B, "Repeated")]
        plan = ui.WizardDraft(target=ui.TargetSelection(TARGET_A), local_address="02:00:00:00:00:10", sdp_uuid16=0x110B).to_plan()
        for width in (60, 100):
            with self.subTest(width=width):
                stream = StringIO()
                output = Console(file=stream, width=width, force_terminal=False, color_system=None)
                with patch.object(ui, "console", output):
                    ui.banner()
                    output.print(ui.render_stepper(2))
                    output.print(ui.render_device_table(devices))
                    output.print(ui.review_panel(plan, ui.TargetSelection(TARGET_A, name)))
                    output.print(ui.dashboard("RUN", Path("SYNTHETIC-UI-ONLY"), 1, plan))
                rendered = stream.getvalue()
                self.assertIn("[bold]", rendered)
                self.assertNotIn("\x1b", rendered)
                self.assertNotIn("\r", rendered)
                self.assertNotIn("\x07", rendered)
                self.assertIn(TARGET_A, rendered)
        self.scan.assert_not_called()
        self.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
