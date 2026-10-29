#!/usr/bin/env python3
"""Bounded normal-protocol workloads. Run only through the supervised worker."""
import asyncio
import json
import math
import os
import socket
import struct
import time
from datetime import datetime, timezone
from pathlib import Path


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
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                      socket.BTPROTO_L2CAP)
    try:
        s.settimeout(3.0)
        s.bind((args.local, 0))
        s.connect((args.target, 0x0001))
        return s
    except BaseException:
        s.close()
        raise


def br_sdp(args):
    stopped(args)
    with br_socket(args) as s:
        log("br_sdp_connected", peer=s.getpeername(), local=s.getsockname())
        started = time.monotonic()
        deadline = started + args.seconds
        count = 0
        while count < args.max_ops and time.monotonic() < deadline:
            stopped(args)
            t0 = time.monotonic()
            tid = count + 1
            req = make_sdp_request(tid, args.uuid16)
            log("sdp_attempt", tid=tid, request_hex=req.hex())
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            s.settimeout(min(3.0, remaining))
            sent = s.send(req)
            if sent != len(req):
                raise RuntimeError("short sequenced-packet send")
            log("sdp_submitted", tid=tid)
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("load deadline after SDP submission")
            s.settimeout(min(3.0, remaining))
            rsp = s.recv(4096)
            log("sdp_received", tid=tid, response_hex=rsp.hex())
            parsed = parse_sdp_response(rsp, tid)
            count += 1
            log("sdp_response", tid=tid, latency_ms=(time.monotonic()-t0)*1000,
                **parsed)
            if parsed["pdu"] == 0x01:
                raise RuntimeError("peer returned SDP error; stop this level")
            if not parsed["complete"]:
                raise IncompleteSDP("normal SDP pagination; follow-up implementation required")
            pause(max(0, min(1/args.rate-(time.monotonic()-t0),
                             deadline-time.monotonic())), args)
        log("summary", completed=count, elapsed_s=time.monotonic()-started)


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
        started = time.monotonic()
        deadline = started + args.seconds
        count = 0
        while count < args.max_ops and time.monotonic() < deadline:
            stopped(args)
            t0 = time.monotonic()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            log("att_read_attempt", sequence=count+1, handle=char.handle)
            data = await asyncio.wait_for(
                client.read_gatt_char(char, use_cached=False), timeout=min(3.0, remaining))
            count += 1
            log("att_read_returned", sequence=count, handle=char.handle,
                length=len(data), latency_ms=(time.monotonic()-t0)*1000)
            await async_pause(max(0, min(1/args.rate-(time.monotonic()-t0),
                                        deadline-time.monotonic())), args)
        log("summary", completed=count, elapsed_s=time.monotonic()-started)
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
