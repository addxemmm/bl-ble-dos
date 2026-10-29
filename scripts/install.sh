#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
NON_INTERACTIVE=0
INSTALL_SYSTEM=0
DEV=0

usage() {
  cat <<'HELP'
用法：bash scripts/install.sh [--non-interactive] [--install-system] [--dev]

Ubuntu / Kali 安装引导（Python 3.10+）。
  --help             显示帮助，不安装、不联网
  --non-interactive  不等待输入；sudo 使用 -n，缺少权限时立即退出
  --install-system   显式允许 apt-get update / install 系统依赖（需要提权）
  --dev              安装后运行离线单元测试

默认只检查系统、创建/复用项目 .venv，并通过 pip 联网安装项目依赖。
默认不执行 apt，不修改 sudoers、文件 capabilities 或蓝牙系统配置。
请使用普通用户运行；仅 --install-system 的 apt 步骤使用 sudo。
系统依赖：sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip bluez
可选的 tshark 不在自动安装范围内，避免其 debconf 权限问答。
HELP
}

for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
    --non-interactive) NON_INTERACTIVE=1 ;;
    --install-system) INSTALL_SYSTEM=1 ;;
    --dev) DEV=1 ;;
    *) printf '未知参数：%s\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -t 0 ]] || NON_INTERACTIVE=1
if (( NON_INTERACTIVE )); then
  export PIP_NO_INPUT=1
fi

fail() { printf '\n[失败] %s\n' "$*" >&2; exit 1; }
step() { printf '\n[%s/6] %s\n' "$1" "$2"; }
system_hint() {
  printf '修复命令：sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip bluez\n' >&2
}
retry_hint() {
  printf '以普通用户重试：bash %q --non-interactive\n' "$ROOT/scripts/install.sh" >&2
}

step 1 '检查运行环境'
[[ "$(uname -s)" == Linux ]] || fail '请在 Ubuntu / Kali Linux 中运行本脚本；Windows/macOS 可先使用离线演示。'
printf '目标系统：Linux；请使用 Python 3.10 或更高版本。\n'

step 2 '检查/安装系统依赖'
if (( INSTALL_SYSTEM )); then
  command -v apt-get >/dev/null 2>&1 || fail '未找到 apt-get；请通过当前发行版包管理器安装 Python 3.10+、venv 和 BlueZ。'
  APT=(env DEBIAN_FRONTEND=noninteractive apt-get)
  if [[ "$(id -u)" != 0 ]]; then
    command -v sudo >/dev/null 2>&1 || fail '未找到 sudo；请让管理员执行帮助中的系统依赖命令，再以普通用户重试。'
    if (( NON_INTERACTIVE )); then
      APT=(sudo -n env DEBIAN_FRONTEND=noninteractive apt-get)
    else
      APT=(sudo env DEBIAN_FRONTEND=noninteractive apt-get)
    fi
  fi
  printf '已显式允许修改系统：apt-get update，然后安装 python3 python3-venv python3-pip bluez（需要网络）。\n'
  "${APT[@]}" update || fail 'apt-get update 失败；检查网络、软件源和 sudo 权限后重试。非交互模式不会等待密码。'
  "${APT[@]}" install -y python3 python3-venv python3-pip bluez || fail '系统依赖安装失败；检查 apt 输出、软件源和权限，修复后重试。'
else
  printf '检查模式：不执行 apt，不修改系统权限。缺少依赖时可执行：\n'
  system_hint
fi
if [[ "$(id -u)" == 0 ]]; then
  retry_hint
  fail '当前为 root；停止在项目中创建 root 所有的虚拟环境。请退出 root/sudo 会话，使用普通用户执行上面的命令。'
fi
command -v python3 >/dev/null 2>&1 || { system_hint; fail '未找到 python3。安装系统依赖后重试。'; }
python3 -c 'import sys; print("Python:", sys.version.split()[0]); sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  system_hint; fail '需要 Python >=3.10；请安装受支持版本后，让 python3 指向该版本再重试。'
}
missing_tools=()
for tool in bluetoothctl btmon; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '已找到 BlueZ 工具：%s\n' "$tool"
  else
    missing_tools+=("$tool")
  fi
done
if (( ${#missing_tools[@]} )); then
  printf '[提示] 缺少 BlueZ 工具：%s；仍可安装和运行离线演示。\n' "${missing_tools[*]}"
  system_hint
fi

step 3 '准备项目虚拟环境（不使用 sudo）'
PYTHON="$ROOT/.venv/bin/python"
if [[ -e "$ROOT/.venv" ]]; then
  [[ -x "$PYTHON" ]] || fail "现有 .venv 不完整或不属于 Linux。请先将其移动到备份位置，再重试：$ROOT/.venv"
  "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.prefix != sys.base_prefix else 1)' || fail '现有 .venv 需要 Python >=3.10 且为有效虚拟环境。请将现有目录备份后重建。'
  printf '复用现有虚拟环境：%s\n' "$ROOT/.venv"
else
  python3 -c 'import venv, ensurepip' || { system_hint; fail 'Python 缺少 venv/ensurepip。安装与 Python 版本匹配的 python3-venv 后重试。'; }
  python3 -m venv "$ROOT/.venv" || { system_hint; fail '创建 .venv 失败；检查目录写权限及 python3-venv。若留下不完整目录，请先备份该目录再重试。'; }
fi
"$PYTHON" -m pip --version || {
  printf '修复命令：%q -m ensurepip --upgrade\n' "$PYTHON" >&2
  fail '虚拟环境缺少 pip；执行上面的修复命令后重试。'
}

step 4 '联网安装 Python 依赖'
printf '即将执行 pip install -e（可能访问包索引；完整输出如下）。仅安装到项目 .venv。\n'
if ! "$PYTHON" -m pip install -e "$ROOT"; then
  printf '重试命令：%q -m pip install -e %q\n' "$PYTHON" "$ROOT" >&2
  fail 'pip 安装失败；检查上方网络/代理/证书/包索引错误后重试。请勿使用 sudo pip。'
fi
chmod +x "$ROOT/bt-dos" || fail "启动器权限设置失败；请检查文件所有者后执行：chmod +x '$ROOT/bt-dos'"

step 5 '设备与虚拟机检查提示（不扫描、不连接无线设备）'
printf 'USB 蓝牙适配器须由当前 Linux 系统识别；虚拟机中请将 USB 蓝牙适配器直通到客户机，而非仅连接宿主机蓝牙。\n'
printf '后续 doctor 会检查工具、权限和适配器状态；本安装脚本不启动蓝牙服务、不扫描、不连接、不运行负载。\n'

step 6 '验证与下一步'
if (( DEV )); then
  printf '运行离线单元测试（--dev）。\n'
  (cd "$ROOT"; "$PYTHON" -m unittest discover -s tests -v) || fail '离线测试失败；检查上方失败用例输出。'
fi
printf '\n安装完成。请以普通用户运行以下绝对路径命令：\n'
printf '  %q doctor\n' "$ROOT/bt-dos"
printf '  %q wizard\n' "$ROOT/bt-dos"
printf 'doctor 的缺项提示需处理后再进行实际设备操作；安装成功不代表蓝牙环境已就绪。\n'
