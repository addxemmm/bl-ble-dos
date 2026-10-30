# 安装与环境准备

[返回首页](../README.md) · [使用指南](usage.md) · [硬件准备](hardware.md) · [协议与用例](protocols.md) · [证据与报告](evidence.md)

本页集中说明安装入口、安装副作用、软件验收和 Linux 蓝牙环境准备。安装成功只表示软件检查通过，不表示适配器、抓包权限、目标连接或任何实际用例已经验证。

## 1. 前置条件

- 实际设备运行：Linux（安装脚本面向 Ubuntu / Kali）、Bash、Python **3.10+**、Python venv/ensurepip、BlueZ，以及可用的交互终端。
- 主动控制器：Linux 可识别的 USB 双模 BR/EDR + BLE 适配器。只执行 BLE 时可用经验证的 LE-only 控制器，但它不支持 BR/EDR 用例。
- 虚拟机：将 USB 适配器独占直通 Linux 客户机，不是仅让宿主机连接蓝牙。宿主与客体抢占、USB 重连或电源管理都可能影响状态。实际 `hciN` 编号会变化，后续均以本次枚举结果为准。
- 网络：默认安装通过 pip 获取构建及项目依赖；它不是离线安装。第 6 阶段的软件检查不需要网络或适配器。

Python 依赖由仓库 `pyproject.toml` 和 pip 处理，不要将依赖逐个装到系统 Python。当前声明：`rich>=13.9,<15`、`prompt-toolkit>=3.0.52,<4`、`bleak==3.0.2`，Linux 另需 `dbus-fast>=5,<6`；构建后端为 `setuptools>=68`。升级后的真实声明以该文件为准。

### 系统命令与用途

| 命令 | 用途及缺项影响 |
| --- | --- |
| `bluetoothctl`、`btmgmt` | BlueZ 控制器信息与能力检查；缺少时仍可安装和 demo，实际环境检查可能不完整 |
| `btmon` | HCI 采集；实际运行需工具及当前主机允许的权限 |
| `sdptool` | BR/EDR 显式服务探测；缺少时该探测不可用，不应误作目标无服务 |
| GNU `timeout` | live 外部命令的预算控制；仅有同名的其他实现不代表兼容 |
| `sudo` | 当前普通用户 live 采集及受限 helper 的提权路径，按主机既有策略准备；不需要它即可 demo |
| `tshark`（可选） | 抓包索引/解码；不负责生成本机 HCI 采集，不在脚本自动安装范围 |
| `nrfutil`（可选） | 额外 BLE sniffer；还需匹配的 `ble-sniffer` 命令、设备及固件，不是普通双模控制器的替代品 |
| `hcitool`（可选） | 仅显式 BLE 连接间隔 helper 需要；缺少时保留默认连接参数即可 |

发行版的 `bluez` 包不保证包含全部历史工具。用 `command -v` 核对本机命令，并按发行版包查询结果补齐所需功能；不要因某项可选工具缺失就更改全局权限。

```bash
# 下面以及本页其余系统命令均为手动操作示例，并非安装脚本已执行的结果。
command -v bluetoothctl btmgmt btmon sdptool timeout sudo
command -v tshark nrfutil hcitool
timeout --version
dpkg-query -L bluez  # Ubuntu / Kali：查看已安装包实际提供的文件
```

## 2. 默认安装与显式系统安装

先准备源码。已有当前工作树可直接使用；从仓库获取时需要Git。开发预览尚未发布，实际内容以取得的提交/工作树为准：

```bash
git clone https://github.com/addxemmm/bl-ble-dos.git
cd bl-ble-dos
```


在项目根目录以**普通用户**运行，无需激活虚拟环境：

```bash
bash scripts/install.sh --help
bash scripts/install.sh --non-interactive
```

默认不执行 apt；创建或复用项目 `.venv`，通过它执行 `pip install -e`，并将项目启动器 `bt-dos` 设为可执行。后续使用 `./bt-dos`。

确实需要安装系统依赖时，再显式选择以下入口。非交互模式下 apt 使用 `sudo -n`，权限未准备即失败，不等待密码：

```bash
# 需要系统包安装权限时，在当前普通用户终端手动准备凭证
sudo -v
bash scripts/install.sh --install-system --non-interactive
```

该选项只允许 `apt-get update` 及 `apt-get install -y python3 python3-venv python3-pip bluez`；通过 `env DEBIAN_FRONTEND=noninteractive` 调用。不自动安装 `tshark`、nRF 工具或额外软件源，不改 sudoers/capabilities/设备权限。软件包安装自身的服务动作由发行版策略决定，脚本本身不调用启动/启用蓝牙服务。

| 选项 | 语义 |
| --- | --- |
| `--help` | 显示帮助即退出，不安装、不联网 |
| `--non-interactive` | pip 禁止输入；sudo 使用 `-n`；标准输入不是 TTY 时也自动启用 |
| `--install-system` | 显式允许上述 apt 操作，仍需网络与现有管理员授权 |
| `--dev` | 软件检查通过后追加离线单元测试，不添加无线验证 |

