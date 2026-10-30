"""Optional, per-connection LE parameter request using fixed BlueZ argv.

No global controller configuration is changed. Tool success is only a request,
not evidence of negotiated parameters; inspect HCI connection-update events.
"""
import math
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time


HELPER_TIMEOUT = 15.0
COMMAND_TIMEOUT = 4.0
REQUEST_TIMEOUT = 5.5
CLEANUP_TIMEOUT = 0.5
MAX_OUTPUT = 65536
HCITOOL = "/usr/bin/hcitool"
RPC_MAX_REQUEST = 2048
_MAC = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\Z")
_CONNECTION = re.compile(
    r"[<>]\s+(LE|ACL|SCO|eSCO)\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"
    r"\s+handle\s+([0-9]+)\s+state\s+([0-9]+)\s+lm(?:\s+([^\r\n]*))?\Z"
)


def _absolute_tool(name, expected):
    found = shutil.which(name)
    if (not found or not PurePosixPath(found).is_absolute()
            or os.path.realpath(found) != os.path.realpath(expected)):
        raise ValueError(f"{name} is not the expected absolute system executable")
    return expected


def _cleanup(proc, deadline):
    """Bound direct-child cleanup; never wait on inherited output pipes."""
    if proc.poll() is not None:
        return "direct_child_reaped"
    try:
        proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=max(0.001, min(0.25, deadline-time.monotonic())))
        return "direct_child_reaped"
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=max(0.001, min(0.25, deadline-time.monotonic())))
        return "direct_child_reaped"
    except subprocess.TimeoutExpired:
        return "unconfirmed"


def _wait_command(proc, timeout, guard):
    end = time.monotonic()+timeout
    while True:
        if guard is not None:
            guard()
        remaining = end-time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        try:
            return proc.wait(timeout=min(0.1, remaining))
        except subprocess.TimeoutExpired:
            continue


def _command(argv, deadline, timeout=COMMAND_TIMEOUT, guard=None):
    if guard is not None:
        guard()
    remaining = deadline-time.monotonic()-CLEANUP_TIMEOUT
    if remaining <= 0:
        return dict(status="timeout", returncode=None, stdout="", stderr="",
                    cleanup_status="not_started")
    # Files avoid PIPE backpressure, unbounded communicate(), and descendants
    # retaining pipe FDs. Only bounded output is read into memory.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=errors, shell=False, close_fds=True)
        status = "completed"
        try:
            _wait_command(proc, min(timeout, remaining), guard)
        except subprocess.TimeoutExpired:
            status = "timeout"
        except BaseException:
            _cleanup(proc, min(deadline, time.monotonic()+CLEANUP_TIMEOUT))
            raise
        cleanup = _cleanup(proc, min(deadline, time.monotonic()+CLEANUP_TIMEOUT))
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(MAX_OUTPUT+1), errors.read(MAX_OUTPUT+1)
        if len(stdout) > MAX_OUTPUT or len(stderr) > MAX_OUTPUT:
            status = "output_limit"
        return dict(status=status, returncode=proc.returncode,
                    stdout=stdout[:MAX_OUTPUT].decode("utf-8", "replace"),
                    stderr=stderr[:MAX_OUTPUT].decode("utf-8", "replace"),
                    cleanup_status=cleanup)


def _target_handle(output, target):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or lines.pop(0) != "Connections:":
        raise ValueError("unrecognized connection-list header")
    # BlueZ hcitool con allocates ten entries: a full list may be truncated.
    if len(lines) >= 10:
        raise ValueError("connection-list completeness is unconfirmed")
    rows, handles = [], set()
    for line in lines:
        match = _CONNECTION.fullmatch(line)
        if not match:
            raise ValueError("unrecognized connection-list entry")
        kind, address, handle, state, _ = match.groups()
        handle = int(handle, 10)
        if not 0 <= handle <= 0x0eff or handle in handles:
            raise ValueError("invalid or ambiguous connection handle")
        handles.add(handle)
        rows.append((kind, address.upper(), handle, int(state, 10)))
    matches = [row for row in rows if row[1] == target]
    if len(matches) != 1 or matches[0][0] != "LE" or matches[0][3] != 1:
        raise ValueError("target is not a unique connected LE link")
    return matches[0][2]


class _GuardFailure(BaseException):
    def __init__(self, original):
        self.original = original


