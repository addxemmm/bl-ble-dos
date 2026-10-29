"""Read-only diagnostics with bounded external commands."""
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import hashlib
from importlib.metadata import version, PackageNotFoundError


def command(argv, timeout=3):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
        return dict(code=proc.returncode, stdout=proc.stdout.strip(), stderr=proc.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(code=-1, stdout="", stderr=str(exc))


def adapters():
    if sys.platform != "linux":
        return []
    result = []
    for path in sorted(Path("/sys/class/bluetooth").glob("hci*")):
        if not re.fullmatch(r"hci\d+", path.name):
            continue
        entry = dict(adapter=path.name, address=None, supported_settings="unknown",
                     current_settings="unknown", source="sysfs")
        address = path / "address"
        if address.exists():
            entry["address"] = address.read_text().strip()
        # HCI sysfs commonly has no address attribute. Bind to the exact BlueZ object.
        dbus_address = command(["busctl", "--system", "get-property", "org.bluez",
                                f"/org/bluez/{path.name}", "org.bluez.Adapter1", "Address"])
        match = re.fullmatch(r's "([0-9A-Fa-f:]{17})"', dbus_address["stdout"])
        if dbus_address["code"] == 0 and match:
            entry["address"] = match.group(1)
            entry["source"] = "bluez-dbus-object"
        result.append(entry)
    listed = command(["bluetoothctl", "list"])
    for address, name in re.findall(r"Controller ([0-9A-Fa-f:]{17}) (.*)", listed["stdout"]):
        for entry in result:
            if entry["address"] and entry["address"].lower() == address.lower():
                entry["name"] = name
    for entry in result:
        info = command(["btmgmt", "--index", entry["adapter"][3:], "info"])
        if info["code"] == 0:
            for field in ("supported_settings", "current_settings"):
                match = re.search(field.replace("_", " ") + r":\s*(.+)", info["stdout"], re.I)
                if match:
                    entry[field] = match.group(1)
            match = re.search(r"addr ([0-9A-Fa-f:]{17})", info["stdout"])
            if match:
                entry["address"] = match.group(1)
        else:
            entry["management_note"] = "能力读取受权限/服务限制；live前需确认BR/EDR/LE能力"
    return result


def diagnose():
    tools = {name: shutil.which(name) for name in
             ("btmon", "btmgmt", "bluetoothctl", "tshark", "timeout", "sudo", "nrfutil")}
    service = command(["systemctl", "is-active", "bluetooth"], timeout=2) if sys.platform == "linux" else dict(stdout="not_linux", code=-1)
    controllers = adapters()
    versions = {}
    for name in ("bluetoothctl", "btmon", "tshark", "nrfutil"):
        if tools.get(name):
            value = command([tools[name], "--version"], timeout=2)
            versions[name] = (value["stdout"] or value["stderr"]).splitlines()[:2]
    dependencies = {}
    for package in ("bleak", "rich", "dbus-fast"):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = "not_installed"
    source_root = Path(__file__).resolve().parent
    source_hash = hashlib.sha256()
    for file in sorted(source_root.glob("*.py")):
        source_hash.update(file.name.encode())
        source_hash.update(file.read_bytes())
    project_root = source_root.parent.parent
    commit = command(["git", "-C", str(project_root), "rev-parse", "HEAD"], timeout=2)
    dirty = command(["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=no"], timeout=2)
    return dict(schema_version=1, platform=platform.platform(), python=platform.python_version(),
                linux=sys.platform == "linux", tools=tools, tool_versions=versions,
                dependencies=dependencies, source_sha256=source_hash.hexdigest(),
                git_commit=commit["stdout"] if commit["code"] == 0 else "unknown",
                git_dirty=bool(dirty["stdout"]) if dirty["code"] == 0 else None,
                bluetooth_service=service["stdout"] or "unknown", adapters=controllers,
                nrf_ports=sorted(str(p) for pattern in ("ttyACM*", "ttyUSB*") for p in Path("/dev").glob(pattern)) if sys.platform == "linux" else [],
                sudo_noninteractive=(command(["sudo", "-n", "true"], timeout=2)["code"] == 0) if tools["sudo"] and sys.platform == "linux" else False,
                ready_for_live=bool(sys.platform == "linux" and controllers and tools["btmon"] and tools["timeout"]),
                note="ready_for_live只是初步环境检查，实际能力/权限/采集就绪仍需run阶段验证；不代表目标或硬件用例验证通过")


def inspect_plan_adapter(plan):
    choices = adapters()
    selected = next((item for item in choices if item["adapter"] == plan.adapter), None)
    if selected is None:
        raise RuntimeError(f"{plan.adapter} 未发现；虚拟机请先直通USB蓝牙设备")
    if plan.case.startswith("br-") and not selected.get("address"):
        raise RuntimeError("未核实所选控制器本机地址，拒绝BR/EDR施压；请检查BlueZ/管理接口权限")
    if plan.local_address and selected.get("address") and selected["address"].lower() != plan.local_address.lower():
        raise RuntimeError("local_address 与所选控制器地址不匹配")
    expected = "br/edr" if plan.case.startswith("br-") else "le"
    settings = selected.get("supported_settings", "unknown")
    if settings != "unknown" and expected not in settings.lower().split():
        raise RuntimeError(f"控制器未报告支持 {expected}")
    return selected
