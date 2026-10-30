"""Hardware-free submission-window and bounded transaction regressions."""
import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from btdos import cases


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def arguments(**changes):
    values = dict(char="00002a00-0000-1000-8000-00805f9b34fb", seconds=1.0,
                  rate=2.0, max_ops=10, uuid16=0x110B)
    values.update(changes)
    return SimpleNamespace(**values)


class FakeSocket:
    def __init__(self, clock, delay=0.6, send_delay=0.0):
        self.clock, self.delay, self.send_delay = clock, delay, send_delay
        self.timeout = 3.0
        self.sends, self.timeouts = [], []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def getpeername(self):
        return ("TARGET", 1)

    def getsockname(self):
        return ("LOCAL", 1)

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def send(self, data):
        self.sends.append((self.clock.now, data))
        self.clock.advance(self.send_delay)
        return len(data)

    def recv(self, count):
        if self.delay > self.timeout:
            self.clock.advance(self.timeout)
            raise TimeoutError("fixture response timeout")
        self.clock.advance(self.delay)
        tid = struct.unpack(">H", self.sends[-1][1][1:3])[0]
        return struct.pack(">BHH", 3, tid, 5) + b"\x00\x00\x00\x00\x00"


class SubmissionWindowTests(unittest.TestCase):
    def ble(self, args=None, delay=0.6, stop=None, log_delays=None):
        args = args or arguments()
        clock, events, calls = Clock(), [], []
        char = SimpleNamespace(uuid=args.char, handle=2, properties=["read"])

        async def read(characteristic, **kwargs):
            calls.append(clock.now)
            clock.advance(delay)
            return b"value"

        client = SimpleNamespace(services=SimpleNamespace(get_characteristic=lambda _: char),
                                 read_gatt_char=read)

        async def connection(opts, body):
            await body(client)

        async def wait(coroutine, timeout):
            before = clock.now
            result = await coroutine
            if clock.now - before > timeout:
                raise TimeoutError("fixture response timeout")
            return result

        async def pause(seconds, opts):
            clock.advance(seconds)

        def stopped(opts):
            if stop is not None:
                stop(clock)

        def log(event, **data):
            events.append(dict(event=event, **data))
            clock.advance((log_delays or {}).get(event, 0))

        with patch.object(cases, "time", SimpleNamespace(monotonic=clock.monotonic)), \
                patch.object(cases, "ble_connection", side_effect=connection), \
                patch.object(cases.asyncio, "wait_for", side_effect=wait), \
                patch.object(cases, "async_pause", side_effect=pause), \
                patch.object(cases, "stopped", side_effect=stopped), \
                patch.object(cases, "log", side_effect=log):
            asyncio.run(cases.ble_read(args))
        return clock, events, calls

    def sdp(self, args=None, delay=0.6, send_delay=0.0, stop=None, log_delays=None):
        args = args or arguments()
        clock, events = Clock(), []
        socket = FakeSocket(clock, delay, send_delay)

        def log(event, **data):
            events.append(dict(event=event, **data))
            clock.advance((log_delays or {}).get(event, 0))

        with patch.object(cases, "time", SimpleNamespace(monotonic=clock.monotonic)), \
                patch.object(cases, "br_socket", return_value=socket), \
                patch.object(cases, "pause", side_effect=lambda seconds, opts: clock.advance(seconds)), \
                patch.object(cases, "stopped", side_effect=lambda opts: stop(clock) if stop else None), \
                patch.object(cases, "log", side_effect=log):
            cases.br_sdp(args)
        return clock, events, socket

    def test_ble_last_response_drains_past_submission_deadline(self):
        clock, events, calls = self.ble()
        self.assertEqual(calls, [0.0, 0.6])
        self.assertAlmostEqual(clock.now, 1.2)
        summary = events[-1]
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["completed_in_window"], 1)
        self.assertEqual(summary["drained"], 1)
        self.assertAlmostEqual(summary["drain_elapsed_s"], 0.2)
        self.assertEqual(events[1]["event"], "load_start")

    def test_sdp_last_response_drains_past_submission_deadline(self):
        clock, events, socket = self.sdp()
        self.assertEqual([stamp for stamp, _ in socket.sends], [0.0, 0.6])
        self.assertAlmostEqual(clock.now, 1.2)
        self.assertEqual(events[-1]["completed_in_window"], 1)
        self.assertEqual(events[-1]["drained"], 1)

    def test_ble_and_sdp_max_ops_prevent_extra_calls(self):
        args = arguments(seconds=10, rate=10, max_ops=2)
        _, events, calls = self.ble(args, delay=0.05)
        self.assertEqual(len(calls), 2)
        self.assertEqual(events[-1]["attempted"], 2)
        _, events, socket = self.sdp(args, delay=0.05)
        self.assertEqual(len(socket.sends), 2)
        self.assertEqual(events[-1]["attempted"], 2)

    def test_expired_window_starts_no_operation(self):
        _, _, calls = self.ble(arguments(seconds=0))
        _, _, socket = self.sdp(arguments(seconds=0))
        self.assertEqual(calls, [])
        self.assertEqual(socket.sends, [])

    def test_slow_attempt_log_marks_skipped_operation_without_submitting(self):
        for operation, event in ((self.ble, "att_read_attempt"), (self.sdp, "sdp_attempt")):
            with self.subTest(operation=operation.__name__):
                _, events, backend = operation(log_delays={event: 1.1})
                self.assertEqual(backend if isinstance(backend, list) else backend.sends, [])
                skipped = [item for item in events if item["event"] == event + "_skipped"]
                self.assertEqual(len(skipped), 1)
                self.assertFalse(skipped[0]["submitted"])
                self.assertEqual(events[-1]["attempted"], 0)

    def test_sdp_response_log_delay_does_not_turn_in_window_reply_into_drain(self):
        _, events, socket = self.sdp(log_delays={"sdp_received": 1.0})
        self.assertEqual(len(socket.sends), 1)
        self.assertEqual(events[-1]["completed_in_window"], 1)
        self.assertEqual(events[-1]["drained"], 0)
        self.assertEqual(events[-1]["drain_elapsed_s"], 0)
        response = next(item for item in events if item["event"] == "sdp_response")
        self.assertAlmostEqual(response["latency_ms"], 600)

    def test_real_response_timeout_remains_an_error(self):
        for operation in (self.ble, self.sdp):
            with self.subTest(operation=operation.__name__), self.assertRaises(TimeoutError):
                operation(arguments(seconds=1), delay=3.1)

    def test_sdp_send_and_receive_share_transaction_budget(self):
        with self.assertRaises(TimeoutError):
            self.sdp(arguments(seconds=10), delay=2.0, send_delay=1.1)

    def test_stop_observed_after_draining_response(self):
        def stop(clock):
            if clock.now >= 1.0:
                raise RuntimeError("STOP fixture")
        for operation in (self.ble, self.sdp):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RuntimeError, "STOP fixture"):
                operation(stop=stop)

    def test_sdp_stop_after_send_does_not_wait_for_receive(self):
        def stop(clock):
            if clock.now >= 0.2:
                raise RuntimeError("STOP after send")
        with self.assertRaisesRegex(RuntimeError, "STOP after send"):
            self.sdp(send_delay=0.2, stop=stop)

    def test_ble_actual_wait_for_timeout_still_cancels_read(self):
        args = arguments(max_ops=1)
        cancelled = []
        char = SimpleNamespace(uuid=args.char, handle=2, properties=["read"])

        async def read(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        async def connection(opts, body):
            await body(SimpleNamespace(services=SimpleNamespace(get_characteristic=lambda _: char),
                                       read_gatt_char=read))

        with patch.object(cases, "ble_connection", side_effect=connection), \
                patch.object(cases, "TRANSACTION_TIMEOUT", 0.01, create=True), \
                patch.object(cases, "stopped"), patch.object(cases, "log"):
            with self.assertRaises(TimeoutError):
                asyncio.run(cases.ble_read(args))
        self.assertEqual(cancelled, [True])


if __name__ == "__main__":
    unittest.main()
