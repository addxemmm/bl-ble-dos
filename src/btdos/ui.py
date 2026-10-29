"""A compact Rich terminal interface; no hardware ownership in the UI layer."""
import json
import sys
from pathlib import Path

from rich.console import Console
from rich import box
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import CASES, Plan
from .doctor import diagnose
from .supervisor import create_demo, run_plan

console = Console(highlight=False)


def banner(output=console):
    title = Text("BL / BLE  DOS LAB", style="bold bright_cyan")
    title.append("\nBluetooth availability · packet evidence", style="white")
    title.append(f"\nv{__version__}  /  BR/EDR + LE  /  Kali & Ubuntu", style="dim")
    output.print(Panel(title, border_style="cyan", padding=(1, 3), subtitle="single dongle → HCI  |  + nRF52840 → BLE air", subtitle_align="left"))


def diagnostics_table(data):
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("检查项", style="cyan")
    table.add_column("状态 / 说明")
    table.add_row("系统", data["platform"])
    table.add_row("Python", data["python"])
    table.add_row("BlueZ service", data["bluetooth_service"])
    table.add_row("控制器", ", ".join(a["adapter"]+" "+str(a.get("address") or "address unknown") for a in data["adapters"]) or "未发现 / 虚拟机先直通USB")
    table.add_row("HCI 权限", "sudo -n 已就绪" if data["sudo_noninteractive"] else "实时采集前在终端执行 sudo -v")
    table.add_row("nRF", ", ".join(data["nrf_ports"]) or "未发现串口（可选）")
    table.add_row("初步环境", "可进入运行前校验" if data["ready_for_live"] else "有待处理项；demo/文档仍可用")
    return table


def show_doctor(data):
    banner()
    console.print(diagnostics_table(data))
    table = Table("工具", "路径", box=box.SIMPLE)
    for name, location in data["tools"].items():
        table.add_row(name, location or "未安装")
    console.print(table)
    console.print("[dim]只读诊断，不扫描、不连接车端、不变更权限。[/dim]")


def dashboard(phase, path, elapsed, plan):
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column()
    table.add_column(justify="right")
    table.add_row(Text(phase, style="bold bright_cyan"), f"{elapsed:6.1f}s / {plan.wall_budget}s budget")
    table.add_row(f"{CASES[plan.case][0]} · {CASES[plan.case][2]}", f"{plan.adapter} · {plan.capture}")
    table.add_row(f"TARGET  {plan.target}", "Ctrl+C 停止")
    table.add_row(f"{plan.rate:g} req/s · ≤{plan.max_ops} ops", f"{plan.cycles} cycles max")
    table.add_row(Text(str(path), style="dim"), "原始包 + JSONL + report + SHA-256")
    return Panel(table, title="SESSION / 实时状态", border_style="cyan", padding=(1, 2))


def execute(plan):
    banner()
    if console.is_terminal:
        with Live(dashboard("PREPARE", "待创建", 0, plan), console=console, refresh_per_second=5) as live:
            path, status = run_plan(plan, lambda phase, path, elapsed: live.update(dashboard(phase, path, elapsed, plan)))
    else:
        seen = set()
        def update(phase, path, elapsed):
            if phase not in seen:
                console.print(f"{phase:14} {elapsed:6.1f}s  {path}")
                seen.add(phase)
        path, status = run_plan(plan, update)
    console.print(Panel(f"运行状态：[bold]{status}[/bold]\n证据目录：{path}\n报告：{path / 'report.md'}\n[dim]脚本完成不等于车辆合规PASS。[/dim]", border_style="green" if status == "completed" else "yellow", title="EVIDENCE / 已保存"))
    return 0 if status == "completed" else 130 if status == "interrupted" else 1


def demo(output):
    banner()
    plan = Plan(case="br-sdp", adapter="hci0", target="02:00:00:00:00:01", local_address="02:00:00:00:00:02")
    console.print(Panel("[bold yellow]SIMULATION / 合成演示[/bold yellow]\n不扫描、不连接、不请求sudo；不生成伪造的真实pcap。", border_style="yellow"))
    path = create_demo(Path(output))
    console.print(dashboard("SIMULATION_COMPLETE", path, 0, plan))
    console.print(f"报告：{path / 'report.md'}")
    console.print(f"校验：./bt-dos verify '{path}'")
    return path


