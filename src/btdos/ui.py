"""Guided Rich UI. Discovery is explicit; editing never starts a workload."""
from dataclasses import dataclass, field
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from . import __version__
from .config import CASES, MAC, Plan, budget_defaults
from .discovery import safe_display_name, scan_devices
from .doctor import command, diagnose
from .supervisor import create_demo, run_plan
from .terminal import Prompt

console = Console(highlight=False, theme=Theme({"accent": "bright_cyan", "muted": "dim", "good": "green", "warn": "yellow"}))
STEPS = ("环境", "用例与适配器", "选择目标", "测试参数", "证据配置", "复核运行")
PHASES = ("VALIDATE", "CAPTURE_START", "BASELINE", "RUN", "RECOVERY", "CAPTURE_STOP", "FINALIZE")
PHASE_LABELS = dict(zip(PHASES, ("检查计划", "启动采集", "基线观察", "执行用例", "恢复观察", "停止采集", "封存报告")))


class Back(Exception):
    """Return to the previous section without discarding the draft."""


class Cancel(Exception):
    """Cancel this wizard, never a request to run a workload."""


@dataclass
class TargetSelection:
    address: str
    display_name: str = "手动输入"
    source: str = "manual"
    address_type: str = "unknown"
    requested_transport: str = ""
    rssi: int | None = None
    paired: bool | None = None
    known_services: list[str] = field(default_factory=list)


@dataclass
class WizardDraft:
    case: str = "br-sdp"
    adapter: str = "hci0"
    target: TargetSelection | None = None
    local_address: str | None = None
    characteristic: str | None = None
    sdp_uuid16: int | None = None
    preset: str = "default"
    probe_result: object | None = None
    settings: dict = field(default_factory=lambda: dict(budget_defaults(),
        capture="hci", nrf_port=None, output_root="runs",
        external_business_probe=False, external_ecu_observation=False,
    ))

    def to_plan(self):
        if self.target is None:
            raise ValueError("先选择并核对一个目标")
        if self.case == "br-sdp" and self.sdp_uuid16 is None:
            raise ValueError("先为当前目标选择或手动确认SDP服务，不能沿用其他目标的服务")
        plan = Plan(case=self.case, adapter=self.adapter, target=self.target.address,
                    local_address=self.local_address if self.case.startswith("br-") else None,
                    characteristic=self.characteristic if self.case == "ble-read" else None,
                    sdp_uuid16=self.sdp_uuid16 if self.sdp_uuid16 is not None else 0x110B, **self.settings)
        plan.validate()
        return plan


def clean(value, limit=120):
    return safe_display_name(str(value), max_length=limit)


def banner(output=None):
    output = output or console
    title = Text("BL / BLE  DOS LAB", style="bold bright_cyan")
    title.append("   /   GUIDED WORKSPACE", style="dim")
    title.append("\n选择设备 · 审阅计划 · 留存证据")
    title.append(f"\nv{__version__}  ·  单 dongle HCI  /  可选 nRF52840 BLE 空口", style="dim")
    output.print(Panel(title, box=box.ROUNDED, border_style="cyan", padding=(1, 2)))


def ask_text(label, default=None, validator=None, choices=None, options=None, history=True):
    """All fields accept b/back and q/cancel; invalid values stay at the field."""
    while True:
        try:
            value = Prompt.ask(Text(label), default=default, console=console,
                               options=options, history=history).strip()
        except EOFError as exc:
            raise Cancel() from exc
        if value.lower() in ("b", "back"):
            raise Back()
        if value.lower() in ("q", "cancel"):
            raise Cancel()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            console.print("[yellow]输入含控制字符；请重新输入，或重新安装后使用方向键编辑。[/yellow]")
            continue
        if choices and value.lower() not in choices:
            console.print(Text("请选择 " + " / ".join(choices), style="yellow"))
            continue
        try:
            if validator:
                validator(value)
            return value.lower() if choices else value
        except (ValueError, TypeError, OverflowError) as exc:
            console.print(Text(f"输入需要调整：{exc}", style="yellow"))


def ask_choice(label, options, default=None):
    """A labelled menu in a TTY; exact keys remain usable in simple terminals."""
    return ask_text(label, default=default, choices=tuple(key for key, _ in options), options=options)


def ask_number(label, default, minimum, maximum, integer=False):
    def validate(value):
        number = int(value) if integer else float(value)
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"范围 {minimum:g}–{maximum:g}" + ("，只接受整数" if integer else ""))
    value = ask_text(label, default=str(default), validator=validate)
    return int(value) if integer else float(value)


def ask_yes(label, default=False):
    return ask_choice(label, [("n", "否 / 未安排"), ("y", "是 / 已安排")], default="y" if default else "n") == "y"


def _mac(value):
    if not MAC.fullmatch(value) or value.upper() in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
        raise ValueError("输入完整单设备地址，例如 02:00:00:00:00:01")


