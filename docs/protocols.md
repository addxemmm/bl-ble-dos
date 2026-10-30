# 技术原理：为什么分别测试 BR/EDR 与 BLE

[返回首页](../README.md) · [安装](installation.md) · [操作指南](usage.md) · [硬件角色](hardware.md) · [结果解读](evidence.md)

## 1. 先把名称说清楚

本项目覆盖 **Bluetooth Classic / BR/EDR** 与 **Bluetooth Low Energy / LE / BLE** 两条实现路径。日常说的“BL测试”在本文中明确写为BR/EDR测试；`BL`不是本项目新增的第三种蓝牙协议。

两者共享“蓝牙”名称，但控制器能力、发现与建链过程、服务访问方式和可观察状态不同。同一双模设备可以同时提供经典蓝牙业务与BLE配置接口，设备名称、地址及可用状态也可能不同。因此，分类依据是**本次选定的承载、实现调用和实际抓包**，不是“这是耳机/传感器”、某个UUID、蓝牙版本号或一次扫描结果。

Bluetooth SIG将Classic明确称为BR/EDR，并与LE分别描述。[官方技术概览](https://www.bluetooth.com/learn-about-bluetooth/tech-overview/)

## 2. 两条实际协议路径

```text
BR/EDR 场景
  Python AF_BLUETOOTH / SOCK_SEQPACKET / BTPROTO_L2CAP
    → 绑定所选控制器的本地蓝牙地址
    → 目标 BR/EDR ACL 承载
    → SDP PSM 0x0001 的动态 L2CAP 信道
    → SDP ServiceSearch 或仅信道生命周期

BLE 场景
  指定适配器的 BleakScanner → BLEDevice → BleakClient / BlueZ
    → LE 连接
    → GATT 特征访问 / ATT 事务，或 LE 连接生命周期

共同记录
  supervisor → btmon → 本机 HCI 抓包
  worker → API操作、时间、返回值和错误
  可选独立 BLE sniffer → 空口观察
```

`GATT`本身并不意味着“只能使用BLE”；规范也定义了BR/EDR承载。本工具把`ble-*`归为BLE，是因为当前实现选择了LE发现、Bleak客户端和LE连接路径，而不是把所有GATT访问都等同于BLE。[Bluetooth Core：GATT承载互通要求](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-61/out/en/host/generic-attribute-profile--gatt-.html)

服务探测与负载还需区分：`inspect --transport bredr`通过`sdptool`获取服务元数据；正式`br-sdp`由本项目socket代码构造查询，并不是不断重复启动`sdptool`。BLE探测只枚举服务/特征，正式读取或写入发生在另一个有计划、抓包和预算的运行中。

## 3. 五个用例分别测试什么

| 用例 | 实际操作与协议路径 | 受测处理过程 | 应核对的有效执行证据 | 不据此推导 |
|---|---|---|---|---|
| `br-sdp` | BR/EDR L2CAP PSM1上串行发送SDP ServiceSearch Request | 服务搜索、请求处理及响应调度 | SDP请求/响应的同一连接、方向与TID；有效记录数、错误和延迟 | 音频吞吐、A2DP业务压力或所有SDP功能均已覆盖 |
| `br-channel` | 在同一目标的SDP PSM1上逐轮建立、保持、关闭动态信道 | 信道配置、生命周期及后续再次建立能力 | 成功Connection Response、双向Configuration及对应Disconnection交互 | 工具强制ACL反复重建，或已直接测出内部内存回收/资源耗尽 |
| `ble-read` | LE连接上重复读取已确认的GATT特征 | ATT读取、目标属性服务与响应调度 | 目标连接上的实际读取事务、匹配响应和值、完成数/延迟 | read属性一定允许访问、一定无副作用，或读取失败就是目标DoS |
| `ble-cycle` | 串行LE连接、保持、主动断开 | LE连接生命周期及断开后的再次可连接性 | 成功LE连接事件、断开完成事件与逐轮时间线 | 并发连接槽位耗尽；一次成功就证明连续多轮成功 |
| `ble-write-command` | LE连接上对固定测试特征发送无响应写命令 | 写入入队、传输与参考外设接收路径 | API接受、HCI TX、参考外设接收日志分别计数；最后值回读作补充 | API成功就是目标逐条ACK、目标业务完成，或最后值能证明累计接收数 |

### BR/EDR：服务搜索与信道循环是两个维度

`br-sdp`发送PDU `0x02`，校验返回的ServiceSearch Response `0x03`或Error Response `0x01`，核对TID、长度及记录计数。当前查询最多请求16条记录，只实现首段处理；在记录计数等解析检查通过后，非空continuation会停止本档并报告`needs_continuation`。当前解析器还限制`total_records <= 16`，更大总记录数会报解析错误，未实现大规模服务枚举；这项实现限制不等于目标协议故障。`sdp_uuid16`是待查服务类，不是L2CAP PSM或RFCOMM channel。

`br-channel`固定使用PSM `0x0001`，不依据探测到的业务PSM/RFCOMM channel自动扩展测试，也不在该用例发送业务数据。关闭L2CAP信道后，ACL是否立即断开取决于主机、控制器及其他客户端。某次抓包观察到ACL也随之关闭，应作为该组合的观察记录，而不是实现保证。

BR连接准备、连接成功/失败与实际负载开始独立记事件。建链阶段失败时可能还没有SDP请求；应先检查目标状态、配对条件、信号和本地适配器，再解释运行结果。

### BLE：有响应读取与无响应写入的负载形态不同

`ble-read`串行等待每次读取返回再调度下一次操作，实际速率会受往返延迟影响。短值常见ATT Read Request/Response为`0x0A/0x0B`；长值、分片和协议栈实现可能让一次API操作产生多个报文，因此不把API计数当作PDU计数。

`ble-write-command`使用`response=False`，典型ATT Write Command为`0x52`，没有逐条ATT响应。Bleak文档说明这种调用会入队后返回；本工具仍需区分本地接受、HCI发出、目标接收与业务处理。[Bleak写入接口](https://bleak.readthedocs.io/en/stable/api/client.html#bleak.BleakClient.write_gatt_char)

写用例只支持专用参考外设：固定服务`9fbb0001-6284-4e2e-9c8e-3b7a2d546f10`、特征`9fbb0002-6284-4e2e-9c8e-3b7a2d546f10`，特征需支持Read和Write Without Response，并显式确认。载荷固定为`BTDS`加uint32小端序号，每次8字节；不是任意设备控制特征的通用写入器。

其同链路读取探针覆盖基线、负载和恢复，按1Hz记录延迟及错误；250ms是项目工程慢阈值，连续两次慢或一次读取错误即停止当前写负载并观察恢复。该探针共享链路和适配器，不能替代独立业务探针。

## 4. 技术特性与运行边界

- **计划化**：JSON校验目标、控制器、本地地址、服务/特征、请求速率、窗口、最大操作数及循环次数；UI与CLI共享规则。
- **有界调度**：原请求场景默认10次/秒、10秒、最多100次；最高20次/秒、60秒、400次。写用例独立最高100命令/秒、10秒、1000次。循环最多10轮，保持1–5秒、间隔3–30秒。
- **响应收尾**：窗口截止后不发起新操作，已提交事务可在3秒单事务预算内收尾；BR send/recv共享预算。队列可能使线上发送晚于API提交。
- **采集与执行分离**：采集先就绪；worker独立进程、普通用户权限；监督器检查STOP、采集存活、时间、磁盘和清理状态。
- **准备不挤占目标窗口**：同次运行共享一份新鲜适配器快照。真实TTY向导在环境/采集准备完成后，才等待最多60秒的人工唤醒确认，然后按计划观察基线。
- **失败保留而非静默重试**：仅清理及完整性校验通过的普通失败可返回复核，重新确认后创建新运行。CLI默认不增加stdin等待；没有后台保活或自动反复重连。
- **可选LE间隔请求**：显式`ble_interval_ms`可请求7.5–50ms、1.25ms倍数；限定helper通过supervisor执行，worker不提权。命令成功不等于实际生效，需HCI Connection Update事件确认。
- **证据分层**：API、HCI、独立空口与目标日志分别保留，不合并成一个未经核实的“成功包数”。

`rate`是操作调度预算，不是射频PPS，也不保证实测速率。100条无响应写、100条有响应读取和100次建链，涉及的处理量完全不同；比较强度应固定场景、目标状态、参数与计数口径，再比较实际达成速率及独立业务指标。

## 5. 与DoS/可用性测试的关系

这些用例提供正常协议负载与连接生命周期刺激，可用于观察目标在负载下的可用性、错误和恢复。它们不是畸形帧注入、射频干扰、SMP配对风暴、任意链路层控制报文注入或全协议覆盖工具。

“有效执行”可由真实请求/响应或生命周期交互证明；“造成服务退化”还需基线、独立业务成功率/延迟、目标状态和恢复记录。两类验收目的应在开始前写明，避免为证明工具能发包而强行要求目标失效。

## 6. 代码与接口入口

- [`cases.py`](../src/btdos/cases.py)：socket、SDP编解码、BLE用例与阶段事件。
- [`config.py`](../src/btdos/config.py)：场景、固定fixture、参数限值及预算。
- [`supervisor.py`](../src/btdos/supervisor.py)、[`capture.py`](../src/btdos/capture.py)：启动顺序、唤醒等待、采集与清理。
- [`discovery.py`](../src/btdos/discovery.py)、[`probe.py`](../src/btdos/probe.py)：限时发现与独立元数据探测。
- [BlueZ SDP工具实现](https://github.com/bluez/bluez/blob/master/tools/sdptool.c)：元数据探测接口依据，不代替本项目实测。

当前实测范围见[验证记录](validation.md)，实际操作见[使用指南](usage.md)。
