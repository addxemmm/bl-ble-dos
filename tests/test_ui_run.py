"""Offline execution UI and conservative new-run retry eligibility."""
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from btdos import ui
from btdos.config import Plan
from btdos.evidence import EvidenceBundle


class UiRunTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stream = StringIO()
        self.console = Console(file=self.stream, width=110, color_system=None, force_terminal=False)
        self.stack.enter_context(patch.object(ui, "console", self.console))
        self.plan = Plan(case="br-sdp", adapter="hci0", target="02:00:00:00:00:01",
                         local_address="02:00:00:00:00:02", output_root=str(self.root))

    def bundle(self, status="failed", **overrides):
        metadata = dict(worker_forced=False, cleanup_errors=[],
                        captures=[dict(name="hci", status="stopped", forced=False, cleanup_confirmed=True)])
        metadata.update(overrides)
        bundle = EvidenceBundle(self.root, self.plan.to_dict())
        # Malformed cleanup metadata is intentional in eligibility tests; report
        # generation is outside this UI test's scope.
        with patch("btdos.report.render_report", return_value="Synthetic offline UI evidence"):
            bundle.finalize(status, metadata)
        return bundle.path

    def test_default_execute_remains_integer_and_does_not_construct_input(self):
        for status, expected in (("completed", 0), ("failed", 1), ("interrupted", 130)):
            with self.subTest(status=status), patch.object(ui, "run_plan", return_value=(self.root, status)) as run, \
                    patch.object(ui, "ReadyInput", side_effect=AssertionError("stdin touched")), \
                    patch.object(ui, "Prompt") as prompt:
                self.assertEqual(ui.execute(self.plan), expected)
            self.assertEqual(run.call_args.kwargs, {})
            prompt.ask.assert_not_called()

    def test_non_tty_never_reads_input_even_with_wizard_flags(self):
        with patch.object(ui, "run_plan", return_value=(self.bundle(), "failed")) as run, \
                patch.object(ui, "ReadyInput", side_effect=AssertionError("stdin touched")), \
                patch.object(ui, "Prompt") as prompt:
            self.assertEqual(ui.execute(self.plan, wait_for_target=True, allow_retry=True), 1)
        self.assertEqual(run.call_args.kwargs, {})
        prompt.ask.assert_not_called()

    def test_tty_poll_is_armed_before_prompt_display_and_not_blocking(self):
        tty = Console(file=self.stream, width=110, color_system=None, force_terminal=True)
        events = []
        adapter = Mock()
        adapter.start.side_effect = lambda: events.append("arm")
        adapter.poll.return_value = "ready"
        context = Mock()
        context.__enter__ = Mock(return_value=adapter)
        context.__exit__ = Mock(return_value=False)
        live = Mock()
        live.update.side_effect = lambda *args, **kwargs: events.append("display")
        live_context = Mock()
        live_context.__enter__ = Mock(return_value=live)
        live_context.__exit__ = Mock(return_value=False)

        def run(plan, update, *, ready_poll):
            update("CAPTURE_START", self.root, 1)
            update("WAIT_TARGET_READY", self.root, 2)
            self.assertEqual(events[-2:], ["arm", "display"])
            self.assertEqual(ready_poll(), "ready")
            update("BASELINE", self.root, 3)
            return self.root, "completed"

        with patch.object(ui, "console", tty), patch.object(ui, "ready_tty", return_value=True), \
                patch.object(ui, "ReadyInput", return_value=context), patch.object(ui, "Live", return_value=live_context), \
                patch.object(ui, "run_plan", side_effect=run):
            self.assertEqual(ui.execute(self.plan, wait_for_target=True), 0)
        context.__exit__.assert_called_once()
        live.update.assert_called_with(unittest.mock.ANY, refresh=True)

    def test_default_tty_cli_still_does_not_wait(self):
        tty = Console(file=self.stream, color_system=None, force_terminal=True)
        with patch.object(ui, "console", tty), patch.object(ui, "ready_tty", return_value=True), \
                patch.object(ui, "ReadyInput", side_effect=AssertionError("stdin touched")), \
                patch.object(ui, "run_plan", return_value=(self.root, "completed")) as run:
            self.assertEqual(ui.execute(self.plan), 0)
        self.assertEqual(run.call_args.kwargs, {})

    def test_readiness_dashboard_shows_additional_budget_and_manual_confirmation(self):
        self.console.print(ui.dashboard("WAIT_TARGET_READY", self.root, 1, self.plan, wait_for_target=True))
        output = self.stream.getvalue()
        self.assertIn(f"/{self.plan.wall_budget + 60:g}s", output)
        for text in (self.plan.target, "60s", "操作员准备", "不是连接或建链证明", "q", "Enter"):
            self.assertIn(text, output)

    def test_verified_failed_statuses_offer_explicit_default_quit_or_review(self):
        for status in ("failed", "worker_failed", "target_not_ready"):
            path = self.bundle(status)
            with self.subTest(status=status), patch.object(ui, "ready_tty", return_value=True), \
                    patch.object(ui, "run_plan", return_value=(path, status)) as run, \
                    patch.object(ui, "ask_choice", return_value="r") as ask:
                self.assertIs(ui.execute(self.plan, allow_retry=True), ui.RETRY)
            self.assertEqual(ask.call_args.kwargs["default"], "q")
            run.assert_called_once()
            self.assertTrue((path / "manifest.json").is_file())

    def test_quit_eof_back_do_not_retry(self):
        path = self.bundle()
        for answer in ("q", ui.Cancel(), ui.Back(), EOFError()):
            with self.subTest(answer=answer), patch.object(ui, "ready_tty", return_value=True), \
                    patch.object(ui, "run_plan", return_value=(path, "failed")), \
                    patch.object(ui, "ask_choice") as ask:
                if isinstance(answer, BaseException):
                    ask.side_effect = answer
                else:
                    ask.return_value = answer
                self.assertEqual(ui.execute(self.plan, allow_retry=True), 1)

    def test_cleanup_metadata_is_conservative_and_status_allowlisted(self):
        for status in ("interrupted", "needs_continuation", "completed", "cleanup_failed",
                       "cleanup_unconfirmed", "cleanup_forced", "anything_else"):
            with self.subTest(status=status):
                self.assertFalse(ui._retry_available(self.bundle(status), status))
        for overrides in (dict(worker_forced=True), dict(worker_forced=None),
                          dict(cleanup_errors=["failed cleanup"]), dict(cleanup_errors=None),
                          dict(cleanup_confirmed=False),
                          dict(captures=None), dict(captures=[]), dict(captures=[{}]),
                          dict(captures=[dict(forced=True, cleanup_confirmed=True)]),
                          dict(captures=[dict(forced=False, cleanup_confirmed=False)]),
                          dict(captures=[dict(forced=False)]),
                          dict(captures=[dict(forced=False, cleanup_confirmed=True, status="cleanup_failed")])):
            with self.subTest(overrides=overrides):
                self.assertFalse(ui._retry_available(self.bundle(**overrides), "failed"))

    def test_empty_capture_retry_requires_explicit_no_worker_and_confirmed_cleanup(self):
        for worker_started, cleanup_confirmed, expected in ((False, True, True),
                (True, True, False), (None, True, False), (False, False, False), (False, None, False)):
            path = self.bundle(captures=[], worker_started=worker_started, cleanup_confirmed=cleanup_confirmed)
            self.assertEqual(ui._retry_available(path, "failed"), expected)

    def test_damaged_missing_or_unsealed_bundle_blocks_retry(self):
        for mutation in ("corrupt", "missing_manifest", "unsealed", "missing_result", "bad_json", "no_metadata"):
            path = self.bundle()
            if mutation == "corrupt":
                (path / "report.md").write_text("tampered", encoding="utf-8")
            elif mutation == "missing_manifest":
                (path / "manifest.json").unlink()
            elif mutation == "unsealed":
                (path / "UNSEALED.json").write_text("{}", encoding="utf-8")
            elif mutation == "missing_result":
                (path / "result.json").unlink()
            else:
                (path / "result.json").write_text("{bad" if mutation == "bad_json" else "{}", encoding="utf-8")
            with self.subTest(mutation=mutation):
                self.assertFalse(ui._retry_available(path, "failed"))

    def test_ineligible_failure_has_no_retry_prompt(self):
        path = self.bundle(worker_forced=True)
        with patch.object(ui, "ready_tty", return_value=True), \
                patch.object(ui, "run_plan", return_value=(path, "failed")), \
                patch.object(ui, "ask_choice", side_effect=AssertionError("retry offered")):
            self.assertEqual(ui.execute(self.plan, allow_retry=True), 1)