def render_stepper(index):
    text = Text()
    for i, label in enumerate(STEPS):
        if i:
            text.append("  ›  ", style="dim")
        text.append(f"{i+1:02} {label}", style="bold bright_cyan" if i == index else "green" if i < index else "dim")
    return Panel(text, border_style="dim", padding=(0, 1))


def _section(index, draft):
    console.print(render_stepper(index))
    line = Text(f"{CASES[draft.case][1]}  ·  {draft.adapter}", style="dim")
    if draft.target:
        line.append(f"  ·  {draft.target.address}")
    console.print(line)
    console.print("[dim]菜单 ↑↓/回车 · 输入框 ←→/Home/End/删除 · b返回 · q首页 · 历史仅在内存[/dim]")


def diagnostics_table(data):
    table = Table(box=box.SIMPLE, expand=True)
    table.add_column("检查项", style="cyan", no_wrap=True)
    table.add_column("状态 / 下一步")
    table.add_row("系统", Text(clean(data["platform"])))
    table.add_row("Python", str(data["python"]))
    table.add_row("Bluetooth", Text(str(data["bluetooth_service"])))
    table.add_row("控制器", Text(", ".join(a["adapter"] for a in data["adapters"]) or "未发现；虚拟机先直通 USB"))
    table.add_row("HCI 权限", "已缓存" if data["sudo_noninteractive"] else "可在最终确认页刷新 sudo；不阻断设备选择")
    table.add_row("nRF 串口", Text(", ".join(data["nrf_ports"]) or "未发现（可选，不影响单 dongle）"))
    table.add_row("初步环境", "可继续配置；运行前仍会复核" if data["ready_for_live"] else "处理设备/依赖后选择 r 重新检测")
    return table


def show_doctor(data):
    banner()
    console.print(diagnostics_table(data))
    table = Table("工具", "路径", box=box.SIMPLE)
    for name, location in data["tools"].items():
        table.add_row(name, Text(clean(location or "未安装", 180)))
    console.print(table)
    console.print("[dim]诊断不扫描、不连接目标，也不修改系统权限。[/dim]")


def filter_devices(devices, query=""):
    query = query.strip().casefold()
    return [d for d in devices if not query or query in d.address.casefold() or query in clean(d.display_name).casefold()]


def render_device_table(devices, query="", page=0, page_size=8):
    visible = filter_devices(devices, query)
    table = Table(box=box.SIMPLE_HEAVY, expand=True, title=f"本次扫描候选  ·  {len(visible)} 项")
    table.add_column("#", width=3, no_wrap=True, style="cyan")
    table.add_column("设备名称", ratio=2, overflow="ellipsis")
    table.add_column("完整地址", width=17, no_wrap=True)
    table.add_column("RSSI", width=7, no_wrap=True, justify="right")
    wide = console.width >= 90
    if wide:
        table.add_column("地址类型", width=9)
        table.add_column("已配对", width=6)
    start = page * page_size
    for index, device in enumerate(visible[start:start+page_size], start+1):
        row = [str(index), Text(clean(device.display_name or "未命名", 80)), Text(device.address),
               "—" if device.rssi is None else f"{device.rssi} dBm"]
        if wide:
            row.extend([Text(clean(device.address_type or "unknown", 12)), "是" if device.paired else "否" if device.paired is not None else "—"])
        table.add_row(*row)
    if not visible:
        table.add_row("—", "本窗口未收到匹配的新发现记录", "", "", *(["", ""] if wide else []))
    return table


def _scan_snapshot(transport, adapter, seconds):
    with console.status(Text(f"{transport.upper()} 扫描 {seconds}s；不配对、不做GATT读写或测试负载"), spinner="dots") as progress:
        def update(elapsed, count):
            progress.update(Text(f"扫描中  {min(elapsed, seconds):.1f}/{seconds}s  ·  当前观察 {count} 项"))
        return scan_devices(transport, adapter=adapter, seconds=seconds, on_progress=update)


def show_scan_result(result):
    console.print(Panel(Text(f"{result.requested_transport.upper()} / {result.adapter}  ·  {result.status}\n"
                             f"观察窗口 {result.duration:.1f}s  ·  本次候选 {len(result.devices)}  ·  清理 {result.cleanup_status}"), border_style="cyan"))
    for warning in result.warnings:
        console.print(Text(clean(warning, 240), style="yellow"))
    console.print(render_device_table(result.devices, page_size=max(1, len(result.devices))))
    console.print("[dim]只展示本窗口观察；发现不是车端身份或传输承载证明，不自动连接或测试。[/dim]")


