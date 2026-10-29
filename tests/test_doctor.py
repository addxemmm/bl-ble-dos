"""Read-only diagnostics do not require installed Bluetooth tools or adapters."""

import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

from btdos.config import Plan
from btdos.doctor import adapters, command, diagnose, inspect_plan_adapter


class DoctorTests(unittest.TestCase):
    def test_no_hardware_is_a_diagnostic_result_not_a_pass(self):
        with patch("btdos.doctor.sys.platform", "win32"), patch("btdos.doctor.shutil.which", return_value=None), patch("btdos.doctor.command") as invoke:
            result = diagnose()
        # Diagnostics may read Git provenance, but do not launch radio tools.
        self.assertTrue(all(call.args[0][0] == "git" for call in invoke.call_args_list))
        self.assertEqual(result["schema_version"], 1)
        self.assertFalse(result["ready_for_live"])
        self.assertEqual(result["adapters"], [])
        self.assertEqual(result["nrf_ports"], [])
        self.assertFalse(result["sudo_noninteractive"])

    def test_missing_command_and_timeout_are_structured(self):
        for error in (OSError("not installed"), subprocess.TimeoutExpired(["fixture"], 3)):
            with self.subTest(error=error):
                with patch("btdos.doctor.subprocess.run", side_effect=error):
                    result = command(["fixture"])
                self.assertEqual(result["code"], -1)
                self.assertEqual(result["stdout"], "")
                self.assertTrue(result["stderr"])

    def test_commands_are_bounded_argv_without_shell(self):
        completed = subprocess.CompletedProcess(["fixture", "list"], 0, " output \n", "")
        with patch("btdos.doctor.subprocess.run", return_value=completed) as run:
            result = command(["fixture", "list"], timeout=2)
        self.assertEqual(result["stdout"], "output")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["fixture", "list"])
        self.assertEqual(kwargs["timeout"], 2)
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def plan(self, **changes):
        values = {"case": "br-sdp", "adapter": "hci0", "target": "02:00:00:00:00:01", "local_address": "02:00:00:00:00:02"}
        values.update(changes)
        return Plan.from_dict(values)

    def test_missing_selected_adapter_is_rejected(self):
        with patch("btdos.doctor.adapters", return_value=[]):
            with self.assertRaises(RuntimeError):
                inspect_plan_adapter(self.plan())

    def test_wrong_local_address_is_rejected(self):
        controllers = [{"adapter": "hci0", "address": "02:00:00:00:00:03", "supported_settings": "br/edr le"}]
        with patch("btdos.doctor.adapters", return_value=controllers):
            with self.assertRaises(RuntimeError):
                inspect_plan_adapter(self.plan())

    def test_unknown_bredr_controller_address_is_rejected(self):
        controllers = [{"adapter": "hci0", "address": None, "supported_settings": "br/edr le"}]
        with patch("btdos.doctor.adapters", return_value=controllers):
            with self.assertRaises(RuntimeError):
                inspect_plan_adapter(self.plan())

    def test_two_adapters_cannot_bind_selected_index_to_other_address(self):
        controllers = [
            {"adapter": "hci0", "address": "02:00:00:00:00:02", "supported_settings": "br/edr le"},
            {"adapter": "hci1", "address": "02:00:00:00:00:03", "supported_settings": "br/edr le"},
        ]
        with patch("btdos.doctor.adapters", return_value=controllers):
            with self.assertRaises(RuntimeError):
                inspect_plan_adapter(self.plan(adapter="hci1"))
            selected = inspect_plan_adapter(self.plan(adapter="hci1", local_address="02:00:00:00:00:03"))
        self.assertEqual(selected["adapter"], "hci1")

    def test_dbus_addresses_bind_exact_hci_objects_when_sysfs_and_btmgmt_unavailable(self):
        paths = [Path("/sys/class/bluetooth/hci1"), Path("/sys/class/bluetooth/hci0")]
        addresses = {"hci0": "02:00:00:00:00:02", "hci1": "02:00:00:00:00:03"}

        def fixture_command(argv, **_kwargs):
            if argv[0] == "busctl":
                adapter = argv[4].rsplit("/", 1)[1]
                return {"code": 0, "stdout": f's "{addresses[adapter]}"', "stderr": ""}
            if argv == ["bluetoothctl", "list"]:
                return {"code": 0, "stdout": "Controller 02:00:00:00:00:03 Second\nController 02:00:00:00:00:02 First", "stderr": ""}
            return {"code": 1, "stdout": "", "stderr": "fixture management permission denied"}

        with patch("btdos.doctor.sys.platform", "linux"), patch("btdos.doctor.Path.glob", return_value=paths), patch("btdos.doctor.Path.exists", return_value=False), patch("btdos.doctor.command", side_effect=fixture_command) as invoke:
            found = adapters()
        self.assertEqual({item["adapter"]: item["address"] for item in found}, addresses)
        self.assertEqual({item["adapter"]: item["name"] for item in found}, {"hci0": "First", "hci1": "Second"})
        self.assertTrue(all(item["source"] == "bluez-dbus-object" for item in found))
        for adapter in addresses:
            invoke.assert_any_call(["busctl", "--system", "get-property", "org.bluez", f"/org/bluez/{adapter}", "org.bluez.Adapter1", "Address"])

    def test_bluetoothctl_list_never_guesses_unknown_hci_address(self):
        def fixture_command(argv, **_kwargs):
            if argv == ["bluetoothctl", "list"]:
                return {"code": 0, "stdout": "Controller 02:00:00:00:00:99 Unmapped", "stderr": ""}
            return {"code": 1, "stdout": "", "stderr": "fixture unavailable"}

        with patch("btdos.doctor.sys.platform", "linux"), patch("btdos.doctor.Path.glob", return_value=[Path("/sys/class/bluetooth/hci0")]), patch("btdos.doctor.Path.exists", return_value=False), patch("btdos.doctor.command", side_effect=fixture_command):
            found = adapters()
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0]["address"])

    def test_missing_radio_mode_is_rejected(self):
        controllers = [{"adapter": "hci0", "address": "02:00:00:00:00:02", "supported_settings": "le"}]
        with patch("btdos.doctor.adapters", return_value=controllers):
            with self.assertRaises(RuntimeError):
                inspect_plan_adapter(self.plan())

    def test_valid_selected_adapter_is_returned(self):
        selected = {"adapter": "hci0", "address": "02:00:00:00:00:02", "supported_settings": "br/edr le"}
        with patch("btdos.doctor.adapters", return_value=[selected]):
            self.assertEqual(inspect_plan_adapter(self.plan()), selected)


if __name__ == "__main__":
    unittest.main()
