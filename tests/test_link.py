"""Mock-only link-parameter tests: no subprocess, sudo, Bluetooth or radio."""
from contextlib import ExitStack
import math
import subprocess
import unittest
from unittest.mock import Mock, patch

from btdos import link


TARGET = "02:00:00:00:00:01"
OTHER = "02:00:00:00:00:02"


def connections(handle=42, target=TARGET, kind="LE", state=1):
    return f"Connections:\n\t< {kind} {target} handle {handle} state {state} lm CENTRAL AUTH ENCRYPT\n"


def completed(stdout="", **changes):
    result = dict(status="completed", returncode=0, stdout=stdout, stderr="",
                  cleanup_status="direct_child_reaped")
    result.update(changes)
    return result


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.which = self.stack.enter_context(patch.object(link.shutil, "which",
            side_effect=lambda name: "/usr/bin/"+name))
        self.command = self.stack.enter_context(patch.object(link, "_command",
            side_effect=[completed(connections()), completed(connections()), completed()]))
        self.popen = self.stack.enter_context(patch.object(link.subprocess, "Popen",
            side_effect=AssertionError("unexpected real subprocess")))

    def test_exact_fixed_argv_decimal_handle_and_units(self):
        result = link.request_le_parameters("hci2", TARGET.lower(), 7.5)
        calls = self.command.call_args_list
        self.assertEqual([call.args[0] for call in calls], [
            ["/usr/bin/hcitool", "-i", "hci2", "con"],
            ["/usr/bin/hcitool", "-i", "hci2", "con"],
            ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout", "--signal=TERM", "--kill-after=1s", "4s",
             "/usr/bin/hcitool", "-i", "hci2", "lecup",
             "--handle", "42", "--min", "6", "--max", "6", "--latency", "0", "--timeout", "400"],
        ])
        self.assertEqual(len({call.args[1] for call in calls}), 1)
        self.assertEqual([call.kwargs["timeout"] for call in calls], [4, 4, 5.5])
        self.assertEqual(result["status"], "requested_unverified")
        self.assertEqual(result["requested_supervision_timeout_ms"], 4000)
        self.assertFalse(result["actual_parameters_verified"])
        self.assertEqual(result["privileged_helper_ttl_s"], 5)
        self.assertFalse(result["privileged_cleanup_verified"])
        self.assertEqual(result["handle"], 42)
        self.popen.assert_not_called()

    def test_interval_upper_boundary_uses_forty_units(self):
        result = link.request_le_parameters("hci0", TARGET, 50)
        self.assertEqual(result["interval_units"], 40)
        self.assertIn("40", self.command.call_args.args[0])

    def test_insufficient_total_budget_prevents_starting_privileged_request(self):
        with patch.object(link.time, "monotonic", side_effect=[0, 10, 10]):
            result = link.request_le_parameters("hci0", TARGET, 7.5)
        self.assertEqual(result["status"], "request_failed")
        self.assertIn("insufficient total budget", result["error"])
        self.assertEqual(self.command.call_count, 2)

    def test_invalid_input_never_reaches_tools(self):
        invalid = [
            ("hci0;id", TARGET, 7.5), ("hci1000", TARGET, 7.5),
            ("hci0", TARGET+";id", 7.5), ("hci0", "FF:FF:FF:FF:FF:FF", 7.5),
        ]
        invalid += [("hci0", TARGET, value) for value in
                    (7.49, 50.01, 8, True, "7.5", None, math.nan, math.inf, 10**1000)]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                link.request_le_parameters(*arguments)
        self.which.assert_not_called()
        self.command.assert_not_called()

    def test_missing_relative_or_unexpected_tool_path_is_rejected(self):
        for found in (None, "hcitool", "/tmp/hcitool", "/home/user/hcitool"):
            with self.subTest(found=found):
                self.which.side_effect = None
                self.which.return_value = found
                result = link.request_le_parameters("hci0", TARGET, 7.5)
                self.assertEqual(result["status"], "not_requested")
        self.command.assert_not_called()

    def test_missing_sudo_prevents_even_initial_lookup(self):
        self.which.side_effect = lambda name: "/usr/bin/hcitool" if name == "hcitool" else None
        self.assertEqual(link.request_le_parameters("hci0", TARGET, 7.5)["status"], "not_requested")
        self.command.assert_not_called()

    def test_missing_or_unexpected_timeout_prevents_even_initial_lookup(self):
        for found in (None, "timeout", "/tmp/timeout"):
            with self.subTest(found=found):
                self.which.side_effect = lambda name: found if name == "timeout" else "/usr/bin/"+name
                self.assertEqual(link.request_le_parameters("hci0", TARGET, 7.5)["status"], "not_requested")
        self.command.assert_not_called()

    def test_mapping_failures_stop_before_update(self):
        invalid = ["", "Usage: con", "Connections:\n", connections(target=OTHER),
                   connections(kind="ACL"), connections(kind="SCO"), connections(state=0),
                   connections(handle=-1), connections(handle=0x0f00),
                   connections(handle="0x002A"), connections(kind="Unknown"),
                   connections()+connections().split("\n", 1)[1],
                   connections()+connections(target=OTHER).split("\n", 1)[1],
                   connections()+"unknown garbage\n"]
        for output in invalid:
            with self.subTest(output=output):
                self.command.reset_mock()
                self.command.side_effect = [completed(output)]
                result = link.request_le_parameters("hci0", TARGET, 7.5)
                self.assertEqual(result["status"], "not_requested")
                self.command.assert_called_once()

    def test_full_ten_record_list_is_rejected_as_potentially_truncated(self):
        output = "Connections:\n"+"".join(
            connections(handle=n, target=f"02:00:00:00:00:{n:02X}").split("\n", 1)[1]
            for n in range(1, 11))
        self.command.side_effect = [completed(output)]
        self.assertEqual(link.request_le_parameters("hci0", TARGET, 7.5)["status"], "not_requested")
        self.command.assert_called_once()

    def test_recheck_changes_or_missing_link_abort_update(self):
        for output in (connections(handle=43), connections(target=OTHER), connections(kind="ACL")):
            with self.subTest(output=output):
                self.command.reset_mock()
                self.command.side_effect = [completed(connections()), completed(output)]
                result = link.request_le_parameters("hci0", TARGET, 7.5)
                self.assertEqual(result["status"], "not_requested")
                self.assertEqual(self.command.call_count, 2)

    def test_decimal_handle_zero_and_maximum_are_valid(self):
        for handle in (0, 0x0eff):
            with self.subTest(handle=handle):
                self.command.side_effect = [completed(connections(handle)), completed(connections(handle)), completed()]
                result = link.request_le_parameters("hci0", TARGET, 7.5)
                self.assertEqual(result["status"], "requested_unverified")
                self.assertEqual(result["handle"], handle)

    def test_command_failure_timeout_stderr_and_usage_do_not_claim_effect(self):
        for failing in (completed(returncode=1), completed(returncode=124), completed(returncode=137),
                        completed(cleanup_status="unconfirmed"), completed(stderr="HCI update failed"),
                        completed(status="timeout"), completed(stdout="Usage: lecup"),
                        completed(status="output_limit")):
            with self.subTest(failing=failing):
                self.command.side_effect = [completed(connections()), completed(connections()), dict(failing)]
                result = link.request_le_parameters("hci0", TARGET, 7.5)
                self.assertEqual(result["status"], "request_failed")
                self.assertFalse(result["actual_parameters_verified"])
                self.assertFalse(result["privileged_cleanup_verified"])
                if failing["status"] == "timeout":
                    self.assertEqual(result["cleanup_status"], "privileged_descendants_unverified")

    def test_lookup_nonzero_stderr_timeout_and_oserror_fail_closed(self):
        for failing in (completed(returncode=1), completed(stderr="permission denied"),
                        completed(status="timeout"), OSError("synthetic spawn failure")):
            with self.subTest(failing=failing):
                self.command.reset_mock()
                self.command.side_effect = [failing]
                result = link.request_le_parameters("hci0", TARGET, 7.5)
                self.assertIn(result["status"], ("lookup_failed", "not_requested"))
                self.command.assert_called_once()

    def test_helper_propagates_original_guard_cancellation_and_timeout(self):
        for error in (InterruptedError("STOP"), TimeoutError("wall budget"), OSError("capture disk error")):
            with self.subTest(error=error):
                guard = Mock(side_effect=error)
                self.command.side_effect = lambda *_args, **kwargs: kwargs["guard"]()
                with self.assertRaises(type(error)) as raised:
                    link.request_le_parameters("hci0", TARGET, 7.5, guard=guard)
                self.assertIs(raised.exception, error)