def pick_target(transport, adapter, current=None):
    """Select from a frozen snapshot or manually enter one MAC, with no connection."""
    seconds, result, query, page = 5, None, "", 0
    while True:
        if result is None:
            if current:
                console.print(Panel(Text(f"当前目标  {current.address}\n{clean(current.display_name)}  ·  {current.source}"), border_style="cyan"))
            console.print("s 扫描后选择 · m 手动地址" + (" · k 保留当前目标" if current else ""))
            choices = [("s", "扫描附近设备并从列表选择"), ("m", "手动填写设备地址")]
            if current:
                choices.append(("k", "保留当前目标 " + current.address))
            choice = ask_choice("选择目标方式", choices, default="k" if current else "s")
            if choice == "k":
                return current
            if choice == "m":
                address = ask_text("目标 MAC", default=current.address if current else None, validator=_mac)
                return TargetSelection(address.upper(), requested_transport=transport)
            query, page = "", 0
            try:
                result = _scan_snapshot(transport, adapter, seconds)
            except KeyboardInterrupt:
                console.print("[yellow]扫描已取消，可重扫或手动输入。[/yellow]")
                continue
            except (RuntimeError, OSError, ValueError) as exc:
                console.print(Text(f"扫描未完成：{clean(exc, 240)}", style="yellow"))
                continue
            for warning in result.warnings:
                console.print(Text(clean(warning, 240), style="yellow"))
            if result.status != "completed":
                console.print(Text(f"本次状态 {result.status} / 清理 {result.cleanup_status}；未自动选择。", style="yellow"))
                result = None
                continue
        snapshot = list(result.devices)
        visible = filter_devices(snapshot, query)
        pages = max(1, math.ceil(len(visible)/8))
        page = min(page, pages-1)
        console.print(render_device_table(snapshot, query, page))
        console.print(Text(f"{transport.upper()}扫描期间观察；承载与车端身份待核实。页 {page+1}/{pages}", style="dim"))
        console.print("编号选择 · r重扫 · f筛选 · t时长 · m手动 · n/p翻页 · b返回选择方式 · q取消")
        try:
            options = [(str(i+1), f"{clean(d.display_name or '未命名', 35)}  {d.address}")
                       for i, d in enumerate(visible) if page*8 <= i < (page+1)*8]
            options.extend([("r", "重新扫描一次"), ("f", "筛选当前列表"), ("t", "调整下次扫描时长"),
                            ("m", "手动输入地址"), ("n", "下一页"), ("p", "上一页")])
            action = ask_choice("设备或操作", options, default="r" if not snapshot else options[0][0])
        except Back:
            result = None
            continue
        lower = action.lower()
        if lower == "t":
            seconds = ask_number("下次扫描秒数", seconds, 1, 15, integer=True)
            console.print(Text(f"已设置为 {seconds}s；按 r 才开始新的扫描。", style="dim"))
            continue
        if lower == "r":
            # Explicit refresh performs exactly one scan; no recursive/background refresh.
            try:
                result = _scan_snapshot(transport, adapter, seconds)
                for warning in result.warnings:
                    console.print(Text(clean(warning, 240), style="yellow"))
                if result.status != "completed":
                    console.print(Text(f"重扫未完成：{result.status} / {result.cleanup_status}", style="yellow"))
                    result = None
            except KeyboardInterrupt:
                console.print("[yellow]重扫已取消。[/yellow]")
                result = None
            except (RuntimeError, OSError, ValueError) as exc:
                console.print(Text(f"重扫未完成：{clean(exc, 240)}", style="yellow"))
                result = None
            query, page = "", 0
            continue
        if lower == "f":
            query, page = ask_text("按名称/地址筛选（空值清除）", default=""), 0
            continue
        if lower in ("n", "p"):
            page = max(0, min(pages-1, page + (1 if lower == "n" else -1)))
            continue
        if lower == "m":
            address = ask_text("目标 MAC", validator=_mac)
            return TargetSelection(address.upper(), requested_transport=transport)
        try:
            index = int(action)-1
            if not page*8 <= index < min(len(visible), (page+1)*8):
                raise ValueError()
            device = visible[index]
            _mac(device.address)
        except ValueError:
            console.print("[yellow]输入当前页有效编号，或选择上面的操作。[/yellow]")
            continue
        return TargetSelection(device.address.upper(), clean(device.display_name), "scan_observation",
                               device.address_type, transport, device.rssi, device.paired, list(device.services))


def review_panel(plan, target_info=None):
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("用例", Text(f"{CASES[plan.case][0]} · {CASES[plan.case][2]}"))
    table.add_row("适配器", Text(plan.adapter + (f" / {plan.local_address}" if plan.local_address else "")))
    table.add_row("完整目标", Text(plan.target, style="bold"))
    if target_info:
        table.add_row("发现来源", Text(f"{clean(target_info.display_name)} · {target_info.source}"))
    if plan.case in ("br-sdp", "ble-read"):
        table.add_row("负载预算", f"{plan.rate:g} req/s · {plan.duration:g}s · ≤{plan.max_ops}次；批准上限 {plan.approved_rate:g}")
    else:
        table.add_row("循环预算", f"{plan.cycles}轮 · 保持 {plan.hold:g}s · 间隔 {plan.gap:g}s")
    if plan.case == "ble-read":
        table.add_row("只读特征", Text(plan.characteristic))
    if plan.case == "br-sdp":
        table.add_row("服务 UUID", f"0x{plan.sdp_uuid16:04X}")
    table.add_row("观察窗口", f"基线 {plan.baseline:g}s / 恢复 {plan.recovery:g}s / 墙钟预算 {plan.wall_budget}s")
    table.add_row("抓包", Text("HCI + nRF BLE空口 / " + plan.nrf_port if plan.capture == "hci+nrf" else "单 dongle HCI（无独立空口）"))
    table.add_row("外部证据", f"业务 {'已声明' if plan.external_business_probe else '未提供'} / ECU {'已声明' if plan.external_ecu_observation else '未提供'}")
    table.add_row("保存位置", Text(clean(plan.output_root, 180)))
    return Panel(table, title="PLAN / 最终计划", border_style="cyan", padding=(1, 2))


