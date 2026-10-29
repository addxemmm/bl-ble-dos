"""Discovery tests are synthetic: no system bus, radio, or Device1 operations."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from btdos import discovery as d


PATH = "/org/bluez/hci0/dev_02_00_00_00_00_01"
OWNER = ":1.42"


def signal(interface, member, body, path="/", sender=OWNER):
    return SimpleNamespace(interface=interface, member=member, body=body, path=path, sender=sender)


def added(props, path=PATH, sender=OWNER):
    return signal(d.OBJECT_MANAGER, "InterfacesAdded", [path, {d.DEVICE_IFACE: props}], sender=sender)


class FakeClient:
    def __init__(self, events=(), fail=None, shared=False, objects=None):
        self.events = events
        self.fail = fail or {}
        self.objects = objects if objects is not None else {
            "/org/bluez/hci0": {d.ADAPTER_IFACE: {"Discovering": shared}},
            PATH: {d.DEVICE_IFACE: {"Address": "02:00:00:00:00:01", "Name": "cached", "Paired": True}},
        }
        self.calls = []
        self.handler = None
        self.closed = False
        self.done = asyncio.Event()

    async def connect(self):
        if "connect" in self.fail:
            raise self.fail["connect"]

    async def call(self, destination, path, interface, member, signature="", body=None):
        self.calls.append((destination, path, interface, member, signature, body))
        if member in self.fail:
            raise self.fail[member]
        if member == "GetNameOwner":
            return [OWNER]
        if member == "GetManagedObjects":
            return [self.objects]
        if member == "StartDiscovery":
            for event in self.events:
                self.handler(event)
        return []

    def add_handler(self, handler):
        self.handler = handler

    def remove_handler(self, handler):
        assert self.handler is handler
        self.handler = None

    def is_signal(self, message):
        return True

    def variant(self, signature, value):
        return SimpleNamespace(signature=signature, value=value)

    async def wait_for_disconnect(self):
        await self.done.wait()

    def disconnect(self):
        self.closed = True
        self.done.set()
        if "disconnect" in self.fail:
            raise self.fail["disconnect"]


class _NoSocketLoop(asyncio.SelectorEventLoop):
    # Windows' default Proactor loop creates a loopback socketpair even for pure
    # coroutine tests. This local loop has no file descriptors or network access.
    def _make_self_pipe(self):
        pass

    def _write_to_self(self):
        pass

    def _close_self_pipe(self):
        pass


def run_synthetic(coroutine):
    loop = _NoSocketLoop()
    loop._selector.select = lambda timeout=None: []
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


class DiscoveryTests(unittest.TestCase):
    def scan(self, client, progress=None):
        clock = SimpleNamespace(now=0.0)
        real_sleep = asyncio.sleep

        async def pause(seconds):
            clock.now += seconds
            await real_sleep(0)

        with patch.object(d, "_new_client", return_value=client), \
                patch.object(d, "time", SimpleNamespace(monotonic=lambda: clock.now)), \
                patch.object(d.asyncio, "sleep", pause):
            return run_synthetic(d._scan_async("le", "hci0", 1, progress))

    def test_cache_is_metadata_never_a_fresh_result(self):
        client = FakeClient()
        result = self.scan(client)
        self.assertEqual(result.devices, [])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.cleanup_status, "released")
        self.assertTrue(client.closed)
        self.assertIsNone(client.handler)

    def test_current_rssi_enriches_cached_name_without_claiming_bearer(self):
        event = signal(d.PROPERTIES, "PropertiesChanged", [d.DEVICE_IFACE, {"RSSI": -43}, []], PATH)
        result = self.scan(FakeClient([event]))
        self.assertEqual(len(result.devices), 1)
        device = result.devices[0]
        self.assertEqual(device.display_name, "cached")
        self.assertEqual(device.address, "02:00:00:00:00:01")
        self.assertTrue(device.paired)
        self.assertFalse(device.transport_verified)
        self.assertEqual(device.rssi, -43)
        self.assertTrue(device.first_seen)
        self.assertEqual(device.first_seen, device.last_seen)
        self.assertEqual(result.to_dict()["devices"][0]["rssi"], -43)

    def test_alias_pairing_uuid_only_updates_do_not_qualify(self):
        events = [signal(d.PROPERTIES, "PropertiesChanged", [d.DEVICE_IFACE, {"Alias": "new", "Paired": True, "UUIDs": ["fixture"]}, []], PATH),
                  added({"Address": "02:00:00:00:00:02", "Alias": "alias"}, PATH[:-2] + "02")]
        self.assertEqual(self.scan(FakeClient(events)).devices, [])

    def test_discovery_keys_each_qualify(self):
        for key in d.DISCOVERY_KEYS:
            with self.subTest(key=key):
                result = self.scan(FakeClient([added({key: "名字" if key == "Name" else -40})]))
                self.assertEqual(len(result.devices), 1)

    def test_owner_adapter_exact_device_path_and_interfaces_are_filtered(self):
        events = [added({"Name": "other"}, PATH.replace("hci0", "hci1")),
            added({"Name": "spoof"}, sender=":1.99"),
            added({"Name": "child"}, PATH + "/service0001"),
            added({"Name": "prefix"}, PATH.replace("hci0", "hci00")),
            signal(d.PROPERTIES, "PropertiesChanged", ["org.bluez.Battery1", {"RSSI": -20}, []], PATH),
            added({"Name": "real"})]
        result = self.scan(FakeClient(events))
        self.assertEqual([item.display_name for item in result.devices], ["real"])

    def test_initial_metadata_signals_are_not_observations(self):
        observed = d._Observations("hci0", [])
        observed.update(PATH, {"Name": "early", "RSSI": -30}, "bluez-properties-changed")
        self.assertEqual(observed.devices, {})
        observed.active = True
        observed.update(PATH, {"Alias": "alias"}, "bluez-properties-changed")
        self.assertEqual(observed.devices, {})

    def test_metadata_update_preserves_last_observed_time_and_removed_observation(self):
        observed = d._Observations("hci0", [])
        observed.active = True
        with patch.object(d, "_utc", side_effect=["first", "second"]):
            observed.update(PATH, {"Name": "name"}, "bluez-interfaces-added")
            observed.update(PATH, {"Alias": "alias", "Paired": True}, "bluez-properties-changed")
        self.assertEqual(observed.devices[PATH].last_seen, "first")
        observed.removed(PATH, [d.DEVICE_IFACE])
        self.assertNotIn(PATH, observed.cache)
        self.assertIn(PATH, observed.devices)

    def test_invalidated_values_are_not_retained(self):
        observed = d._Observations("hci0", [])
        observed.active = True
        observed.update(PATH, {"Name": "first", "RSSI": -20}, "bluez-interfaces-added")
        observed.update(PATH, {"ManufacturerData": {}}, "bluez-properties-changed", ["RSSI", "Name"])
        self.assertIsNone(observed.devices[PATH].rssi)
        self.assertEqual(observed.devices[PATH].display_name, "未命名设备")

    def test_metadata_and_observation_memory_are_bounded(self):
        warnings = []
        observed = d._Observations("hci0", warnings)
        observed.active = True
        for number in range(510):
            path = f"/org/bluez/hci0/dev_02_00_00_00_{number // 256:02X}_{number % 256:02X}"
            observed.update(path, {"Name": str(number), "ManufacturerData": {1: b"x" * 3000}}, "bluez-interfaces-added")
        self.assertEqual(len(observed.cache), d.MAX_DEVICES)
        self.assertEqual(len(observed.devices), d.MAX_DEVICES)
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("ManufacturerData", next(iter(observed.cache.values())))

    def test_transport_filter_and_only_owned_discovery_calls(self):
        client = FakeClient(shared=True)
        result = self.scan(client)
        members = [call[3] for call in client.calls]
        self.assertEqual(members.count("StartDiscovery"), 1)
        self.assertEqual(members.count("StopDiscovery"), 1)
        forbidden = {"Connect", "Pair", "ConnectDevice", "RemoveDevice", "Set"}
        self.assertTrue(forbidden.isdisjoint(members))
        filter_call = next(call for call in client.calls if call[3] == "SetDiscoveryFilter")
        self.assertEqual(filter_call[5][0]["Transport"].value, "le")
        self.assertEqual(filter_call[5][0]["DuplicateData"].value, True)
        self.assertEqual(filter_call[0], OWNER)
        self.assertTrue(result.shared_discovery_at_start)
        self.assertTrue(result.warnings)

    def test_failed_or_uncertain_start_closes_connection_without_stop(self):
        for error in (RuntimeError("org.bluez.Error.NotReady"), TimeoutError()):
            with self.subTest(error=error):
                client = FakeClient(fail={"StartDiscovery": error})
                result = self.scan(client)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.cleanup_status, "connection_closed")
                self.assertTrue(client.closed)
                self.assertNotIn("StopDiscovery", [call[3] for call in client.calls])

    def test_stop_failure_cannot_look_like_success(self):
        client = FakeClient(fail={"StopDiscovery": RuntimeError("denied")})
        result = self.scan(client)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.cleanup_status, "connection_closed")
        self.assertTrue(client.closed)
        self.assertTrue(any("StopDiscovery" in note for note in result.warnings))

    def test_disconnect_failure_is_unconfirmed_not_success(self):
        result = self.scan(FakeClient(fail={"disconnect": RuntimeError("fixture close failure")}))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.cleanup_status, "unconfirmed")

    def test_filter_connection_and_missing_adapter_errors_close_connection(self):
        cases = [FakeClient(fail={"connect": OSError("system bus missing")}),
                 FakeClient(fail={"SetDiscoveryFilter": RuntimeError("not supported")}),
                 FakeClient(objects={})]
        for client in cases:
            with self.subTest(client=client):
                result = self.scan(client)
                self.assertEqual(result.status, "failed")
                self.assertTrue(client.closed)
                self.assertNotIn("StartDiscovery", [call[3] for call in client.calls])

    def test_bluez_restart_is_failure_not_new_daemon_stop(self):
        change = signal("org.freedesktop.DBus", "NameOwnerChanged", ["org.bluez", OWNER, ":1.55"],
                        "/org/freedesktop/DBus", "org.freedesktop.DBus")
        client = FakeClient([change])
        result = self.scan(client)
        self.assertEqual(result.status, "failed")
        self.assertTrue(client.closed)
        self.assertNotIn("StopDiscovery", [call[3] for call in client.calls])

    def test_mid_scan_bus_disconnect_is_failure(self):
        client = FakeClient()

        async def disconnected():
            return None

        client.wait_for_disconnect = disconnected
        self.assertEqual(self.scan(client).status, "failed")
        self.assertTrue(client.closed)

    def test_cancellation_and_keyboard_interrupt_return_cleanup_evidence(self):
        for error in (asyncio.CancelledError(), KeyboardInterrupt()):
            client = FakeClient()

            def progress(elapsed, count):
                raise error

            with self.subTest(error=error):
                result = self.scan(client, progress)
                self.assertEqual(result.status, "interrupted")
                self.assertEqual(result.cleanup_status, "released")
            self.assertTrue(client.closed)
            self.assertIn("StopDiscovery", [call[3] for call in client.calls])

    def test_cancellation_preserves_stop_failure_and_close_failure_details(self):
        for error in (asyncio.CancelledError(), KeyboardInterrupt()):
            for member, expected_cleanup, note in (
                    ("StopDiscovery", "connection_closed", "StopDiscovery"),
                    ("disconnect", "unconfirmed", "连接关闭未确认")):
                with self.subTest(interrupt=error, member=member):
                    client = FakeClient(fail={member: RuntimeError("fixture cleanup failure")})

                    def progress(elapsed, count):
                        raise error

                    result = self.scan(client, progress)
                    self.assertEqual(result.status, "interrupted")
                    self.assertEqual(result.cleanup_status, expected_cleanup)
                    self.assertTrue(any(note in warning for warning in result.warnings))
                    self.assertTrue(client.closed)

    def test_actual_task_cancel_returns_interrupted_after_cleanup(self):
        client = FakeClient()

        def progress(elapsed, count):
            asyncio.current_task().cancel()

        result = self.scan(client, progress)
        self.assertEqual(result.status, "interrupted")
        self.assertEqual(result.cleanup_status, "released")
        self.assertTrue(client.closed)

    def test_outer_runner_keyboard_interrupt_retains_shared_cleanup_result(self):
        for finished in (True, False):
            expected = d.ScanResult("le", "hci0", "fixture", 0.0, [],
                status="interrupted", cleanup_status="connection_closed",
                warnings=["StopDiscovery fixture failure"])

            async def scan(*args, _result_sink):
                _result_sink.update(result=expected, cleanup_finished=finished)
                return expected

            def runner(coroutine):
                run_synthetic(coroutine)
                raise KeyboardInterrupt()

            with self.subTest(cleanup_finished=finished), patch.object(d.sys, "platform", "linux"), \
                    patch.object(d, "adapter_lock", return_value=nullcontext()), \
                    patch.object(d, "_scan_async", new=scan), \
                    patch.object(d.asyncio, "run", side_effect=runner):
                result = d.scan_devices("le")
            self.assertIs(result, expected)
            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.cleanup_status, "connection_closed" if finished else "unconfirmed")
            self.assertIn("StopDiscovery fixture failure", result.warnings)

    def test_outer_runner_interrupt_without_result_is_unconfirmed(self):
        def runner(coroutine):
            coroutine.close()
            raise KeyboardInterrupt()

        with patch.object(d.sys, "platform", "linux"), \
                patch.object(d, "adapter_lock", return_value=nullcontext()), \
                patch.object(d.asyncio, "run", side_effect=runner):
            result = d.scan_devices("le")
        self.assertEqual(result.status, "interrupted")
        self.assertEqual(result.cleanup_status, "unconfirmed")
        self.assertTrue(result.warnings)

    def test_progress_is_bounded_and_counts_only_current_observations(self):
        values = []
        result = self.scan(FakeClient([added({"Name": "fresh"})]), lambda seconds, count: values.append((seconds, count)))
        self.assertEqual(result.duration, 1.0)
        self.assertTrue(values)
        self.assertEqual(values[-1], (1, 1))
        self.assertTrue(all(0 <= seconds <= 1 and count == 1 for seconds, count in values))

    def test_name_sanitization_preserves_chinese_and_literal_markup(self):
        hostile = "\x1b[31m中文\x1b[0m\n\x1b]0;title\x07[red]文字[/red]\u202e\x00"
        self.assertEqual(d.safe_display_name(hostile), "中文[red]文字[/red]")
        self.assertEqual(d.safe_display_name("abc", 2), "ab")
        self.assertEqual(d.safe_display_name("abc", -1), "")

    def test_input_validation_has_no_bus_or_lock_side_effects(self):
        with patch.object(d, "_new_client") as bus, patch.object(d, "adapter_lock") as lock:
            for kwargs in ({"transport": "auto"}, {"transport": "le", "adapter": "hci0/dev"},
                           {"transport": "le", "adapter": "hci1000"},
                           {"transport": "le", "adapter": "hci１"},
                           {"transport": "le", "seconds": 0}, {"transport": "le", "seconds": 16},
                           {"transport": "le", "seconds": True}, {"transport": "le", "seconds": 1.5}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    d.scan_devices(**kwargs)
            with patch.object(d.sys, "platform", "win32"), self.assertRaises(RuntimeError):
                d.scan_devices("le")
        bus.assert_not_called()
        lock.assert_not_called()

    def test_sync_wrapper_uses_shared_lock_and_runtime_lock_errors_are_results(self):
        with patch.object(d.sys, "platform", "linux"), \
                patch.object(d, "adapter_lock", side_effect=RuntimeError("hci0 busy")):
            result = d.scan_devices("le")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.cleanup_status, "not_started")
        client = FakeClient()
        with patch.object(d.sys, "platform", "linux"), patch.object(d, "adapter_lock", return_value=nullcontext()) as lock, \
                patch.object(d, "_scan_async", return_value=result), \
                patch.object(d.asyncio, "run", side_effect=run_synthetic):
            self.assertIs(d.scan_devices("bredr", seconds=15), result)
        lock.assert_called_once_with("hci0")


if __name__ == "__main__":
    unittest.main()
