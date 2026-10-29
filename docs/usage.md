# 安装与使用

[返回首页](../README.md) · [硬件](hardware.md) · [证据](evidence.md)

## 1. 安装

运行环境为 Linux（面向 Ubuntu / Kali）、Python 3.10+、Bash 和 BlueZ。Windows 可用于源码与发布管理。先安装系统依赖，再安装项目；系统包安装会请求管理员权限。

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip bluez
chmod +x bt-dos scripts/install.sh
./scripts/install.sh
./bt-dos doctor
```

`install.sh` 在仓库内建立 `.venv`，使用 `pip install -e .` 安装项目；日常使用 `./bt-dos`，无需手动激活环境。`./scripts/install.sh --dev` 在安装后运行单元测试。系统工具缺失、适配器不可用和权限问题以 `doctor` 的实际输出为准，不以安装成功代替运行环境就绪。

0.2.0 的 Linux 扫描通过共享的 BlueZ D-Bus API 实现，直接依赖 `dbus-fast>=5,<6`；项目安装会按 Linux 平台依赖安装它。CLI 与向导使用同一限时扫描后端。

### 从 Windows 部署已提交源码

在 Windows PowerShell 中可使用项目脚本部署到 Linux：

```powershell
.\scripts\deploy.ps1 -HostName HOST -UserName USER -RemotePath /home/USER/workspace/bl_ble_dos
```

替换占位符并先确认远程目录用于本项目。脚本通过 SSH/SCP 传输 Git `HEAD` 的源码归档，执行远程安装、单元测试和 `doctor --json`；不会自动发起无线测试。需要已有提交且无已跟踪的未提交变更，未跟踪文件不在归档中。它不是发布或硬件验证；实际运行仍在 Linux 终端完成。

## 2. 先运行离线演示

```bash
./bt-dos demo --output runs/demo
# 将 RUN_DIR 替换为上一步打印的实际子目录
./bt-dos report RUN_DIR
./bt-dos verify RUN_DIR
```

`--output` 是父目录而不是最终证据目录：每次在其下创建新的时间戳/随机子目录，终端会打印实际路径、报告位置和可直接复制的校验命令。不要对父目录直接运行 `verify`。

`demo` 不扫描、不连接、不调用无线采集，也不需要蓝牙硬件。它保存带 `simulation` 标识的计划、合成事件、报告和清单，不伪造 `host-hci.btsnoop` 或 `air.pcap`。它用于检查交付流程和界面，不证明任何硬件能力。

## 3. 交互向导

```bash
./bt-dos
# 或
./bt-dos wizard
```

0.2.0 向导按六步组织：**环境 → 用例与适配器 → 选择目标 → 测试参数 → 证据配置 → 复核运行**。普通字段中 `b` / `back` 返回上一步，`q` / `cancel` 回首页。草稿仅保留在当前会话内存，返回首页不会自动启动负载；退出应用后不恢复草稿。首页演示、诊断或一次向导操作完成后回到菜单。

确认前应核对目标地址、实验隔离、停止条件及恢复方法。实际运行时计划写入新运行目录的 `plan.json`，不建立长期保存车辆信息的项目配置。

### 扫描或手动选择目标

目标选择方式页支持：

| 输入 | 操作 |
| --- | --- |
| `s` | 明确发起一次限时扫描，默认 5 秒，结束后展示冻结的候选快照 |
| `m` | 手动输入完整目标 MAC，不扫描、不连接 |
| `k` | 已有目标时保留当前目标，不扫描、不连接 |
| `b` | 返回“用例与适配器”步骤 |
| `q` | 回首页，草稿保留于当前会话内存 |

冻结列表显示名称、完整 MAC、RSSI；终端宽度足够时显示地址类型和配对状态。按当前页编号选择，列表不会后台刷新导致编号移动。列表操作：

| 输入 | 操作 |
| --- | --- |
| `r` | 恰好重扫一次，不递归扫描、不自动连续刷新 |
| `f` | 按名称或地址筛选已有快照，空筛选恢复全部 |
| `t` | 仅设置下次扫描为 1–15 秒，不触发无线操作；按 `r` 才重扫 |
| `n` / `p` | 下一页 / 上一页，每页 8 项 |
| `m` | 改用手动 MAC |
| `b` | 返回目标选择方式页；再按 `b` 才返回前一个向导步骤 |
| `q` | 回首页，不启动测试 |

名称、RSSI 和已配对状态只是观察元数据，不证明目标身份或传输承载。更换适配器或 BR/EDR / BLE 传输会同时清除旧目标与特征；任何目标变化都会清除旧 GATT 特征，包括先从 `ble-read` 切到其他场景更换目标、再切回的情况，须重新填写该目标的只读特征。扫描异常不会自动选择候选，可处理后显式重扫或手动输入。

### 预算与最终复核

| 预设 | 请求型窗口 / 速率 | 循环次数 | 基线 / 恢复 |
| --- | --- | --- | --- |
| 默认 `default` | 10 秒 / 1 req/s | 5 | 5 / 10 秒 |
| 快速预检 `quick` | 5 秒 / 1 req/s | 3 | 2 / 5 秒 |
| 自定义 `custom` | 逐项编辑并校验 | 逐项编辑并校验 | 逐项编辑并校验 |

预设不扩大协议上限；请求型与循环型参数各按对应场景生效。默认与快速预检均保持 `max_ops=400`、`hold=2`、`gap=3`。复核页可按 `1` 修改用例/适配器、`2` 修改目标、`3` 修改参数、`4` 修改证据，或按 `j` 查看 JSON；保留其他配置，不必从头重填。依赖变化仍会要求补充目标或特征。

无参数入口要求交互式终端；输入或输出被重定向时显示帮助，不进入询问。窄终端由 Rich 自动适配。关闭颜色使用标准环境变量，不是 `--no-color` 参数：

```bash
NO_COLOR=1 ./bt-dos doctor
```

最终启动需要先输入完整目标 MAC。确认后，交互向导可在当前 TTY 调用原生 `sudo -v` 刷新采集权限；密码由系统 sudo 直接读取，应用不读取或保存密码。未完成认证时不启动采集或负载，可返回配置。

也可提前以普通用户刷新 sudo 凭证；非交互 `run` 必须采用这种方式：

```bash
sudo -v
./bt-dos run --plan PLAN.json --confirm TARGET
```

仅 `btmon` 子进程使用 `sudo -n`，不会在后台等待密码输入。不要用 `sudo ./bt-dos` 启动整个向导；项目不自动添加 sudoers、修改 capabilities 或调整系统安全策略。

## 4. 诊断、枚举与发现

```bash
./bt-dos doctor
./bt-dos doctor --json
./bt-dos adapters
./bt-dos scan --transport le --adapter hci0 --seconds 5
./bt-dos scan --transport bredr --adapter hci0 --seconds 5
./bt-dos scan --transport le --adapter hci0 --seconds 5 --json
./bt-dos ble-info --target TARGET --adapter hci0
```

`hci0` 是适配器示例，按枚举结果替换；`TARGET` 是被测蓝牙地址占位符。`--seconds` 范围为 1–15 秒，默认 5 秒。

**输出兼容变化**：0.2.0 的 CLI `scan` 默认显示 Rich 人读表格，不再沿用 0.1 的默认输出形态。脚本应显式使用 `--json`，获取 `ScanResult` 对象：

- `requested_transport`、`adapter`、`started_at`、`duration`：请求的扫描条件和实际观察窗口；
- `devices`：本窗口候选，包含完整地址、显示名称、RSSI、配对/地址类型、观察时间、来源及 `transport_verified`；
- `status`、`cleanup_status`、`warnings`：完成或错误状态、发现会话释放结果及限制提示；
- `shared_discovery_at_start`：开始时是否已有其他发现会话。

BlueZ 多客户端发现过滤器会合并；当前所有候选的 `transport_verified` 都是 `false`，只表示“本次 LE / BR/EDR 扫描期间观察”，不是该候选的实际承载证明。已有缓存仅用于补充元数据，未在本窗口更新的缓存设备不列作新观察。空列表不证明没有设备，目标可发现状态、隐私地址、信号、共享发现和 USB 直通均会影响结果。

扫描仅在明确选择 `s` / `r` 或执行 CLI `scan` 后开始；`t` 仅修改下次时长，不发起扫描。不调用 Connect / Pair，不执行 GATT 读写或测试负载；但发现可包含 inquiry、主动扫描和名称解析，因此并非完全被动。扫描结束后冻结候选，清理只释放本客户端的发现会话，不关闭其他客户端的发现。

取消扫描返回 `ScanResult(status="interrupted")`，保留清理状态和警告，而不是丢弃 `cleanup_status` / `warnings`；CLI 退出 `130`。须检查清理结果，`cleanup_status="unconfirmed"` 不视作普通成功。完成退出 `0`，其他扫描失败退出 `1`。

`ble-info` 与扫描是不同操作：它会显式连接并发现 GATT 服务，列出特征 UUID、句柄和属性，不发起特征读写。已配对目标的访问策略仍由 BlueZ 与目标决定。

## 5. 使用 JSON 计划运行

以下为 `ble-read` 计划结构。保存为 `PLAN.json`，将大写占位符替换为本次实验的真实值后运行。JSON 不支持注释。

```json
{
  "schema_version": 1,
  "case": "ble-read",
  "adapter": "hci0",
  "target": "TARGET",
  "characteristic": "CHARACTERISTIC_UUID",
  "sdp_uuid16": 4363,
  "rate": 1,
  "approved_rate": 1,
  "duration": 10,
  "max_ops": 400,
  "cycles": 5,
  "hold": 2,
  "gap": 3,
  "baseline": 5,
  "recovery": 10,
  "capture": "hci",
  "output_root": "runs",
  "external_business_probe": false,
  "external_ecu_observation": false
}
```

```bash
sudo -v
./bt-dos run --plan PLAN.json --confirm TARGET
```

`--confirm` 必须与计划目标一致。程序先校验计划，再创建运行快照、启动采集并等待就绪，之后才启动 worker。请求型场景到达持续时间或操作数上限时结束，不因配置了较大 `max_ops` 而延长请求窗口。信道/连接循环以循环次数、保持/间隔和监督超时为边界，不以 `duration` 作为总时长；连接准备与基线/恢复也会增加全程时长。基线与恢复窗口用于限定前后观察时间，不自动提供外部业务监测。

### 参数参考

| 字段 | 默认值 / 取值 | 说明 |
| --- | --- | --- |
| `schema_version` | `1` | 计划格式版本 |
| `case` | 四个场景之一 | `br-sdp`、`br-channel`、`ble-read`、`ble-cycle` |
| `adapter` | 如 `hci0` | 本次使用的 Linux 蓝牙适配器 |
| `target` | 必填地址 | 本次目标，需与确认值匹配 |
| `local_address` | BR/EDR 必填 | 对应所选本地适配器的蓝牙地址；不是目标地址 |
| `characteristic` | `ble-read` 必填 | 已确认可读的完整 GATT 特征 UUID，或 `0xHANDLE` 形式句柄 |
| `sdp_uuid16` | `4363`（`0x110B`） | SDP 查询使用的 16 位服务 UUID 整数；按目标服务设置 |
| `rate` | `1`，范围 `0.1–20` | 请求速率，单位 req/s；不等于空口 PPS |
| `approved_rate` | `1` | 本次实验明确接受的请求速率上界；`rate` 应不高于此值，项目仍限制实际配置速率不超过 20 |
| `duration` | `10`，范围 `1–60` | 请求型场景负载窗口秒数，不包含全部前后准备时间 |
| `max_ops` | `400`，范围 `1–400` | 请求型场景操作次数上限，不保证一定达到 |
| `cycles` | `5`，最高 `10` | 顺序连接循环上限 |
| `hold` | `2`，范围 `1–5` | 每轮连接保持秒数 |
| `gap` | `3`，范围 `3–30` | 循环之间间隔秒数 |
| `baseline` | `5`，范围 `0–60` | 负载前观察窗口秒数 |
| `recovery` | `10`，范围 `0–120` | 负载后恢复观察窗口秒数 |
| `capture` | `hci` / `hci+nrf` | HCI 必选；nRF 是额外 BLE 空口采集 |
| `nrf_port` | 如 `/dev/ttyACM0` | 选择 `hci+nrf` 时所需的 sniffer 串口 |
| `output_root` | `runs` | 新运行目录的父目录 |
| `external_business_probe` | `false` | 外部业务探针声明；单凭此字段不构成证据 |
| `external_ecu_observation` | `false` | 外部 ECU 观察声明；单凭此字段不构成证据 |

`approved_rate` 是试验预算字段，不是身份认证或访问权限机制；填写较大的预算也不会提升项目的 20 req/s 配置上限。

未用于当前场景的参数不会把该场景扩展为另一种测试。例如 `cycles` 不会让 `br-channel` 变成 ACL 链路循环；`rate` 也不保证 BLE 连接循环每秒完成相同次数。目标应答、连接时延和主机调度可能降低实际负载。

### BR/EDR 计划差异

将 `case` 改为 `br-sdp` 或 `br-channel`，添加 `"local_address": "LOCAL_ADDRESS"`，并删除 BLE 特征字段。运行前核对本地地址确实属于计划里的 `adapter`。`br-sdp` 的目标应支持所选服务 UUID；服务缺失与设备失去可用性是不同结论。

`br-channel` 固定使用 SDP 的 PSM `0x0001`，只建立、保持和关闭 L2CAP 信道，不发送业务负载，不开放任意 PSM 配置。控制器可能保留底层 ACL，因此观察到本地信道关闭不等于目标 ACL 已断开。`br-sdp` 当前是首响应页查询；遇到正常 SDP continuation 时停止并标记未完成，不应把分页当成目标故障或完整服务枚举。

### 可选 BLE 空口采集

BLE 计划使用 `"capture": "hci+nrf"`，并添加 `"nrf_port": "/dev/ttyACM0"`。需提前安装 Nordic `nrfutil` / `ble-sniffer` 并独立完成 sniffer 固件准备；本项目不刷写设备固件。

监督进程使用的接口形态：

```bash
nrfutil ble-sniffer sniff --port PORT --follow TARGET --output-pcap-file PATH
```

`PORT`、`TARGET`、`PATH` 是占位符。nrfutil 的接口超时参数不代表总录制时长；生命周期由本项目的监督进程控制。项目命令行不传入配对密钥。详情见[硬件指南](hardware.md#可选nrf52840-ble-sniffer)。

## 6. 停止、报告与复核

需要中止时按 `Ctrl+C`。程序应停止 worker 和采集子进程，并将已取得的数据与中止原因保留在运行目录。若目标业务异常，按实验停止条件处置；不要以追加负载代替故障调查。

采集写入进程退出未确认时生成 `UNSEALED.json`，不生成完整性清单；已确认退出但使用强制清理时记录 `cleanup_forced`，不作为自动成功。详情见[清理与未封存状态](evidence.md#清理与未封存状态)。

```bash
./bt-dos report RUN_DIR
./bt-dos verify RUN_DIR
```

`report` 先检查完整性，再显示已封存的 Markdown 报告，不改写证据包；若清单不一致会警告并退出 `1`。`verify` 校验清单及其引用文件并列出完整性差异。解释参见[证据指南](evidence.md)。

| 命令 / 结果 | 退出码 | 含义 |
| --- | --- | --- |
| `run` 完成 | `0` | 脚本流程完成，不代表合规 PASS |
| `run` 运行失败 | `1` | 环境、采集或执行失败，查看事件与报告 |
| `run` 用户停止 | `130` | 中止运行；已保存内容仍需按完整性检查 |
| `verify` 清单一致 | `0` | 文件与清单一致 |
| `verify` 缺失 / 损坏 / 不一致 | `1` | 需保留原始目录并调查差异 |

证据不足不是自动的“DoS 失败”或“合规 FAIL”；请求返回成功也不是自动 PASS。

## 常见问题

- **找不到 `btmon`**：确认安装了系统 BlueZ 工具，查看 `doctor` 的路径检测。
- **`sudo -n` 失败**：在同一会话先运行 `sudo -v`，然后重新运行；不自动放宽系统权限。
- **没有适配器**：检查 `rfkill`、Bluetooth 服务及 USB 直通；参见硬件指南。
- **GATT 读取被拒绝**：确认特征的 read 属性及目标配对/加密要求，不将访问拒绝等同于目标故障。
- **空口文件为空或跟随失败**：核对串口、固件、当前目标地址和采集开始时机；文件存在不等于捕获完整。
- **退出成功但结论不充分**：查看证据缺口；项目只对现有观察作结论，不补造业务或 ECU 数据。