def dashboard(phase, path, elapsed, plan):
    content = Table.grid(expand=True, padding=(0, 1))
    content.add_column()
    content.add_row(Text(PHASE_LABELS.get(phase, phase), style="bold bright_cyan"))
    phase_line = Text()
    active = PHASES.index(phase) if phase in PHASES else -1
    for i, name in enumerate(PHASES):
        phase_line.append(("● " if i <= active else "○ ") + PHASE_LABELS[name] + "  ",
                          style="bold cyan" if i == active else "green" if i < active else "dim")
    content.add_row(phase_line)
    content.add_row(Text(f"{CASES[plan.case][0]} · {plan.adapter} · {plan.target}"))
    budget = f"{plan.rate:g} req/s · {plan.duration:g}s · ≤{plan.max_ops}次" if plan.case in ("br-sdp", "ble-read") else f"{plan.cycles}轮 · 保持{plan.hold:g}s / 间隔{plan.gap:g}s"
    content.add_row(Text(budget + "   /   " + plan.capture))
    content.add_row(ProgressBar(total=plan.wall_budget, completed=min(elapsed, plan.wall_budget), complete_style="cyan", finished_style="green"))
    content.add_row(Text(f"墙钟预算用时 {elapsed:.1f}/{plan.wall_budget}s（不是完成率/ETA） · Ctrl+C停止", style="dim"))
    content.add_row(Text(str(path), style="dim", overflow="fold"))
    return Panel(content, title="SESSION / 实时状态", border_style="cyan", padding=(1, 2))


def execute(plan):
    banner()
    if console.is_terminal:
        with Live(dashboard("PREPARE", "待创建", 0, plan), console=console, refresh_per_second=5) as live:
            path, status = run_plan(plan, lambda phase, path, elapsed: live.update(dashboard(phase, path, elapsed, plan)))
    else:
        seen = set()
        def update(phase, path, elapsed):
            if phase not in seen:
                console.print(Text(f"{phase:14} {elapsed:6.1f}s  {path}"))
                seen.add(phase)
        path, status = run_plan(plan, update)
    sealed = (path / "manifest.json").is_file()
    text = Text(f"运行状态  {status}\n", style="bold green" if status == "completed" else "bold yellow")
    text.append(f"证据目录  {path}\n报告      {path / 'report.md'}\n")
    text.append("已封存；可使用 verify 复核完整性。" if sealed else "未封存/部分记录，请保留目录并检查 UNSEALED 标记。", style="green" if sealed else "yellow")
    text.append("\n执行完成不等于车辆合规PASS。", style="dim")
    console.print(Panel(text, border_style="green" if status == "completed" else "yellow", title="EVIDENCE / 运行结果"))
    return 0 if status == "completed" else 130 if status == "interrupted" else 1


def demo(output):
    banner()
    plan = Plan(case="br-sdp", adapter="hci0", target="02:00:00:00:00:01", local_address="02:00:00:00:00:02")
    console.print(Panel("[bold yellow]SIMULATION / 合成演示[/bold yellow]\n不扫描、不连接、不请求sudo；不生成伪造的真实pcap。", border_style="yellow"))
    path = create_demo(Path(output))
    console.print(dashboard("SIMULATION_COMPLETE", path, 0, plan))
    console.print(Text(f"报告：{path / 'report.md'}"))
    console.print(Text(f"校验：./bt-dos verify '{path}'"))
    return path


def _environment(info):
    while True:
        console.print(diagnostics_table(info))
        value = ask_choice("环境检查后的操作", [("n", "继续配置"), ("r", "重新检测环境")], default="n")
        if value.lower() == "r":
            with console.status("重新检查本机环境…"):
                info = diagnose()
            continue
        if info["ready_for_live"]:
            return info
        console.print("[yellow]处理控制器/系统依赖后按 r 刷新，或 q 返回首页。[/yellow]")


