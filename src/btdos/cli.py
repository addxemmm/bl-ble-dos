"""Stable CLI plus an optional interactive guided terminal interface."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

from . import __version__
from .config import Plan, load_plan
from .doctor import adapters, command, diagnose
from .evidence import verify_bundle


def parser():
    p = argparse.ArgumentParser(prog="bt-dos", description="BR/EDR + BLE 有界可用性测试与流量证据工具")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")
    sub.add_parser("wizard", help="引导式终端界面（默认）")
    d = sub.add_parser("doctor", help="只读环境与硬件诊断")
    d.add_argument("--json", action="store_true")
    sub.add_parser("adapters", help="枚举主机控制器")
    demo = sub.add_parser("demo", help="无硬件、无sudo的合成演示")
    demo.add_argument("--output", default="runs")
    run = sub.add_parser("run", help="确认计划后实时执行并自动保存抓包")
    run.add_argument("--plan", required=True)
    run.add_argument("--confirm", required=True, help="必须与计划中的目标MAC一致")
    for name in ("report", "verify"):
        check = sub.add_parser(name)
        check.add_argument("run_dir")
    scan = sub.add_parser("scan", help="显式、限时设备发现；不是DoS负载")
    scan.add_argument("--transport", choices=["le", "bredr"], required=True)
    scan.add_argument("--adapter", default="hci0")
    scan.add_argument("--seconds", type=int, default=5)
    info = sub.add_parser("ble-info", help="显式连接并发现GATT服务（不请求读写）")
    info.add_argument("--target", required=True)
    info.add_argument("--adapter", default="hci0")
    return p


def discover(args):
    if sys.platform != "linux":
        raise RuntimeError("发现功能需要Linux/BlueZ")
    if not re.fullmatch(r"hci[0-9]{1,3}", args.adapter) or not 1 <= args.seconds <= 15:
        raise ValueError("adapter格式错误或扫描时长不在1..15秒")
    if args.transport == "le":
        from bleak import BleakScanner
        async def scan():
            values = await BleakScanner.discover(timeout=args.seconds, return_adv=True,
                                                bluez={"adapter": args.adapter})
            return [dict(address=device.address, name=device.name, rssi=advertisement.rssi,
                         service_uuids=advertisement.service_uuids) for device, advertisement in values.values()]
        from .supervisor import adapter_lock
        with adapter_lock(args.adapter):
            print(json.dumps(asyncio.run(scan()), ensure_ascii=False, indent=2))
    else:
        local = next((v for v in adapters() if v["adapter"] == args.adapter), None)
        if not local or not local.get("address"):
            raise RuntimeError("未取得所选控制器地址")
        # Explicit controller selection in a private bluetoothctl session, then bounded scan.
        import subprocess
        import tempfile
        import time
        from .supervisor import adapter_lock
        with adapter_lock(args.adapter), tempfile.TemporaryFile(mode="w+t") as output:
            process = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True)
            try:
                process.stdin.write(f"select {local['address']}\nscan bredr\n")
                process.stdin.flush()
                time.sleep(args.seconds)
                process.communicate("scan off\nquit\n", timeout=3)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            output.seek(0)
            print(output.read())


def ble_info(args):
    if sys.platform != "linux":
        raise RuntimeError("GATT发现需要Linux/BlueZ")
    plan = Plan(case="ble-cycle", adapter=args.adapter, target=args.target)
    plan.validate()
    from . import cases
    from types import SimpleNamespace
    import tempfile
    from .supervisor import adapter_lock
    with adapter_lock(plan.adapter), tempfile.TemporaryDirectory(prefix="btdos-info-") as temp:
        opts = SimpleNamespace(target=plan.target, adapter=plan.adapter, stop_file=str(Path(temp)/"STOP"))
        asyncio.run(asyncio.wait_for(cases.ble_info(opts), timeout=30))


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.command in (None, "wizard"):
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                p.print_help()
                return 0
            from .ui import wizard
            return wizard()
        if args.command == "doctor":
            data = diagnose()
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                from .ui import show_doctor
                show_doctor(data)
            return 0
        if args.command == "adapters":
            print(json.dumps(adapters(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "demo":
            from .ui import demo
            demo(args.output)
            return 0
        if args.command == "run":
            plan = load_plan(args.plan)
            if args.confirm.upper() != plan.target.upper():
                raise ValueError("--confirm 与计划目标不一致，未启动负载")
            from .ui import execute
            return execute(plan)
        if args.command == "verify":
            result = verify_bundle(Path(args.run_dir))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "report":
            root = Path(args.run_dir)
            result = verify_bundle(root)
            if not result["ok"]:
                print("WARNING: evidence integrity mismatch", file=sys.stderr)
            print((root/"report.md").read_text(encoding="utf-8"))
            return 0 if result["ok"] else 1
        if args.command == "scan":
            discover(args)
            return 0
        if args.command == "ble-info":
            ble_info(args)
            return 0
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
