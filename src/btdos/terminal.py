"""Terminal input adapter: editable fields and arrow-key menus, memory only."""
import os
import select
import sys

from rich.prompt import Prompt as SimplePrompt
from rich.text import Text


def ready_tty(console):
    """Readiness is available even with the simple TTY prompt backend."""
    return (sys.platform == "linux" and console.is_terminal
            and sys.stdin.isatty() and sys.stdout.isatty())


class ReadyInput:
    """Canonical, nonblocking stdin polling; no threads or terminal-mode changes.

    Call start immediately *before* displaying the readiness prompt. Flushing
    here (not in poll) discards old wizard input without losing a fresh answer.
    """

    def __init__(self, stream=None):
        self.stream = sys.stdin if stream is None else stream
        self.fd = None
        self.started = False
        self.buffer = b""
        self.result = None

    def __enter__(self):
        if not self.stream.isatty():
            raise ValueError("readiness input requires a TTY")
        self.fd = self.stream.fileno()
        self.was_blocking = os.get_blocking(self.fd)
        os.set_blocking(self.fd, False)
        return self

    def start(self):
        if not self.started:
            import termios
            termios.tcflush(self.fd, termios.TCIFLUSH)
            self.buffer = b""
            self.started = True

    def poll(self):
        if self.result is not None:
            return self.result
        if not self.started:
            return None
        if not select.select([self.fd], [], [], 0)[0]:
            return None
        try:
            chunk = os.read(self.fd, 4096)
        except BlockingIOError:
            return None
        if not chunk:
            self.result = "cancel"
            return self.result
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            answer = line.strip().lower()
            if answer in (b"", b"r"):
                self.result = "ready"
            elif answer == b"q":
                self.result = "cancel"
            if self.result is not None:
                return self.result
        # Bound invalid/partial input; never store it in prompt history.
        self.buffer = self.buffer[-4096:]
        return None

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.started:
                import termios
                termios.tcflush(self.fd, termios.TCIFLUSH)
        finally:
            os.set_blocking(self.fd, self.was_blocking)
            self.fd = None
            self.buffer = b""
        return False


def interactive(console):
    return (console.is_terminal and sys.stdin.isatty() and sys.stdout.isatty()
            and os.environ.get("TERM", "") != "dumb"
            and os.environ.get("BTDOS_SIMPLE_INPUT") != "1")


class Prompt:
    """Keep one mockable input seam; never write addresses/history to disk."""
    _sessions = {}

    @classmethod
    def ask(cls, label, *, default=None, console, options=None, history=True):
        if not interactive(console):
            if options:
                for key, text in options:
                    console.print(Text(f"  {key}  {text}"))
                console.print(Text("输入所示快捷键后回车 · b返回 · q取消（简易模式不支持方向键）", style="dim"))
            return SimplePrompt.ask(label, default=default, console=console)
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory, DummyHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.shortcuts.choice_input import ChoiceInput
        from prompt_toolkit.styles import Style

        title = str(label)
        style = Style.from_dict({"selected-option": "ansicyan bold",
                                 "frame.border": "ansicyan", "number": "ansicyan"})
        if options:
            values = list(options)
            for key, text in (("b", "返回上一步"), ("q", "取消并返回首页")):
                if not any(value == key for value, _ in values):
                    values.append((key, text))
            bindings = KeyBindings()
            # Displayed keys are direct shortcuts; arrows select, Enter accepts.
            # Do not use RadioList's positional numbers (they differ on page 2).
            for key, _ in values:
                if key and all(char.isascii() and char.isalnum() for char in key):
                    def select(event, value=key):
                        event.app.exit(result=value)
                    eager = not any(other != key and other.startswith(key) for other, _ in values)
                    bindings.add(*tuple(key), eager=eager)(select)
            @bindings.add("c-d")
            def eof(event):
                event.app.exit(exception=EOFError())
            choice = ChoiceInput(message=title + "  [↑↓ 选择 · Enter 确认]",
                options=[(key, f"{key}  {text}") for key, text in values],
                default=default, show_numbers=False, key_bindings=bindings,
                style=style, show_frame=False)
            return choice.prompt()
        # Field-scoped histories prevent a MAC recalled in a UUID/number field.
        key = title if history else None
        if key is None:
            session = PromptSession(history=DummyHistory())
        else:
            if key not in cls._sessions:
                if len(cls._sessions) >= 64:
                    cls._sessions.pop(next(iter(cls._sessions)))
                cls._sessions[key] = PromptSession(history=InMemoryHistory())
            session = cls._sessions[key]
        suffix = f"（回车采用 {default}）" if default is not None else ""
        value = session.prompt(title + suffix + ": ",
            enable_history_search=False, complete_while_typing=False,
            enable_suspend=False, enable_system_prompt=False,
            enable_open_in_editor=False)
        return str(default) if not value.strip() and default is not None else value

    @classmethod
    def clear_history(cls):
        cls._sessions.clear()