def _case_adapter(draft, info):
    table = Table("#", "传输", "用例", "侧重点", box=box.SIMPLE_HEAVY, expand=True)
    hints = ("服务发现处理", "动态通道释放，非ACL循环", "非控制性短只读特征", "串行连接回收，非槽位耗尽")
    for i, ((name, details), hint) in enumerate(zip(CASES.items(), hints), 1):
        table.add_row(str(i), details[1], details[0]+" "+details[2], hint)
    console.print(table)
    case_options = [(str(i), f"{details[1]} · {details[2]}") for i, details in enumerate(CASES.values(), 1)]
    case = list(CASES)[int(ask_choice("选择测试场景", case_options, str(list(CASES).index(draft.case)+1)))-1]
    devices = info["adapters"]
    table = Table("#", "控制器", "本机地址", "名称", box=box.SIMPLE, expand=True)
    for i, item in enumerate(devices, 1):
        table.add_row(str(i), item["adapter"], Text(item.get("address") or "未核实"), Text(clean(item.get("name", "—"))))
    console.print(table)
    default = next((i for i, item in enumerate(devices, 1) if item["adapter"] == draft.adapter), 1)
    controller_options = [(str(i), f"{item['adapter']} · {item.get('address') or '地址待核实'} · {clean(item.get('name', 'USB蓝牙控制器'))}")
                          for i, item in enumerate(devices, 1)]
    selected = devices[int(ask_choice("选择发起测试的USB控制器", controller_options, str(default)))-1]
    changed = selected["adapter"] != draft.adapter or CASES[case][1] != CASES[draft.case][1]
    if changed:
        if draft.target:
            console.print("[yellow]适配器或传输已改变，旧目标与特征已清除，请重新核对。[/yellow]")
        draft.target = None
        draft.characteristic = None
        draft.sdp_uuid16 = None
        draft.probe_result = None
    draft.case, draft.adapter, draft.local_address = case, selected["adapter"], selected.get("address")
    if case.startswith("br-") and not draft.local_address:
        raise ValueError("控制器地址尚未核实，请返回环境页刷新BlueZ状态")
    if case.startswith("br-"):
        draft.settings.update(capture="hci", nrf_port=None)


def probe_device(*args, **kwargs):
    # Lazy import keeps offline input/demo paths independent of probe dependencies.
    from .probe import probe_device as collect
    return collect(*args, **kwargs)


def target_panel(target, adapter):
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="cyan")
    rows.add_column()
    rows.add_row("设备", Text(clean(target.display_name)))
    rows.add_row("完整地址", Text(target.address, style="bold"))
    rows.add_row("控制器 / 来源", Text(f"{adapter} / {target.source}"))
    rows.add_row("地址类型 / RSSI", Text(f"{target.address_type} / {target.rssi if target.rssi is not None else '未知'}"))
    rows.add_row("已配对", "是" if target.paired else "否" if target.paired is not None else "未知")
    if target.known_services:
        rows.add_row("BlueZ已知UUID提示", Text("\n".join(clean(value) for value in target.known_services[:20])))
    rows.add_row("状态", "以上为发现元数据，不证明车端身份、当前承载或完整服务清单。")
    return Panel(rows, title="DEVICE / 已选设备", border_style="cyan")


def probe_panel(result):
    summary = Text(f"{result.transport.upper()} · {result.adapter} · {result.target}\n"
                   f"状态 {result.status} / 清理 {result.cleanup_status} / 用时 {result.duration:.1f}s\n"
                   "服务探测发生在正式抓包基线前，不计作测试负载或正式流量证据。")
    console.print(Panel(summary, title="CAPABILITIES / 设备服务快照", border_style="cyan"))
    for warning in result.warnings:
        console.print(Text(clean(warning, 400), style="yellow"))
    metadata = getattr(result, "metadata", {})
    if metadata:
        console.print(Text("BlueZ元数据  " + " · ".join(
            f"{clean(key, 30)}={clean(value, 100)}" for key, value in metadata.items() if key != "UUIDs")))
    services = Table("服务名称", "服务UUID", "记录 / PSM / RFCOMM", box=box.SIMPLE, expand=True)
    for service in result.services[:20]:
        record = getattr(service, "record_handle", None)
        details = f"{record if record is not None else '—'} / {getattr(service, 'psm', None) or '—'} / {getattr(service, 'channel', None) or '—'}"
        services.add_row(Text(clean(service.name or "未命名")), Text(clean(service.uuid)), Text(details))
    if result.services:
        console.print(services)
        if len(result.services) > 20:
            console.print(Text(f"服务共{len(result.services)}项；摘要仅展示前20项，选择页支持筛选和翻页。", style="dim"))
    if result.characteristics:
        table = Table("特征名称", "特征UUID / 当前句柄", "属性", box=box.SIMPLE, expand=True)
        for char in result.characteristics[:20]:
            handle = f"0x{char.handle:04X}" if char.handle is not None else "句柄未知"
            table.add_row(Text(clean(char.description or "未命名", 50)), Text(f"{char.uuid}\n{handle}"),
                          Text(", ".join(char.properties)))
        console.print(table)
        if len(result.characteristics) > 20:
            console.print(Text(f"特征共{len(result.characteristics)}项；摘要仅展示前20项，选择页支持筛选和翻页。", style="dim"))


