#!/usr/bin/env python3
"""Bounded normal-protocol workloads. Run only through the supervised worker."""
import asyncio
import json
import math
import os
import socket
import stat
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

TRANSACTION_TIMEOUT = 3.0
PROBE_UUID = "00002a00-0000-1000-8000-00805f9b34fb"
PROBE_TIMEOUT = 1.0
PROBE_SLOW_MS = 250.0
PROBE_INTERVAL = 1.0


def load_window(args):
    """Bound new API operations; already-started transactions may drain."""
    started = time.monotonic()
    deadline = started + args.seconds
    log("load_start", configured_rate=args.rate, submission_window_s=args.seconds,
        started_monotonic_s=started, submission_deadline_monotonic_s=deadline,
        transaction_timeout_s=TRANSACTION_TIMEOUT, max_ops=args.max_ops)
    return started, deadline


def load_summary(args, started, attempted, completed, completed_in_window, last_completed_at):
    elapsed = time.monotonic() - started
    log("summary", attempted=attempted, completed=completed,
        completed_in_window=completed_in_window, drained=completed-completed_in_window,
        configured_rate=args.rate, submission_window_s=args.seconds,
        elapsed_s=elapsed,
        drain_elapsed_s=max(0.0, last_completed_at-started-args.seconds),
        end_reason="max_ops" if attempted >= args.max_ops else "submission_window_elapsed")


class IncompleteSDP(Exception):
    """Normal SDP pagination; this first-page-only sample stops here."""


def log(event, **data):
    print(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),
                          monotonic_ns=time.monotonic_ns(), event=event, **data),
                     ensure_ascii=False), flush=True)


def stopped(args):
    if getattr(args, "parent_pid", None) is not None and os.getppid() != args.parent_pid:
        raise RuntimeError("supervisor exited")
    if Path(args.stop_file).exists():
        raise RuntimeError("STOP file detected")


def pause(seconds, args):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        stopped(args)
        time.sleep(min(0.1, max(0, end-time.monotonic())))


async def async_pause(seconds, args):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        stopped(args)
        await asyncio.sleep(min(0.1, max(0, end-time.monotonic())))


def make_sdp_request(tid, uuid16):
    # Data Element Sequence, one UUID16; max 16 records; no continuation.
    params = b"\x35\x03\x19" + struct.pack(">H", uuid16) + b"\x00\x10\x00"
    return struct.pack(">BHH", 0x02, tid, len(params)) + params


def parse_sdp_response(data, tid):
    if len(data) < 5:
        raise ValueError("truncated SDP header")
    pdu, reply_tid, length = struct.unpack(">BHH", data[:5])
    if reply_tid != tid or len(data) != 5 + length:
        raise ValueError("SDP transaction or length mismatch")
    if pdu == 0x01:
        if length != 2:
            raise ValueError("invalid SDP ErrorResponse")
        return dict(pdu=pdu, error_code=int.from_bytes(data[5:7], "big"))
    if pdu != 0x03 or length < 5:
        raise ValueError("unexpected SDP response")
    total, current = struct.unpack(">HH", data[5:9])
    if total > 16 or current > total:
        raise ValueError("invalid SDP record count")
    pos = 9 + 4 * current
    if pos >= len(data):
        raise ValueError("truncated SDP records")
    continuation_length = data[pos]
    if continuation_length > 16 or len(data) != pos + 1 + continuation_length:
        raise ValueError("invalid SDP continuation")
    if not continuation_length and current != total:
        raise ValueError("inconsistent first SDP response record count")
    return dict(pdu=pdu, total_records=total, returned_records=current,
                complete=not bool(continuation_length),
                continuation_hex=data[pos+1:].hex())


