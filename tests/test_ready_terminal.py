"""Readiness input is entirely mocked: no threads, radio, or real stdin."""
from contextlib import ExitStack
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from btdos import terminal


class ReadyInputTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stream = Mock()
        self.stream.isatty.return_value = True
        self.stream.fileno.return_value = 42
        self.termios = SimpleNamespace(TCIFLUSH=0, tcflush=Mock())
        self.stack.enter_context(patch.dict("sys.modules", {"termios": self.termios}))
        self.blocking = self.stack.enter_context(patch.object(terminal.os, "set_blocking"))
        self.stack.enter_context(patch.object(terminal.os, "get_blocking", return_value=True))
        self.select = self.stack.enter_context(patch.object(terminal.select, "select", return_value=([42], [], [])))
        self.read = self.stack.enter_context(patch.object(terminal.os, "read", return_value=b"\n"))

    def test_no_input_until_started_and_flush_only_before_visible_prompt(self):
        with terminal.ReadyInput(self.stream) as ready:
            self.assertIsNone(ready.poll())
            self.select.assert_not_called()
            ready.start()
            self.termios.tcflush.assert_called_once_with(42, 0)
            ready.start()
            self.assertEqual(ready.poll(), "ready")
            self.assertEqual(ready.poll(), "ready")
            self.assertEqual(self.termios.tcflush.call_count, 1)
            self.read.assert_called_once_with(42, 4096)
            self.select.assert_called_once_with([42], [], [], 0)
        self.assertEqual(self.termios.tcflush.call_count, 2)
        self.assertEqual(self.blocking.call_args_list[0].args, (42, False))
        self.assertEqual(self.blocking.call_args_list[-1].args, (42, True))
        self.assertIsNone(ready.fd)
        self.assertEqual(ready.buffer, b"")

    def test_confirm_cancel_eof_and_partial_input(self):
        for chunks, result in (([b"r\n"], "ready"), ([b"Q\n"], "cancel"),
                               ([b""], "cancel"), ([b"r", b"\n"], "ready"),
                               ([b"wrong\nr\n"], "ready")):
            with self.subTest(chunks=chunks), terminal.ReadyInput(self.stream) as ready:
                ready.start()
                self.read.side_effect = chunks
                for index in range(len(chunks)):
                    actual = ready.poll()
                    self.assertEqual(actual, result if index == len(chunks)-1 else None)

    def test_pending_and_read_race_are_nonblocking(self):
        with terminal.ReadyInput(self.stream) as ready:
            ready.start()
            self.select.return_value = ([], [], [])
            self.assertIsNone(ready.poll())
            self.read.assert_not_called()
            self.select.return_value = ([42], [], [])
            self.read.side_effect = BlockingIOError()
            self.assertIsNone(ready.poll())

    def test_interrupt_propagates_and_restores_fd_without_touching_tty_mode(self):
        with self.assertRaises(KeyboardInterrupt):
            with terminal.ReadyInput(self.stream) as ready:
                ready.start()
                self.read.side_effect = KeyboardInterrupt()
                ready.poll()
        self.blocking.assert_called_with(42, True)
        self.assertEqual(self.termios.tcflush.call_count, 2)
        self.assertEqual(terminal.Prompt._sessions, {})

    def test_cleanup_flush_failure_still_restores_original_nonblocking_flag(self):
        with patch.object(terminal.os, "get_blocking", return_value=False):
            with self.assertRaises(OSError):
                with terminal.ReadyInput(self.stream) as ready:
                    ready.start()
                    self.termios.tcflush.side_effect = OSError("closed TTY")
        self.blocking.assert_called_with(42, False)

    def test_non_tty_rejected_without_reading_or_changing_flags(self):
        self.stream.isatty.return_value = False
        with self.assertRaises(ValueError), terminal.ReadyInput(self.stream):
            self.fail("non-TTY context entered")
        self.blocking.assert_not_called()
        self.read.assert_not_called()

    def test_readiness_tty_is_independent_of_prompt_backend(self):
        with patch.object(terminal.sys, "platform", "linux"), \
                patch.object(terminal.sys, "stdin", self.stream), \
                patch.object(terminal.sys, "stdout", self.stream), \
                patch.dict(terminal.os.environ, {"BTDOS_SIMPLE_INPUT": "1", "TERM": "dumb"}):
            self.assertTrue(terminal.ready_tty(SimpleNamespace(is_terminal=True)))
            self.assertFalse(terminal.ready_tty(SimpleNamespace(is_terminal=False)))
            self.stream.isatty.return_value = False
            self.assertFalse(terminal.ready_tty(SimpleNamespace(is_terminal=True)))


@unittest.skipUnless(sys.platform == "linux", "PTY readiness integration requires Linux")
class ReadyPtyTests(unittest.TestCase):
    def test_stale_enter_is_discarded_but_fresh_confirmation_survives(self):
        import termios
        master, slave = os.openpty()
        try:
            with os.fdopen(os.dup(slave), "r") as stream:
                original = termios.tcgetattr(slave)
                original_blocking = os.get_blocking(slave)
                os.write(master, b"\n")
                with terminal.ReadyInput(stream) as ready:
                    ready.start()
                    self.assertIsNone(ready.poll())
                    os.write(master, b"r\n")
                    self.assertEqual(ready.poll(), "ready")
                    self.assertEqual(termios.tcgetattr(slave), original)
                self.assertEqual(termios.tcgetattr(slave), original)
                self.assertEqual(os.get_blocking(slave), original_blocking)
        finally:
            os.close(master)
            os.close(slave)