def _choose_catalog(options, title):
    """Bound the visible menu; filtering never triggers another radio operation."""
    page, query = 0, ""
    while True:
        filtered = [(value, label) for value, label in options
                    if query.casefold() in label.casefold() or query.casefold() in value.casefold()]
        pages = max(1, math.ceil(len(filtered)/8))
        page = min(page, pages-1)
        visible = filtered[page*8:(page+1)*8]
        labels = [(str(i), label) for i, (_, label) in enumerate(visible, 1)]
        labels.extend([("f", "按名称/UUID筛选当前快照"), ("n", "下一页"), ("p", "上一页")])
        console.print(Text(f"{len(filtered)}项 · 页{page+1}/{pages} · 选择不重新探测", style="dim"))
        action = ask_choice(title, labels, default="1" if visible else "f")
        if action == "f":
            query, page = ask_text("服务/特征筛选词（空值清除）", default=""), 0
        elif action in ("n", "p"):
            page = max(0, min(pages-1, page+(1 if action == "n" else -1)))
        else:
            return visible[int(action)-1][0]


def _probe_matches(draft):
    result = draft.probe_result
    return (result is not None and result.target.upper() == draft.target.address.upper()
            and result.adapter == draft.adapter
            and result.transport == ("bredr" if draft.case.startswith("br-") else "le"))


def _manual_service(draft):
    if draft.case == "ble-read":
        console.print("[dim]高级手动输入：UUID须唯一；句柄可能随数据库变化，需现场重新核对。read属性不保证无业务副作用。[/dim]")
        def char_valid(value):
            Plan(case="ble-read", adapter=draft.adapter, target=draft.target.address, characteristic=value).validate()
        draft.characteristic = ask_text("特征 UUID / 0xHANDLE", default=draft.characteristic, validator=char_valid)
    elif draft.case == "br-sdp":
        console.print("[dim]未探测时的0x110B仅为手动输入示例，不表示目标支持该服务。[/dim]")
        def uuid_valid(value):
            if not 0 <= int(value, 0) <= 65535:
                raise ValueError("UUID16 范围 0x0000..0xFFFF")
        default = f"0x{draft.sdp_uuid16:04x}" if draft.sdp_uuid16 is not None else None
        draft.sdp_uuid16 = int(ask_text("SDP服务UUID16（核对服务清单）", default=default, validator=uuid_valid), 0)


def _service_parameters(draft):
    """Explicit service discovery, then select metadata; never read values here."""
    console.print(target_panel(draft.target, draft.adapter))
    needs_service = draft.case in ("br-sdp", "ble-read")
    while True:
        result = draft.probe_result if _probe_matches(draft) else None
        selectable = []
        if result is not None:
            probe_panel(result)
            if result.status == "completed" and result.cleanup_status in ("released", "disconnected", "closed", "process_exited"):
                if draft.case == "br-sdp":
                    selectable = [(f"0x{s.uuid16:04X}", f"{clean(s.name or 'SDP服务', 50)} · 0x{s.uuid16:04X}")
                                  for s in result.services if s.selectable and s.uuid16 is not None]
                    # Distinct records may share one service class; one UUID option is enough.
                    selectable = list(dict(selectable).items())
                elif draft.case == "ble-read":
                    counts = Counter(char.uuid.lower() for char in result.characteristics)
                    selectable = [(char.uuid, f"{clean(char.description or '可读特征', 35)} · {char.uuid}")
                                  for char in result.characteristics if char.readable and counts[char.uuid.lower()] == 1]
                    if any(char.readable and counts[char.uuid.lower()] > 1 for char in result.characteristics):
                        console.print("[yellow]重复UUID的特征仅展示，不自动取第一项；需高级手动核对或调整设备配置。[/yellow]")
        options = [("p", "获取/刷新服务信息（将对这个目标发起限时服务探测）")]
        if selectable:
            options.insert(0, ("s", "从已探测的服务/可读特征列表选择"))
        if needs_service:
            options.append(("m", "高级：手动输入目标服务UUID / 特征"))
            selected = draft.characteristic if draft.case == "ble-read" else draft.sdp_uuid16
            if selected is not None:
                options.append(("k", "保留已核对的服务/特征选择"))
            default = "s" if selectable else "k" if selected is not None else "p"
        else:
            options.append(("k", "继续：本用例不需要选择业务服务"))
            default = "k"
        action = ask_choice("目标服务与参数来源", options, default=default)
        if action == "k":
            return
        if action == "m":
            _manual_service(draft)
            return
        if action == "s":
            try:
                value = _choose_catalog(selectable, "选择本次请求的服务" if draft.case == "br-sdp" else "选择本次请求的只读特征")
            except Back:
                continue
            if draft.case == "ble-read":
                if not ask_yes("已由厂家/测试计划确认该特征可重复读取且无控制副作用？", default=False):
                    continue
                draft.characteristic = value
            else:
                draft.sdp_uuid16 = int(value, 16)
            return
        transport = "bredr" if draft.case.startswith("br-") else "le"
        console.print(Panel(Text(f"目标 {draft.target.address} / {draft.adapter} / {transport.upper()}\n"
            "本次仅调用一次限时服务探测，不启动测试负载；底层库/BlueZ可能重试。\n"
            "SDP会产生服务发现请求；BLE会连接并枚举服务，不读取特征值、不配对、不写入或订阅。\n"
            "请使用独立测试控制器并停止其他程序连接此目标；共享连接存在竞态，结束断开可能影响同期客户端。"),
            border_style="yellow", title="主动探测确认"))
        if not ask_yes("确认现在对上述单个目标获取服务信息？", default=False):
            continue
        with console.status("获取设备服务信息…；连接预算及额外清理时间见使用指南"):
            draft.probe_result = probe_device(transport, draft.target.address, adapter=draft.adapter,
                                              local_address=draft.local_address if transport == "bredr" else None)
        # Failed/partial snapshots are shown, never silently used as complete lists.


