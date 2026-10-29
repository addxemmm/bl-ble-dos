"""Synthetic capability probes: never touch a bus, controller, or socket."""
import asyncio
import json
import subprocess
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from btdos import probe as p

TARGET = "02:00:00:00:00:01"
LOCAL = "02:00:00:00:00:02"
PATH = "/org/bluez/hci0/dev_02_00_00_00_00_01"
XML = b'''Browsing 02:00:00:00:00:01 ...
<?xml version="1.0"?><record>
<attribute id="0x0000"><uint32 value="0x10000"/></attribute>
<attribute id="0x0001"><sequence><uuid value="0x1101"/></sequence></attribute>
<attribute id="0x0004"><sequence>
<sequence><uuid value="0x0100"/><uint16 value="0x0003"/></sequence>
<sequence><uuid value="0x0003"/><uint8 value="7"/></sequence>
</sequence></attribute>
<attribute id="0x0100"><text value="fixture"/></attribute></record>'''


class _NoSocketLoop(asyncio.SelectorEventLoop):
    def _make_self_pipe(self):
        pass

    def _write_to_self(self):
        pass

    def _close_self_pipe(self):
        pass


def run(coroutine):
    loop = _NoSocketLoop()
    loop._selector.select = lambda timeout=None: []
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


class FakeBus:
    def __init__(self, connected=False, objects=None, fail=None):
        self.objects = objects if objects is not None else {
            "/org/bluez/hci0": {p.discovery.ADAPTER_IFACE: {"Address": LOCAL}},
            PATH: {p.discovery.DEVICE_IFACE: {"Connected": connected,
                "Name": "\x1b[31mfixture\n", "Paired": False, "ServicesResolved": False,
                "AddressType": "random", "UUIDs": ["1101"], "ManufacturerData": {1: b"secret"}}}}
        self.fail = fail or {}
        self.calls = []
        self.closed = False
        self.done = asyncio.Event()

    async def connect(self):
        if "connect" in self.fail:
            raise self.fail["connect"]

    async def call(self, destination, path, interface, member, *args):
        self.calls.append(member)
        if member in self.fail:
            raise self.fail[member]
        return [":1.42"] if member == "GetNameOwner" else [self.objects]

    def disconnect(self):
        self.closed = True
        self.done.set()
        if "disconnect" in self.fail:
            raise self.fail["disconnect"]

    async def wait_for_disconnect(self):
        await self.done.wait()


class FakeStream:
    def __init__(self, data):
        self.data = data

    async def read(self, count):
        part, self.data = self.data[:count], self.data[count:]
        return part


class FakeProcess:
    def __init__(self, stdout=XML, stderr=b"", code=0, waiting=False, terminate_fail=False, kill_fail=False):
        self.stdout, self.stderr = FakeStream(stdout), FakeStream(stderr)
        self.returncode = None
        self.code = code
        self.waiting = waiting
        self.terminated = False
        self.killed = False
        self.terminate_fail = terminate_fail
        self.kill_fail = kill_fail
        self.done = asyncio.Event()

    async def wait(self):
        if self.waiting and not self.terminated and not self.killed:
            await self.done.wait()
        self.returncode = self.code
        return self.code

    def terminate(self):
        if self.terminate_fail:
            raise OSError("fixture termination failure")
        self.terminated = True
        self.done.set()

    def kill(self):
        if self.kill_fail:
            raise OSError("fixture kill failure")
        self.killed = True
        self.done.set()


class FakeBLE:
    def __init__(self, fail=None, services=None):
        self.is_connected = False
        self.calls = []
        self.fail = fail or {}
        char = SimpleNamespace(uuid="2a00", handle=3, properties=["read"], description="fixture")
        self.services = services if services is not None else [
            SimpleNamespace(uuid="1800", description="Generic Access", characteristics=[char])]

    async def connect(self):
        self.calls.append("connect")
        self.is_connected = True
        if "connect" in self.fail:
            raise self.fail["connect"]

    async def disconnect(self):
        self.calls.append("disconnect")
        if "disconnect" in self.fail:
            raise self.fail["disconnect"]
        self.is_connected = False


