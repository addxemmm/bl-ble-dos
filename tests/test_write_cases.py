"""Command-producer and same-link probe tests; no hardware or radio access."""
import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from btdos import cases
from btdos.config import TEST_SERVICE_UUID, TEST_WRITE_UUID


def args(**changes):
    values = dict(service_uuid=TEST_SERVICE_UUID, char=TEST_WRITE_UUID,
                  write_test_confirmed=True, rate=100, seconds=1, max_ops=3,
                  baseline=0, recovery=0)
    values.update(changes)
    return SimpleNamespace(**values)


class WriteCaseTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, options=None, properties=None, service_uuid=TEST_SERVICE_UUID,
                      writer=None, baseline=None):
        options = options or args()
        char = SimpleNamespace(uuid=TEST_WRITE_UUID, handle=20, service_uuid=service_uuid,
                               max_write_without_response_size=20,
                               properties=properties if properties is not None else ["read", "write-without-response"])
        probe = SimpleNamespace(uuid=cases.PROBE_UUID, properties=["read"], handle=2)
        values, events, phases = [], [], []

        async def write(characteristic, value, **kwargs):
            self.assertIs(characteristic, char)
            self.assertEqual(kwargs, {"response": False})
            values.append(bytes(value))
            if writer is not None:
                await writer()
            await asyncio.sleep(0)

        async def read(characteristic, **kwargs):
            return values[-1] if characteristic is char and values else b"nRF5x"

        service = SimpleNamespace(get_characteristic=lambda uuid: char if uuid == TEST_WRITE_UUID else None)
        client = SimpleNamespace(services=SimpleNamespace(get_service=lambda uuid: service,
                                   get_characteristic=lambda uuid: probe),
                                 write_gatt_char=write, read_gatt_char=read)

        async def connection(opts, body):
            await body(client)

        async def observe(client, char, opts, phase, seconds, stop_load=None, sink=None):
            phases.append(phase)
            rows = (baseline if baseline is not None else [{"status": "ok", "latency_ms": 20}]) if phase == "baseline" else []
            if sink is not None:
                sink.extend(rows)
                return sink
            return rows

        async def no_pause(*arguments):
            await asyncio.sleep(0)

        with patch.object(cases, "ble_connection", side_effect=connection), \
                patch.object(cases, "observe_service", side_effect=observe), \
                patch.object(cases, "async_pause", side_effect=no_pause), \
                patch.object(cases, "stopped"), \
                patch.object(cases, "log", side_effect=lambda event, **data: events.append(dict(event=event, **data))):
            await cases.ble_write_command(options)
        return values, events, phases

    async def test_fixed_payload_sequence_response_false_and_no_extra_writes(self):
        values, events, phases = await self.execute()
        self.assertEqual(values, [b"BTDS" + struct.pack("<I", n) for n in (1, 2, 3)])
        self.assertEqual(set(phases), {"baseline", "load", "recovery"})
        summary = next(event for event in events if event["event"] == "write_command_summary")
        self.assertEqual((summary["attempted"], summary["api_accepted"], summary["accepted_payload_bytes"]), (3, 3, 24))
        self.assertEqual(summary["target_received"], "not_verified")
        readback = next(event for event in events if event["event"] == "write_fixture_readback")
        self.assertTrue(readback["matches_last_payload"])

    async def test_wrong_fixture_or_missing_confirmation_never_connects(self):
        with patch.object(cases, "ble_connection") as connect:
            for options in (args(write_test_confirmed=False), args(char=cases.PROBE_UUID),
                            args(service_uuid="00001800-0000-1000-8000-00805f9b34fb")):
                with self.assertRaises(ValueError):
                    await cases.ble_write_command(options)
            connect.assert_not_called()

    async def test_wrong_properties_or_service_fail_before_load(self):
        for properties in (["read"], ["write"], ["write-without-response"]):
            with self.subTest(properties=properties), self.assertRaises(ValueError):
                await self.execute(properties=properties)
        with self.assertRaises(ValueError):
            await self.execute(service_uuid="00001800-0000-1000-8000-00805f9b34fb")

    async def test_bad_baseline_does_not_enter_load(self):
        for baseline in ([], [{"status": "slow"}]):
            with self.subTest(baseline=baseline), self.assertRaisesRegex(RuntimeError, "baseline"):
                await self.execute(baseline=baseline)

    async def test_real_command_timeout_is_not_silently_successful(self):
        async def blocked():
            await asyncio.Event().wait()
        with patch.object(cases, "TRANSACTION_TIMEOUT", 0.01):
            with self.assertRaises(TimeoutError):
                await self.execute(writer=blocked)

    async def test_probe_timeout_sets_stop_and_does_not_claim_device_dos(self):
        stop = asyncio.Event()
        client = SimpleNamespace(read_gatt_char=AsyncMock(side_effect=TimeoutError("fixture")))
        events = []
        with patch.object(cases, "stopped"), patch.object(cases, "log", side_effect=lambda event, **data: events.append(dict(event=event, **data))):
            rows = await cases.observe_service(client, object(), args(), "load", 1, stop)
        self.assertTrue(stop.is_set())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(rows[0]["scope"], "same_link_communication_only")

    async def test_partial_load_probe_observations_survive_writer_error(self):
        observations = []
        probe_ran = asyncio.Event()
        async def observe(*arguments, sink=None, **kwargs):
            sink.append({"phase": "load", "status": "ok", "latency_ms": 20})
            probe_ran.set()
            return sink
        async def failed_write(*arguments, **kwargs):
            await probe_ran.wait()
            raise RuntimeError("fixture write failed")
        client = SimpleNamespace(write_gatt_char=failed_write)
        with patch.object(cases, "observe_service", side_effect=observe), \
                patch.object(cases, "stopped"), patch.object(cases, "log"):
            with self.assertRaisesRegex(RuntimeError, "fixture write failed"):
                await cases.write_command_load(client, object(), object(), args(), observations)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["latency_ms"], 20)

    async def test_two_consecutive_slow_probes_stop_current_load(self):
        class Clock:
            now = 0
            def monotonic(self):
                return self.now
        clock = Clock()
        calls = []
        async def read(*a, **k):
            clock.now += 0.3
            calls.append(True)
            return b"value"
        async def pause(seconds, opts):
            clock.now += seconds
        stop = asyncio.Event()
        with patch.object(cases, "time", SimpleNamespace(monotonic=clock.monotonic)), \
                patch.object(cases, "async_pause", side_effect=pause), \
                patch.object(cases, "stopped"), patch.object(cases, "log"):
            rows = await cases.observe_service(SimpleNamespace(read_gatt_char=read), object(), args(), "load", 5, stop)
        self.assertEqual(len(calls), 2)
        self.assertTrue(stop.is_set())
        self.assertEqual([row["status"] for row in rows], ["slow", "slow"])

    async def test_link_parameter_request_only_when_explicit_and_before_body(self):
        client = SimpleNamespace(is_connected=True, connect=AsyncMock(), disconnect=AsyncMock())
        body = AsyncMock()
        device = SimpleNamespace(address="TARGET")
        import sys
        module = SimpleNamespace(BleakClient=Mock(return_value=client))
        with patch.dict(sys.modules, bleak=module), \
                patch.object(cases, "find_ble", new=AsyncMock(return_value=device)), \
                patch.object(cases, "stopped"), patch.object(cases, "log"), \
                patch.object(cases, "request_link_parameters", new=AsyncMock(return_value={"status": "requested_unverified"})) as request:
            await cases.ble_connection(SimpleNamespace(target="TARGET", adapter="hci0", ble_interval_ms=15), body)
        request.assert_awaited_once()
        body.assert_awaited_once_with(client)
        client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