def _parameters(draft):
    _service_parameters(draft)
    console.print(Panel("1  默认计划  ·  1req/s / 10s，5轮，基线5s / 恢复10s\n2  快速预检  ·  1req/s / 5s，3轮，基线2s / 恢复5s\n3  自定义    ·  保留现有值，逐项校验；不扩大工具上限", border_style="blue", title="预算预设"))
    console.print("[dim]请求用例使用请求率/窗口；循环用例使用轮数/保持/间隔，不同时叠加。[/dim]")
    default = {"default": "1", "quick": "2", "custom": "3"}[draft.preset]
    preset = ask_choice("选择测试预算", [("1", "默认低负载 · 请求1次/秒、10秒 / 循环5轮"),
                        ("2", "快速预检 · 请求1次/秒、5秒 / 循环3轮"),
                        ("3", "自定义 · 逐项调整并校验")], default=default)
    draft.preset = {"1": "default", "2": "quick", "3": "custom"}[preset]
    settings = draft.settings
    if preset != "3":
        settings.update(budget_defaults(quick=preset == "2"))
        return
    if draft.case in ("br-sdp", "ble-read"):
        settings["approved_rate"] = ask_number("试验计划批准上限 req/s（参数声明）", settings["approved_rate"], 0.1, 20)
        settings["rate"] = ask_number("本次请求率 req/s", min(settings["rate"], settings["approved_rate"]), 0.1, min(20, settings["approved_rate"]))
        settings["duration"] = ask_number("请求窗口秒", settings["duration"], 1, 60)
        settings["max_ops"] = ask_number("最大操作数", settings["max_ops"], 1, 400, integer=True)
    else:
        settings["cycles"] = ask_number("循环次数", settings["cycles"], 1, 10, integer=True)
        settings["hold"] = ask_number("每轮保持秒", settings["hold"], 1, 5)
        settings["gap"] = ask_number("每轮间隔秒", settings["gap"], 3, 30)
    settings["baseline"] = ask_number("基线观察秒", settings["baseline"], 0, 60)
    settings["recovery"] = ask_number("恢复观察秒", settings["recovery"], 0, 120)


def _evidence(draft, info):
    console.print(Panel("1  单 dongle HCI  ·  最少硬件，记录本机主机/控制器流量\n2  HCI + nRF52840 ·  额外BLE空口，需嗅探固件及nrfutil", border_style="blue"))
    options = ("1", "2") if draft.case.startswith("ble-") else ("1",)
    default = "2" if draft.settings["capture"] == "hci+nrf" and "2" in options else "1"
    while True:
        labels = {"1": "单USB dongle：本机HCI抓包（必选基础证据）", "2": "HCI + nRF52840：额外BLE空口采集"}
        choice = ask_choice("选择抓包硬件", [(key, labels[key]) for key in options], default=default)
        if choice != "2" or info["tools"].get("nrfutil"):
            break
        console.print("[yellow]尚未安装nrfutil；可选1继续，或按docs/hardware.md准备后再配置。[/yellow]")
    draft.settings["capture"] = "hci+nrf" if choice == "2" else "hci"
    if choice == "2":
        def port_valid(value):
            if not re.fullmatch(r"/dev/(?:ttyACM|ttyUSB)[0-9]+", value):
                raise ValueError("需要 /dev/ttyACM0 或 /dev/ttyUSB0")
        default_port = draft.settings["nrf_port"] or (info["nrf_ports"][0] if info["nrf_ports"] else "/dev/ttyACM0")
        draft.settings["nrf_port"] = ask_text("已刷嗅探固件的串口", default=default_port, validator=port_valid)
    else:
        draft.settings["nrf_port"] = None
    draft.settings["external_business_probe"] = ask_yes("另有独立业务探针并留存结果？", draft.settings["external_business_probe"])
    draft.settings["external_ecu_observation"] = ask_yes("另有ECU/车辆状态日志？", draft.settings["external_ecu_observation"])
    def output_valid(value):
        if not value or "\x00" in value:
            raise ValueError("填写有效目录，默认runs；真实证据不提交Git")
    draft.settings["output_root"] = ask_text("证据根目录", default=draft.settings["output_root"], validator=output_valid)


