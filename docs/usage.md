# 安装与使用

[返回首页](../README.md) · [硬件](hardware.md) · [证据](evidence.md)

## 1. 安装入口

完整的环境要求、六阶段安装、可选依赖、权限准备、升级和故障定位统一放在[安装指南](installation.md)，[Windows显式部署](installation.md#windows-deploy)也在该页说明，避免多处步骤不一致。

```bash
bash scripts/install.sh --help
bash scripts/install.sh
./bt-dos --version
```

默认安装会通过pip联网安装项目依赖；第6步执行依赖一致性及CLI入口自检，不自动启动蓝牙服务、扫描或连接设备。安装成功、Linux控制器就绪、目标协议验证是三个不同层次。

首次使用建议先完成下节离线演示，再按安装指南准备控制器和权限。BR/EDR与BLE各用例的原理和选择依据见[技术说明](protocols.md)。

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

0.3.0 向导按六步组织：**环境 → 用例与适配器 → 选择目标 → 测试参数 → 证据配置 → 复核运行**。普通字段中 `b` / `back` 返回上一步，`q` / `cancel` 回首页。草稿仅保留在当前会话内存，返回首页不会自动启动负载；退出应用后不恢复草稿。首页演示、诊断或一次向导操作完成后回到菜单。

确认前应核对目标地址、实验隔离、停止条件及恢复方法。实际运行时计划写入新运行目录的 `plan.json`，不建立长期保存目标信息的项目配置。

### 临运行唤醒与失败后保留计划

真实 Linux 输入/输出 TTY 中，向导按以下顺序执行：

`检查计划与一次新鲜适配器快照 → 采集就绪 → 等待目标唤醒 → 基线 → 用例 → 恢复与封存`

先完成服务选择、参数和 sudo 权限准备，看到“等待目标唤醒”后，再让耳机进入配对/可连接模式或让 BLE 外设开启广播。**Enter** 或 `r` 后回车确认已准备，`q` 后回车、EOF 或 Ctrl+C 取消；最多等待60秒，超时不启动worker。确认只代表人工准备完成，不证明目标可发现、可连接或未休眠。等待期间继续检查STOP、采集存活和证据空间，不增加扫描、连接、保活或后台自动重试。

基线仍按原计划在确认之后独立执行，不会为避免休眠而偷偷缩短。若目标的可连接窗口短于基线，应在试验方案中明确调整目标状态或观察窗口，不能把正常电源策略直接判为DoS。

等待是向导的运行时条件，不增加JSON计划字段。启用时总墙钟预算增加60秒，采集独立终止期限同步增加；worker负载预算不变。结果的 `target_ready`、`runtime_wall_budget_s` 以及就绪事件记录条件与实际等待时间。简易TTY输入也支持；`run --plan ... --confirm ...` 与非TTY调用维持原行为，不读取新增就绪输入。

普通失败或就绪超时在**清理明确完成且封存校验通过**后，向导提供 `r` 保留计划返回复核页。它不立即重发：需要再次选择运行、核对完整目标地址、准备权限和唤醒目标，并创建新的独立证据目录。目标、服务与参数保留在当前草稿；旧计数和旧原始证据不续写。取消/结束不困在重试循环，强制清理、清理未确认、未封存或清单损坏的记录不提供一键重试入口。

BR连接阶段会单独记录尝试、成功/失败、耗时及真实异常；worker错误进入结果与报告摘要。“连接未建立”与“已开始负载后的失败”需结合事件区分，不自动标记成休眠或拒绝服务。

### 方向键、字段编辑与备用输入

- 菜单使用 **↑↓ 选择、Enter 确认**；页面显示的 `s`、`m`、`b` 等快捷键仍可直接选择。
- 文字字段支持 **←→** 移动光标、**Home / End** 到行首尾、**Backspace / Delete** 删除；↑↓ 可回看同一字段的当前进程历史。
- 字段历史彼此隔离，只保存在内存，不建立持久历史文件；草稿和探测快照退出后也不恢复。终端自身的滚屏或会话录制由使用者管理。
- 输入不兼容的终端可用 `BTDOS_SIMPLE_INPUT=1 ./bt-dos` 切换简单输入。**备用模式不支持方向键编辑**，使用所示编号或字母后回车。它不是原始终端箭头序列的编辑修复。

参考 [prompt_toolkit 输入文档](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html)与[选择菜单文档](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_a_choice.html)。

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

名称、RSSI 和已配对状态只是观察元数据，不证明目标身份或传输承载。选定目标后自动展示已有元数据，但不因此自动连接。更换设备、适配器或传输会清除旧探测快照、SDP 服务和 GATT 特征选择；适配器或传输变化还会清除旧目标。包括切换场景期间变更目标的情况，都须重新核对服务参数。扫描异常不会自动选择候选，可处理后显式重扫或手动输入。

### 参数页的服务来源

| 输入 | 操作 |
| --- | --- |
| `p` | 获取/刷新当前单目标服务信息；先展示主动通信和共享连接风险，确认默认 `n`，明确接受后才探测 |
| `s` | 存在适用结果时，从已探测的服务 / 可读特征中选择 |
| `m` | `br-sdp` / `ble-read` 高级手动输入；无探测结果时也可用，须独立核对 |
| `k` | 保留已核对的服务/特征；`br-channel` / `ble-cycle` 可直接继续，无需探测 |

SDP 列表仅将 `ServiceClassIDList` 的服务 UUID 写入计划，不把协议描述中的 L2CAP / RFCOMM UUID 当服务。可映射到 Bluetooth 基础 UUID 的 UUID16 可选；自定义 UUID128 仅展示，不转换成任意 UUID16。记录句柄、PSM 和 RFCOMM channel 仅元数据，**不改变 `br-channel` 固定 PSM `0x0001`**。

BLE 仅连接和枚举服务 / 特征 UUID、描述、属性、句柄，不读取特征值、不配对、不写入、不订阅。列表只允许选择具有 read 属性且 UUID 唯一的特征，保存完整 UUID 而非瞬时句柄。重复 UUID 仅展示，需高级手动核对，不能自动取第一项；手动 `0xHANDLE` 需现场重新核对数据库。read 属性不是访问权限或无副作用保证，选择后仍需确认厂家允许读取且没有业务副作用。

探测完成不自动选参数，也不自动启动测试。失败、无结果、部分结果或清理未确认均应查看警告，手动输入不应掩盖这些状态。探测时机、预算与保存边界见下文。

### 预算与最终复核

本节预算表用于原四个场景；第五场景的独立边界见下节。

| 预设 | 请求型窗口 / 速率 / max_ops | 循环次数 | 基线 / 恢复 |
| --- | --- | --- | --- |
| 默认 `default` | 10 秒 / 10 req/s / 100 | 5 | 5 / 10 秒 |
| 快速预检 `quick` | 5 秒 / 1 req/s / 5 | 3 | 2 / 5 秒 |
| 自定义 `custom` | 逐项编辑并校验 | 逐项编辑并校验 | 逐项编辑并校验 |

预设不扩大协议上限；请求型与循环型参数各按对应场景生效。默认 `rate=approved_rate=10`、`max_ops=100`，对应 10 秒请求窗口；快速预检保持 `rate=approved_rate=1`、`max_ops=5`，对应 5 秒请求窗口；均保持 `hold=2`、`gap=3`。硬上限仍为 `max_ops<=400`，显式写有 `max_ops=400` 的旧计划仍通过原边界校验，不会被默改。复核页可按 `1` 修改用例/适配器、`2` 修改目标、`3` 修改参数、`4` 修改证据，或按 `j` 查看 JSON；保留其他配置，不必从头重填。依赖变化仍会要求补充目标或特征。

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

`btmon`采集子进程使用 `sudo -n`；仅显式设置BLE连接间隔时，保留TTY的supervisor还会经内部文件RPC执行限定argv的可选参数helper（`sudo -n`），不提升worker权限，不在后台等待密码。临时请求/响应文件保留在run目录并随运行封存，内部RPC路径不是用户Plan字段。helper预算15秒，worker等待上限20秒。不要用 `sudo ./bt-dos` 启动整个向导；项目不自动添加或扩大sudoers、不修改capabilities或调整系统安全策略。

### 专用fixture写入与BLE连接参数

`ble-write-command` 是第五场景，**非只读模式**。仅对专用测试fixture的固定服务 `9fbb0001-6284-4e2e-9c8e-3b7a2d546f10`、固定写特征 `9fbb0002-6284-4e2e-9c8e-3b7a2d546f10` 生效；不接受Generic Access、DFU、业务/未知特征、句柄或任意载荷。fixture特征须同时实现 `read` 与 `write-without-response` 属性，供同连接服务探针、末值回读与写命令使用。向导会单独显示模式变化，要求默认否的明确确认，不从其他特征列表选写目标；更换目标/适配器后重新确认。

第五场景默认预设独立为 `rate=approved_rate=100`、`duration=10`、`max_ops=1000`；快速预检为1cmd/s、5秒、最多5次。硬边界100cmd/s、10秒、1000次，且速率不超过计划批准值；每条命令固定 `BTDS` + uint32序号，共8字节，累计不超过8000字节。旧四场景仍受原20req/s、60秒、400次边界限制，Plan全局默认仍为10req/s、10秒、100次。切回旧场景会恢复旧默认预算。

以下JSON保存为 `PLAN.json` 后，可通过已有 `./bt-dos run --plan PLAN.json --confirm TARGET` 入口运行。`target` 应填写已核对的单设备MAC，TARGET与其一致；示例中的本地fixture地址不可冒充现场实测地址：

```json
{
  "schema_version": 1,
  "case": "ble-write-command",
  "adapter": "hci0",
  "target": "02:00:00:00:00:01",
  "service_uuid": "9fbb0001-6284-4e2e-9c8e-3b7a2d546f10",
  "characteristic": "9fbb0002-6284-4e2e-9c8e-3b7a2d546f10",
  "write_test_confirmed": true,
  "rate": 100,
  "approved_rate": 100,
  "duration": 10,
  "max_ops": 1000,
  "capture": "hci"
}
```

BLE三种场景均可在JSON增加可选 `"ble_interval_ms": 7.5`，请求连接间隔；向导目前不另设此高级输入字段。允许7.5..50ms且为1.25ms倍数，拒绝布尔值/非有限值；BR/EDR拒绝此字段。省略或 `null` 保持BlueZ默认，不改变控制器全局设置；指定后由supervisor执行15秒预算的参数helper，worker等待最多20秒。read/write场景额外预留20秒，cycle场景每轮连接各预留20秒（共 `20 * cycles` 秒）。请求成功不等于目标接受，须在HCI连接更新事件核对实际协商间隔及是否实际生效。旧场景设置 `service_uuid` 或启用 `write_test_confirmed` 会被拒绝，避免把fixture写入参数误带入只读计划。

写入worker内的 `probe_phase=baseline/load/recovery` 表示**实际连接态**的同链路服务读取探针：先观察 `baseline` 秒，再在写入负载期间并行观测，最后观察 `recovery` 秒。固定1Hz、每个读取事务1秒超时；250ms为工程慢阈值，连续慢2次或任一读取失败即停止本档写入，并继续恢复观察。这是同链路通信SLA，不是独立全设备业务观测；认证条件未知时，访问失败也不能直接判作退化，更不声称250ms是法规阈值。外层supervisor仍分别执行抓包就绪后的基线观察及worker退出后的恢复采集，两组窗口不可混为同一组探针测量。write的worker预算为 `duration + baseline + recovery + 35` 秒；指定间隔再加20秒。总墙钟预算还包含外层基线/恢复及封存余量。

无响应写API完成只表示本地提交，不证明目标接收、处理或完成；队列可能延后线上发送，cmd/s并非空口PPS。以同一BLE链路探测延迟/错误时，只反映该链路与适配器的共享争用，不应单独解释为全设备DoS、业务退化或目标系统故障。需独立业务观测与基线/负载/恢复证据佐证；本轮新增接口仅经离线验证，未据此声称真实服务已退化。

## 4. 诊断、枚举与发现

```bash
./bt-dos doctor
./bt-dos doctor --json
./bt-dos adapters
./bt-dos scan --transport le --adapter hci0 --seconds 5
./bt-dos scan --transport bredr --adapter hci0 --seconds 5
./bt-dos scan --transport le --adapter hci0 --seconds 5 --json
./bt-dos inspect --transport le --adapter hci0 --target TARGET --confirm TARGET
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

### 主动能力探测与私有 JSON 快照

`inspect` 与不连接的 `scan` 不同，它明确发起 SDP 服务查询，或连接 BLE 并枚举 GATT 元数据。`--confirm` 必须提供并与目标完整 MAC 一致；没有确认或确认不匹配时不启动探测。不需要 `sudo`，不修改配对、信任或适配器权限。

兼容入口 `ble-info` 在0.3.0也要求 `--confirm TARGET`，并使用同一探测后端；旧版不带确认的调用需更新。建议统一使用 `inspect`。

```bash
./bt-dos inspect --transport le --adapter hci0 --target TARGET --confirm TARGET
./bt-dos inspect --transport bredr --adapter hci0 --target TARGET --confirm TARGET --local-address LOCAL_ADDRESS
# 如需保存，使用已忽略的私有目录；不要把真实设备快照提交Git
umask 077
mkdir -p .local
./bt-dos inspect --transport le --adapter hci0 --target TARGET --confirm TARGET --json > .local/device-snapshot.json
```

`--json` 的标准输出只包含 JSON，不混入 Rich 表格或进度文本。快照包含目标、适配器、传输、服务/特征、少量元数据、时间，以及 `status` / `cleanup_status` / `warnings`；即使有服务条目，也须检查完成和清理状态。名称、地址、配对状态、UUID 等可能标识设备，分享前去标识化。向导只保留内存、不自动保存；CLI 仅由使用者显式重定向保存。

`completed`、`no_results` 的CLI退出码为0，`interrupted`为130，其他失败为1；`no_results`不证明不存在其他服务。参数/缺少确认错误在探测前结束，须查看标准错误。

探测在测试采集与基线开始**之前**单独发生，**不属于本次运行证据包，也不证明负载时的设备状态**。发现后可能发生服务变化、地址变化或权限变化；`ServicesResolved` 等快照不是未来可读保证。正式测试仍由 supervisor 先等待采集就绪。

| 阶段 | 预算 / 限制 |
| --- | --- |
| SDP `sdptool browse --xml` | 执行预算 15 秒，stdout + stderr 合计最多 1 MiB；记录数与 XML 结构限额，拒绝 DTD/实体声明 |
| SDP 进程清理 | 超时/出错/取消后停止保留输出、丢弃式排空PIPE；terminate/kill各另有2秒等待，末次排空另有2秒；未确认退出如实标记 |
| BLE 找目标 | 同适配器扫描，8 秒预算 |
| BLE connect / 枚举 | 一次应用层 connect 调用，10 秒连接预算 |
| BLE disconnect | 另有 5 秒预算，失败标记 `unconfirmed` |
| D-Bus 元数据调用 | 每次 5 秒预算，包含连接/状态查询/关闭确认等独立步骤 |

以上是分阶段预算，**不是整个操作的硬墙钟保证**：特别是 Bleak 的取消传播和库内清理可能延长实际耗时。应用不自动重试；Bleak / BlueZ 底层仍可能重试或复用连接。已连接目标前置拒绝，BLE 发现后连接前再次检查；同用户适配器锁只协调本工具，不锁住其他 BlueZ 客户端。请用独立实验控制器并停止其他程序连接目标，检查之后仍存在竞态，探测结束断开可能影响同期连接。

SDP 先通过 BlueZ `Adapter1.Address` 核对 `hciN` 对应本地地址，再以固定参数调用普通用户 `sdptool`，不通过通用 `Device1.Connect` 探测 BR/EDR。`browse` 可能递归服务组，因此“一次有界调用”不是一个 SDP 请求；退出码 0、不为空的服务表、或空结果都不证明清单完整。CLI 和向导复用同一后端，失败不静默当成功。

实现边界参考 [BlueZ sdptool 源码](https://github.com/bluez/bluez/blob/master/tools/sdptool.c)、[Bleak 客户端文档](https://bleak.readthedocs.io/en/stable/api/client.html)及锁定的 [Bleak v3.0.2 BlueZ 客户端源码](https://github.com/hbldh/bleak/blob/v3.0.2/bleak/backends/bluezdbus/client.py)。这些是接口依据，不是本项目目标或 nRF 实测结论。

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
  "rate": 10,
  "approved_rate": 10,
  "duration": 10,
  "max_ops": 100,
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

`--confirm` 必须与计划目标一致。程序先校验计划，再创建运行快照、启动采集并等待就绪，之后才启动 worker。请求型场景到达持续时间或操作数上限时停止发起新 API 操作，不因配置了较大 `max_ops` 而延长请求窗口；已发起请求可在最多 3 秒的单事务响应预算内排空（BR send/recv 共享此预算），清理另受现有监督预算约束，BlueZ / 控制器排队可能使线上数据超出窗口。`rate` 是请求预算，不保证实测速率，也不等于空口 PPS。信道/连接循环以循环次数、保持/间隔和监督超时为边界，不以 `duration` 作为总时长；连接准备与基线/恢复也会增加全程时长。基线与恢复窗口用于限定前后观察时间，不自动提供外部业务监测。

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
| `rate` | `10`，范围 `0.1–20`；快速预检为 `1` | 请求速率预算，单位 req/s；不保证实测速率，不等于空口 PPS |
| `approved_rate` | `10`；快速预检为 `1` | 本次实验明确接受的请求速率上界；`rate` 应不高于此值，项目仍限制实际配置速率不超过 20 |
| `duration` | `10`，范围 `1–60` | 请求型场景负载窗口秒数，不包含全部前后准备时间 |
| `max_ops` | `100`，范围 `1–400`；快速预检为 `5` | 请求型场景操作次数上限，不保证一定达到 |
| `cycles` | `5`，最高 `10` | 顺序连接循环上限 |
| `hold` | `2`，范围 `1–5` | 每轮连接保持秒数 |
| `gap` | `3`，范围 `3–30` | 循环之间间隔秒数 |
| `baseline` | `5`，范围 `0–60` | 负载前观察窗口秒数 |
| `recovery` | `10`，范围 `0–120` | 负载后恢复观察窗口秒数 |
| `capture` | `hci` / `hci+nrf` | HCI 必选；nRF 是额外 BLE 空口采集 |
| `nrf_port` | 如 `/dev/ttyACM0` | 选择 `hci+nrf` 时所需的 sniffer 串口 |
| `output_root` | `runs` | 新运行目录的父目录 |
| `external_business_probe` | `false` | 外部业务探针声明；单凭此字段不构成证据 |
| `external_ecu_observation` | `false` | 外部目标系统观察声明（历史字段名）；单凭此字段不构成证据 |

`approved_rate` 是试验预算字段，不是身份认证或访问权限机制；填写较大的预算也不会提升项目的 20 req/s 配置上限。

未用于当前场景的参数不会把该场景扩展为另一种测试。例如 `cycles` 不会让 `br-channel` 变成 ACL 链路循环；`rate` 也不保证 BLE 连接循环每秒完成相同次数。目标应答、连接时延和主机调度可能降低实际负载。

### BR/EDR 计划差异

将 `case` 改为 `br-sdp` 或 `br-channel`，添加 `"local_address": "LOCAL_ADDRESS"`，并删除 BLE 特征字段。运行前核对本地地址确实属于计划里的 `adapter`。`br-sdp` 的目标应支持所选服务 UUID；服务缺失与设备失去可用性是不同结论。

`br-channel` 固定使用 SDP 的 PSM `0x0001`，只建立、保持和关闭 L2CAP 信道，不发送业务负载，不开放任意 PSM 配置。控制器可能保留底层 ACL，因此观察到本地信道关闭不等于目标 ACL 已断开。`br-sdp` 当前是首响应页查询，解析器限制总记录数不超过16；计数等检查通过后，非空 SDP continuation 会停止并标记 `needs_continuation`。更大总记录数会触发解析限制，不应直接解释为目标故障；本用例不实现完整服务枚举。

### 可选 BLE 空口采集

BLE 计划使用 `"capture": "hci+nrf"`，并添加 `"nrf_port": "/dev/ttyACM0"`。需提前安装 Nordic `nrfutil` / `ble-sniffer` 并独立完成 sniffer 固件准备；本项目不刷写设备固件。

监督进程使用的接口形态：

```bash
nrfutil ble-sniffer sniff --port PORT --follow TARGET --output-pcap-file PATH
```

`PORT`、`TARGET`、`PATH` 是占位符。nrfutil 的接口超时参数不代表总录制时长；生命周期由本项目的监督进程控制。项目命令行不传入配对密钥。详情见[硬件指南](hardware.md#nrf-sniffer)。

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
| `run` 完成 | `0` | 执行流程完成，不自动判定目标业务通过 |
| `run` 运行失败 | `1` | 环境、采集或执行失败，查看事件与报告 |
| `run` 用户停止 | `130` | 中止运行；已保存内容仍需按完整性检查 |
| `verify` 清单一致 | `0` | 文件与清单一致 |
| `verify` 缺失 / 损坏 / 不一致 | `1` | 需保留原始目录并调查差异 |

证据不足不是自动的“DoS 失败”或“业务失败”；请求返回成功也不是自动 PASS。

## 常见问题

- **更新后仍是旧输入界面**：退出旧 `bt-dos` 进程再启动；确认执行的是当前仓库入口及其 `.venv`。
- **方向键显示 `^[[A` 等字符**：确认是交互终端且已重启更新后的进程；简单输入备用模式不支持方向键编辑。
- **找不到 `btmon`**：确认安装了系统 BlueZ 工具，查看 `doctor` 的路径检测。
- **`sudo -n` 失败**：在同一会话先运行 `sudo -v`，然后重新运行；不自动放宽系统权限。
- **没有适配器**：检查 `rfkill`、Bluetooth 服务及 USB 直通；参见硬件指南。
- **GATT 读取被拒绝**：确认特征的 read 属性及目标配对/加密要求，不将访问拒绝等同于目标故障。
- **空口文件为空或跟随失败**：核对串口、固件、当前目标地址和采集开始时机；文件存在不等于捕获完整。
- **退出成功但结论不充分**：查看证据缺口；项目只对现有观察作结论，不补造业务或目标系统数据。