def br_socket(args):
    started = time.monotonic()
    details = dict(target=args.target, local=args.local, psm=1, timeout_s=3.0)
    log("br_connect_attempt", **details, elapsed_s=0.0, connect_elapsed_s=None)
    s = None
    connect_started = None
    connected_at = None
    stage = "socket_create"
    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                          socket.BTPROTO_L2CAP)
        stage = "settimeout"
        s.settimeout(3.0)
        stage = "bind"
        s.bind((args.local, 0))
        stage = "connect"
        connect_started = time.monotonic()
        s.connect((args.target, 0x0001))
        connected_at = time.monotonic()
        stage = "connected_log"
        log("br_connect_succeeded", **details, elapsed_s=connected_at-started,
            connect_elapsed_s=connected_at-connect_started)
        return s
    except BaseException as exc:
        try:
            failed_at = time.monotonic()
            log("br_connect_failed", **details, failure_stage=stage,
                elapsed_s=failed_at-started,
                connect_elapsed_s=((connected_at if connected_at is not None else failed_at)-connect_started)
                if connect_started is not None else None,
                error=repr(exc), error_type=type(exc).__name__, errno=getattr(exc, "errno", None))
        except BaseException:
            # A broken event sink must not mask the socket failure or skip cleanup.
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except BaseException:
                    # Keep the initiating failure, rather than a secondary close error.
                    pass
        raise


def br_sdp(args):
    stopped(args)
    with br_socket(args) as s:
        log("br_sdp_connected", peer=s.getpeername(), local=s.getsockname())
        started, deadline = load_window(args)
        count = attempted = completed_in_window = 0
        last_completed_at = started
        while count < args.max_ops and time.monotonic() < deadline:
            stopped(args)
            t0 = time.monotonic()
            if t0 >= deadline:
                break
            tid = count + 1
            req = make_sdp_request(tid, args.uuid16)
            log("sdp_attempt", tid=tid, request_hex=req.hex())
            # Share one transaction budget across send/receive. The load window
            # gates starts, not the response to a request already submitted.
            transaction_started = time.monotonic()
            if transaction_started >= deadline:
                log("sdp_attempt_skipped", tid=tid, reason="submission_deadline", submitted=False)
                break
            transaction_deadline = transaction_started + TRANSACTION_TIMEOUT
            attempted += 1
            s.settimeout(TRANSACTION_TIMEOUT)
            sent = s.send(req)
            if sent != len(req):
                raise RuntimeError("short sequenced-packet send")
            log("sdp_submitted", tid=tid)
            stopped(args)
            remaining = transaction_deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("SDP transaction timeout after submission")
            s.settimeout(remaining)
            rsp = s.recv(4096)
            received_at = time.monotonic()
            log("sdp_received", tid=tid, response_hex=rsp.hex())
            parsed = parse_sdp_response(rsp, tid)
            count += 1
            last_completed_at = received_at
            completed_in_window += received_at <= deadline
            log("sdp_response", tid=tid, latency_ms=(received_at-t0)*1000,
                **parsed)
            stopped(args)
            if parsed["pdu"] == 0x01:
                raise RuntimeError("peer returned SDP error; stop this level")
            if not parsed["complete"]:
                raise IncompleteSDP("normal SDP pagination; follow-up implementation required")
            pause(max(0, min(1/args.rate-(time.monotonic()-t0),
                             deadline-time.monotonic())), args)
        load_summary(args, started, attempted, count, completed_in_window, last_completed_at)


def br_channel(args):
    # PSM deliberately fixed to SDP. This cycles a channel, NOT necessarily ACL.
    for index in range(1, args.cycles + 1):
        stopped(args)
        log("channel_attempt", cycle=index, psm=1)
        with br_socket(args) as s:
            log("channel_connected", cycle=index, peer=s.getpeername())
            pause(args.hold, args)
            stopped(args)
        log("channel_local_closed", cycle=index)
        pause(args.gap, args)


async def find_ble(args):
    from bleak import BleakScanner
    stopped(args)
    # Discovery is setup traffic, not part of the intentional ATT load count.
    device = await BleakScanner.find_device_by_address(
        args.target, timeout=10.0, bluez={"adapter": args.adapter},
        scanning_mode="active")
    if device is None:
        raise RuntimeError("target not found on selected LE adapter")
    stopped(args)
    log("le_discovered", address=device.address, adapter=args.adapter)
    return device


