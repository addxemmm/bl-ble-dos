# 硬件准备与能力边界

[返回首页](../README.md) · [使用](usage.md) · [证据](evidence.md)

## 最小配置

1. Ubuntu / Kali Linux 主机，Python 3.10+、BlueZ 与可用终端。
2. USB 双模（BR/EDR + BLE）蓝牙适配器，由 Linux 驱动识别为 `hciN`。
3. 被测蓝牙目标、实验隔离条件、恢复手段和独立的状态观察方法。
4. 足够磁盘空间保存日志与捕获，稳定的 USB 连接与电源。

“蓝牙 5.x”“双模”等商品标签不替代运行验证。控制器、固件、Linux 驱动、BlueZ、USB 直通与目标协议栈组合决定实际行为；不按商标或版本号承诺兼容。单适配器顺序覆盖 BR/EDR 与 BLE 场景，不承诺同时执行两种负载。

## 主动适配器与抓包不是同一能力

| 设备/接口 | 本项目用途 | 不据此推断 |
| --- | --- | --- |
| USB 双模适配器 | 发送 BR/EDR 或 BLE 正常协议请求 | 通用被动空口捕获、全部信道监听、控制器任意底层帧注入 |
| `btmon` / HCI monitor | 记录本机主机—控制器交互 | 射频发送一定成功、目标一定收到、其他设备的全部通信 |
| nRF52840 + sniffer 固件 | 额外观察 BLE 空口 | BR/EDR 空口抓包、零漏包、全部加密内容可读 |
| nRF52840 + 匹配板型的 HCI USB controller 固件 | Linux 枚举为 `hciX` 后主动执行 BLE 场景并记录 HCI | BR/EDR 支持、同时作为 sniffer、已验证本项目兼容性 |
| 外部业务探针 / ECU 记录 | 观察目标业务与内部状态 | 单凭蓝牙请求日志已经具备这类证据 |

BR/EDR 与 LE 名称、地址及服务可能不同，应以对应传输下的当前目标身份为准。BLE 随机地址或隐私地址可能变化；旧扫描地址可能不再可用。

## Linux 检查

```bash
./bt-dos doctor
./bt-dos adapters
bluetoothctl list
rfkill list bluetooth
systemctl status bluetooth --no-pager
```

安装默认不运行 apt；只有 `scripts/install.sh --install-system` 显式允许系统包安装，`--non-interactive` 不等待密码或输入。项目脚本不自动启动或启用蓝牙服务；系统包安装自身行为取决于发行版策略。诊断不自动改变 rfkill、系统服务、sudoers、capabilities 或设备权限。按当前主机管理要求处理发现的问题，再重新诊断。BR/EDR 计划需明确填写与所选 `adapter` 对应的 `local_address`。

`btmon` 采集需要当前系统允许的权限。0.3.0 交互向导在输入完整目标 MAC 后，可在当前 TTY 调用原生 `sudo -v`；密码由 sudo 直接读取，应用不读取或保存密码。非交互 `run` 仍需预先刷新凭证。监督进程仅对采集子进程使用 `sudo -n`；普通用户的 BlueZ D-Bus 权限和串口权限仍按系统策略检查。不要把 root TUI 作为通用故障修复方法。

## 扫描候选不等于硬件或承载验证

0.3.0 的向导与 CLI 共用独立 BlueZ D-Bus 扫描 API，Linux 直接依赖为 `dbus-fast>=5,<6`，由项目安装处理。选择目标页先选 `s` 才发起扫描，默认窗口 5 秒，可设为 1–15 秒；也可手动输入或保留当前目标。扫描结果冻结后供筛选、翻页和编号选择，不因浏览列表而后台持续扫描。

CLI 默认输出人读表格，自动化应使用 `scan ... --json`，并检查 `status`、`cleanup_status`、`warnings` 与候选元数据。BlueZ 多客户端发现过滤器会合并，候选的 `transport_verified` 均为 `false`；“LE / BR/EDR 扫描期间观察”不是该设备实际承载、目标身份或场景兼容性证明。未在本窗口更新的缓存设备不列作新观察，空窗口不证明设备不存在。