def request_le_parameters(adapter, target, interval_ms, guard=None):
    """Return request metadata; invalid inputs raise before any tool execution.

    Called only after establishing the caller's LE connection. Mapping is read
    twice immediately before the update, but this does not eliminate handle-
    reuse races with other clients. Use a dedicated controller. No reconnect,
    retry, permission change, password input, or root worker is performed.
    The privileged hcitool runs under a privileged timeout (TERM at 4 seconds,
    KILL after one more second), including if the Python caller is interrupted.
    """
    if not isinstance(adapter, str) or not re.fullmatch(r"hci[0-9]{1,3}", adapter):
        raise ValueError("adapter must be hciN")
    if not isinstance(target, str) or not _MAC.fullmatch(target):
        raise ValueError("target must be one Bluetooth MAC")
    target = target.upper()
    if target in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
        raise ValueError("target must identify one device")
    if (isinstance(interval_ms, bool) or not isinstance(interval_ms, (float, int))
            or not 7.5 <= interval_ms <= 50 or not math.isfinite(interval_ms)
            or interval_ms / 1.25 != int(interval_ms / 1.25)):
        raise ValueError("interval_ms must be 7.5..50 in exact 1.25 ms units")
    units = int(interval_ms / 1.25)
    started = time.monotonic()
    deadline = started+HELPER_TIMEOUT
    result = dict(status="not_requested", adapter=adapter, target=target,
                  requested_interval_ms=float(interval_ms), interval_units=units,
                  requested_latency=0, requested_supervision_timeout_units=400,
                  requested_supervision_timeout_ms=4000, actual_parameters_verified=False,
                  privileged_helper_ttl_s=5.0, privileged_cleanup_verified=False,
                  handle=None, commands=[], cleanup_status="not_started",
                  warning="Verify actual negotiated parameters in HCI; exit 0 is not proof.")
    def checked_guard():
        if guard is not None:
            try:
                guard()
            except BaseException as exc:
                raise _GuardFailure(exc) from exc

    try:
        tool = _absolute_tool("hcitool", HCITOOL)
        sudo = _absolute_tool("sudo", "/usr/bin/sudo")
        timeout_tool = _absolute_tool("timeout", "/usr/bin/timeout")
        handle = None
        for stage in ("lookup", "recheck", "request"):
            if stage == "request" and deadline-time.monotonic() < REQUEST_TIMEOUT+CLEANUP_TIMEOUT:
                result["status"] = "request_failed"
                result["error"] = "insufficient total budget for privileged TTL and cleanup"
                break
            argv = ([tool, "-i", adapter, "con"] if stage != "request" else
                    [sudo, "-n", "--", timeout_tool, "--signal=TERM", "--kill-after=1s", "4s",
                     tool, "-i", adapter, "lecup",
                     "--handle", str(handle), "--min", str(units), "--max", str(units),
                     "--latency", "0", "--timeout", "400"])
            command = _command(argv, deadline,
                               timeout=REQUEST_TIMEOUT if stage == "request" else COMMAND_TIMEOUT,
                               guard=checked_guard if guard is not None else None)
            if stage == "request" and command["status"] == "timeout":
                # Privileged timeout supplies its own TTL, independent of Python.
                # Reaping sudo still does not prove privileged descendants exited.
                command["cleanup_status"] = "privileged_descendants_unverified"
            result["commands"].append(dict(stage=stage, argv=argv, **command))
            result["cleanup_status"] = command["cleanup_status"]
            if (command["status"] != "completed" or command["returncode"] != 0
                    or command["cleanup_status"] != "direct_child_reaped"
                    or command["stderr"].strip()):
                result["status"] = "request_failed" if stage == "request" else "lookup_failed"
                break
            if stage != "request":
                current = _target_handle(command["stdout"], target)
                if handle is not None and handle != current:
                    raise ValueError("target handle changed before parameter request")
                handle = current
                result["handle"] = handle
            else:
                # hcitool reports some controller failures without nonzero exit.
                # Any unexpected stdout (e.g. usage) is not accepted as success.
                result["status"] = "requested_unverified" if not command["stdout"].strip() else "request_failed"
    except _GuardFailure as exc:
        raise exc.original
    except (OSError, ValueError) as exc:
        result["status"] = "not_requested"
        result["error"] = str(exc)
    finally:
        result["elapsed_s"] = time.monotonic()-started
    return result


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _read_rpc_request(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > RPC_MAX_REQUEST:
        raise RuntimeError("link RPC request must be a regular file of at most 2048 bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise RuntimeError("link RPC request changed while opening")
        raw = os.read(fd, RPC_MAX_REQUEST+1)
        after = os.fstat(fd)
        current = path.lstat()
        if (len(raw) > RPC_MAX_REQUEST or len(raw) != after.st_size or not os.path.samestat(opened, current)
                or not stat.S_ISREG(current.st_mode) or opened.st_size != after.st_size
                or opened.st_mtime_ns != after.st_mtime_ns or opened.st_mtime_ns != current.st_mtime_ns):
            raise RuntimeError("link RPC request changed while reading")
    finally:
        os.close(fd)
    try:
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("invalid link RPC JSON") from exc
    if (not isinstance(request, dict) or set(request) != {"schema_version", "request_id", "sequence"}
            or type(request["schema_version"]) is not int or request["schema_version"] != 1
            or type(request["sequence"]) is not int
            or not isinstance(request["request_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", request["request_id"])):
        raise RuntimeError("invalid link RPC request fields")
    return request, hashlib.sha256(raw).hexdigest()


def _publish_rpc_response(folder, sequence, response):
    payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
    if len(payload) > 512*1024:
        raise RuntimeError("link RPC response exceeds 512 KiB")
    final = folder / f"link-response-{sequence}.json"
    temporary = folder / f".link-response-{sequence}-{secrets.token_hex(16)}.tmp"
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        # Atomic no-replace publication. POSIX rename() replaces an existing
        # name, so use link(); Windows rename() already rejects an existing name.
        if os.name == "nt":
            os.rename(temporary, final)
        else:
            os.link(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def serve_parameter_requests(plan, folder, state, guard):
    """Process at most one fixed-file RPC; only Plan supplies helper arguments.

    Requests remain as evidence. Unchanged consumed requests are not executed
    again; changed files, IDs reused at another sequence, gaps, and extra queued
    requests fail closed. Protocol/file errors propagate to supervisor cleanup.
    """
    folder = Path(folder)
    if not stat.S_ISDIR(folder.lstat().st_mode):
        raise RuntimeError("link RPC folder must be a real directory")
    paths = [path for path in folder.iterdir()
             if path.name.startswith("link-request-") and path.name.endswith(".json")]
    processed = state.setdefault("processed", {})
    last = state.setdefault("last_sequence", 0)
    if not paths and not processed:
        return
    if not plan.case.startswith("ble-") or getattr(plan, "ble_interval_ms", None) is None:
        raise RuntimeError("link RPC is not enabled for this plan")
    maximum = plan.cycles if plan.case == "ble-cycle" else 1
    entries = {}
    for path in paths:
        match = re.fullmatch(r"link-request-([1-9][0-9]*)\.json", path.name)
        if not match:
            raise RuntimeError("invalid link RPC filename")
        sequence = int(match.group(1))
        if not 1 <= sequence <= maximum:
            raise RuntimeError("link RPC request exceeds plan connection budget")
        request, digest = _read_rpc_request(path)
        if request["sequence"] != sequence:
            raise RuntimeError("link RPC sequence does not match filename")
        if sequence in processed:
            if processed[sequence] != (request["request_id"], digest):
                raise RuntimeError("consumed link RPC request was modified or replayed")
        else:
            entries[sequence] = (request, digest)
    if any(not (folder / f"link-request-{sequence}.json").exists() for sequence in processed):
        raise RuntimeError("consumed link RPC request was removed")
    if not entries:
        return
    if len(entries) != 1 or set(entries) != {last+1}:
        raise RuntimeError("link RPC requests must be strictly sequential")
    sequence = last+1
    request, digest = entries[sequence]
    if request["request_id"] in {item[0] for item in processed.values()}:
        raise RuntimeError("link RPC request_id was replayed")
    response_path = folder / f"link-response-{sequence}.json"
    if os.path.lexists(response_path):
        raise RuntimeError("link RPC response path already exists")
    guard()
    # Mark before executing: an exception never makes this request retryable.
    processed[sequence] = (request["request_id"], digest)
    state["last_sequence"] = sequence
    result = request_le_parameters(plan.adapter, plan.target, plan.ble_interval_ms, guard=guard)
    guard()
    _publish_rpc_response(folder, sequence,
                          dict(schema_version=1, request_id=request["request_id"],
                               sequence=sequence, result=result))
