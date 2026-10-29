"""Terminal-only input tests; never invoke discovery, subprocesses, or hardware.

Portable tests mock application creation. Linux additionally sends real key
sequences through prompt_toolkit's pipe input and uses a DummyOutput renderer.
"""

from contextlib import contextmanager
from io import StringIO
import os
import signal
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
import prompt_toolkit
from prompt_toolkit.application import create_app_session
from prompt_toolkit.history import DummyHistory, InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
import prompt_toolkit.shortcuts.choice_input as choice_module

from btdos import terminal


OPTIONS = [("1", "First"), ("2", "Second"), ("7", "Seventh displayed key")]
MAC = "02:00:00:00:00:01"


class TerminalAdapterTests(unittest.TestCase):
    def setUp(self):
        terminal.Prompt.clear_history()
        self.addCleanup(terminal.Prompt.clear_history)
        self.console = Console(file=StringIO(), force_terminal=False)

    @contextmanager
    def tty(self):
        with patch.object(terminal, "interactive", return_value=True):
            yield

    def test_interactive_requires_all_tty_conditions_and_respects_opt_out(self):
        console = SimpleNamespace(is_terminal=True)
        stdin, stdout = Mock(), Mock()
        stdin.isatty.return_value = stdout.isatty.return_value = True
        with patch.object(terminal.sys, "stdin", stdin), patch.object(terminal.sys, "stdout", stdout), \
                patch.dict(os.environ, {"TERM": "xterm", "BTDOS_SIMPLE_INPUT": "0"}):
            self.assertTrue(terminal.interactive(console))
            for stream in (stdin, stdout):
                stream.isatty.return_value = False
                self.assertFalse(terminal.interactive(console))
                stream.isatty.return_value = True
            console.is_terminal = False
            self.assertFalse(terminal.interactive(console))
            console.is_terminal = True
            with patch.dict(os.environ, {"TERM": "dumb"}):
                self.assertFalse(terminal.interactive(console))
            with patch.dict(os.environ, {"BTDOS_SIMPLE_INPUT": "1"}):
                self.assertFalse(terminal.interactive(console))

    def test_non_tty_delegates_to_rich_without_creating_toolkit_session(self):
        with patch.object(terminal, "interactive", return_value=False), \
                patch.object(terminal.SimplePrompt, "ask", return_value="q") as simple, \
                patch.object(prompt_toolkit, "PromptSession", side_effect=AssertionError("TTY application created")):
            result = terminal.Prompt.ask("Menu", default="1", options=OPTIONS, console=self.console)
        self.assertEqual(result, "q")
        self.assertIn("First", self.console.file.getvalue())
        self.assertIn("简易模式不支持方向键", self.console.file.getvalue())
        simple.assert_called_once_with("Menu", default="1", console=self.console)
        self.assertEqual(terminal.Prompt._sessions, {})

    def test_non_tty_actual_rich_read_uses_default(self):
        with patch.object(terminal, "interactive", return_value=False), \
                patch.object(self.console, "input", return_value=""):
            self.assertEqual(terminal.Prompt.ask("Count", default="3", console=self.console), "3")
        self.assertEqual(terminal.Prompt._sessions, {})

    def test_fields_use_scoped_memory_histories_and_clear(self):
        created = []

        def new_session(**kwargs):
            session = Mock(history=kwargs["history"])
            session.prompt.return_value = MAC
            created.append(session)
            return session

        with self.tty(), patch.object(prompt_toolkit, "PromptSession", side_effect=new_session), \
                patch("prompt_toolkit.history.FileHistory", side_effect=AssertionError("disk history created")):
            terminal.Prompt.ask("MAC", console=self.console)
            terminal.Prompt.ask("UUID", console=self.console)
            terminal.Prompt.ask("MAC", console=self.console)
            self.assertEqual(len(created), 2)
            self.assertIs(type(created[0].history), InMemoryHistory)
            self.assertIs(type(created[1].history), InMemoryHistory)
            self.assertIsNot(created[0].history, created[1].history)
            self.assertEqual(created[0].prompt.call_count, 2)
            terminal.Prompt.clear_history()
            self.assertEqual(terminal.Prompt._sessions, {})
            terminal.Prompt.ask("MAC", console=self.console)
            self.assertEqual(len(created), 3)
        kwargs = created[0].prompt.call_args.kwargs
        for name in ("enable_history_search", "complete_while_typing", "enable_suspend", "enable_system_prompt", "enable_open_in_editor"):
            self.assertIs(kwargs[name], False)

    def test_sensitive_confirmation_uses_dummy_history_and_is_never_cached(self):
        with self.tty(), patch.object(prompt_toolkit, "PromptSession") as factory, \
                patch("prompt_toolkit.history.FileHistory", side_effect=AssertionError("disk history created")):
            factory.return_value.prompt.return_value = MAC
            for _ in range(2):
                self.assertEqual(terminal.Prompt.ask("Full MAC confirmation", console=self.console, history=False), MAC)
        self.assertEqual(factory.call_count, 2)
        for call in factory.call_args_list:
            self.assertIs(type(call.kwargs["history"]), DummyHistory)
        self.assertEqual(terminal.Prompt._sessions, {})

    def test_memory_session_cache_is_bounded(self):
        with self.tty(), patch.object(prompt_toolkit, "PromptSession") as factory:
            factory.return_value.prompt.return_value = "value"
            for index in range(65):
                terminal.Prompt.ask(f"Field {index}", console=self.console)
        self.assertEqual(len(terminal.Prompt._sessions), 64)
        self.assertNotIn("Field 0", terminal.Prompt._sessions)
        self.assertIn("Field 64", terminal.Prompt._sessions)

    def test_field_default_enter_and_typed_value(self):
        with self.tty(), patch.object(prompt_toolkit, "PromptSession") as factory:
            factory.return_value.prompt.side_effect = ["", "   ", "typed value"]
            self.assertEqual(terminal.Prompt.ask("Rate", default=3, console=self.console), "3")
            self.assertEqual(terminal.Prompt.ask("Rate", default=3, console=self.console), "3")
            self.assertEqual(terminal.Prompt.ask("Rate", default=3, console=self.console), "typed value")

    def test_field_eof_and_interrupt_propagate_for_wizard_to_handle(self):
        with self.tty(), patch.object(prompt_toolkit, "PromptSession") as factory:
            for exception in (EOFError, KeyboardInterrupt):
                factory.return_value.prompt.side_effect = exception()
                with self.assertRaises(exception):
                    terminal.Prompt.ask("Field", console=self.console)

    def test_menu_keeps_displayed_shortcuts_and_navigation_bindings(self):
        with self.tty(), patch.object(choice_module, "ChoiceInput", autospec=True) as factory:
            factory.return_value.prompt.return_value = "7"
            self.assertEqual(terminal.Prompt.ask("Page 2", default="7", options=OPTIONS, console=self.console), "7")
        kwargs = factory.call_args.kwargs
        self.assertEqual([key for key, _ in kwargs["options"]], ["1", "2", "7", "b", "q"])
        self.assertEqual(kwargs["default"], "7")
        self.assertFalse(kwargs["show_numbers"])
        for key in ("1", "2", "7", "b", "q"):
            event = SimpleNamespace(app=Mock())
            bindings = kwargs["key_bindings"].get_bindings_for_keys((key,))
            self.assertEqual(len(bindings), 1)
            bindings[0].handler(event)
            event.app.exit.assert_called_once_with(result=key)
        event = SimpleNamespace(app=Mock())
        bindings = kwargs["key_bindings"].get_bindings_for_keys((Keys.ControlD,))
        self.assertEqual(len(bindings), 1)
        bindings[0].handler(event)
        self.assertIsInstance(event.app.exit.call_args.kwargs["exception"], EOFError)
        self.assertEqual(terminal.Prompt._sessions, {})

    def test_existing_back_and_quit_options_are_not_duplicated(self):
        values = [("b", "Custom back"), ("q", "Custom quit")]
        with self.tty(), patch.object(choice_module, "ChoiceInput", autospec=True) as factory:
            terminal.Prompt.ask("Navigation", options=values, console=self.console)
        self.assertEqual(factory.call_args.kwargs["options"], [("b", "b  Custom back"), ("q", "q  Custom quit")])
        self.assertEqual(values, [("b", "Custom back"), ("q", "Custom quit")])