请勿使用 `sudo bash scripts/install.sh` 作为普通安装方式。root 默认会在建立虚拟环境前退出；root 显式使用 `--install-system` 可完成限定 apt 阶段，但随后仍退出，不创建 root 所有的 `.venv`。

## 3. 六阶段行为与成功判据

1. **运行环境**：确认 Linux，输出 Python 版本要求；非 Linux 立即退出。
2. **系统依赖**：默认仅查找命令；显式选项才执行 apt。核对普通用户与 Python 3.10+，区分 BlueZ、GNU timeout、权限及可选工具缺项。命令缺失不阻止离线软件安装，但不代表对应 live 功能可用。
3. **虚拟环境**：创建或复用 `.venv/bin/python`；复用时检查 Python 版本及虚拟环境身份。损坏或其他平台的现有 `.venv` 保留并报错，不覆盖或删除。检查该解释器的 pip 入口。
4. **项目安装**：通过 `.venv` 执行 `pip install -e`，可能访问包索引；失败输出精确重试命令，停止后续步骤。只调整仓库启动器的可执行位。
5. **硬件提示**：说明 USB 直通及准备顺序；不扫描、不连接、不采集、不运行负载。
6. **离线软件验证**：执行 `.venv/bin/python -m pip check`，检查已安装包声明的依赖一致性；然后执行 `.venv/bin/python -m btdos --version`，确认 CLI 入口能启动。任一非零退出都显示修复/重试命令并停止，不打印安装完成。`--dev` 再执行 `python -m unittest discover -s tests -v`，失败同样停止。

只有所选检查全部返回成功才输出“软件安装完成”。`pip check` 不是导入、功能、设备或固件测试；版本号输出也不证明硬件就绪。脚本**不自动执行** `doctor`、`adapters`、`demo`、扫描、服务/rfkill/适配器电源操作；最后按顺序显示可手动运行的下一步。

## 4. 分层验收

### 软件与证据流程：无需适配器

```bash
./bt-dos --version
./bt-dos demo --output runs/demo
# RUN_DIR 替换为 demo 打印的实际时间戳/随机子目录，不是 runs/demo 父目录
./bt-dos verify RUN_DIR
./bt-dos report RUN_DIR
```

demo 使用标明 `simulation` 的合成数据，不扫描、不连接、不采集，也不证明真实设备能力。版本入口、demo 和证据清单校验分别验证不同软件路径。详细文件说明见[证据与报告](evidence.md)。

### 蓝牙环境：手动只读诊断

```bash
./bt-dos doctor
./bt-dos adapters
systemctl status bluetooth --no-pager
rfkill list bluetooth
bluetoothctl list
```

`doctor` / `adapters` 是只读诊断，但会调用 BlueZ/管理接口及外部工具，可能耗时；不是完全不接触蓝牙环境的离线软件检查。它们不扫描、不配对、不连接，也不启动服务。退出成功或初步 `ready_for_live` 不保证完整能力、抓包就绪或目标可连接。若服务未运行，按下一节手动准备后重新诊断。

### 操作准备：交互向导

```bash
./bt-dos wizard
```

