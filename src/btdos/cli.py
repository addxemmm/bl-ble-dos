"""Stable CLI plus an optional interactive guided terminal interface."""
import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .config import Plan, load_plan
from .doctor import adapters, diagnose
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
    scan.add_argument("--json", action="store_true", help="输出结构化扫描快照与清理状态")
    info = sub.add_parser("ble-info", help="显式连接并发现GATT服务（不请求读写）")
    info.add_argument("--target", required=True)
    info.add_argument("--adapter", default="hci0")
    info.add_argument("--confirm", required=True, help="确认对这个MAC进行主动服务探测")
    info.add_argument("--json", action="store_true")
    inspect = sub.add_parser("inspect", help="确认后获取目标SDP/GATT服务元数据；不是测试负载")
    inspect.add_argument("--transport", choices=["le", "bredr"], required=True)
    inspect.add_argument("--adapter", default="hci0")
    inspect.add_argument("--target", required=True)
    inspect.add_argument("--confirm", required=True, help="必须与目标MAC一致")
    inspect.add_argument("--local-address", help="可选：核对BR/EDR控制器本机地址")
    inspect.add_argument("--json", action="store_true", help="仅输出结构化服务快照")
    return p


def discover(args):
    from .discovery import scan_devices
    if args.json:
        result = scan_devices(args.transport, adapter=args.adapter, seconds=args.seconds)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        from .ui import _scan_snapshot, show_scan_result
        result = _scan_snapshot(args.transport, args.adapter, args.seconds)
        show_scan_result(result)
    return 0 if result.status == "completed" else 130 if result.status == "interrupted" else 1


def inspect_device(args):
    plan = Plan(case="ble-cycle", adapter=args.adapter, target=args.target)
    plan.validate()
    if args.confirm.upper() != plan.target.upper():
        raise ValueError("--confirm 与目标不一致，未发起服务探测")
    from .probe import probe_device
    result = probe_device(getattr(args, "transport", "le"), plan.target,
                          adapter=plan.adapter, local_address=getattr(args, "local_address", None))
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        from .ui import probe_panel
        probe_panel(result)
    return 0 if result.status in ("completed", "no_results") else 130 if result.status == "interrupted" else 1


def main(argv=None):
    # Windows pipes often default to cp1252/ACP; help and JSON are UTF-8 APIs.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
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
            return discover(args)
        if args.command in ("inspect", "ble-info"):
            return inspect_device(args)
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
