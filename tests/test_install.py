"""Offline installer checks: all Python, apt and sudo commands are local fakes."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
BASH = shutil.which("bash") if sys.platform != "win32" else None

FAKE_COMMAND = r'''#!PYTHON
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps([name, args]) + "\n")
if name == "uname":
    print(os.environ.get("INSTALL_TEST_OS", "Linux"))
elif name == "id":
    print(os.environ.get("INSTALL_TEST_UID", "1000"))
elif name == "dirname":
    if args and args[0] == "--":
        args = args[1:]
    print(os.path.dirname(args[0]))
elif name == "sudo":
    if os.environ.get("INSTALL_TEST_SUDO_FAIL"):
        sys.exit(1)
    if args[0] == "-n":
        args.pop(0)
    sys.exit(subprocess.call(args))
elif name == "apt-get":
    sys.exit(int(os.environ.get("INSTALL_TEST_APT_FAIL", "0")))
elif name in ("python", "python3"):
    if args[:2] == ["-m", "venv"]:
        target = Path(args[2]) / "bin" / "python"
        target.parent.mkdir(parents=True)
        shutil.copyfile(sys.argv[0], target)
        target.chmod(0o755)
    elif args[:3] == ["-m", "pip", "install"]:
        sys.exit(int(os.environ.get("INSTALL_TEST_PIP_FAIL", "0")))
    elif args == ["-m", "pip", "check"]:
        sys.exit(int(os.environ.get("INSTALL_TEST_PIP_CHECK_FAIL", "0")))
    elif args == ["-m", "btdos", "--version"]:
        sys.exit(int(os.environ.get("INSTALL_TEST_CLI_FAIL", "0")))
    elif args == ["-m", "unittest", "discover", "-s", "tests", "-v"]:
        sys.exit(int(os.environ.get("INSTALL_TEST_TEST_FAIL", "0")))
    elif args and args[0] == "-c":
        code = args[1]
        if "import venv" in code and os.environ.get("INSTALL_TEST_NO_VENV"):
            sys.exit(1)
        if "sys.version_info" in code and os.environ.get("INSTALL_TEST_OLD_PYTHON"):
            sys.exit(1)
        if "sys.prefix" in code and os.environ.get("INSTALL_TEST_BAD_VENV"):
            sys.exit(1)
'''


@unittest.skipUnless(BASH, "Installer integration checks require native Linux/POSIX bash")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "fixture repo"
        self.bin = self.base / "mock-bin"
        self.bin.mkdir()
        (self.root / "scripts").mkdir(parents=True)
        self.script = self.root / "scripts" / "install.sh"
        shutil.copyfile(SOURCE, self.script)
        (self.root / "bt-dos").write_text("fixture", encoding="utf-8")
        self.log = self.base / "calls.jsonl"
        fake = FAKE_COMMAND.replace("#!PYTHON", "#!" + sys.executable)
        for name in ("uname", "id", "dirname", "sudo", "apt-get", "python3", "chmod",
                     "bluetoothctl", "btmon", "btmgmt", "sdptool", "timeout", "tshark", "nrfutil",
                     "systemctl", "service", "rfkill", "hciconfig", "hcitool"):
            target = self.bin / name
            target.write_text(fake, encoding="utf-8")
            target.chmod(0o755)
        # Only these harmless utilities may reach the host; everything capable of
        # installing packages or changing permissions is stubbed above.
        for name in ("cat", "env"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = dict(os.environ, PATH=str(self.bin), INSTALL_TEST_LOG=str(self.log))

    def run_install(self, *args, **overrides):
        return subprocess.run(
            [BASH, str(self.script), *args], stdin=subprocess.DEVNULL,
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
            env=dict(self.env, **overrides),
        )

    def calls(self, name):
        if not self.log.exists():
            return []
        return [args for command, args in map(json.loads, self.log.read_text().splitlines()) if command == name]

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bash_syntax(self):
        result = subprocess.run([BASH, "-n", str(SOURCE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_and_unknown_argument_have_no_install_side_effects(self):
        result = self.run_install("--help")
        self.assert_ok(result)
        self.assertIn("--non-interactive", result.stdout)
        self.assertIn("--install-system", result.stdout)
        self.assertEqual(self.run_install("--unknown").returncode, 2)
        self.assertFalse(self.calls("python3"))
        self.assertFalse(self.calls("apt-get"))

    def test_default_install_is_nonblocking_and_does_not_modify_system(self):
        result = self.run_install("--non-interactive")
        self.assert_ok(result)
        self.assertIn("[6/6]", result.stdout)
        self.assertIn("USB", result.stdout)
        self.assertIn(str(self.root).replace(" ", "\\ ") + "/bt-dos doctor", result.stdout)
        self.assertIn("/bt-dos wizard", result.stdout)
        self.assertIn(["-m", "pip", "install", "-e", str(self.root)], self.calls("python"))
        self.assertIn(["-m", "pip", "check"], self.calls("python"))
        self.assertIn(["-m", "btdos", "--version"], self.calls("python"))
        self.assertIn("/bt-dos demo --output", result.stdout)
        self.assertIn("/bt-dos verify RUN_DIR", result.stdout)
        self.assertIn("/bt-dos adapters", result.stdout)
        self.assertFalse(self.calls("apt-get"))
        self.assertFalse(self.calls("sudo"))
        self.assertFalse(self.calls("bluetoothctl"))
        self.assertFalse(self.calls("btmon"))
        for name in ("btmgmt", "sdptool", "timeout", "tshark", "nrfutil", "systemctl", "service", "rfkill", "hciconfig", "hcitool"):
            self.assertFalse(self.calls(name), name)
        self.assertEqual([args for args in self.calls("python") if args[:2] == ["-m", "btdos"]],
                         [["-m", "btdos", "--version"]])

    def test_repeat_reuses_venv_and_dev_runs_tests(self):
        self.assert_ok(self.run_install())
        result = self.run_install("--dev", "--non-interactive")
        self.assert_ok(result)
        self.assertIn("复用", result.stdout)
        self.assertEqual(sum(args[:2] == ["-m", "venv"] for args in self.calls("python3")), 1)
        self.assertIn(["-m", "unittest", "discover", "-s", "tests", "-v"], self.calls("python"))

    def test_explicit_system_install_is_fake_noninteractive_and_excludes_tshark(self):
        self.assert_ok(self.run_install("--install-system", "--non-interactive"))
        self.assertEqual(self.calls("apt-get"), [
            ["update"], ["install", "-y", "python3", "python3-venv", "python3-pip", "bluez"],
        ])
        self.assertTrue(all(args[0] == "-n" for args in self.calls("sudo")))

    def test_root_never_creates_venv(self):
        result = self.run_install(INSTALL_TEST_UID="0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("普通用户", result.stderr)
        self.assertFalse(self.calls("python3"))
        self.assertFalse((self.root / ".venv").exists())
        self.assertFalse(self.calls("apt-get"))

    def test_root_explicit_system_install_stops_before_venv(self):
        result = self.run_install("--install-system", INSTALL_TEST_UID="0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls("apt-get")), 2)
        self.assertFalse(self.calls("sudo"))
        self.assertFalse(self.calls("python3"))

    def test_system_install_permission_failure_does_not_continue(self):
        result = self.run_install("--install-system", "--non-interactive", INSTALL_TEST_SUDO_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sudo", result.stderr)
        self.assertFalse(self.calls("apt-get"))
        self.assertFalse(self.calls("python3"))

    def test_unsupported_system_and_old_python_have_remediation(self):
        result = self.run_install(INSTALL_TEST_OS="Darwin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Ubuntu", result.stderr)
        result = self.run_install(INSTALL_TEST_OLD_PYTHON="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python >=3.10", result.stderr)
        self.assertFalse((self.root / ".venv").exists())

    def test_missing_venv_has_apt_repair_command(self):
        result = self.run_install(INSTALL_TEST_NO_VENV="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("python3-venv", result.stderr)
        self.assertFalse(self.calls("apt-get"))

    def test_pip_failure_prints_exact_retry_and_preserves_venv(self):
        result = self.run_install(INSTALL_TEST_PIP_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("-m pip install -e", result.stderr)
        self.assertIn("请勿使用 sudo pip", result.stderr)
        self.assertTrue((self.root / ".venv" / "bin" / "python").exists())
        self.assertNotIn("安装完成", result.stdout)
        self.assertNotIn(["-m", "pip", "check"], self.calls("python"))

    def test_pip_check_failure_stops_before_cli_and_completion(self):
        result = self.run_install("--dev", INSTALL_TEST_PIP_CHECK_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("依赖一致性检查失败", result.stderr)
        self.assertIn("-m pip check", result.stderr)
        self.assertIn("-m pip install -e", result.stderr)
        self.assertNotIn("安装完成", result.stdout)
        self.assertNotIn(["-m", "btdos", "--version"], self.calls("python"))
        self.assertFalse(any(args[:2] == ["-m", "unittest"] for args in self.calls("python")))
        self.assertTrue((self.root / ".venv" / "bin" / "python").exists())

    def test_cli_validation_failure_is_not_reported_as_install_complete(self):
        result = self.run_install("--dev", INSTALL_TEST_CLI_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CLI 入口验证失败", result.stderr)
        self.assertIn("-m btdos --version", result.stderr)
        self.assertIn(["-m", "pip", "check"], self.calls("python"))
        self.assertNotIn("安装完成", result.stdout)
        self.assertFalse(any(args[:2] == ["-m", "unittest"] for args in self.calls("python")))
        self.assertFalse(self.calls("apt-get"))
        self.assertFalse(self.calls("sudo"))

    def test_dev_test_failure_is_not_reported_as_install_complete(self):
        result = self.run_install("--dev", INSTALL_TEST_TEST_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("离线测试失败", result.stderr)
        self.assertNotIn("安装完成", result.stdout)

    def test_incomplete_existing_venv_is_preserved(self):
        (self.root / ".venv").mkdir()
        marker = self.root / ".venv" / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("备份", result.stderr)
        self.assertEqual(marker.read_text(), "preserve")

    def test_missing_bluez_warns_but_allows_offline_install(self):
        for name in ("btmon", "bluetoothctl", "btmgmt", "sdptool"):
            (self.bin / name).unlink()
        result = self.run_install()
        self.assert_ok(result)
        self.assertIn("缺少 BlueZ", result.stdout)
        self.assertIn("bluez", result.stderr)
        self.assertFalse(self.calls("apt-get"))

    def test_missing_live_and_optional_tools_does_not_install_or_block_demo(self):
        for name in ("timeout", "sudo", "tshark", "nrfutil", "hcitool"):
            (self.bin / name).unlink()
        result = self.run_install()
        self.assert_ok(result)
        self.assertIn("缺少 GNU timeout", result.stdout)
        self.assertIn("缺少 sudo", result.stdout)
        self.assertIn("可选 tshark", result.stdout)
        self.assertIn("可选 nrfutil", result.stdout)
        self.assertIn("可选 hcitool", result.stdout)
        self.assertFalse(self.calls("apt-get"))
        self.assertFalse(self.calls("sudo"))


class InstallerStaticTests(unittest.TestCase):
    def test_offline_validation_uses_fixed_argv(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('if ! "$PYTHON" -m pip check; then', source)
        self.assertIn('if ! "$PYTHON" -m btdos --version; then', source)
        self.assertLess(source.index('"$PYTHON" -m pip check'), source.index('"$PYTHON" -m btdos --version'))
        self.assertLess(source.index('"$PYTHON" -m btdos --version'), source.index('软件安装完成'))

    def test_fake_failure_controls_match_validation_commands(self):
        for command, variable in (( '["-m", "pip", "check"]', "INSTALL_TEST_PIP_CHECK_FAIL"),
                                  ('["-m", "btdos", "--version"]', "INSTALL_TEST_CLI_FAIL")):
            self.assertIn("args == " + command, FAKE_COMMAND)
            self.assertIn(variable, FAKE_COMMAND)


if __name__ == "__main__":
    unittest.main()