class SDPParserTests(unittest.TestCase):
    def test_service_class_only_not_protocol_ids(self):
        services = p.parse_sdp_xml(XML)
        self.assertEqual(len(services), 1)
        service = services[0]
        self.assertEqual(service.uuid16, 0x1101)
        self.assertEqual(service.uuid, "00001101-0000-1000-8000-00805f9b34fb")
        self.assertEqual((service.name, service.record_handle, service.psm, service.channel),
                         ("fixture", 0x10000, 3, 7))
        self.assertTrue(service.selectable)

    def test_uuid16_base128_and_custom_uuid(self):
        for value in ("1101", "0x1101", "00001101-0000-1000-8000-00805f9b34fb"):
            self.assertEqual(p.normalize_uuid(value)[1], 0x1101)
        custom = "12345678-1234-5678-9876-123456789abc"
        parsed = p.parse_sdp_xml(XML.replace(b"0x1101", custom.encode()))[0]
        self.assertEqual(parsed.uuid, custom)
        self.assertIsNone(parsed.uuid16)
        self.assertFalse(parsed.selectable)
        self.assertIsNone(p.normalize_uuid("12341101-0000-1000-8000-00805f9b34fb")[1])

    def test_repeated_declarations_and_wrapped_records(self):
        self.assertEqual(len(p.parse_sdp_xml(XML + XML)), 2)
        self.assertEqual(len(p.parse_sdp_xml(b"<records>" + XML + b"</records>")), 1)

    def test_empty_or_no_class_is_no_result(self):
        self.assertEqual(p.parse_sdp_xml(b"Browsing fixture ..."), [])
        self.assertEqual(p.parse_sdp_xml(b"<record><attribute id='0x0004'><uuid value='0x0100'/></attribute></record>"), [])

    def test_untrusted_xml_and_malformed_records_rejected(self):
        for data in (b'<!DOCTYPE x [<!ENTITY y "x">]><record/>', b"<record>",
                     XML + b"<oops/>", XML.replace(b"fixture", b"&external;"),
                     b"<record><attribute></record>"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                p.parse_sdp_xml(data)

    def test_output_record_and_depth_limits(self):
        for data in (b"x" * (p.MAX_OUTPUT + 1), b"<record></record>" * (p.MAX_RECORDS + 1),
                     b"<record>" + b"<sequence>" * 34 + b"</sequence>" * 34 + b"</record>"):
            with self.assertRaises(ValueError):
                p.parse_sdp_xml(data)

    def test_display_name_is_sanitized(self):
        name = p.parse_sdp_xml(XML.replace(b"fixture", b"hello&#10;world"))[0].name
        self.assertEqual(name, "helloworld")


class ProbeTests(unittest.TestCase):
    def probe(self, bus=None, process=None, ble=None, device=None, transport="le", local_address=LOCAL):
        bus = bus or FakeBus()
        device = device or SimpleNamespace(address=TARGET, details={"path": PATH})
        with patch.object(p.discovery, "_new_client", return_value=bus), \
                patch.object(p, "_find_ble", new=AsyncMock(return_value=device)), \
                patch.object(p, "_new_ble_client", return_value=ble or FakeBLE()), \
                patch.object(p.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process or FakeProcess())):
            result = run(p._probe_async(transport, TARGET, "hci0", local_address))
        return result

    def test_gatt_enumerates_metadata_without_io(self):
        ble = FakeBLE()
        result = self.probe(ble=ble)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.cleanup_status, "released")
        self.assertEqual(ble.calls, ["connect", "disconnect"])
        char = result.characteristics[0]
        self.assertEqual((char.service_uuid, char.uuid, char.handle, char.readable),
                         ("00001800-0000-1000-8000-00805f9b34fb", "00002a00-0000-1000-8000-00805f9b34fb", 3, True))
        self.assertEqual(result.to_dict()["metadata"]["Name"], "fixture")
        self.assertNotIn("ManufacturerData", result.metadata)

    def test_no_gatt_services_is_not_successful_catalog(self):
        result = self.probe(ble=FakeBLE(services=[]))
        self.assertEqual(result.status, "no_results")

    def test_already_connected_target_never_creates_client_or_process(self):
        for transport in ("le", "bredr"):
            with patch.object(p.discovery, "_new_client", return_value=FakeBus(connected=True)), \
                    patch.object(p, "_new_ble_client") as client, \
                    patch.object(p.asyncio, "create_subprocess_exec") as process:
                result = run(p._probe_async(transport, TARGET, "hci0", LOCAL))
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.cleanup_status, "released")
            client.assert_not_called()
            process.assert_not_called()

    def test_connected_at_second_snapshot_never_connects_or_disconnects(self):
        device = SimpleNamespace(address=TARGET, details={"path": PATH})
        for second_bus in (FakeBus(connected=True), FakeBus(objects={
                "/org/bluez/hci0": {p.discovery.ADAPTER_IFACE: {"Address": LOCAL}}})):
            with patch.object(p.discovery, "_new_client", side_effect=[FakeBus(), second_bus]), \
                    patch.object(p, "_find_ble", new=AsyncMock(return_value=device)), \
                    patch.object(p, "_new_ble_client") as client:
                result = run(p._probe_async("le", TARGET, "hci0", LOCAL))
            self.assertEqual(result.status, "failed")
            client.assert_not_called()

    def test_unknown_connected_status_is_fail_closed(self):
        bus = FakeBus()
        del bus.objects[PATH][p.discovery.DEVICE_IFACE]["Connected"]
        self.assertEqual(self.probe(bus=bus).status, "failed")

    def test_wrong_adapter_missing_adapter_and_local_mismatch(self):
        cases = [dict(device=SimpleNamespace(address=TARGET, details={"path": PATH.replace("hci0", "hci1")})),
                 dict(bus=FakeBus(objects={})), dict(local_address="00:00:00:00:00:00")]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.probe(**kwargs).status, "failed")

    def test_connect_error_or_cancellation_disconnects_attempted_client(self):
        # Coroutine cancellation and process SIGINT are different layers.
        # On Python 3.10 a Task-raised KeyboardInterrupt exits the test loop;
        # actual SIGINT through the public runner is covered below instead.
        for error in (RuntimeError("denied"), asyncio.CancelledError()):
            ble = FakeBLE(fail={"connect": error})
            result = self.probe(ble=ble)
            self.assertEqual(result.status, "failed" if isinstance(error, Exception) else "interrupted")
            self.assertEqual(result.cleanup_status, "released")
            self.assertEqual(ble.calls, ["connect", "disconnect"])

    def test_actual_connect_budget_timeout_disconnects(self):
        ble = FakeBLE()

        async def connect():
            ble.calls.append("connect")
            ble.is_connected = True
            await asyncio.Event().wait()

        ble.connect = connect
        with patch.object(p, "CONNECT_TIMEOUT", 0.001):
            result = self.probe(ble=ble)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "released"))
        self.assertEqual(ble.calls, ["connect", "disconnect"])

    def test_actual_task_cancel_preserves_cleanup(self):
        ble = FakeBLE()

        async def connect():
            ble.calls.append("connect")
            ble.is_connected = True
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

        ble.connect = connect
        result = self.probe(ble=ble)
        self.assertEqual((result.status, result.cleanup_status), ("interrupted", "released"))
        self.assertEqual(ble.calls, ["connect", "disconnect"])

    def test_disconnect_failure_or_timeout_is_unconfirmed(self):
        for error in (RuntimeError("fixture fail"), asyncio.TimeoutError()):
            result = self.probe(ble=FakeBLE(fail={"disconnect": error}))
            self.assertEqual((result.status, result.cleanup_status), ("failed", "unconfirmed"))
            self.assertTrue(any("disconnect" in warning for warning in result.warnings))

    def test_cancellation_plus_disconnect_failure_remains_interrupted(self):
        result = self.probe(ble=FakeBLE(fail={"connect": asyncio.CancelledError(),
                                             "disconnect": RuntimeError("fixture cleanup fail")}))
        self.assertEqual((result.status, result.cleanup_status), ("interrupted", "unconfirmed"))

    def test_bus_failure_is_fail_closed_and_connection_closed(self):
        for member in ("connect", "GetManagedObjects", "GetNameOwner", "disconnect"):
            bus = FakeBus(fail={member: RuntimeError("fixture")})
            result = self.probe(bus=bus)
            self.assertTrue(bus.closed)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.cleanup_status, "unconfirmed" if member == "disconnect" else "released")
            self.assertTrue(set(bus.calls).issubset({"GetNameOwner", "GetManagedObjects"}))

    def test_sdp_fixed_argv_uses_verified_local_address(self):
        process = FakeProcess()
        with patch.object(p.discovery, "_new_client", return_value=FakeBus()), \
                patch.object(p.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)) as start:
            result = run(p._probe_async("bredr", TARGET, "hci0", None))
        self.assertEqual(start.call_args.args, ("sdptool", "-i", LOCAL, "browse", "--xml", TARGET))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.cleanup_status, "released")
        self.assertEqual(len(result.services), 1)
        self.assertNotIn("shell", start.call_args.kwargs)

    def test_sdp_empty_exit_zero_and_nonzero(self):
        empty = self.probe(transport="bredr", process=FakeProcess(stdout=b""))
        failed = self.probe(transport="bredr", process=FakeProcess(code=1, stderr=b"fixture"))
        self.assertEqual(empty.status, "no_results")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.services, [])

    def test_sdp_output_overflow_terminates_process(self):
        process = FakeProcess(stdout=b"x" * (p.MAX_OUTPUT + 1), waiting=True)
        result = self.probe(transport="bredr", process=process)
        self.assertTrue(process.terminated)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "released"))

    def test_sdp_timeout_or_cancellation_terminates_process(self):
        for error in (asyncio.TimeoutError(), asyncio.CancelledError()):
            process = FakeProcess(waiting=True)
            injected = False

            async def fail_read(count):
                nonlocal injected
                if not injected:
                    injected = True
                    raise error
                return b""  # Fresh cleanup drainer reaches EOF after the abort.

            process.stdout.read = fail_read
            result = self.probe(transport="bredr", process=process)
            self.assertTrue(process.terminated)
            self.assertEqual(result.status, "failed" if isinstance(error, Exception) else "interrupted")
            self.assertEqual(result.cleanup_status, "released")

    def test_persistent_pipe_failure_does_not_claim_cleanup_success(self):
        process = FakeProcess(waiting=True)
        async def broken_pipe(count):
            raise OSError("fixture pipe failure")
        process.stdout.read = broken_pipe
        result = self.probe(transport="bredr", process=process)
        self.assertTrue(process.terminated)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "unconfirmed"))

    def test_actual_sdp_budget_timeout_terminates_process(self):
        process = FakeProcess(waiting=True)
        with patch.object(p, "SDP_TIMEOUT", 0.001):
            result = self.probe(transport="bredr", process=process)
        self.assertTrue(process.terminated)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "released"))

    def test_sdp_cleanup_failure_is_not_success(self):
        process = FakeProcess(stdout=b"x" * (p.MAX_OUTPUT + 1), waiting=True,
                              terminate_fail=True, kill_fail=True)
        result = self.probe(transport="bredr", process=process)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "unconfirmed"))

    def test_sdp_terminate_failure_still_attempts_kill_and_reaps(self):
        process = FakeProcess(stdout=b"x" * (p.MAX_OUTPUT + 1), waiting=True, terminate_fail=True)
        result = self.probe(transport="bredr", process=process)
        self.assertTrue(process.killed)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "released"))

    def test_find_not_seen_and_find_failure(self):
        for device, error, expected in ((None, None, "no_results"), (None, RuntimeError("scan failed"), "failed")):
            with patch.object(p.discovery, "_new_client", return_value=FakeBus()), \
                    patch.object(p, "_find_ble", new=AsyncMock(return_value=device, side_effect=error)), \
                    patch.object(p, "_new_ble_client") as client:
                result = run(p._probe_async("le", TARGET, "hci0", LOCAL))
            self.assertEqual(result.status, expected)
            self.assertEqual(result.cleanup_status, "released" if error is None else "unconfirmed")
            client.assert_not_called()

    def test_sync_validation_precedes_lock_or_bus(self):
        with patch.object(p, "adapter_lock") as lock, patch.object(p.discovery, "_new_client") as bus:
            for kwargs in (dict(transport="auto"), dict(target="x; command"), dict(adapter="hci１"),
                           dict(adapter="hci0/path"), dict(local_address="bad")):
                arguments = dict(transport="le", target=TARGET)
                arguments.update(kwargs)
                with self.assertRaises(ValueError):
                    p.probe_device(**arguments)
            with patch.object(p.sys, "platform", "win32"), self.assertRaises(RuntimeError):
                p.probe_device("le", TARGET)
        lock.assert_not_called()
        bus.assert_not_called()

    def test_lock_failure_is_structured_and_sync_uses_lock(self):
        with patch.object(p.sys, "platform", "linux"), \
                patch.object(p, "adapter_lock", side_effect=RuntimeError("busy")):
            result = p.probe_device("bredr", TARGET)
        self.assertEqual((result.status, result.cleanup_status), ("failed", "not_started"))
        with patch.object(p.sys, "platform", "linux"), \
                patch.object(p, "adapter_lock", return_value=nullcontext()) as lock, \
                patch.object(p, "_probe_async", new=AsyncMock(return_value=result)), \
                patch.object(p.asyncio, "run", side_effect=run):
            self.assertIs(p.probe_device("le", TARGET), result)
        lock.assert_called_once_with("hci0")

    def test_outer_runner_keyboard_interrupt_retains_cleanup_evidence(self):
        for finished in (True, False):
            expected = p.ProbeResult(TARGET, "hci0", "le", cleanup_status="released")

            async def probe(*args):
                args[-1].update(result=expected, cleanup_finished=finished)
                return expected

            def interrupted_runner(coroutine):
                run(coroutine)
                raise KeyboardInterrupt()

            with patch.object(p.sys, "platform", "linux"), \
                    patch.object(p, "adapter_lock", return_value=nullcontext()), \
                    patch.object(p, "_probe_async", new=probe), \
                    patch.object(p.asyncio, "run", side_effect=interrupted_runner):
                result = p.probe_device("le", TARGET)
            self.assertIs(result, expected)
            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.cleanup_status, "released" if finished else "unconfirmed")


