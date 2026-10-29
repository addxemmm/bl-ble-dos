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

诊断不自动改变 rfkill、系统服务、sudoers、capabilities 或设备权限。按当前主机管理要求处理发现的问题，再重新诊断。BR/EDR 计划需明确填写与所选 `adapter` 对应的 `local_address`。

`btmon` 采集需要当前系统允许的权限。执行 `sudo -v` 后，监督进程仅对采集子进程使用 `sudo -n`；普通用户的 BlueZ D-Bus 权限和串口权限仍按系统策略检查。不要把 root TUI 作为通用故障修复方法。

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