def _ensure_capture_permission():
    if getattr(os, "geteuid", lambda: 1000)() == 0 or command(["sudo", "-n", "true"], timeout=2)["code"] == 0:
        return True
    console.print(Panel("HCI抓包需要sudo认证。可在当前终端刷新；密码由系统sudo读取，本工具不保存。", border_style="yellow"))
    action = ask_choice("采集权限准备", [("a", "现在通过系统sudo认证（密码不保存）")], default="a")
    if action == "a":
        from .capture import system_binary
        result = subprocess.run([system_binary("sudo"), "-v"], check=False)
        if result.returncode != 0:
            console.print("[yellow]认证未完成，尚未启动采集或负载。[/yellow]")
            return False
    return command(["sudo", "-n", "true"], timeout=2)["code"] == 0


def run_wizard(info=None, draft=None):
    draft = draft or WizardDraft()
    if sys.platform != "linux":
        console.print("[yellow]实时向导面向Linux；此处可运行演示、诊断和报告。[/yellow]")
        return 1
    if info is None:
        with console.status("检查本机环境与适配器…"):
            info = diagnose()
    index, review_after_edit = 0, False
    while True:
        _section(index, draft)
        try:
            if index == 0:
                info = _environment(info)
            elif index == 1:
                _case_adapter(draft, info)
                review_after_edit = False
            elif index == 2:
                old_address = draft.target.address if draft.target else None
                draft.target = pick_target("bredr" if draft.case.startswith("br-") else "le", draft.adapter, draft.target)
                if old_address != draft.target.address:
                    draft.characteristic = None
                    draft.sdp_uuid16 = None
                    draft.probe_result = None
                    if draft.case in ("br-sdp", "ble-read"):
                        review_after_edit = False
                        console.print("[dim]目标已改变，下一步重新确认该目标的服务或只读特征。[/dim]")
            elif index == 3:
                _parameters(draft)
            elif index == 4:
                _evidence(draft, info)
            else:
                plan = draft.to_plan()
                console.print(review_panel(plan, draft.target))
                console.print("r 运行 · 1 修改用例/适配器 · 2 修改目标 · 3 修改参数 · 4 修改证据 · j 查看JSON · q取消")
                action = ask_choice("下一步", [("r", "核对完整目标地址后运行"), ("1", "修改用例和USB控制器"),
                    ("2", "修改目标设备"), ("3", "修改服务和测试参数"), ("4", "修改抓包与证据配置"),
                    ("j", "查看完整JSON计划")], default="r")
                if action == "j":
                    console.print_json(json.dumps(plan.to_dict()))
                    continue
                if action in ("1", "2", "3", "4"):
                    index, review_after_edit = int(action), True
                    continue
                console.print("[yellow]核对静止隔离台架、非控制性负载、独立停止措施和批准预算。发现候选不证明车端身份。[/yellow]")
                confirmation = ask_text("输入完整目标MAC确认（其他输入取消本次启动）", default="CANCEL", history=False)
                if confirmation.upper() != plan.target.upper():
                    console.print("[yellow]未确认，保持计划并返回复核页；没有启动负载。[/yellow]")
                    continue
                if _ensure_capture_permission():
                    return execute(plan)
                continue
            index = 5 if review_after_edit else index+1
        except Back:
            if index == 0:
                return 0
            index = max(0, index-1)
            review_after_edit = False
        except Cancel:
            console.print("[dim]已返回首页，计划保留于本次会话；没有自动启动负载。[/dim]")
            return 0
        except (ValueError, OSError, RuntimeError) as exc:
            console.print(Text(f"此步骤需要处理：{clean(exc, 240)}", style="yellow"))


def wizard():
    draft = WizardDraft()
    while True:
        banner()
        table = Table("", "入口", "说明", box=box.SIMPLE_HEAVY, expand=True)
        for key, label, hint in (("1", "快速演示", "不接触硬件，熟悉报告与界面"), ("2", "环境诊断", "检查USB、BlueZ、采集权限"), ("3", "引导式测试", "分步配置，可扫描选目标、返回编辑"), ("4", "使用文档", "安装、硬件与证据边界"), ("0", "退出", "")):
            table.add_row(Text(key, style="cyan"), label, Text(hint, style="dim"))
        console.print(table)
        try:
            choice = ask_choice("选择入口", [("1", "离线演示：不使用无线硬件"), ("2", "检查环境和USB设备"),
                ("3", "引导式测试：扫描目标、选择服务、复核执行"), ("4", "查看使用文档位置"), ("0", "退出程序")], default="1")
            if choice == "0":
                Prompt.clear_history()
                return 0
            if choice == "1":
                demo("runs")
            elif choice == "2":
                with console.status("读取本机诊断信息…"):
                    info = diagnose()
                show_doctor(info)
            elif choice == "3":
                run_wizard(draft=draft)
            else:
                console.print(Panel("README.md\ndocs/usage.md\ndocs/hardware.md\ndocs/evidence.md", title="文档导航", border_style="cyan"))
            console.rule("返回首页")
        except (Cancel, Back, EOFError):
            Prompt.clear_history()
            return 0
        except KeyboardInterrupt:
            Prompt.clear_history()
            console.print("\n[dim]已退出向导。[/dim]")
            return 130
        except (OSError, ValueError, RuntimeError) as exc:
            console.print(Text(f"本次操作未完成：{clean(exc, 240)}", style="yellow"))