async def request_link_parameters(args):
    """Ask the authenticated supervisor; keep this worker unprivileged/isolated."""
    stopped(args)
    folder = Path(args.parameter_rpc_dir)
    if (not folder.is_absolute() or not folder.is_dir() or folder.is_symlink()
            or folder.resolve() != Path(args.stop_file).resolve().parent):
        raise ValueError("invalid internal parameter request directory")
    sequence = getattr(args, "_link_rpc_sequence", 0)+1
    if not 1 <= sequence <= 10:
        raise ValueError("parameter request sequence exceeds run limit")
    args._link_rpc_sequence = sequence
    request_id = uuid.uuid4().hex
    request = folder / f"link-request-{sequence}.json"
    response = folder / f"link-response-{sequence}.json"
    temporary = folder / f".link-request-{sequence}.{request_id}.tmp"
    if request.exists() or request.is_symlink() or response.exists() or response.is_symlink():
        raise RuntimeError("parameter request path already exists")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(schema_version=1, request_id=request_id, sequence=sequence), handle)
            handle.flush()
        os.replace(temporary, request)
    finally:
        if temporary.exists():
            temporary.unlink()
    log("le_parameter_wait", sequence=sequence, request_id=request_id, timeout_s=20)
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        stopped(args)
        try:
            fd = os.open(response, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            await asyncio.sleep(0.05)
            continue
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 512*1024:
                raise ValueError("invalid parameter response file")
            raw = handle.read(512*1024+1)
        if len(raw) > 512*1024:
            raise ValueError("parameter response too large")
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "request_id", "sequence", "result"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["sequence"]) is not int or value["sequence"] != sequence
                or value["request_id"] != request_id or not isinstance(value["result"], dict)):
            raise ValueError("parameter response does not match request")
        stopped(args)
        return value["result"]
    raise TimeoutError("supervisor parameter response timeout")


async def ble_connection(args, body):
    from bleak import BleakClient
    device = await find_ble(args)
    client = BleakClient(device, bluez={"adapter": args.adapter}, timeout=10.0)
    try:
        stopped(args)
        log("le_connect_attempt", address=device.address)
        await asyncio.wait_for(client.connect(), timeout=10.0)
        if not client.is_connected:
            raise RuntimeError("BLE client not connected")
        log("le_connected", address=device.address)
        stopped(args)
        if getattr(args, "ble_interval_ms", None) is not None:
            result = await request_link_parameters(args)
            log("le_parameter_request", **result)
            stopped(args)
            if result["status"] != "requested_unverified":
                raise RuntimeError("LE parameter request failed; workload not started")
        await body(client)
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
            log("le_disconnect_call_finished")
        except Exception as exc:
            log("le_disconnect_error", error=repr(exc))
            raise


async def ble_read(args):
    async def body(client):
        spec = int(args.char, 16) if args.char.lower().startswith("0x") else args.char
        char = client.services.get_characteristic(spec)
        if char is None or "read" not in char.properties:
            raise ValueError("characteristic missing or not readable")
        log("le_characteristic", uuid=char.uuid, handle=char.handle,
            properties=list(char.properties))
        started, deadline = load_window(args)
        count = attempted = completed_in_window = 0
        last_completed_at = started
        while count < args.max_ops and time.monotonic() < deadline:
            stopped(args)
            t0 = time.monotonic()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            log("att_read_attempt", sequence=count+1, handle=char.handle)
            # BlueZ may queue the ATT transaction: this is the API start cutoff,
            # not a guarantee that no controller traffic occurs after it.
            if time.monotonic() >= deadline:
                log("att_read_attempt_skipped", sequence=count+1,
                    reason="submission_deadline", submitted=False)
                break
            attempted += 1
            data = await asyncio.wait_for(
                client.read_gatt_char(char, use_cached=False), timeout=TRANSACTION_TIMEOUT)
            returned_at = time.monotonic()
            count += 1
            last_completed_at = returned_at
            completed_in_window += returned_at <= deadline
            log("att_read_returned", sequence=count, handle=char.handle,
                length=len(data), latency_ms=(returned_at-t0)*1000)
            stopped(args)
            await async_pause(max(0, min(1/args.rate-(time.monotonic()-t0),
                                        deadline-time.monotonic())), args)
        load_summary(args, started, attempted, count, completed_in_window, last_completed_at)
    await ble_connection(args, body)


