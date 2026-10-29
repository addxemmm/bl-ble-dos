#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in ''|--dev) ;; *) echo 'Usage: scripts/install.sh [--dev]' >&2; exit 2;; esac
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
chmod +x "$ROOT/bt-dos"
printf '\nInstalled. Start: %q doctor\n' "$ROOT/bt-dos"
printf 'System tools (install explicitly): sudo apt install bluez tshark python3-venv\n'
if [[ "${1:-}" == --dev ]]; then
  (cd "$ROOT"; "$ROOT/.venv/bin/python" -m unittest discover -s tests -v)
fi
