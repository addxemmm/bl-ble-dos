# bl-ble-dos

**Bounded Bluetooth availability workloads, with evidence you can review.**

![License: MIT](https://img.shields.io/badge/license-MIT-3b82f6)
![Runtime: Linux](https://img.shields.io/badge/runtime-Ubuntu%20%7C%20Kali-0f766e)
![Interface: Rich + prompt_toolkit](https://img.shields.io/badge/interface-Rich%20%2B%20prompt__toolkit-7c3aed)
![Status: development preview](https://img.shields.io/badge/status-development%20preview-f59e0b)

[中文主文档](README.md) · [Usage](docs/usage.md) · [Hardware](docs/hardware.md) · [Evidence](docs/evidence.md)

A Bash entry point and a Rich + prompt_toolkit terminal wizard for Linux BR/EDR and Bluetooth LE availability experiments. A USB dual-mode Bluetooth adapter generates normal-protocol, bounded workloads; `btmon` records host HCI traffic. An additional nRF52840 with sniffer firmware can provide optional BLE over-the-air capture.

**Development preview: 0.3.0 is unreleased.** Badges describe the project, not CI results or hardware certification. No adapter or target compatibility claim is currently hardware-verified. Synthetic demonstrations are not live-test evidence.

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

`--output` is a parent directory: demo creates a new timestamped/random child and prints its actual path and verification command. The six-step installer checks Linux and system dependencies, prepares a local `.venv`, installs Python dependencies online, presents hardware checks, and validates the result. Default installation does not run apt or enable the Bluetooth service. `--install-system` explicitly permits apt; `--non-interactive` never waits for input; `--dev` adds offline tests; `--help` only shows usage. With no arguments, `bt-dos` opens the wizard in an interactive terminal, or shows help for non-interactive input/output. Live operations target Ubuntu/Kali with BlueZ; Windows is suitable for repository management, not the Bluetooth runtime.

## Cases and limits

| Case | Scope |
| --- | --- |
| `br-sdp` | Bounded BR/EDR SDP service queries |
| `br-channel` | Opens/holds/closes a BR/EDR L2CAP channel on SDP PSM `0x0001`, without application payload; not ACL link cycling |
| `ble-read` | No write operations are initiated; the manufacturer must confirm read-side effects |
| `ble-cycle` | Sequential connect/hold/disconnect cycles; not concurrent slot exhaustion |

Defaults: `1 req/s × 10 s` with `max_ops=10`; quick preflight uses `1 req/s × 5 s` with `max_ops=5`. Explicit old plans with `max_ops=400` remain valid. Unchanged hard limits: request workloads are bounded by `20 req/s`, a `60 s` duration, and `400` operations. Channel/connection workloads use at most `10` sequential cycles, `1–5 s` hold time, and `3–30 s` gaps, plus connection/supervision timeouts. Total experiment time can exceed 60 seconds. Rate also stays within the plan's `approved_rate`. These are software limits, not regulatory thresholds or guarantees of target tolerance.

The cases use normal protocol operations, not controller exploit injection or RF interference. Run in an isolated test setup with an agreed target, stop criteria, and a recovery procedure.

```bash
./bt-dos adapters
./bt-dos scan --transport le --adapter hci0 --seconds 5
./bt-dos scan --transport le --adapter hci0 --seconds 5 --json
./bt-dos inspect --transport le --adapter hci0 --target TARGET --confirm TARGET
./bt-dos inspect --transport bredr --adapter hci0 --target TARGET --confirm TARGET
./bt-dos run --plan PLAN.json --confirm TARGET
./bt-dos report RUN_DIR
./bt-dos verify RUN_DIR
```

Replace the uppercase placeholders. Scanning and service inspection are live radio operations. BR/EDR plans also identify the local adapter address.

## Guided interaction in 0.3.0

Six steps cover environment, case/adapter, target, parameters, evidence, and review. Use `b` to go back and `q` to return home; the draft lives only in the current session's memory. Demo and diagnostics return to the home menu. Review supports section editing and `j` to inspect JSON without re-entering the entire plan. Default, quick, and custom budgets retain the existing protocol limits.

Choose `s` for an explicit scan (5 seconds by default), `m` for manual entry, or `k` to keep the current target. Select from a frozen numbered table showing names, full MAC addresses, RSSI, and, on wide terminals, address type and pairing state. `r` performs exactly one rescan; `f` filters; `t` only sets the next scan to 1–15 seconds—press `r` to scan; `n`/`p` change pages. From the list, `b` returns to the selection-method page; another `b` returns to the previous wizard step. Changing the device, adapter, or transport clears the old probe snapshot and SDP/GATT selection; adapter/transport changes also clear the target. Recheck parameters for the current target.

Menus support Up/Down and Enter, with displayed shortcuts retained. Text fields support Left/Right, Home/End, Backspace/Delete and field-scoped, in-memory history; no history file is written. `BTDOS_SIMPLE_INPUT=1 ./bt-dos` selects the basic fallback, which **does not support arrow-key editing**. Exit any old process and restart after updating; running wizards do not hot-reload.

![Synthetic scan-selection preview, not live device evidence](docs/assets/scan-preview.svg)

CLI `scan` now defaults to a human-readable table, a change from 0.1. Scripts should use `--json` for a `ScanResult` with `devices`, `status`, `cleanup_status`, and `warnings`. BlueZ merges discovery filters from multiple clients, so every candidate has `transport_verified: false`: it was observed during the requested LE/BR scan window, not proven to use that bearer. Unchanged cached entries are not fresh observations; an empty window does not prove no devices are nearby.

Cancellation returns `status: interrupted`, retaining `cleanup_status` and `warnings`; CLI exit is `130`. An `unconfirmed` cleanup is not ordinary success.

Scanning starts only after an explicit action and does not call Connect/Pair, perform GATT reads/writes, or start a workload. Discovery may include inquiry, active scanning, and name resolution; it is not wholly passive.

## Explicit service probing and parameter selection

Selecting a target displays known metadata without connecting. On the parameter page, `p` probes/refreshes only after confirmation (default **no**); `s` selects an eligible result, `m` enters advanced manual parameters, and `k` keeps a checked selection or skips probing for cycle cases.

SDP plans accept service-class UUID16 values from `ServiceClassIDList` only. Custom UUID128 values remain display-only; PSM/RFCOMM metadata does not open arbitrary workloads, and `br-channel` stays fixed to SDP PSM `0x0001`. BLE probing connects and enumerates service/characteristic metadata without reading values, pairing, writing, or subscribing. Only readable characteristics with a unique UUID are list-selectable; the plan stores the UUID, not a transient handle. Duplicate UUIDs require advanced manual verification. The manufacturer must still confirm read access and lack of business side effects before a read workload.

Probes are **separate snapshots before the capture baseline, not test evidence**. Wizard results remain in memory and are not saved automatically. `inspect` requires matching `--confirm TARGET`; `--json` emits JSON only, which can be redirected to a private file. SDP has a 15-second execution budget plus bounded process cleanup; BLE find/connect/disconnect budgets are 8/10/5 seconds and each D-Bus call has a 5-second budget. Cancellation propagation and library cleanup can exceed these budgets. One application connect call is not one guaranteed wire attempt: Bleak/BlueZ may retry or reuse connections. Use a dedicated controller and stop other clients; prechecks do not eliminate shared-connection races. See [usage and snapshot limits](docs/usage.md#主动能力探测与私有-json-快照).

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
