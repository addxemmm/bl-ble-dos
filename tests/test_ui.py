"""Scripted Rich UI tests: synthetic metadata, no bus, subprocess, or radio."""

from contextlib import contextmanager, ExitStack
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from btdos import ui
from btdos.discovery import Device, ScanResult


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
        return [None, None, None, "m", TARGET_A, None, "1", "1", "n", "n", None]

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
        script = [None, None, None, "m", TARGET_A, None, "3", "NaN", "0", "1",
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
        script = self.br_to_review()[:7] + ["b", "b", "k", None, None, "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_A)
        self.assertEqual(sum(label == "目标 MAC" for label, _ in observed), 1)
        self.assertEqual(draft.sdp_uuid16, 0x110B)
        self.execute.assert_not_called()
        self.scan.assert_not_called()

    def test_review_target_edit_and_cancel_never_request_permission_or_execute(self):
        draft = ui.WizardDraft()
        with self.prompts(self.br_to_review() + ["2", "m", TARGET_B, "q"]):
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_B)
        self.execute.assert_not_called()
        self.permission.assert_not_called()

    def test_review_parameter_edit_retains_target_and_returns_to_review(self):
        draft = ui.WizardDraft()
        with self.prompts(self.br_to_review() + ["3", None, "2", "q"]):
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        self.assertEqual(draft.target.address, TARGET_A)
        self.assertEqual(draft.settings["duration"], 5)
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
        plan = self.execute.call_args.args[0]
        self.assertEqual(plan.target, TARGET_A)
        self.assertEqual(plan.adapter, "hci0")

    def test_changed_adapter_or_transport_clears_old_target(self):
        for case_choice, controller in (("3", "2"), ("1", "1")):
            with self.subTest(case_choice=case_choice, controller=controller):
                draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(TARGET_A), characteristic="0x0012")
                with self.prompts([case_choice, controller]):
                    ui._case_adapter(draft, self.info)
                self.assertIsNone(draft.target)
                self.assertIsNone(draft.characteristic)
        self.execute.assert_not_called()
        self.scan.assert_not_called()

    def test_ble_read_target_edit_clears_characteristic_and_reconfirms_parameters(self):
        draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(TARGET_A), characteristic="0x0012")
        script = [None, None, None, "k", None, "1", "1", "n", "n", None,
                  "2", "m", TARGET_B, "0x0030", None, "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        character_defaults = [kwargs["default"] for label, kwargs in observed if label == "特征 UUID / 0xHANDLE"]
        self.assertEqual(character_defaults, ["0x0012", None])
        self.assertEqual(draft.target.address, TARGET_B)
        self.assertEqual(draft.characteristic, "0x0030")
        self.execute.assert_not_called()

    def test_cycle_target_change_cannot_reuse_previous_read_characteristic(self):
        draft = ui.WizardDraft(case="ble-read", target=ui.TargetSelection(TARGET_A), characteristic="0x0012")
        script = [None, "4", None, "m", TARGET_B, "1", "1", "n", "n", None,
                  "1", "3", None, "k", "0x0030", None, "1", "n", "n", None, "q"]
        with self.prompts(script) as observed:
            self.assertEqual(ui.run_wizard(self.info, draft), 0)
        character_defaults = [kwargs["default"] for label, kwargs in observed if label == "特征 UUID / 0xHANDLE"]
        self.assertEqual(character_defaults, [None])
        self.assertEqual(draft.case, "ble-read")
        self.assertEqual(draft.target.address, TARGET_B)
        self.assertEqual(draft.characteristic, "0x0030")
        self.execute.assert_not_called()
        self.permission.assert_not_called()

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
        plan = ui.WizardDraft(target=ui.TargetSelection(TARGET_A), local_address="02:00:00:00:00:10").to_plan()
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