async def observe_service(client, char, args, phase, seconds, stop_load=None, sink=None):
    """A same-link communication probe, not independent target business data."""
    end = time.monotonic() + seconds
    observations = sink if sink is not None else []
    consecutive_slow = 0
    log("probe_phase", phase=phase, seconds=seconds, interval_s=PROBE_INTERVAL,
        timeout_s=PROBE_TIMEOUT, slow_threshold_ms=PROBE_SLOW_MS,
        scope="same_link_communication_only")
    while time.monotonic() < end and not (stop_load and stop_load.is_set()):
        stopped(args)
        before = time.monotonic()
        try:
            value = await asyncio.wait_for(client.read_gatt_char(char, use_cached=False),
                                           timeout=PROBE_TIMEOUT)
            elapsed_ms = (time.monotonic()-before)*1000
            slow = elapsed_ms > PROBE_SLOW_MS
            observation = dict(phase=phase, status="slow" if slow else "ok",
                               latency_ms=elapsed_ms, length=len(value),
                               scope="same_link_communication_only")
            consecutive_slow = consecutive_slow+1 if slow else 0
        except Exception as exc:
            observation = dict(phase=phase, status="error", error=repr(exc),
                               latency_ms=(time.monotonic()-before)*1000,
                               scope="same_link_communication_only")
            consecutive_slow = 0
        observations.append(observation)
        log("service_probe", **observation)
        stopped(args)
        if observation["status"] == "error" or consecutive_slow >= 2:
            if stop_load is not None:
                stop_load.set()
                log("load_stop_requested", reason="same_link_probe_error_or_slowdown")
            if phase != "recovery":
                break
        await async_pause(max(0, min(PROBE_INTERVAL-(time.monotonic()-before),
                                     end-time.monotonic())), args)
    return observations


async def write_command_load(client, char, probe, args, observations=None):
    """One paced producer. Commands are API submissions, not target ACKs."""
    stop_load = asyncio.Event()
    started, deadline = load_window(args)
    observations = observations if observations is not None else []
    probe_task = asyncio.create_task(observe_service(client, probe, args, "load", args.seconds,
                                                   stop_load, sink=observations))
    accepted = attempted = accepted_in_window = 0
    last_accepted_at = started
    last_payload = None
    try:
        while attempted < args.max_ops and time.monotonic() < deadline and not stop_load.is_set():
            stopped(args)
            t0 = time.monotonic()
            payload = b"BTDS" + struct.pack("<I", attempted+1)
            log("write_command_attempt", sequence=attempted+1, bytes=len(payload))
            if time.monotonic() >= deadline:
                log("write_command_attempt_skipped", sequence=attempted+1,
                    reason="submission_deadline", submitted=False)
                break
            attempted += 1
            await asyncio.wait_for(client.write_gatt_char(char, payload, response=False),
                                   timeout=TRANSACTION_TIMEOUT)
            last_accepted_at = time.monotonic()
            accepted += 1
            accepted_in_window += last_accepted_at <= deadline
            last_payload = payload
            log("write_command_accepted", sequence=attempted, bytes=len(payload),
                latency_ms=(last_accepted_at-t0)*1000, target_acknowledged=False)
            stopped(args)
            # No catch-up bursts after slow submissions. No response RTT gate.
            await async_pause(max(0, min(1/args.rate-(time.monotonic()-t0),
                                         deadline-time.monotonic())), args)
    finally:
        probe_stopped = stop_load.is_set()
        stop_load.set()
        await probe_task
    log("write_command_summary", attempted=attempted, api_accepted=accepted,
        accepted_in_window=accepted_in_window, drained=accepted-accepted_in_window,
        accepted_payload_bytes=accepted*8, configured_rate=args.rate,
        submission_window_s=args.seconds, elapsed_s=time.monotonic()-started,
        drain_elapsed_s=max(0, last_accepted_at-deadline),
        target_received="not_verified", target_processed="not_verified",
        end_reason="probe_stop" if probe_stopped else
                   "max_ops" if attempted >= args.max_ops else "submission_window_elapsed")
    return observations, last_payload


