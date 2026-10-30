# bl-ble-dos

**BR/EDR 与 Bluetooth LE 的有界协议负载、连接生命周期观察和可复核流量证据。**

[English](README.en.md) · [安装](docs/installation.md) · [协议依据](docs/protocols.md) · [使用指南](docs/usage.md) · [验证记录](docs/validation.md)

面向 Linux / BlueZ 的命令行工具与 Rich + prompt_toolkit 终端向导。使用普通 USB 蓝牙适配器执行正常协议交互，以独立 supervisor 管理采集、负载、超时和清理；每次运行保存独立证据包，便于区分配置、执行结果和实际线上流量。

**开发预览：0.3.0 尚未发布。** 已有指定软硬件组合的执行验证，不构成通用适配器、目标或协议栈兼容保证；范围和限制见[验证记录](docs/validation.md)。离线合成演示与真实运行分开标记。

## 终端演示

直接展示 Bash 会话，不使用演示图片。下面是实际离线运行的**路径归一化节选**：省略完整 Rich 界面输出，状态从生成的 `result.json` 提取，不是实测流量或手绘终端。

`DEMO_ROOT` 替换为输出父目录（例如 `runs/demo`）；`RUN_DIR` 替换为 demo 打印的实际新建子目录。

```console
$ ./bt-dos --version
0.3.0

$ ./bt-dos demo --output DEMO_ROOT
# Rich output omitted; RUN_DIR is printed by demo.

$ grep -E '"simulation"|"status"' RUN_DIR/result.json
  "simulation": true,
  "status": "simulation_completed",

$ ls -1 RUN_DIR
events.jsonl
manifest.json
plan.json
report.md
result.json

$ ./bt-dos verify RUN_DIR
{
  "ok": true,
  "errors": []
}
```

## 测的是 BR/EDR，还是 BLE？

本项目同时支持 **BR/EDR（Bluetooth Classic，`br-*`）** 和 **Bluetooth LE / BLE（`ble-*`）**；“BL”不是这里使用的正式协议名称。分类依据是本工具实际使用的传输路径、API 和可核对的 HCI 记录，不是设备名称、设备类别或某个 UUID。

| 用例 | 实际路径 | 观察范围与解释边界 |
| --- | --- | --- |
| `br-sdp` | Linux Bluetooth L2CAP socket → BR/EDR SDP PSM `0x0001` → SDP ServiceSearch 请求 | 有界服务查询，校验响应、事务号；解析限额内遇 continuation 报告 `needs_continuation`，详见协议说明 |
| `br-channel` | 同一 BR/EDR SDP PSM 的 L2CAP 信道建立、保持、关闭 | 不发送业务负载；不强制每轮拆建 ACL，也不直接证明目标内存已回收 |
| `ble-read` | `BleakScanner` / `BleakClient` 的 LE 路径 → GATT / ATT 特征读取 | 工具不发起写操作；具体特征的读取副作用仍须核对 |
| `ble-cycle` | 同一 LE 路径上的顺序连接、保持、断开 | 有界生命周期观察，不是并发连接槽位耗尽，也不保证每次 API 调用等于一次空口尝试 |
| `ble-write-command` | 同一 LE 路径 → 专用 fixture 特征的无响应写 | 固定服务/特征及 `BTDS + uint32` 的 8 字节载荷；非只读，无任意载荷输入 |

这里的 BLE 分类描述**当前实现选择的 LE 路径**，不是声称 GATT 只能使用 LE 承载。双模目标可同时具有两类能力，发现窗口中的候选也不自动成为传输承载证明。[实现路径、协议依据与证据判读 →](docs/protocols.md)

## 技术特性

- **可复核的有界调度**：计划校验速率、窗口、操作数和循环预算；请求窗口截止后停止发起新 API 操作，已发起事务与清理另受超时约束。请求率不是实测吞吐或空口 PPS。
- **分离发现、服务探测与正式运行**：扫描必须显式发起，候选表冻结后可筛选、分页；确认后才获取 SDP / GATT 服务快照。变更目标或传输会清除不再适用的选择，避免沿用旧特征。
- **采集就绪后才进入负载流程**：supervisor 管理 `btmon`、worker、停止信号和进程清理；向导可在采集启动后留出目标唤醒窗口，关键采集失败不会继续负载。
- **诊断与运行环境留痕**：所选控制器核对和诊断复用同一枚举快照；证据记录工具/依赖版本、源码哈希、提交及 dirty 标记，而不只依赖版本号。
- **普通用户执行，限定提权**：TUI 和 worker 不要求 root；采集及显式请求的可选 BLE 间隔 helper 使用限定的 sudo 路径。项目不自动扩展 sudoers 或设置 capabilities。
- **证据封存与失败状态分离**：计划、事件、worker 日志、采集、报告和 SHA-256 清单独立保存；未确认采集写入者退出时不生成完整性清单，强制清理不冒充成功。
- **适合终端操作，也保留脚本入口**：六步可回退向导、方向键菜单、可编辑字段和仅内存历史；`scan` / `inspect` 可输出 JSON，`run` 接受显式计划及完整目标确认。

请求类默认预算为 `10 req/s × 10 s`、最多 `100` 次；快速预检为 `1 req/s × 5 s`、最多 `5` 次。循环类采用轮数/保持/间隔预算。专用写入 fixture 使用独立预设 `100 cmd/s × 10 s`、最多 `1000` 次，不改变其他用例默认值。配置速率仍受 `approved_rate` 与用例硬上限约束；实际完成数可能更少。[全部预算、fixture 与连接参数 →](docs/usage.md)