环境问题解决后再进入向导；扫描、探测和正式执行有各自明确操作入口，不因安装完成自动发生。步骤、目标准备与失败后重试见[使用指南](usage.md#3-交互向导)。

## 5. 手动准备 Linux 服务、电源和权限

以下是有副作用的**手动示例**，仅在确认本机需要该变更后执行。项目安装不代替管理员的服务与设备管理：

```bash
# 服务未运行时，按主机策略手动启动；不等同于设置开机启用
sudo systemctl start bluetooth
# 仅存在软件阻断且允许解除时使用；硬件阻断需处理实体开关/宿主设置
sudo rfkill unblock bluetooth
```

先用 `./bt-dos adapters` / `bluetoothctl list` 找到当前实际 `hciN` 与本机控制器地址，不假定永远是 `hci0`。例如需要为指定控制器上电时，进入 `bluetoothctl`，将 `LOCAL_ADDRESS` 替换为该控制器的实际地址：

```text
list
select LOCAL_ADDRESS
show
power on
quit
```

再次核对所选控制器、地址、支持的 BR/EDR/LE 模式及当前设置。BR/EDR 计划的 `adapter` 和 `local_address` 必须指向同一控制器；完整要求见[硬件准备](hardware.md#linux-检查)。服务运行、电源开启不等于目标处于可连接状态。

实际 HCI 抓包按当前策略由普通用户启动 supervisor，再使用 `sudo -n` 调用采集工具。交互向导可在当前 TTY 调用原生 `sudo -v`；密码交给 sudo，不由应用读取或保存。非交互运行须提前准备同一终端/会话中的凭证：

```bash
sudo -v
./bt-dos wizard
```

凭证可能过期或受 sudo 的终端策略约束；在另一个终端、SSH 会话或自动任务中刷新，不保证当前会话可用。不要用 root 向导、全局 NOPASSWD、修改 capabilities 或关闭权限检查作为默认修复。普通用户的 BlueZ D-Bus 及可选串口访问还受系统自身策略约束；缺少 sudo 时先与主机管理员确认 live 权限路径，离线 demo 不受影响。

## 6. 可选 tshark 与 nRF 角色

`tshark` 用于对已有抓包索引/解码，不是采集工具 `btmon` 的替代品。需要时由管理员按发行版策略单独安装、处理包管理问答；安装器不自动安装它，也不改变抓包组权限。

`nrfutil` 仅用于额外的 BLE 空口 sniffer 路径，还需匹配的 `ble-sniffer` 命令、固件、USB/串口与权限。安装项目不自动刷写硬件，不把串口枚举视为 sniffer 就绪。同一 nRF 板的 sniffer 固件与主动 HCI controller 固件是不同角色；后者不是 BR/EDR 双模控制器。准备与限制见[硬件指南：nRF52840](hardware.md#nrf52840-roles)。

## 7. 升级与本地修改保护

先退出已有 `bt-dos` 进程，旧进程不会热加载更新。检查 `git status --short`；将需要保留的源码变更提交到适当分支或单独备份，证据及私有配置另行保存，不将敏感记录混入提交。工作树整理后，再按项目既定 Git 分支流程获取并合并更新，例如跟踪分支已配置时执行 `git pull --ff-only`。

```bash
git status --short
# 确认本地修改已妥善保留、分支/远端正确后再更新
git pull --ff-only
bash scripts/install.sh --non-interactive
./bt-dos --version
```

安装器复用现有有效 `.venv`，重新执行 editable 安装及软件检查。不需要删虚拟环境来升级；遇到冲突先看具体依赖错误。不使用 `git reset --hard`、`git clean` 或直接删除 `.venv` 作为更新步骤。本地未提交代码不等于已部署提交，核对版本还应结合实际 commit/工作树及进程是否重启。

## 8. 常见失败与修复

| 现象 | 处理 |
| --- | --- |
| pip 网络、代理、证书或索引错误 | 保留 `.venv`，检查脚本输出的具体原因和已有网络配置，修复后用输出的精确 pip 命令或重新运行安装器；不添加不可信源、不禁用证书校验、不用 sudo pip |
| 缺 `venv` / `ensurepip` | 安装与当前 Python 对应的 venv 支持；脚本给出系统包提示，不会默认执行 apt |
| `.venv` 不完整、来自 Windows 或版本太旧 | 先退出使用它的进程，将目录移动到唯一备份位置，再重跑安装器；不覆盖原目录。Windows 的 `Scripts/python.exe` 不是 Linux 的 `bin/python` |
| 虚拟环境没有 pip | 使用脚本打印的该环境解释器执行 `-m ensurepip --upgrade`，再重跑；若发行版限制 ensurepip，核对 Python/venv 系统包 |
| `pip check` 失败 | 按缺项/冲突输出在项目 `.venv` 修复依赖，执行 `-m pip install -e` 与 `-m pip check` 后重跑安装器；入口版本成功不替代依赖检查 |
| CLI 版本检查失败 | 检查导入堆栈、editable 安装及项目解释器，按输出重新安装并执行 `-m btdos --version`；不误报硬件故障 |
| root 提示 | 退出 root/sudo shell，重新用普通用户运行；已有文件属主问题交给管理员按实际路径处理，不递归放宽所有权限 |
| Bluetooth 服务不运行、被阻断或没有控制器 | 按[手动准备](#5-手动准备-linux-服务电源和权限)核对服务、rfkill、USB 直通和控制器电源，再运行诊断；软件安装通过并不排除此类问题 |

<a id="windows-deploy"></a>

## 9. Windows 开发与已提交源码部署

Windows 可编辑仓库、运行适用的单元测试或离线 demo；Linux Bash 安装集成测试在 Windows 跳过。Windows 测试通过不代表 Linux BlueZ、USB 直通或 live 抓包可用，也不将 Windows `.venv` 复制为 Linux 环境。

现有 `scripts/deploy.ps1` 是显式 SSH/SCP 部署脚本，不由安装器调用：仅归档已提交的 Git `HEAD`，拒绝已跟踪的未提交/已暂存更改；未跟踪文件不在归档里。它检查远端路径、传输/解包源码后执行既有远端安装 `--dev` 和 `doctor --json`，不自动执行无线负载。运行前另行核对远端目录及修改备份；不能将本地未提交文件误认为已部署。本页不改变该脚本既有行为。入口为[现有部署脚本](../scripts/deploy.ps1)。


显式部署示例（先替换HOST、USER和远端目录，另行备份远端修改）：

```powershell
.\scripts\deploy.ps1 -HostName HOST -UserName USER -RemotePath /home/USER/workspace/bl_ble_dos
```

该命令有远端写入和安装副作用，不是文档检查命令；本次整理没有执行它。
