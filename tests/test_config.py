"""Offline validation tests: invalid plans never reach a radio backend."""

import json
import math
from pathlib import Path
import unittest

from btdos.config import Plan, budget_defaults


def valid_plan(**changes):
    values = {
        "case": "br-sdp",
        "adapter": "hci0",
        "target": "02:00:00:00:00:01",
        "local_address": "02:00:00:00:00:02",
    }
    values.update(changes)
    return values


class PlanTests(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        plan = Plan.from_dict(valid_plan())
        plan.validate()
        self.assertEqual(Plan.from_dict(plan.to_dict()).to_dict(), plan.to_dict())

    def test_default_limits_are_bounded(self):
        values = Plan.from_dict(valid_plan()).to_dict()
        self.assertEqual(values["rate"], 10)
        self.assertEqual(values["approved_rate"], 10)
        self.assertEqual(values["duration"], 10)
        self.assertEqual(values["max_ops"], 100)
        self.assertEqual(values["cycles"], 5)
        self.assertEqual(values["hold"], 2)
        self.assertEqual(values["gap"], 3)
        self.assertEqual(values["baseline"], 5)
        self.assertEqual(values["recovery"], 10)
        self.assertEqual(values["capture"], "hci")
        self.assertFalse(values["external_business_probe"])
        self.assertFalse(values["external_ecu_observation"])

    def test_numeric_boundaries(self):
        boundaries = {
            "rate": (0.1, 20),
            "duration": (1, 60),
            "max_ops": (1, 400),
            "cycles": (1, 10),
            "hold": (1, 5),
            "gap": (3, 30),
            "baseline": (0, 60),
            "recovery": (0, 120),
        }
        for field, endpoints in boundaries.items():
            for value in endpoints:
                with self.subTest(field=field, value=value):
                    plan = Plan.from_dict(valid_plan(approved_rate=20, **{field: value}))
                    plan.validate()

    def test_budget_defaults_are_independent_copies_and_quick_is_five_operations(self):
        default = budget_defaults()
        quick = budget_defaults(quick=True)
        self.assertEqual(default["rate"], 10)
        self.assertEqual(default["approved_rate"], 10)
        self.assertEqual(default["max_ops"], 100)
        self.assertEqual(quick["rate"], 1)
        self.assertEqual(quick["approved_rate"], 1)
        self.assertEqual(quick["max_ops"], 5)
        self.assertEqual(quick["duration"], 5)
        default["max_ops"] = 400
        quick["duration"] = 60
        self.assertEqual(budget_defaults()["max_ops"], 100)
        self.assertEqual(budget_defaults(quick=True)["duration"], 5)

    def test_existing_explicit_400_operation_plan_remains_supported(self):
        plan = Plan.from_dict(valid_plan(max_ops=400))
        plan.validate()
        self.assertEqual(plan.max_ops, 400)
        self.assertEqual(Plan.from_dict(plan.to_dict()).max_ops, 400)

    def test_existing_explicit_low_load_plan_is_not_silently_upgraded(self):
        plan = Plan.from_dict(valid_plan(rate=1, approved_rate=1, duration=10, max_ops=10))
        self.assertEqual((plan.rate, plan.approved_rate, plan.duration, plan.max_ops), (1, 1, 10, 10))
        self.assertEqual(Plan.from_dict(plan.to_dict()).to_dict(), plan.to_dict())

    def test_example_plans_match_current_default_budget(self):
        examples = Path(__file__).resolve().parents[1] / "examples" / "plans"
        plans = sorted(examples.glob("*.json"))
        self.assertEqual(len(plans), 5)
        for path in plans:
            with self.subTest(plan=path.name):
                values = json.loads(path.read_text(encoding="utf-8"))
                for field, default in budget_defaults().items():
                    self.assertEqual(values[field], default)

    def test_numeric_out_of_bounds(self):
        invalid = {
            "rate": (0, 0.09, 20.01),
            "duration": (0, 61),
            "max_ops": (0, 401),
            "cycles": (0, 11),
            "hold": (0, 6),
            "gap": (2, 31),
            "baseline": (-1, 61),
            "recovery": (-1, 121),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        Plan.from_dict(valid_plan(approved_rate=20, **{field: value})).validate()

    def test_nonfinite_numeric_values_are_rejected(self):
        fields = (
            "rate", "approved_rate", "duration", "max_ops", "cycles", "hold",
            "gap", "baseline", "recovery",
        )
        for field in fields:
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        Plan.from_dict(valid_plan(**{field: value})).validate()

    def test_oversized_json_integers_are_rejected_as_validation_errors(self):
        for field in ("rate", "approved_rate", "duration", "hold", "gap", "baseline", "recovery"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(**{field: 10 ** 1000}))

    def test_effective_rate_cannot_exceed_approved_rate(self):
        with self.assertRaises(ValueError):
            Plan.from_dict(valid_plan(rate=2, approved_rate=1)).validate()

    def test_case_and_capture_are_enumerated(self):
        for changes in ({"case": "unknown"}, {"capture": "none"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(**changes)).validate()

    def test_json_wrong_types_are_validation_errors(self):
        for field in ("case", "adapter", "target", "local_address", "capture", "output_root"):
            for value in ([], {}, 1):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        Plan.from_dict(valid_plan(**{field: value}))

    def test_unknown_fields_and_missing_required_fields_are_rejected(self):
        for value in (valid_plan(unknown=True), {}, [], None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Plan.from_dict(value)

    def test_boolean_and_fractional_counts_are_rejected(self):
        for field in ("sdp_uuid16", "max_ops", "cycles"):
            for value in (True, 1.5, "1"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        Plan.from_dict(valid_plan(**{field: value}))

    def test_boolean_numeric_limits_are_rejected(self):
        for field in ("rate", "approved_rate", "duration", "hold", "gap", "baseline", "recovery"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(**{field: True}))

    def test_schema_version_is_an_explicit_supported_integer(self):
        for version in (0, 2, "1", True):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(schema_version=version))

    def test_target_must_be_a_mac_address(self):
        for target in (
            "", "TARGET", "02:00:00:00:00", "02:00:00:00:00:GG",
            "00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF",
        ):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(target=target)).validate()

    def test_bredr_requires_local_adapter_address(self):
        for case in ("br-sdp", "br-channel"):
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(case=case, local_address=None)).validate()

    def test_ble_read_requires_characteristic(self):
        with self.assertRaises(ValueError):
            Plan.from_dict(valid_plan(case="ble-read", characteristic=None)).validate()

    def test_ble_read_accepts_explicit_uuid_or_handle(self):
        for characteristic in ("0x0012", "00002a00-0000-1000-8000-00805f9b34fb"):
            with self.subTest(characteristic=characteristic):
                Plan.from_dict(valid_plan(case="ble-read", characteristic=characteristic))

    def test_ble_cycle_needs_no_characteristic(self):
        Plan.from_dict(valid_plan(case="ble-cycle", local_address=None)).validate()

    def test_nrf_capture_requires_port(self):
        with self.assertRaises(ValueError):
            Plan.from_dict(valid_plan(capture="hci+nrf", nrf_port=None)).validate()

    def test_nrf_capture_is_limited_to_ble_and_explicit_device_port(self):
        Plan.from_dict(valid_plan(case="ble-cycle", capture="hci+nrf", nrf_port="/dev/ttyACM0"))
        for changes in (
            {"capture": "hci+nrf", "nrf_port": "/dev/ttyACM0"},
            {"case": "ble-cycle", "capture": "hci+nrf", "nrf_port": "/tmp/device"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    Plan.from_dict(valid_plan(**changes))

    def test_budgets_are_positive_and_include_observation_windows(self):
        for case in ("br-sdp", "br-channel", "ble-read", "ble-cycle"):
            plan = Plan.from_dict(valid_plan(case=case, characteristic="0x0012"))
            with self.subTest(case=case):
                self.assertGreater(plan.worker_budget, 0)
                self.assertGreaterEqual(plan.wall_budget, plan.worker_budget + plan.baseline + plan.recovery)


if __name__ == "__main__":
    unittest.main()