async def ble_write_command(args):
    from .config import TEST_SERVICE_UUID, TEST_WRITE_UUID
    if (getattr(args, "service_uuid", None) != TEST_SERVICE_UUID or args.char != TEST_WRITE_UUID
            or getattr(args, "write_test_confirmed", False) is not True):
        raise ValueError("explicit test fixture service/characteristic confirmation required")

    async def body(client):
        service = client.services.get_service(TEST_SERVICE_UUID)
        if service is None:
            raise ValueError("test service missing")
        char = service.get_characteristic(TEST_WRITE_UUID)
        if (char is None or str(getattr(char, "service_uuid", "")).lower() != TEST_SERVICE_UUID
                or "write-without-response" not in char.properties or "read" not in char.properties):
            raise ValueError("test characteristic must support command writes and readback")
        if getattr(char, "max_write_without_response_size", 20) < 8:
            raise ValueError("test payload exceeds negotiated command size")
        probe = client.services.get_characteristic(PROBE_UUID)
        if probe is None or "read" not in probe.properties:
            raise ValueError("same-link communication probe characteristic missing")
        log("write_fixture", service_uuid=TEST_SERVICE_UUID, characteristic=TEST_WRITE_UUID,
            handle=char.handle, properties=list(char.properties), payload_format="BTDS+uint32le",
            payload_size=8, target_acknowledged=False)
        baseline = await observe_service(client, probe, args, "baseline", args.baseline)
        if not baseline or any(item["status"] != "ok" for item in baseline):
            raise RuntimeError("communication baseline not healthy; command load not started")
        failure = None
        observations, last_payload = [], None
        try:
            observations, last_payload = await write_command_load(client, char, probe, args, observations)
        except Exception as exc:
            failure = exc
            log("write_load_error", error=repr(exc))
        recovery = await observe_service(client, probe, args, "recovery", args.recovery)
        if last_payload is not None:
            try:
                value = await asyncio.wait_for(client.read_gatt_char(char, use_cached=False),
                                               timeout=PROBE_TIMEOUT)
                log("write_fixture_readback", matches_last_payload=bytes(value) == last_payload,
                    value_hex=bytes(value).hex(), expected_hex=last_payload.hex(),
                    note="Last-value observation is not a cumulative receive counter")
            except Exception as exc:
                log("write_fixture_readback_error", error=repr(exc))
        log("communication_probe_summary", baseline_samples=len(baseline),
            load_samples=len(observations), recovery_samples=len(recovery),
            load_slow=sum(item["status"] == "slow" for item in observations),
            load_errors=sum(item["status"] == "error" for item in observations),
            recovery_errors=sum(item["status"] == "error" for item in recovery),
            scope="same_link_communication_only", whole_device_dos="not_determined")
        if failure is not None:
            raise failure
    await ble_connection(args, body)


async def ble_cycle(args):
    for index in range(1, args.cycles+1):
        stopped(args)
        log("le_cycle_start", cycle=index)
        async def hold(client):
            await async_pause(args.hold, args)
            stopped(args)
            if not client.is_connected:
                raise RuntimeError("unexpected LE disconnection during hold")
        await ble_connection(args, hold)
        log("le_cycle_end", cycle=index)
        await async_pause(args.gap, args)


async def ble_info(args):
    async def body(client):
        for service in client.services:
            for char in service.characteristics:
                log("characteristic", service_uuid=service.uuid, uuid=char.uuid,
                    handle=char.handle, properties=list(char.properties))
    await ble_connection(args, body)
