# 硬件准备与能力边界

[返回首页](../README.md) · [安装](installation.md) · [协议原理](protocols.md) · [使用](usage.md) · [证据](evidence.md)

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
| nRF52840 + 匹配板型的 HCI USB controller 固件 | Linux 枚举为 `hciX` 后作为BLE主动控制器候选 | BR/EDR支持、同时作为sniffer、已验证兼容性 |
| nRF52840 + Connectivity + 桌面BLE App | 作为被Linux测试端连接的参考BLE外设 | 已成为Linux控制器或独立空口sniffer |
| 外部业务探针 / 目标系统记录 | 观察目标业务与内部状态 | 单凭蓝牙请求日志已经具备这类证据 |

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

`btmon`采集需要当前系统允许的权限。0.3.0交互向导在输入完整目标MAC后，可在当前TTY调用原生 `sudo -v`；密码由sudo直接读取，应用不读取或保存密码。非交互 `run` 仍需预先刷新凭证。监督进程对采集子进程使用 `sudo -n`；仅显式设置BLE连接间隔时，还由保留TTY的supervisor通过内部文件RPC执行限定argv的参数helper（`sudo -n`），worker保持普通用户权限。请求/响应随run封存；helper预算15秒，worker等待最多20秒。不自动添加或扩大sudoers规则；普通用户的BlueZ D-Bus权限和串口权限仍按系统策略检查。不要把root TUI作为通用故障修复方法。

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

源码与接口依据见 [BlueZ sdptool](https://github.com/bluez/bluez/blob/master/tools/sdptool.c)、[Bleak 客户端](https://bleak.readthedocs.io/en/stable/api/client.html)与 [v3.0.2 BlueZ 后端](https://github.com/hbldh/bleak/blob/v3.0.2/bleak/backends/bluezdbus/client.py)。接口文档不是本项目的硬件验证结论；具体实测状态见 [validation](validation.md)。

## 虚拟机与 Windows 管理

实际蓝牙实验运行在 Linux。若 Linux 位于虚拟机中，应将 USB 适配器独占直通给客体系统；额外 nRF 也需单独直通。宿主与客体竞争同一设备、USB 自动重连、休眠和电源管理可能造成断链或采集丢失。

Windows 上的编辑、单元测试或 demo 成功不能证明 Linux 实测能力。WSL、虚拟机和实体 Linux 的蓝牙访问路径并不等价。首个实测记录应写明宿主/客体方式、内核、BlueZ、控制器固件和 USB 设备信息。

<a id="nrf52840-roles"></a>

## nRF52840：三种部署角色

固件和配套主机软件决定角色，不由USB外观或串口名称决定。同一块板在本项目中不同时充当下列多种角色；切换前应准备匹配板型的固件、刷写及恢复流程。项目不自动刷写。

### 1. Connectivity + 桌面 BLE App：参考外设

Windows上的nRF Connect for Desktop BLE App可通过Connectivity固件控制板卡，配置GATT server并启动广播；此时它是Kali主动测试端连接的**BLE参考外设**，不是Kali的`hciX`控制器，也不是sniffer。

本项目已在这种部署上观察到真实读取及固定无响应写的发送/目标接收。写测试需按[专用fixture说明](usage.md#专用fixture写入与ble连接参数)配置固定服务与Read/Write Without Response特征，并保留外设接收日志核对序号。

当前参考配置断开后需重新开启广播。因此已验证的一轮连接/断开不等于无人干预的连续重连；持续多轮应另行准备断开后恢复广播的外设配置或固件，不能由一次成功推断全部循环已通过。

### 2. HCI USB controller固件：主动LE控制器

匹配板型的Zephyr HCI USB controller固件需要板卡具备相应USB与蓝牙支持，可向主机提供HCI接口。[Zephyr官方示例](https://docs.zephyrproject.org/latest/samples/bluetooth/hci_usb/README.html)

准备后应在Linux通过`./bt-dos adapters`核对实际`hciX`、地址及BlueZ能力，才将其作为`ble-*`主动适配器候选并记录HCI。nRF52840用于LE，不替代BR/EDR适配器；本项目尚未验证这一固件角色的真实兼容性，参考外设角色的实测不证明本角色可用。

<a id="nrf-sniffer"></a>

### 3. nRF Sniffer固件：独立BLE空口观察端

额外使用受支持板卡、匹配sniffer固件和nRF Util。[Nordic官方说明](https://www.nordicsemi.com/Products/Development-tools/nRF-Sniffer-for-Bluetooth-LE)

```bash
nrfutil install ble-sniffer
nrfutil ble-sniffer sniff --help
```

这些是可选准备命令，可能访问网络，不属于默认安装。项目使用`--port`、`--follow`和`--output-pcap-file`；固件、板型及选项以本机安装版本的官方文档为准。选择`capture: "hci+nrf"`并填写实际`nrf_port`，普通用户需有串口访问权限。

sniffer应在建链前就绪，仍可能跟随失败、漏包或因隐私地址变化失去目标；加密内容不保证可读，单板不保证零漏包，也不覆盖BR/EDR。该固件角色的真实跟随/拔插兼容性尚待验证。

### 如何选择

- 只做BR/EDR或BLE正常协议负载：先使用Linux中可用的USB双模适配器，不必先购买或刷写nRF。
- 需要可观察接收序号的BLE参考外设：使用Connectivity角色或自建等价fixture。
- 需要独立BLE空口观察：额外准备sniffer，不把目标端接收日志当作独立空口记录。
- Windows可控制参考外设，主动测试程序仍运行在Linux；两种主机角色不混淆。

## 兼容性状态

已有指定控制器/参考目标组合的真实协议执行记录，见[验证记录](validation.md)；不推导全部适配器、目标或固件角色兼容。新增记录至少包含：

- 运行编号、提交版本、发行版、内核和 BlueZ 版本；
- 主动适配器 USB VID/PID、控制器/固件信息和 `hciN`；
- 目标设备类别、固件版本与所测协议/服务（公开提交时去标识化）；
- 所有生效参数、采集方式、开始/停止原因和日志；
- 实际观察、失败现象、恢复行为及证据缺口。

不要将一次 `doctor`、离线 demo 或单个 GATT 读取成功写成全场景兼容。查看 [CONTRIBUTING.md](../CONTRIBUTING.md) 中的记录要求。