class CommandTests(unittest.TestCase):
    def test_request_wait_exceeds_inner_five_second_ttl_and_respects_total_deadline(self):
        for remaining, expected in ((15, 5.5), (3, 2.5)):
            with self.subTest(remaining=remaining):
                proc = Mock(returncode=0)
                proc.poll.return_value = 0
                with patch.object(link.subprocess, "Popen", return_value=proc), patch.object(link.time, "monotonic", return_value=0), patch.object(link, "_wait_command") as wait:
                    result = link._command(["SYNTHETIC"], remaining, timeout=link.REQUEST_TIMEOUT)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(wait.call_args.args[1], expected)

    def test_command_uses_no_shell_or_pipe_and_bounded_timeout(self):
        proc = Mock(returncode=0)
        proc.poll.return_value = 0
        with patch.object(link.subprocess, "Popen", return_value=proc) as popen:
            result = link._command(["SYNTHETIC"], link.time.monotonic()+15)
        self.assertEqual(result["status"], "completed")
        self.assertLessEqual(proc.wait.call_args.kwargs["timeout"], 0.1)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertTrue(popen.call_args.kwargs["stdout"].closed)
        self.assertTrue(popen.call_args.kwargs["stderr"].closed)

    def test_deadline_exhaustion_does_not_spawn(self):
        with patch.object(link.subprocess, "Popen") as popen:
            result = link._command(["SYNTHETIC"], link.time.monotonic()-1)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["cleanup_status"], "not_started")
        popen.assert_not_called()

    def test_timeout_terminates_then_kills_and_reaps(self):
        proc = Mock(returncode=-9)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("SYNTHETIC", .25), -9]
        with patch.object(link.subprocess, "Popen", return_value=proc), patch.object(link, "_wait_command", side_effect=subprocess.TimeoutExpired("SYNTHETIC", 4)):
            result = link._command(["SYNTHETIC"], link.time.monotonic()+15)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["cleanup_status"], "direct_child_reaped")
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertTrue(all(call.kwargs["timeout"] <= .25 for call in proc.wait.call_args_list))

    def test_cleanup_failure_remains_explicit_and_bounded(self):
        proc = Mock()
        proc.poll.return_value = None
        proc.terminate.side_effect = PermissionError("synthetic")
        proc.kill.side_effect = PermissionError("synthetic")
        proc.wait.side_effect = subprocess.TimeoutExpired("SYNTHETIC", .25)
        self.assertEqual(link._cleanup(proc, link.time.monotonic()+.5), "unconfirmed")
        self.assertEqual(proc.wait.call_count, 2)

    def test_keyboard_interrupt_cleans_up_and_propagates(self):
        proc = Mock(returncode=-15)
        proc.poll.return_value = None
        proc.wait.side_effect = [KeyboardInterrupt(), -15]
        with patch.object(link.subprocess, "Popen", return_value=proc) as popen, self.assertRaises(KeyboardInterrupt):
            link._command(["SYNTHETIC"], link.time.monotonic()+15)
        proc.terminate.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["stdout"].closed)

    def test_excess_output_is_bounded_and_reported(self):
        proc = Mock(returncode=0)
        proc.poll.return_value = 0
        def spawn(*_args, **kwargs):
            kwargs["stdout"].write(b"x"*(link.MAX_OUTPUT+100))
            return proc
        with patch.object(link.subprocess, "Popen", side_effect=spawn):
            result = link._command(["SYNTHETIC"], link.time.monotonic()+15)
        self.assertEqual(result["status"], "output_limit")
        self.assertEqual(len(result["stdout"]), link.MAX_OUTPUT)

    def test_wait_checks_guard_at_most_every_tenth_second_until_cancellation(self):
        elapsed = [0.0]
        proc = Mock()
        error = InterruptedError("STOP fixture")
        def waiting(timeout):
            elapsed[0] += timeout
            raise subprocess.TimeoutExpired("SYNTHETIC", timeout)
        def guard():
            if elapsed[0] >= .3:
                raise error
        proc.wait.side_effect = waiting
        with patch.object(link.time, "monotonic", side_effect=lambda: elapsed[0]), self.assertRaises(InterruptedError) as raised:
            link._wait_command(proc, 4, guard)
        self.assertIs(raised.exception, error)
        self.assertLessEqual(elapsed[0], .300000001)
        self.assertTrue(all(call.kwargs["timeout"] <= .1 for call in proc.wait.call_args_list))

    def test_wait_timeout_has_exact_budget_without_sleep_or_real_process(self):
        elapsed = [0.0]
        proc = Mock()
        def waiting(timeout):
            elapsed[0] += timeout
            raise subprocess.TimeoutExpired("SYNTHETIC", timeout)
        proc.wait.side_effect = waiting
        guard = Mock()
        with patch.object(link.time, "monotonic", side_effect=lambda: elapsed[0]), self.assertRaises(subprocess.TimeoutExpired):
            link._wait_command(proc, 5.5, guard)
        self.assertAlmostEqual(elapsed[0], 5.5)
        self.assertGreaterEqual(guard.call_count, 55)
        self.assertTrue(all(call.kwargs["timeout"] <= .1 for call in proc.wait.call_args_list))

    def test_guard_failure_before_spawn_does_not_start_process(self):
        with patch.object(link.subprocess, "Popen") as popen, self.assertRaises(InterruptedError):
            link._command(["SYNTHETIC"], link.time.monotonic()+15,
                          guard=Mock(side_effect=InterruptedError("STOP")))
        popen.assert_not_called()

    def test_guard_failure_with_running_process_triggers_cleanup(self):
        for error in (InterruptedError("STOP"), TimeoutError("wall budget"), RuntimeError("capture failed")):
            with self.subTest(error=error):
                proc = Mock(returncode=-15)
                proc.poll.return_value = None
                proc.wait.side_effect = [subprocess.TimeoutExpired("SYNTHETIC", .1), -15]
                guard = Mock(side_effect=[None, None, error])
                with patch.object(link.subprocess, "Popen", return_value=proc), self.assertRaises(type(error)) as raised:
                    link._command(["SYNTHETIC"], link.time.monotonic()+15, guard=guard)
                self.assertIs(raised.exception, error)
                proc.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
