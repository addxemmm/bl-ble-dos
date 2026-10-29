# bl-ble-dos

**Bounded Bluetooth availability workloads, with evidence you can review.**

![License: MIT](https://img.shields.io/badge/license-MIT-3b82f6)
![Runtime: Linux](https://img.shields.io/badge/runtime-Ubuntu%20%7C%20Kali-0f766e)
![Interface: Rich TUI](https://img.shields.io/badge/interface-Rich%20TUI-7c3aed)
![Status: development preview](https://img.shields.io/badge/status-development%20preview-f59e0b)

[中文主文档](README.md) · [Usage](docs/usage.md) · [Hardware](docs/hardware.md) · [Evidence](docs/evidence.md)

A Bash entry point and a Rich terminal wizard for Linux BR/EDR and Bluetooth LE availability experiments. A USB dual-mode Bluetooth adapter generates normal-protocol, bounded workloads; `btmon` records host HCI traffic. An additional nRF52840 with sniffer firmware can provide optional BLE over-the-air capture.

**Development preview: 0.2.0 is unreleased.** Badges describe the project, not CI results or hardware certification. No adapter or target compatibility claim is currently hardware-verified. Synthetic demonstrations are not live-test evidence.

![Synthetic Rich terminal preview, not hardware-test evidence](docs/assets/terminal-demo.svg)

## Quick start

```bash
git clone https://github.com/addxemmm/bl-ble-dos.git
cd bl-ble-dos
chmod +x bt-dos scripts/install.sh
./scripts/install.sh
./bt-dos doctor
./bt-dos demo --output runs/demo
# Replace RUN_DIR with the actual directory printed above, or copy its verify command
./bt-dos verify RUN_DIR
./bt-dos
```

`--output` is a parent directory: demo creates a new timestamped/random child and prints its actual path and verification command. The installer creates a local `.venv` and performs an editable installation. With no arguments, `bt-dos` opens the wizard in an interactive terminal, or shows help for non-interactive input/output. Live operations target Ubuntu/Kali with BlueZ; Windows is suitable for repository management, not the Bluetooth runtime.

## Cases and limits

| Case | Scope |
| --- | --- |
| `br-sdp` | Bounded BR/EDR SDP service queries |
| `br-channel` | Opens/holds/closes a BR/EDR L2CAP channel on SDP PSM `0x0001`, without application payload; not ACL link cycling |
| `ble-read` | No write operations are initiated; the manufacturer must confirm read-side effects |
| `ble-cycle` | Sequential connect/hold/disconnect cycles; not concurrent slot exhaustion |

Defaults: `1 req/s`; request workloads are bounded by `20 req/s`, a `60 s` duration, and `400` operations. Channel/connection workloads use at most `10` sequential cycles, `1–5 s` hold time, and `3–30 s` gaps, plus connection/supervision timeouts. Total experiment time can exceed 60 seconds. Rate also stays within the plan's `approved_rate`. These are software limits, not regulatory thresholds or guarantees of target tolerance.

The cases use normal protocol operations, not controller exploit injection or RF interference. Run in an isolated test setup with an agreed target, stop criteria, and a recovery procedure.

```bash
./bt-dos adapters
./bt-dos scan --transport le --adapter hci0 --seconds 5
./bt-dos scan --transport le --adapter hci0 --seconds 5 --json
./bt-dos ble-info --target TARGET --adapter hci0
./bt-dos run --plan PLAN.json --confirm TARGET
./bt-dos report RUN_DIR
./bt-dos verify RUN_DIR
```

Replace the uppercase placeholders. Scanning and service inspection are live radio operations. BR/EDR plans also identify the local adapter address.

## Guided interaction in 0.2.0

Six steps cover environment, case/adapter, target, parameters, evidence, and review. Use `b` to go back and `q` to return home; the draft lives only in the current session's memory. Demo and diagnostics return to the home menu. Review supports section editing and `j` to inspect JSON without re-entering the entire plan. Default, quick, and custom budgets retain the existing protocol limits.

Choose `s` for an explicit scan (5 seconds by default), `m` for manual entry, or `k` to keep the current target. Select from a frozen numbered table showing names, full MAC addresses, RSSI, and, on wide terminals, address type and pairing state. `r` performs exactly one rescan; `f` filters; `t` only sets the next scan to 1–15 seconds—press `r` to scan; `n`/`p` change pages. From the list, `b` returns to the selection-method page; another `b` returns to the previous wizard step. Changing adapter or transport clears both target and characteristic. Any target change clears the old GATT characteristic, including target changes made in another case; selecting `ble-read` again requires re-entry.

![Synthetic scan-selection preview, not live device evidence](docs/assets/scan-preview.svg)

CLI `scan` now defaults to a human-readable table, a change from 0.1. Scripts should use `--json` for a `ScanResult` with `devices`, `status`, `cleanup_status`, and `warnings`. BlueZ merges discovery filters from multiple clients, so every candidate has `transport_verified: false`: it was observed during the requested LE/BR scan window, not proven to use that bearer. Unchanged cached entries are not fresh observations; an empty window does not prove no devices are nearby.

Cancellation returns `status: interrupted`, retaining `cleanup_status` and `warnings`; CLI exit is `130`. An `unconfirmed` cleanup is not ordinary success.

Scanning starts only after an explicit action and does not call Connect/Pair, perform GATT reads/writes, or start a workload. Discovery may include inquiry, active scanning, and name resolution; it is not wholly passive.

## Evidence and privileges

The supervisor starts capture before the worker. Live bundles contain the plan snapshot, supervisor/worker JSONL logs, `host-hci.btsnoop`, an optional `air.pcap`, a Markdown report, and a SHA-256 manifest. Keep original bundles private: captures may expose identifiers or application data.

HCI is a host/controller view, not a complete RF trace. nRF52840 capture is BLE-only and may miss traffic or fail to follow encrypted/private-address connections. Neither source alone proves that business functions or ECUs remained available. Missing evidence stays explicit instead of turning into an automatic pass. Hash verification establishes consistency with the manifest, not authenticity or compliance.

nRF52840 has two mutually exclusive firmware roles: sniffer firmware for BLE air capture, or board-matched Zephyr HCI USB controller firmware for active `ble-read` / `ble-cycle` testing after Linux exposes an `hciX` adapter, with `btmon` capture. It is LE-only, not BR/EDR. A single board does not serve both roles simultaneously. Firmware is prepared separately; real nRF compatibility remains unverified. See [hardware roles](docs/hardware.md#nrf52840两种互斥角色).

If capture-writer exit is unconfirmed, the directory is marked `UNSEALED.json` without a manifest. A confirmed exit requiring forced cleanup is recorded as `cleanup_forced`, not automatic success.

Run the TUI and workers as a regular user. After full target-MAC confirmation, the interactive wizard can invoke native `sudo -v` in the current TTY: sudo reads the password; the application does not store it. Non-interactive `run` still requires credentials refreshed beforehand. Only the `btmon` child uses `sudo -n`. The project does not install sudoers rules or capabilities and does not require a root TUI.

## Standards and development

The project supports a subset of risk-driven evidence gathering, not vehicle type approval. See [evidence documentation](docs/evidence.md#法规映射) for official GB 44495-2024/amendment and UN R155 references. Project limits are not a universal PPS requirement.

```bash
./scripts/install.sh --dev
.venv/bin/python -m unittest discover -s tests -v
```

[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)