def wizard():
    banner()
    console.print(Columns([Panel("[bold cyan]01[/bold cyan]  单 dongle\nBR/EDR + BLE · HCI", border_style="blue"), Panel("[bold cyan]02[/bold cyan]  + nRF52840\nBLE 独立空口 · 可选", border_style="blue")], equal=True, expand=True))
    menu = Table("选项", "入口", box=box.SIMPLE)
    for key, value in (("1", "快速演示（不接触硬件）"), ("2", "环境诊断"), ("3", "实时测试向导"), ("4", "打开使用说明路径"), ("0", "退出")):
        menu.add_row(key, value)
    console.print(menu)
    choice = Prompt.ask("选择", choices=["0", "1", "2", "3", "4"], default="1")
    if choice == "0":
        return 0
    if choice == "1":
        demo("runs")
        return 0
    if choice == "2":
        show_doctor(diagnose())
        return 0
    if choice == "4":
        console.print("README.md · docs/usage.md · docs/hardware.md · docs/evidence.md")
        return 0
    if sys.platform != "linux":
        console.print("[yellow]实时测试需要Linux；请在Kali/Ubuntu执行同一入口。[/yellow]")
        return 1
    console.rule("01 / 环境与硬件")
    info = diagnose()
    console.print(diagnostics_table(info))
    if not info["ready_for_live"]:
        console.print("[yellow]先处理USB直通/系统工具和适配器，再进入实时用例。[/yellow]")
        return 1
    if not info["sudo_noninteractive"]:
        console.print("[yellow]先退出本向导，在终端执行 sudo -v，然后重新进入。[/yellow]")
        return 1
    adapter = Prompt.ask("施压控制器", choices=[a["adapter"] for a in info["adapters"]], default=info["adapters"][0]["adapter"])
    console.rule("02 / 用例")
    for index, (name, value) in enumerate(CASES.items(), 1):
        console.print(f"{index}. {value[0]}  {value[1]:7}  {value[2]} ({name})")
    case = list(CASES)[int(Prompt.ask("用例编号", choices=["1", "2", "3", "4"], default="1"))-1]
    console.rule("03 / 目标与接口")
    target = Prompt.ask("车端目标 Bluetooth MAC（BR/EDR与LE地址可能不同）").strip()
    selected = next(item for item in info["adapters"] if item["adapter"] == adapter)
    local = None
    if case.startswith("br-"):
        local = Prompt.ask("所选dongle本机地址", default=selected.get("address") or "LOCAL_BDADDR")
    characteristic = Prompt.ask("厂家确认无副作用的只读特征 UUID / 0xHANDLE") if case == "ble-read" else None
    uuid16 = int(Prompt.ask("SDP服务UUID16（须核实服务清单）", default="0x110b"), 0) if case == "br-sdp" else 0x110B
    console.rule("04 / 抓包与外部证据")
    nrf = case.startswith("ble-") and Confirm.ask("额外启用nRF52840 BLE空口采集？", default=False)
    nrf_port = Prompt.ask("已刷嗅探固件的串口", default=info["nrf_ports"][0] if info["nrf_ports"] else "/dev/ttyACM0") if nrf else None
    business = Confirm.ask("已有独立业务探针并另行留存其结果？", default=False)
    ecu = Confirm.ask("已有ECU/车辆状态日志并另行留存？", default=False)
    console.rule("05 / 有界预算")
    rate, approved, duration = 1.0, 1.0, 10.0
    cycles = 5
    if case in ("br-sdp", "ble-read"):
        approved = FloatPrompt.ask("试验计划批准上限 req/s（参数声明）", default=1.0)
        rate = FloatPrompt.ask("本次请求率 req/s（工具上限20）", default=1.0)
        duration = FloatPrompt.ask("请求负载窗口秒（1..60）", default=10.0)
    else:
        cycles = IntPrompt.ask("串行循环次数（1..10）", default=5)
    baseline = FloatPrompt.ask("负载前观察秒（0..60）", default=5.0)
    recovery = FloatPrompt.ask("负载后恢复观察秒（0..120）", default=10.0)
    output_root = Prompt.ask("证据根目录（包含敏感目标信息，不提交Git）", default="runs")
    plan = Plan(case=case, adapter=adapter, target=target, local_address=local,
                characteristic=characteristic, sdp_uuid16=uuid16, rate=rate,
                approved_rate=approved, duration=duration, cycles=cycles,
                baseline=baseline, recovery=recovery,
                capture="hci+nrf" if nrf else "hci", nrf_port=nrf_port,
                output_root=output_root, external_business_probe=business,
                external_ecu_observation=ecu)
    plan.validate()
    console.rule("06 / 最终复核")
    console.print_json(json.dumps(plan.to_dict()))
    console.print(f"墙钟预算 {plan.wall_budget}s。必选采集失败即停止；单dongle模式不是独立空口证据。")
    console.print("[yellow]确认静止隔离台架、无执行器负载、独立停止措施，以及目标身份和批准预算。[/yellow]")
    if Prompt.ask("输入目标MAC以确认本次实时测试", default="CANCEL").upper() != plan.target.upper():
        console.print("已取消，没有启动采集或负载。")
        return 0
    return execute(plan)