@unittest.skipUnless(sys.platform.startswith("linux"), "Real pipe-input tests run on Linux; Windows uses mock application tests")
class TerminalPipeTests(unittest.TestCase):
    def setUp(self):
        terminal.Prompt.clear_history()
        self.addCleanup(terminal.Prompt.clear_history)
        self.console = Console(file=StringIO(), force_terminal=False)

    @contextmanager
    def pipe_session(self):
        # An explicit timeout makes malformed escape sequences fail rather than
        # waiting forever for a key on Linux CI. No actual terminal is opened.
        def timeout(signum, frame):
            raise AssertionError("prompt_toolkit pipe input did not finish within 5 seconds")

        previous = signal.signal(signal.SIGALRM, timeout)
        signal.alarm(5)
        try:
            with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()), \
                    patch.object(terminal, "interactive", return_value=True):
                yield pipe
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    def ask(self, pipe, keys, label="Field", **kwargs):
        pipe.send_text(keys)
        return terminal.Prompt.ask(label, console=self.console, **kwargs)

    def test_actual_left_right_home_end_delete_and_backspace_editing(self):
        with self.pipe_session() as pipe:
            keys = "ac\x1b[Db\x1b[Cd\x1b[HZ\x1b[3~\x1b[F!\x7f?\r"
            self.assertEqual(self.ask(pipe, keys), "Zbcd?")

    def test_actual_up_down_enter_and_menu_default(self):
        with self.pipe_session() as pipe:
            self.assertEqual(self.ask(pipe, "\x1b[B\x1b[B\x1b[A\r", "Menu", options=OPTIONS, default="1"), "2")
            self.assertEqual(self.ask(pipe, "\r", "Menu", options=OPTIONS, default="7"), "7")

    def test_actual_back_quit_and_nonpositional_numeric_shortcuts(self):
        with self.pipe_session() as pipe:
            for key in ("b", "q", "7"):
                with self.subTest(key=key):
                    self.assertEqual(self.ask(pipe, key, "Menu", options=OPTIONS), key)

    def test_actual_second_page_multidigit_shortcut_matches_displayed_value(self):
        with self.pipe_session() as pipe:
            self.assertEqual(self.ask(pipe, "10", "Page 2", options=[("9", "Ninth"), ("10", "Tenth"), ("11", "Eleventh")]), "10")

    def test_actual_ctrl_d_and_ctrl_c_raise_for_fields_and_menus(self):
        with self.pipe_session() as pipe:
            for options in (None, OPTIONS):
                for key, error in (("\x04", EOFError), ("\x03", KeyboardInterrupt)):
                    with self.subTest(options=bool(options), key=repr(key)):
                        with self.assertRaises(error):
                            self.ask(pipe, key, options=options)

    def test_actual_default_is_used_only_for_blank_input(self):
        with self.pipe_session() as pipe:
            self.assertEqual(self.ask(pipe, "\r", default="hci0"), "hci0")
            self.assertEqual(self.ask(pipe, "  \r", default=3), "3")
            self.assertEqual(self.ask(pipe, "hci2\r", default="hci0"), "hci2")

    def test_actual_field_history_is_isolated_in_memory_and_clearable(self):
        with self.pipe_session() as pipe, \
                patch("prompt_toolkit.history.FileHistory", side_effect=AssertionError("disk history created")):
            self.assertEqual(self.ask(pipe, MAC + "\r", "MAC"), MAC)
            self.assertEqual(self.ask(pipe, "\x1b[A\r", "UUID"), "")
            self.assertEqual(self.ask(pipe, "\x1b[A\r", "MAC"), MAC)
            history = terminal.Prompt._sessions["MAC"].history
            self.assertIs(type(history), InMemoryHistory)
            self.assertIn(MAC, history.get_strings())
            terminal.Prompt.clear_history()
            self.assertEqual(self.ask(pipe, "\x1b[A\r", "MAC"), "")

    def test_actual_full_mac_confirmation_never_recalls_history(self):
        with self.pipe_session() as pipe:
            self.assertEqual(self.ask(pipe, MAC + "\r", "Confirm MAC", history=False), MAC)
            self.assertEqual(self.ask(pipe, "\x1b[A\r", "Confirm MAC", history=False), "")
            self.assertEqual(terminal.Prompt._sessions, {})


if __name__ == "__main__":
    unittest.main()
