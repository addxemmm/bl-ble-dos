# bl-ble-dos

**Bounded BR/EDR and Bluetooth LE workloads, connection-lifecycle observation, and reviewable traffic evidence.**

[中文](README.md) · [Installation](docs/installation.md) · [Protocol paths](docs/protocols.md) · [Usage](docs/usage.md) · [Validation](docs/validation.md)

A Linux / BlueZ CLI and Rich + prompt_toolkit terminal wizard. An ordinary USB Bluetooth adapter generates normal-protocol interactions; a separate supervisor manages capture, workers, timeouts, and cleanup. Each run gets its own evidence bundle so configuration, execution results, and observed wire traffic can be reviewed separately.

**Development preview: 0.3.0 is unreleased.** Execution has been validated on specified software/hardware combinations, not every adapter, target, or stack. See [validation records and limits](docs/validation.md). Synthetic demonstrations are explicitly distinguished from live runs.

## Terminal demo

A Bash session instead of a demo image. This is a **path-normalized excerpt from an actual offline run**: the full Rich display is omitted, and status fields are read from the generated `result.json`. It is neither live traffic nor a hand-drawn terminal.

Replace `DEMO_ROOT` with an output parent such as `runs/demo`, and `RUN_DIR` with the actual new directory printed by demo.

```console
$ ./bt-dos --version
0.3.0

$ ./bt-dos demo --output DEMO_ROOT
# Rich output omitted; RUN_DIR is printed by demo.

$ grep -E '"simulation"|"status"' RUN_DIR/result.json
  "simulation": true,
  "status": "simulation_completed",

$ ls -1 RUN_DIR
events.jsonl
manifest.json
plan.json
report.md
result.json

$ ./bt-dos verify RUN_DIR
{
  "ok": true,
  "errors": []
}
```

## BR/EDR or BLE—which does it test?

The project supports both **BR/EDR (Bluetooth Classic, `br-*`)** and **Bluetooth LE / BLE (`ble-*`)**. “BL” is not a formal protocol name used here. Classification follows the tool's actual transport path, APIs, and inspectable HCI records—not a device's name, category, or a particular UUID.

| Case | Actual path | Observation scope and limits |
| --- | --- | --- |
| `br-sdp` | Linux Bluetooth L2CAP socket → BR/EDR SDP PSM `0x0001` → SDP ServiceSearch requests | Bounded queries with response and transaction-ID validation; continuation within parser limits yields `needs_continuation` (see protocol details) |
| `br-channel` | Open, hold, and close an L2CAP channel on the same BR/EDR SDP PSM | No application payload; does not force ACL teardown/re-establishment each cycle or directly prove target memory reclamation |
| `ble-read` | LE path through `BleakScanner` / `BleakClient` → GATT / ATT characteristic reads | The tool initiates no writes; read-side effects still need verification for the selected characteristic |
| `ble-cycle` | Sequential connect, hold, and disconnect on the same LE path | Bounded lifecycle observation, not concurrent connection-slot exhaustion; one API call is not necessarily one wire attempt |
| `ble-write-command` | Same LE path → write without response to a dedicated fixture characteristic | Fixed service/characteristic and an 8-byte `BTDS + uint32` payload; not read-only and accepts no arbitrary payload |

“BLE” in this table describes **the LE path selected by this implementation**, not a claim that GATT can only use an LE bearer. Dual-mode targets may support both transports; a discovery-window candidate is not automatically proof of its bearer. [Implementation paths, protocol basis, and evidence interpretation →](docs/protocols.md)

## Technical features

- **Bounded, reviewable scheduling:** plans validate rates, windows, operation counts, and cycle budgets. The request deadline stops new API operations; in-flight transactions and cleanup have separate timeout limits. Configured request rate is not measured throughput or over-the-air PPS.
- **Discovery, service probing, and execution stay separate:** scans are explicit; frozen candidate lists support filtering and pagination. SDP / GATT snapshots require confirmation. Changing a target or transport invalidates selections that no longer apply, avoiding reuse of stale characteristics.
- **Capture readiness precedes the workload:** the supervisor manages `btmon`, workers, stop signals, and process cleanup. The wizard can leave a target-preparation window after capture startup; a critical capture failure stops the workload path.
- **Recorded environment and diagnostics:** selected-controller checks and diagnostics reuse one enumeration snapshot. Evidence records tool/dependency versions, a source hash, commit, and dirty state—not just a version string.
- **Regular-user execution with narrow privilege boundaries:** neither TUI nor worker requires root. Capture and an explicitly requested optional BLE interval helper use constrained sudo paths; the project does not widen sudoers or set capabilities automatically.
- **Sealing is distinct from execution success:** plans, events, worker logs, captures, reports, and SHA-256 manifests are retained separately. An unconfirmed capture-writer exit prevents manifest generation; forced cleanup is not reported as ordinary success.
- **Interactive and scriptable interfaces:** a reversible six-step wizard, arrow-key menus, editable fields, and memory-only histories; JSON output for `scan` / `inspect`, and an explicit plan plus full target confirmation for `run`.

