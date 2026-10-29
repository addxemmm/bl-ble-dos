# bl-ble-dos

**把蓝牙可用性测试做成有边界、可追溯、可复核的一次运行。**

![License: MIT](https://img.shields.io/badge/license-MIT-3b82f6)
![Runtime: Linux](https://img.shields.io/badge/runtime-Ubuntu%20%7C%20Kali-0f766e)
![Interface: Rich TUI](https://img.shields.io/badge/interface-Rich%20TUI-7c3aed)
![Status: development preview](https://img.shields.io/badge/status-development%20preview-f59e0b)

[English](README.en.md) · [使用指南](docs/usage.md) · [硬件边界](docs/hardware.md) · [证据与报告](docs/evidence.md) · [参与开发](CONTRIBUTING.md)

面向 Ubuntu / Kali 的 BR/EDR 与 Bluetooth LE 有界负载和可用性观察工具。一个 Bash 入口、一个 Rich 终端向导，使用常见 USB 双模蓝牙适配器发起正常协议交互，同时保存本机 HCI 流量；可另接已刷入 sniffer 固件的 nRF52840，补充 BLE 空口观察。

> **开发预览，0.1.0 尚未发布。** 徽章仅描述项目定位，不表示 CI、硬件兼容或认证结果。合成 demo 与真实硬件运行严格区分；尚无已验证的适配器/被测设备兼容性结论。Kali 实机验证状态以可复核的测试记录为准。

![Rich 终端界面合成预览：非实测结果](docs/assets/terminal-demo.svg)

## 为什么使用它

- **终端里完成流程**：环境检查 → 适配器/目标选择 → 计划确认 → 采集 → 运行 → 报告。
- **有界而非无限发送**：默认每秒 1 次请求；速率、持续时间、操作数和连接循环均设硬上限。
- **采集先于负载**：supervisor 等待 `btmon` 就绪后才启动 worker；关键采集失败会终止运行。
- **结论不越过证据**：本机请求成功不等于业务正常；缺少业务探针、ECU 观察或空口数据时明确记录缺口。
- **离线可演示、可复核**：`demo` 不接触硬件；运行目录包含计划快照、事件、报告和文件哈希清单。

## 快速开始

```bash
git clone https://github.com/addxemmm/bl-ble-dos.git
cd bl-ble-dos
chmod +x bt-dos scripts/install.sh
./scripts/install.sh
./bt-dos doctor
./bt-dos demo --output runs/demo
# 复制上一步输出的校验命令，或将 RUN_DIR 替换为实际运行目录
./bt-dos verify RUN_DIR
./bt-dos
```

`--output` 指定父目录，demo 在其下新建时间戳/随机子目录，并打印实际目录及可直接复制的校验命令。无参数在交互终端启动 Rich 向导，非交互输入/输出时显示帮助。先用 `demo` 熟悉输出，实际运行前按 `doctor` 提示准备 Linux、BlueZ、适配器和采集权限。安装脚本在项目内建立 `.venv` 并执行可编辑安装；不要求全局安装 Python 包。详见[安装与运行](docs/usage.md)。

## 四个测试场景

| 场景 | 交互范围 | 测试含义与边界 |
| --- | --- | --- |
| `br-sdp` | BR/EDR SDP 服务查询 | 有界服务发现请求；目标须提供相应服务 |
| `br-channel` | BR/EDR SDP PSM `0x0001` 的 L2CAP 信道循环 | 建立、保持、关闭信道，不发送业务负载；**不是** ACL 链路反复建立/释放测试 |
| `ble-read` | BLE GATT 特征读取 | 工具不发起写操作；读取副作用需厂家确认 |
| `ble-cycle` | BLE 顺序连接、保持、断开 | 有界连接生命周期观察；**不是**并发连接槽位耗尽 |

这些场景均为 **normal-protocol bounded workload**：不包含控制器漏洞注入、畸形底层帧、射频干扰或“任何蓝牙设备必定失效”的承诺。正常协议请求仍可能影响脆弱目标；应在目标隔离、停止条件和恢复手段已明确的实验环境运行。

默认请求速率为 `1 req/s`，最高 `20 req/s`，且配置速率不高于计划中的 `approved_rate`；请求型负载的 `duration` 最长 `60 s`，最多 `400` 次操作。信道/连接循环最多 `10` 次，保持 `1–5 s`，循环间隔 `3–30 s`，另有连接与监督超时预算；不保证整个实验在 60 秒内完成。限值是项目的运行边界，**不是法规阈值，也不是目标承受能力保证**。

## 命令速览

```text
./bt-dos                         # 向导，等价于 wizard
./bt-dos doctor [--json]         # 本机环境诊断
./bt-dos adapters                # 适配器枚举
./bt-dos scan --transport le --adapter hci0 --seconds 5
./bt-dos scan --transport bredr --adapter hci0 --seconds 5
./bt-dos ble-info --target TARGET --adapter hci0
./bt-dos run --plan PLAN.json --confirm TARGET
./bt-dos report RUN_DIR
./bt-dos verify RUN_DIR
./bt-dos demo --output runs/demo
```

`TARGET`、`PLAN.json`、`RUN_DIR` 是占位符。实测目标须与计划及 `--confirm` 一致；BR/EDR 场景还需记录实际本地适配器地址。扫描与 `ble-info` 也会触发蓝牙通信，不属于离线演示。

## 硬件与证据边界

```text
Linux + BlueZ
  ├─ USB 双模适配器 ── BR/EDR 或 BLE 正常交互 ── TARGET
  │       └─ btmon → host-hci.btsnoop（本机 HCI 视角）
  └─ 可选 nRF52840 + sniffer 固件 → air.pcap（BLE 空口视角）
```

- 单 USB 双模适配器可作为 BR/EDR、BLE 主动通信端；本项目按场景顺序运行，不把它描述成通用空口分析仪。
- `btmon` 是主机—控制器接口观察，既不等于全部射频流量，也不覆盖目标内部业务和 ECU 状态。
- nRF52840 有两种**互斥固件角色**：sniffer 固件用于 BLE 空口采集；匹配板型的 Zephyr HCI USB controller 固件在 Linux 枚举为 `hciX` 后，可选作 `ble-read` / `ble-cycle` 的主动适配器并使用 `btmon`。它是 LE-only，不用于 BR/EDR；同一块板不同时承担两种角色。项目不自动刷固件，nRF 真实兼容性尚未验证。[角色与准备 →](docs/hardware.md#nrf52840两种互斥角色)
- Windows 用于代码、文档和发布管理；实际蓝牙运行面向 Linux。虚拟机需独占 USB 直通，不能据 Windows 能识别设备推断 Linux 测试通过。

[硬件准备与故障定位 →](docs/hardware.md)

[已完成验证与尚待现场确认的范围 →](docs/validation.md)

## 权限模型

向导、计划、worker、报告以普通用户运行。真实运行前交互执行 `sudo -v`，仅由采集子进程使用 `sudo -n btmon`。项目不自动修改 sudoers、不设置 `setcap`，也不要求将整个 TUI 作为 root 启动。BlueZ D-Bus 和串口访问遵循当前系统的策略；先用 `doctor` 检查。

## 运行产物

```text
RUN_DIR/
├── plan.json             # 本次计划快照
├── events.jsonl          # supervisor 生命周期事件
├── worker.jsonl          # 协议操作与结果
├── result.json           # 运行状态、统计与采集元数据
├── host-hci.btsnoop      # 真实运行的原始 HCI 采集
├── air.pcap              # 可选 BLE 空口采集
├── report.md             # 范围、观察、缺口与结论
└── manifest.json         # 文件哈希清单
```

演示目录使用显著的 simulation 标识，包含合成事件与报告，**不生成伪造的 HCI/PCAP**。哈希校验只说明文件与清单一致，不证明采集完整、目标无故障或符合认证要求。[证据解释 →](docs/evidence.md)

若采集写入进程退出未确认，目录标记 `UNSEALED.json` 且不生成 `manifest.json`；确认退出但需要强制清理时记录 `cleanup_forced`，不自动当作成功完成。

## 法规定位

本工具为风险导向的测试活动提供部分记录，**不是 GB 44495 或 UN R155 认证工具**。GB 44495-2024 及其第 1 号修改单应一并核对；UN R155 附录 5 的威胁条目 8.1 与缓解措施 M13 关注拒绝服务风险及检测、恢复。这里的默认速率和上限不是它们规定的统一 PPS 条款；适用范围、负载与判据仍由整车风险分析和试验方案确定。详见[官方依据及映射限制](docs/evidence.md#法规映射)。

## 开发与贡献

```bash
./scripts/install.sh --dev
.venv/bin/python -m unittest discover -s tests -v
```

欢迎提交可复现的故障、带证据的兼容性记录和小而明确的改进。提交前阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题按 [SECURITY.md](SECURITY.md) 私下报告。变更见 [CHANGELOG.md](CHANGELOG.md)。

MIT © 2026 addxemmm。见 [LICENSE](LICENSE)。