扫描不调用 Connect / Pair、GATT 读写或测试负载，但发现过程可含 inquiry、主动扫描和名称解析，不是完全被动。结束时仅释放本客户端的发现会话，不关闭其他客户端的会话。更换设备、适配器或传输会清除旧探测快照及 SDP / GATT 参数选择，适配器或传输变化也会清除旧目标。目标地址仍需独立核对，尤其是 BLE 隐私地址变化时。详细按键见[使用指南](usage.md#扫描或手动选择目标)。

## 服务探测不是兼容性或负载证据

目标选择后自动展示已知名称、配对等元数据，不自动连接。仅在参数页 `p` 后将默认 `n` 改为明确确认，或执行带匹配 `--confirm TARGET` 的 CLI `inspect` 时探测。向导快照只留在内存，发生在抓包基线之前，不自动写入运行证据包；CLI JSON 如需保存应显式重定向至私有文件。

- **BR/EDR**：通过指定适配器的 BlueZ 地址核对本地地址，再由普通用户调用 `sdptool -i LOCAL_ADDRESS browse --xml TARGET`。15 秒执行预算、1 MiB 输出与记录/XML 结构限额，另有有限进程清理；`browse` 可能递归，退出 0 不证明完整枚举。只从服务类列表提取可用于计划的 UUID16，自定义 UUID128 只展示。PSM / RFCOMM channel 只做元数据；`br-channel` 仍固定 SDP PSM `0x0001`。
- **BLE**：同适配器发现 8 秒、一次应用 connect 预算 10 秒、disconnect 预算 5 秒，D-Bus 调用各 5 秒。只枚举服务/特征 UUID、属性、描述和句柄，不读取值、不配对、不写入或订阅。可读且 UUID 唯一的特征可选并以 UUID 保存；重复 UUID 需高级手动核对，不自动选第一个。瞬时句柄可能随数据库变化。
- **共享状态**：已连接目标前置拒绝，BLE 连接前再检查。使用独立实验控制器并停止其他客户端；本工具的同用户锁不阻止其他程序，检查后仍可能出现竞态，disconnect 可能影响同期连接。底层 Bleak / BlueZ 可能重试或复用，取消传播也可能超过预算，分阶段超时不是整体硬墙钟保证。任何清理未确认均需保留该状态。
- **业务边界**：read 属性不等于权限允许或读取无副作用，正式 `ble-read` 前须厂家确认。未配对设备若要求认证，本探测不自动配对；拒绝访问、服务缺失、目标不广播与目标业务故障应分别判断。

源码与接口依据见 [BlueZ sdptool](https://github.com/bluez/bluez/blob/master/tools/sdptool.c)、[Bleak 客户端](https://bleak.readthedocs.io/en/stable/api/client.html)与 [v3.0.2 BlueZ 后端](https://github.com/hbldh/bleak/blob/v3.0.2/bleak/backends/bluezdbus/client.py)。当前改进未新增真实车端 SDP / GATT 或 nRF 兼容性验证结论；现场验证状态见 [validation](validation.md)。

## 虚拟机与 Windows 管理

实际蓝牙实验运行在 Linux。若 Linux 位于虚拟机中，应将 USB 适配器独占直通给客体系统；额外 nRF 也需单独直通。宿主与客体竞争同一设备、USB 自动重连、休眠和电源管理可能造成断链或采集丢失。

Windows 上的编辑、单元测试或 demo 成功不能证明 Linux 实测能力。WSL、虚拟机和实体 Linux 的蓝牙访问路径并不等价。首个实测记录应写明宿主/客体方式、内核、BlueZ、控制器固件和 USB 设备信息。

## nRF52840：两种互斥角色

nRF52840 的实际用途取决于当前固件，而不是 USB 外观或产品名称。以下角色须二选一；同一块板在本项目中不同时作为主动 HCI 控制器和独立 sniffer。项目不自动刷写、恢复或更改固件。

### 主动 BLE 控制器

板卡具备 Zephyr 所需的 Bluetooth 与 USB 支持，且已准备匹配板型的 **Zephyr HCI USB controller** 固件时，可通过主机 BlueZ 使用。参见 [Zephyr HCI USB 官方示例](https://docs.zephyrproject.org/latest/samples/bluetooth/hci_usb/README.html)。具体板型与固件兼容性需单独核对；板型确认后再由设备准备流程构建与刷入相应固件。

Linux 将其识别为 `hciX` 后，用 `./bt-dos adapters` 核对实际编号，并将该编号填入计划的 `adapter`，运行 `ble-read` 或 `ble-cycle`，使用 `capture: "hci"` 保存 `btmon` 证据。它是 **LE-only**，不用于 `br-sdp` 或 `br-channel`。若同时需要 BLE 空口观察，另用一块已准备 sniffer 固件的设备。

当前实机观察到的 `nRF52 Connectivity` 串口枚举，**不等于** Linux HCI 控制器就绪，也不等于 sniffer 固件或接口就绪。需要分别验证 `hciX`/BlueZ 或 sniffer 能力；nRF 主动与嗅探两种角色的真实兼容性均尚未验证。

### 可选：nRF52840 BLE sniffer

使用额外 nRF52840 Dongle 或受 Nordic 支持的开发板，事先按官方流程刷入匹配的 nRF Sniffer 固件。此固件角色是 **BLE 空口观察设备**，不是主动 HCI 控制器，也不是 BR/EDR 双模适配器的替代品。

从 [Nordic nRF Util 官方页面](https://www.nordicsemi.com/Products/Development-tools/nRF-Util) 安装 `nrfutil`，然后安装并检查 `ble-sniffer` 命令：

```bash
nrfutil install ble-sniffer
nrfutil ble-sniffer sniff --help
```

官方接口使用已刷 sniffer 固件的设备记录 BLE 空口并输出 PCAP，项目使用 `--port`、`--follow` 和 `--output-pcap-file`。固件准备、受支持硬件和确切选项以所安装版本的[官方文档](https://docs.nordicsemi.com/r/bundle/nrfutil/page/nrfutil-ble-sniffer/nrfutil-ble-sniffer-sniff.html)为准。

在项目计划中设置 `capture` 为 `hci+nrf`，并填写实际 `nrf_port`。确保普通用户可打开该串口。接口的 `--timeout` 不是整个测试的录制时长控制，采集结束由项目监督进程负责。

### 空口观察的限制

- sniffer 需在连接建立前就绪，仍可能错过建链或跟随失败。
- 位置、干扰、信道变化和接收条件会影响捕获完整性。
- BLE 隐私地址变化可能使按旧地址跟随失效。
- 加密链路不保证可解码。本项目不通过命令行传递密钥，也不自动获取密钥。
- PCAP 文件非空并不证明相关事务全部被捕获；需结合时间线和 HCI/外部记录复核。

参考 [Nordic Sniffer 使用指南](https://docs-be.nordicsemi.com/bundle/nrfutil_ble_sniffer_pdf/raw/resource/enus/nRF_Sniffer_BLE_UG_v4.0.0.pdf)。

## 兼容性状态

当前没有列为“已验证”的具体控制器、目标或空口采集组合。新增兼容性记录至少包含：

- 运行编号、提交版本、发行版、内核和 BlueZ 版本；
- 主动适配器 USB VID/PID、控制器/固件信息和 `hciN`；
- 目标设备类别、固件版本与所测协议/服务（公开提交时去标识化）；
- 所有生效参数、采集方式、开始/停止原因和日志；
- 实际观察、失败现象、恢复行为及证据缺口。

不要将一次 `doctor`、离线 demo 或单个 GATT 读取成功写成全场景兼容。查看 [CONTRIBUTING.md](../CONTRIBUTING.md) 中的记录要求。