Request-based cases default to `10 req/s × 10 s`, at most `100` operations; quick preflight uses `1 req/s × 5 s`, at most `5`. Cycle cases use cycle/hold/gap budgets. The dedicated write fixture has a separate `100 cmd/s × 10 s`, at-most-`1000` preset without changing other case defaults. Rates remain constrained by `approved_rate` and case-specific hard limits; actual completion counts may be lower. [All budgets, fixture requirements, and connection parameters →](docs/usage.md)

## Quick installation

Linux, Python 3.10+, and Bash are required; live device operations also need BlueZ and a usable controller. The installer targets Ubuntu / Kali. Run as a regular user:

```bash
git clone https://github.com/addxemmm/bl-ble-dos.git
cd bl-ble-dos
bash scripts/install.sh
./bt-dos demo --output runs/demo
# Copy the demo's verify command, or replace RUN_DIR with its actual new directory
./bt-dos verify RUN_DIR
./bt-dos doctor
./bt-dos
```

Default installation checks the system, creates/reuses a project `.venv`, and installs Python dependencies online. It **does not run apt, start Bluetooth services automatically, scan, or connect to devices**. To explicitly install system dependencies, use `bash scripts/install.sh --install-system`. Do not start the whole wizard with `sudo`. Virtual machines need USB Bluetooth passthrough to Linux; successful installation and software self-checks do not establish hardware readiness.

`demo --output` specifies a parent directory; each demo creates a new child. It uses no radio hardware or sudo and does not fabricate HCI / PCAP files. The no-argument entry point opens the wizard only with interactive input/output; otherwise it shows help. [Dependencies, privileges, non-interactive options, and troubleshooting →](docs/installation.md)

## From review to execution

1. **Environment → case/adapter → target → parameters → evidence → review.** Select the appropriate transport and service. Discovery, service probing, and execution are separate operations; browsing a page does not start a workload.
2. **Confirm the full target MAC, then check capture privileges.** The interactive wizard can invoke native `sudo -v`; sudo reads the password, and the application does not store it.
3. **Real-TTY wizard: capture ready → target preparation (up to 60 seconds) → baseline → workload → recovery observation → capture stop and sealing.** Wake the target, then press Enter or type `r` followed by Enter. Type `q` followed by Enter, send EOF, or press Ctrl+C to stop. Confirmation means only that the operator is prepared—not that a connection exists. Waiting adds no active scan, connection, or keepalive, and does not enlarge the worker's workload budget.
4. **A failed run can retain its plan, but never retransmits automatically.** Only selected failure statuses with explicitly completed cleanup and verified evidence offer `r` to return to review or `q` to finish (the default). A new run still requires selecting run again, full-MAC confirmation, and a privilege check, and creates a new evidence directory; it does not resume the old run or continue its operation count.

Preparation waiting is enabled only for the wizard with real stdin/stdout TTYs, including `BTDOS_SIMPLE_INPUT=1`. CLI `run` does not add this step or new stdin reads, even in a TTY; scripts must arrange target readiness before starting. Simple input mode does not support arrow-key editing. [Full workflow and status explanations →](docs/usage.md)

## Hardware, results, and evidence

One dual-mode USB controller can perform BR/EDR or LE interactions according to the chosen case. `btmon` provides **the local host/controller HCI view, not an independent over-the-air capture**. Optional nRF52840 sniffer, LE HCI controller, and Connectivity GATT fixture roles use different firmware/configurations and must not be conflated: the Connectivity fixture is a target peripheral, not Kali's `hciX` controller. One board does not serve these roles simultaneously. [Hardware roles and preparation →](docs/hardware.md)

A run directory normally includes `plan.json`, `environment.json`, `events.jsonl`, `worker.jsonl`, `result.json`, `host-hci.btsnoop`, `report.md`, and `manifest.json`, with optional BLE air capture in `air.pcap`. A failure before worker startup may have no worker log. Keep raw packets and identifiers in private evidence storage.

- `completed` means the execution flow finished, not that target business functions remained available. Successful local submission of a write without response is not target acknowledgement.
- `target_not_ready` indicates preparation timeout; cancellation is `interrupted`. Interpret `needs_continuation`, `worker_failed`, and other statuses alongside the report.
- `cleanup_*` indicates cleanup problems. An unconfirmed capture-writer exit leaves `UNSEALED.json` without `manifest.json` and is not eligible for plan-retaining retry.
- `verify` checks file consistency against the manifest, not capture completeness, over-the-air delivery, internal target state, or healthy business functions. Missing independent observations remain explicit evidence gaps.

[Evidence format and interpretation →](docs/evidence.md) · [Completed validation and remaining limits →](docs/validation.md)

## Documentation and development

| Document | Contents |
| --- | --- |
| [Installation](docs/installation.md) | System dependencies, virtual environment, privileges, and installation failures |
| [Protocol paths](docs/protocols.md) | BR/EDR / LE classification, implementation paths, and HCI interpretation |
| [Usage](docs/usage.md) | Wizard, CLI, JSON plans, budgets, and the fixed write fixture |
| [Hardware](docs/hardware.md) | USB passthrough, controller capabilities, and the three nRF52840 roles |
| [Evidence](docs/evidence.md) | Artifacts, statuses, integrity checks, and observation limits |
| [Validation](docs/validation.md) | Results for specified environments and coverage limits |

```bash
bash scripts/install.sh --dev
.venv/bin/python -m unittest discover -s tests -v
```

[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) · [MIT License](LICENSE)