@unittest.skipUnless(sys.platform == "linux", "Real Linux subprocess pipe lifecycle")
class RealProbeProcessTests(unittest.TestCase):
    def test_real_sigint_public_entry_drains_attempted_client(self):
        source = r'''
import asyncio, json, os, signal
from contextlib import nullcontext
from types import SimpleNamespace
from btdos import probe as p
target = '02:00:00:00:00:01'
calls = []
class Client:
    is_connected = False
    async def connect(self):
        calls.append('connect')
        self.is_connected = True
        asyncio.get_running_loop().call_later(.05, lambda: os.kill(os.getpid(), signal.SIGINT))
        await asyncio.Event().wait()
    async def disconnect(self):
        calls.append('disconnect')
        self.is_connected = False
client = Client()
async def snapshot(*args, **kwargs):
    return '02:00:00:00:00:02'
async def find(*args):
    return SimpleNamespace(address=target, details={'path':'/org/bluez/hci0/dev_02_00_00_00_00_01'})
p._snapshot = snapshot
p._find_ble = find
p._new_ble_client = lambda *args: client
p.adapter_lock = lambda *args: nullcontext()
result = p.probe_device('le', target)
print(json.dumps({'status':result.status, 'cleanup_status':result.cleanup_status,
                  'calls':calls, 'connected':client.is_connected}))
'''
        result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(status="interrupted", cleanup_status="released",
                                                        calls=["connect", "disconnect"], connected=False))
        self.assertNotIn("never awaited", result.stderr)
        self.assertNotIn("Task was destroyed", result.stderr)
        self.assertNotIn("exception was never retrieved", result.stderr)

    def test_oversize_stdout_is_drained_and_process_reaped(self):
        processes = []
        create = asyncio.create_subprocess_exec
        code = ("import sys,time,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "sys.stdout.buffer.write(b'X'*(4*1024*1024));sys.stdout.flush();time.sleep(30)")

        async def launch(*argv, **kwargs):
            child = await create(sys.executable, "-c", code, **kwargs)
            processes.append(child)
            return child

        async def scenario():
            result = p.ProbeResult(TARGET, "hci0", "bredr")
            try:
                with patch.object(p.asyncio, "create_subprocess_exec", side_effect=launch):
                    with self.assertRaisesRegex(ValueError, "上限"):
                        await asyncio.wait_for(p._probe_sdp(result, LOCAL), 8)
                self.assertEqual(result.cleanup_status, "released")
                self.assertIsNotNone(processes[0].returncode)
            finally:
                # This teardown also cleans up a failing implementation, so the
                # regression cannot leave a fixture process behind on CI.
                for child in processes:
                    if child.returncode is None:
                        child.kill()
                    await asyncio.wait_for(child.communicate(), 3)

        started = time.monotonic()
        asyncio.run(scenario())
        self.assertLess(time.monotonic()-started, 8)


class Uuid32Tests(unittest.TestCase):
    def test_sdp_uuid32_is_preserved_not_truncated_and_protocols_are_not_services(self):
        fixture = XML.replace(b'value="0x1101"', b'value="0x00001101"')
        fixture = fixture.replace(b'value="0x0100"', b'value="0x00000100"')
        fixture = fixture.replace(b'value="0x0003"/>', b'value="0x00000003"/>', 1)
        services = p.parse_sdp_xml(fixture)
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0].uuid16, 0x1101)
        self.assertTrue(services[0].selectable)
        custom = p.parse_sdp_xml(fixture.replace(b'value="0x00001101"', b'value="0x12341101"'))[0]
        self.assertEqual(custom.uuid, "12341101-0000-1000-8000-00805f9b34fb")
        self.assertIsNone(custom.uuid16)
        self.assertFalse(custom.selectable)

    def test_reserved_targets_are_rejected_before_bus_access(self):
        with patch.object(p.discovery, "_new_client") as bus:
            for target in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
                with self.assertRaises(ValueError):
                    p.probe_device("le", target)
        bus.assert_not_called()


if __name__ == "__main__":
    unittest.main()