## 快速安装

需要 Linux、Python 3.10+ 和 Bash；实际设备操作还需要 BlueZ 与可用控制器。Ubuntu / Kali 是安装脚本面向的环境。以普通用户运行：

```bash
git clone https://github.com/addxemmm/bl-ble-dos.git
cd bl-ble-dos
bash scripts/install.sh
./bt-dos demo --output runs/demo
# 复制 demo 输出的 verify 命令，或用实际新建目录替换 RUN_DIR
./bt-dos verify RUN_DIR
./bt-dos doctor
./bt-dos
```

默认安装检查系统、创建/复用项目 `.venv` 并联网安装 Python 依赖；**不运行 apt、不自动启动蓝牙服务、不扫描或连接设备**。如需显式安装系统依赖，使用 `bash scripts/install.sh --install-system`；不要用 `sudo` 启动整个向导。虚拟机需把 USB 蓝牙设备直通给 Linux，安装完成与软件自检通过不代表硬件或运行环境已就绪。

`demo --output` 指定父目录，每次在其下创建新目录；demo 不使用无线硬件或 sudo，也不伪造 HCI / PCAP 文件。无参数入口仅在交互终端开启向导，非交互输入/输出显示帮助。[依赖安装、权限、非交互选项与故障处理 →](docs/installation.md)

## 从复核到运行

1. **环境 → 用例/适配器 → 目标 → 参数 → 证据 → 复核。** 选择需要的传输和服务；扫描、服务探测与正式执行是不同操作，不因浏览页面自动发起负载。
2. **复核后确认完整目标 MAC，再检查采集权限。** 交互向导可调用系统 `sudo -v`；密码由 sudo 读取，应用不保存。
3. **真实 TTY 向导：采集就绪 → 等待目标准备（最多 60 秒）→ 基线 → 负载 → 恢复观察 → 停止采集与封存。** 操作员唤醒目标后按 Enter，或输入 `r` 后回车；`q` 后回车、EOF 或 Ctrl+C 可停止。确认仅表示操作员准备完成，不证明连接已建立；等待阶段不增加主动扫描、连接或 keepalive。额外等待不扩大 worker 的负载预算。
4. **失败后可保留计划，但不会自动重发。** 仅部分失败状态、清理明确完成且证据校验通过时，向导提供 `r` 返回复核、`q` 结束（默认）。再次运行仍须重新选择运行、确认完整 MAC、检查权限，并创建新证据目录；不是恢复旧运行或续计操作数。

唤醒等待仅用于真实 stdin/stdout TTY 的向导，也适用于 `BTDOS_SIMPLE_INPUT=1` 简易输入模式。CLI `run` 即使在 TTY 中也默认不增加这一步、不新增 stdin 读取；脚本应在启动前安排目标就绪。简易输入模式不支持方向键编辑。[完整操作流程与状态说明 →](docs/usage.md)

## 硬件、结果与证据

单个双模 USB 控制器可按所选用例执行 BR/EDR 或 LE 交互；`btmon` 提供**本机主机—控制器 HCI 视角，不是独立空口抓包**。可选 nRF52840 的 sniffer、LE HCI 控制器、Connectivity GATT fixture 是不同固件/角色，不应混用：Connectivity fixture 是目标外设，不是 Kali 的 `hciX` 控制器；同一块板不同时承担这些角色。[硬件角色与准备 →](docs/hardware.md)

每次运行目录通常包含 `plan.json`、`environment.json`、`events.jsonl`、`worker.jsonl`、`result.json`、`host-hci.btsnoop`、`report.md` 和 `manifest.json`；BLE 空口采集为可选 `air.pcap`。尚未启动 worker 的失败运行可没有 worker 日志。原始包和标识信息应保留在私有证据目录。

- `completed` 表示执行流程完成，不是目标业务可用性结论；本地无响应写提交成功也不等于目标已接收。
- `target_not_ready` 表示准备等待超时；取消为 `interrupted`。`needs_continuation`、`worker_failed` 等状态须结合报告解释。
- `cleanup_*` 表示清理存在问题；采集写入者退出未确认时保留 `UNSEALED.json`，不生成 `manifest.json`，不提供保留计划重试。
- `verify` 只验证文件与清单一致，不证明采集完整、空口到达、目标内部状态或业务无故障；缺少独立业务观察时保留证据缺口。

[证据格式与判读 →](docs/evidence.md) · [已完成验证及尚待确认范围 →](docs/validation.md)

## 文档与开发

| 文档 | 内容 |
| --- | --- |
| [安装指南](docs/installation.md) | 系统依赖、虚拟环境、权限及安装故障 |
| [协议路径](docs/protocols.md) | BR/EDR / LE 分类依据、用例实现与 HCI 判读 |
| [使用指南](docs/usage.md) | 向导、CLI、JSON 计划、预算和固定写入 fixture |
| [硬件指南](docs/hardware.md) | USB 直通、控制器能力及 nRF52840 三种角色 |
| [证据说明](docs/evidence.md) | 产物、状态、完整性校验与观察边界 |
| [验证记录](docs/validation.md) | 指定环境的实测结果与覆盖限制 |

```bash
bash scripts/install.sh --dev
.venv/bin/python -m unittest discover -s tests -v
```

[参与贡献](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [变更记录](CHANGELOG.md) · [MIT License](LICENSE)
