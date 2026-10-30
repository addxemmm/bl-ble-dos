"""Synchronous, hardware-free BR socket setup and diagnostic regressions."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from btdos import cases


class BRConnectionTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(target="TARGET", local="LOCAL")
        self.sock = Mock()
        self.factory = Mock(return_value=self.sock)
        self.socket_api = SimpleNamespace(socket=self.factory, AF_BLUETOOTH=31,
                                          SOCK_SEQPACKET=5, BTPROTO_L2CAP=0)
        self.now = 10.0
        self.events = []
        self.socket_patch = patch.object(cases, "socket", self.socket_api)
        self.time_patch = patch.object(cases.time, "monotonic", side_effect=lambda: self.now)
        self.log_patch = patch.object(cases, "log", side_effect=self.record)
        for item in (self.socket_patch, self.time_patch, self.log_patch):
            item.start()
            self.addCleanup(item.stop)

    def record(self, event, **data):
        self.events.append(dict(event=event, **data))

    def failure(self):
        return next(item for item in self.events if item["event"] == "br_connect_failed")

    def assert_no_workload_or_retry(self):
        self.factory.assert_called_once_with(31, 5, 0)
        self.sock.send.assert_not_called()
        self.sock.recv.assert_not_called()
        self.assertEqual(sum(item["event"] == "br_connect_attempt" for item in self.events), 1)

    def test_success_connects_once_with_original_timeout_and_addresses(self):
        def bind(_address):
            self.now += 0.25

        def connect(_address):
            self.now += 0.5

        self.sock.bind.side_effect = bind
        self.sock.connect.side_effect = connect
        self.assertIs(cases.br_socket(self.args), self.sock)
        self.sock.settimeout.assert_called_once_with(3.0)
        self.sock.bind.assert_called_once_with(("LOCAL", 0))
        self.sock.connect.assert_called_once_with(("TARGET", 1))
        self.sock.close.assert_not_called()
        self.assertEqual([item["event"] for item in self.events],
                         ["br_connect_attempt", "br_connect_succeeded"])
        for event in self.events:
            self.assertEqual({name: event[name] for name in ("target", "local", "psm", "timeout_s")},
                             dict(target="TARGET", local="LOCAL", psm=1, timeout_s=3.0))
        self.assertEqual(self.events[-1]["elapsed_s"], 0.75)
        self.assertEqual(self.events[-1]["connect_elapsed_s"], 0.5)
        self.assert_no_workload_or_retry()

    def test_connect_timeout_closes_without_sending_or_retrying(self):
        error = TimeoutError("fixture connect timeout")

        def connect(_address):
            self.now += 3.0
            raise error

        self.sock.connect.side_effect = connect
        with self.assertRaises(TimeoutError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.settimeout.assert_called_once_with(3.0)
        self.sock.connect.assert_called_once_with(("TARGET", 1))
        self.sock.close.assert_called_once_with()
        event = self.failure()
        self.assertEqual(event["failure_stage"], "connect")
        self.assertEqual(event["error"], repr(error))
        self.assertEqual(event["error_type"], "TimeoutError")
        self.assertEqual(event["elapsed_s"], 3.0)
        self.assertEqual(event["connect_elapsed_s"], 3.0)
        self.assert_no_workload_or_retry()

    def test_bind_failure_closes_without_connect_or_send(self):
        error = OSError(99, "fixture bind failure")
        self.sock.bind.side_effect = error
        with self.assertRaises(OSError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.connect.assert_not_called()
        self.sock.close.assert_called_once_with()
        self.assertEqual(self.failure()["failure_stage"], "bind")
        self.assertEqual(self.failure()["errno"], 99)
        self.assertIsNone(self.failure()["connect_elapsed_s"])
        self.assert_no_workload_or_retry()

    def test_socket_create_failure_is_logged_without_retry(self):
        error = OSError("fixture socket failure")
        self.factory.side_effect = error
        with self.assertRaises(OSError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.close.assert_not_called()
        self.sock.connect.assert_not_called()
        self.assertEqual(self.failure()["failure_stage"], "socket_create")
        self.assertIsNone(self.failure()["connect_elapsed_s"])
        self.assert_no_workload_or_retry()

    def test_timeout_setup_failure_closes_created_socket(self):
        error = OSError("fixture timeout setup failure")
        self.sock.settimeout.side_effect = error
        with self.assertRaises(OSError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.bind.assert_not_called()
        self.sock.connect.assert_not_called()
        self.sock.close.assert_called_once_with()
        self.assertEqual(self.failure()["failure_stage"], "settimeout")
        self.assert_no_workload_or_retry()

    def test_failure_log_error_does_not_skip_close_or_mask_original_exception(self):
        error = TimeoutError("original connection failure")
        self.sock.connect.side_effect = error

        def log(event, **data):
            self.record(event, **data)
            if event == "br_connect_failed":
                raise BrokenPipeError("fixture log failure")

        cases.log.side_effect = log
        with self.assertRaises(TimeoutError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.close.assert_called_once_with()
        self.assert_no_workload_or_retry()

    def test_success_log_error_still_closes_socket(self):
        error = BrokenPipeError("fixture success log failure")

        def log(event, **data):
            self.record(event, **data)
            if event == "br_connect_succeeded":
                raise error

        cases.log.side_effect = log
        with self.assertRaises(BrokenPipeError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.close.assert_called_once_with()
        self.assertEqual(self.failure()["failure_stage"], "connected_log")
        self.assert_no_workload_or_retry()

    def test_secondary_close_error_preserves_connect_exception(self):
        error = TimeoutError("original connection failure")
        self.sock.connect.side_effect = error
        self.sock.close.side_effect = OSError("fixture close failure")
        with self.assertRaises(TimeoutError) as raised:
            cases.br_socket(self.args)
        self.assertIs(raised.exception, error)
        self.sock.close.assert_called_once_with()
        self.assert_no_workload_or_retry()


if __name__ == "__main__":
    unittest.main()
