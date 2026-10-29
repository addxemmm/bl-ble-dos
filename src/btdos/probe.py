"""Explicit, bounded SDP/GATT metadata probing; no characteristic I/O.

Callers obtain confirmation before calling. Importing this module performs no
radio or bus operations. Results are observations, never an exhaustive catalog.
"""
import asyncio
from dataclasses import asdict, dataclass, field
import re
import sys
import time
import uuid as uuidlib
import xml.etree.ElementTree as ET

from . import discovery
from .discovery import safe_display_name, _utc, _unwrap
from .supervisor import adapter_lock

MAX_OUTPUT = 1024 * 1024
MAX_RECORDS = 256
MAX_CHARACTERISTICS = 2048
SDP_TIMEOUT = 15.0
FIND_TIMEOUT = 8.0
CONNECT_TIMEOUT = 10.0
DISCONNECT_TIMEOUT = 5.0
CALL_TIMEOUT = 5.0
_MAC = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"


@dataclass
class Service:
    uuid: str
    name: str = ""
    uuid16: int | None = None
    record_handle: int | None = None
    psm: int | None = None
    channel: int | None = None
    source: str = "sdp"
    selectable: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class Characteristic:
    service_uuid: str
    uuid: str
    handle: int | None
    properties: list[str]
    description: str = ""
    readable: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class ProbeResult:
    target: str
    adapter: str
    transport: str
    services: list[Service] = field(default_factory=list)
    characteristics: list[Characteristic] = field(default_factory=list)
    status: str = "completed"
    cleanup_status: str = "not_started"
    warnings: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=_utc)
    duration: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def normalize_uuid(value):
    """Return canonical UUID128 and a UUID16 only when exactly representable."""
    value = str(value).strip().lower()
    if re.fullmatch(r"(?:0x)?(?:[0-9a-f]{4}|[0-9a-f]{8})", value):
        number = int(value.removeprefix("0x"), 16)
        return f"{number:08x}{_BASE_SUFFIX}", number if number <= 0xffff else None
    parsed = str(uuidlib.UUID(value))
    small = int(parsed[:8], 16)
    return parsed, small if parsed.endswith(_BASE_SUFFIX) and small <= 0xffff else None


def _integer(element):
    if element is None:
        return None
    try:
        text = element.get("value", "")
        return int(text, 16 if text.lower().startswith("0x") else 10)
    except (TypeError, ValueError):
        return None


def parse_sdp_xml(data):
    """Parse only bounded record fragments; never resolve entities or DTDs.

    sdptool emits leading status text and can repeat XML declarations. Protocol
    UUIDs are interpreted only as metadata, not as service-class identifiers.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_OUTPUT:
        raise ValueError("SDP 输出超过上限")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
        raise ValueError("SDP XML 含 DTD/实体声明")
    text = data.decode("utf-8", errors="strict")
    text = re.sub(r"<\?xml\b.*?\?>", "", text, flags=re.S | re.I)
    records = list(re.finditer(r"<record\b[^>]*>.*?</record\s*>", text, re.S))
    if len(records) > MAX_RECORDS:
        raise ValueError("SDP record 数超过上限")
    remaining = re.sub(r"<record\b[^>]*>.*?</record\s*>", "", text, flags=re.S)
    remaining = re.sub(r"</?(?:records|root)\s*>", "", remaining)
    if "<" in remaining or ">" in remaining:
        raise ValueError("SDP XML 含未识别或不完整的记录")
    services = []
    for match in records:
        try:
            record = ET.fromstring(match.group())
        except ET.ParseError as exc:
            raise ValueError("SDP XML 记录解析失败") from exc
        stack = [(record, 0)]
        count = 0
        while stack:
            element, depth = stack.pop()
            count += 1
            if depth > 32 or count > 8192:
                raise ValueError("SDP XML 结构超过上限")
            stack.extend((child, depth + 1) for child in element)
        attributes = {}
        for attribute in record.findall("attribute"):
            try:
                ident = int(attribute.get("id", ""), 16)
            except ValueError:
                continue
            attributes[ident] = attribute
        service_class = attributes.get(0x0001)
        if service_class is None:
            continue
        name_attr = attributes.get(0x0100)
        name_node = name_attr.find("text") if name_attr is not None else None
        name = safe_display_name(name_node.get("value", "")) if name_node is not None else ""
        handle_attr = attributes.get(0x0000)
        handle = _integer(handle_attr.find("uint32")) if handle_attr is not None else None
        psm = channel = None
        protocols = attributes.get(0x0004)
        if protocols is not None:
            for sequence in protocols.findall("./sequence/sequence"):
                children = list(sequence)
                if not children or children[0].tag != "uuid":
                    continue
                _, protocol = normalize_uuid(children[0].get("value", ""))
                if protocol == 0x0100 and len(children) > 1:
                    psm = _integer(children[1])
                elif protocol == 0x0003 and len(children) > 1:
                    channel = _integer(children[1])
        for node in service_class.findall("./sequence/uuid"):
            canonical, small = normalize_uuid(node.get("value", ""))
            services.append(Service(canonical, name, small, handle, psm, channel,
                                    selectable=small is not None))
            if len(services) > MAX_CHARACTERISTICS:
                raise ValueError("SDP 服务数超过上限")
    return services


async def _snapshot(result, local_address, require_target=False):
    """Own a private read-only bus connection; reject uncertain ownership."""
    client = None
    waiter = None
    interrupted = None
    try:
        client = discovery._new_client()
        result.cleanup_status = "unconfirmed"
        await asyncio.wait_for(client.connect(), CALL_TIMEOUT)
        waiter = asyncio.create_task(client.wait_for_disconnect())
        owner = (await asyncio.wait_for(client.call("org.freedesktop.DBus",
            "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", "s", ["org.bluez"]), CALL_TIMEOUT))[0]
        objects = (await asyncio.wait_for(client.call(owner, "/", discovery.OBJECT_MANAGER,
                                                     "GetManagedObjects"), CALL_TIMEOUT))[0]
        adapter_path = f"/org/bluez/{result.adapter}"
        props = objects.get(adapter_path, {}).get(discovery.ADAPTER_IFACE)
        if not isinstance(props, dict):
            raise RuntimeError("指定 BlueZ 适配器不存在")
        address = _unwrap(props.get("Address", ""))
        if not isinstance(address, str) or not _MAC.fullmatch(address):
            raise RuntimeError("适配器 Address 缺失或无效")
        address = address.upper()
        if local_address is not None and address != local_address:
            raise RuntimeError("adapter 与 local_address 不一致")
        result.metadata["local_address"] = address
        target_path = adapter_path + "/dev_" + result.target.replace(":", "_")
        target = objects.get(target_path, {}).get(discovery.DEVICE_IFACE)
        if require_target and target is None:
            raise RuntimeError("目标 D-Bus 对象已消失，停止探测")
        if target is not None:
            connected = _unwrap(target.get("Connected"))
            if type(connected) is not bool:
                raise RuntimeError("目标 Connected 状态未知，停止探测")
            for key in ("AddressType", "Name", "Paired", "Connected", "ServicesResolved", "UUIDs"):
                value = _unwrap(target.get(key))
                if key in {"Paired", "Connected", "ServicesResolved"} and type(value) is bool:
                    result.metadata[key] = value
                elif key in {"Name", "AddressType"} and isinstance(value, str):
                    result.metadata[key] = safe_display_name(value)
                elif key == "UUIDs" and isinstance(value, (list, tuple)):
                    result.metadata[key] = [safe_display_name(item) for item in value[:128]]
            if connected:
                raise RuntimeError("目标已连接；停止探测，不复用或断开他人的连接")
        return address
    except (KeyboardInterrupt, asyncio.CancelledError):
        result.status = "interrupted"
        raise
    finally:
        if client is not None:
            try:
                client.disconnect()
                if waiter is not None:
                    await asyncio.wait_for(asyncio.shield(waiter), CALL_TIMEOUT)
                result.cleanup_status = "released"
            except BaseException as exc:
                result.cleanup_status = "unconfirmed"
                result.warnings.append("D-Bus 连接关闭未确认：" + safe_display_name(exc, 180))
                interrupted = exc if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else None
                if interrupted is None:
                    raise RuntimeError("D-Bus 预检清理未确认") from exc
            finally:
                if waiter is not None and not waiter.done():
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
            if interrupted is not None:
                raise interrupted


def _new_ble_client(device, adapter):
    from bleak import BleakClient
    return BleakClient(device, pair=False, bluez={"adapter": adapter}, timeout=CONNECT_TIMEOUT)


async def _find_ble(target, adapter):
    from bleak import BleakScanner
    return await BleakScanner.find_device_by_address(target, timeout=FIND_TIMEOUT,
                                                    bluez={"adapter": adapter})


async def _probe_le(result, local_address):
    result.cleanup_status = "unconfirmed"
    device = await asyncio.wait_for(_find_ble(result.target, result.adapter), FIND_TIMEOUT)
    result.cleanup_status = "released"
    if device is None:
        result.status = "no_results"
        result.warnings.append("限时扫描未观察到目标；没有建立 GATT 连接。")
        return
    expected = f"/org/bluez/{result.adapter}/dev_{result.target.replace(':', '_')}"
    details = getattr(device, "details", None)
    if (str(getattr(device, "address", "")).upper() != result.target or
            not isinstance(details, dict) or details.get("path") != expected):
        raise RuntimeError("扫描设备地址/适配器路径不一致")
    await _snapshot(result, local_address, require_target=True)
    result.warnings.append("应用仅调用一次 connect、不自动重试；Bleak/BlueZ 底层可能重试或复用连接。")
    result.warnings.append("共享 BlueZ 存在检查后竞态，disconnect 可能影响同期客户端；应使用独立实验适配器并停止其他客户端。")
    result.warnings.append("连接预算 10 秒、断开预算 5 秒；取消传播/库内清理可能使实际耗时超过预算。")
    client = _new_ble_client(device, result.adapter)
    attempted = False
    try:
        attempted = True
        result.metadata["application_connect_calls"] = 1
        result.cleanup_status = "unconfirmed"
        await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
        if not client.is_connected:
            raise RuntimeError("GATT 连接未确认")
        for service in client.services:
            if len(result.services) >= MAX_RECORDS:
                raise ValueError("GATT 服务数超过上限")
            canonical, small = normalize_uuid(service.uuid)
            result.services.append(Service(canonical, safe_display_name(service.description), small,
                                           source="gatt", selectable=False))
            for char in service.characteristics:
                if len(result.characteristics) >= MAX_CHARACTERISTICS:
                    raise ValueError("GATT 特征数超过上限")
                char_uuid, _ = normalize_uuid(char.uuid)
                properties = [safe_display_name(p, 40) for p in list(char.properties)[:32]]
                handle = char.handle if type(char.handle) is int and 0 <= char.handle <= 0xffff else None
                result.characteristics.append(Characteristic(canonical, char_uuid, handle,
                    properties, safe_display_name(char.description), "read" in properties))
        if not result.services:
            result.status = "no_results"
        result.warnings.append("GATT 仅枚举元数据；未读取、写入、订阅、配对或设置信任；属性不保证当前可读。")
    except (KeyboardInterrupt, asyncio.CancelledError):
        result.status = "interrupted"
        raise
    finally:
        if attempted:
            try:
                await asyncio.wait_for(client.disconnect(), DISCONNECT_TIMEOUT)
                if client.is_connected:
                    raise RuntimeError("disconnect 后仍为 connected")
                result.cleanup_status = "released"
            except BaseException as exc:
                result.cleanup_status = "unconfirmed"
                result.warnings.append("GATT disconnect 未确认：" + safe_display_name(exc, 180))
                raise


class _OutputLimit(ValueError):
    pass


async def _probe_sdp(result, local_address):
    proc = None
    tasks = []
    stdout = bytearray()
    stderr = bytearray()
    total = 0

    async def read(stream, destination):
        nonlocal total
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return
            total += len(chunk)
            if total > MAX_OUTPUT:
                raise _OutputLimit("SDP 子进程输出超过 1 MiB 上限")
            destination.extend(chunk)

    try:
        result.cleanup_status = "unconfirmed"
        try:
            proc = await asyncio.create_subprocess_exec("sdptool", "-i", local_address,
            "browse", "--xml", result.target, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError:
            result.cleanup_status = "not_started"
            raise
        tasks = [asyncio.create_task(read(proc.stdout, stdout)),
                 asyncio.create_task(read(proc.stderr, stderr)), asyncio.create_task(proc.wait())]
        await asyncio.wait_for(asyncio.gather(*tasks), SDP_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError(f"sdptool exit={proc.returncode}：" + safe_display_name(stderr.decode("utf-8", "replace"), 180))
        if stderr:
            result.warnings.append("sdptool stderr：" + safe_display_name(stderr.decode("utf-8", "replace"), 180))
        result.services = parse_sdp_xml(stdout)
        result.status = "completed" if result.services else "no_results"
        result.warnings.append("这是一次限时 SDP browse，可能递归服务组；退出码 0 不证明清单完整或目标没有其他服务。")
    except (KeyboardInterrupt, asyncio.CancelledError):
        result.status = "interrupted"
        raise
    finally:
        if proc is not None:
            # A reader stops retaining data at MAX_OUTPUT. Waiting for a killed
            # asyncio process with a paused, full PIPE can otherwise deadlock.
            # Stop old readers, then drain/discard while reaping the process.
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            async def discard(stream):
                while await stream.read(8192):
                    pass

            drains = [asyncio.create_task(discard(proc.stdout)),
                      asyncio.create_task(discard(proc.stderr))]
            try:
                if proc.returncode is None:
                    terminate_failed = False
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass  # It may have exited between poll and terminate.
                    except OSError as exc:
                        terminate_failed = True
                        result.warnings.append("sdptool terminate 失败，尝试 kill：" + safe_display_name(exc, 140))
                    if not terminate_failed:
                        try:
                            await asyncio.wait_for(proc.wait(), 2.0)
                        except asyncio.TimeoutError:
                            terminate_failed = True
                    if terminate_failed:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        await asyncio.wait_for(proc.wait(), 2.0)
                await asyncio.wait_for(asyncio.gather(*drains), 2.0)
                result.cleanup_status = "released" if proc.returncode is not None else "unconfirmed"
            except BaseException as exc:
                result.cleanup_status = "unconfirmed"
                result.warnings.append("sdptool 子进程清理未确认：" + safe_display_name(exc, 180))
                raise
            finally:
                for task in drains:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*drains, return_exceptions=True)


async def _probe_async(transport, target, adapter, local_address, _result_sink=None):
    result = ProbeResult(target, adapter, transport)
    if _result_sink is not None:
        _result_sink.update(result=result, cleanup_finished=False)
    started = time.monotonic()
    try:
        local_address = await _snapshot(result, local_address)
        result.warnings.append("连接状态来自探测前快照；共享 BlueZ 的其他客户端仍可能并发改变状态。")
        if transport == "bredr":
            await _probe_sdp(result, local_address)
        else:
            await _probe_le(result, local_address)
    except (KeyboardInterrupt, asyncio.CancelledError):
        result.status = "interrupted"
    except Exception as exc:
        if result.status != "interrupted":
            result.status = "failed"
        result.warnings.append("探测失败：" + safe_display_name(str(exc) or type(exc).__name__, 240))
    finally:
        result.duration = round(max(0, time.monotonic() - started), 3)
        if result.cleanup_status == "unconfirmed" and result.status != "interrupted":
            result.status = "failed"
        if _result_sink is not None:
            _result_sink["cleanup_finished"] = True
    return result


def probe_device(transport, target, adapter="hci0", local_address=None):
    """Perform one confirmed probe under the shared same-user adapter lock.

    This API does not prompt, persist evidence, retry, or elevate privileges.
    The UI must explicitly confirm that metadata enumeration creates traffic.
    """
    if transport not in {"le", "bredr"}:
        raise ValueError("transport 必须是 le 或 bredr")
    if not isinstance(target, str) or not _MAC.fullmatch(target):
        raise ValueError("target 必须是 MAC 地址")
    if target.upper() in {"00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"}:
        raise ValueError("target 必须是单设备地址")
    if not isinstance(adapter, str) or not re.fullmatch(r"hci[0-9]{1,3}", adapter):
        raise ValueError("adapter 必须是 hciN")
    if local_address is not None and (not isinstance(local_address, str) or not _MAC.fullmatch(local_address)):
        raise ValueError("local_address 必须是 MAC 地址")
    if sys.platform != "linux":
        raise RuntimeError("设备能力探测需要 Linux 和 BlueZ")
    target = target.upper()
    local_address = local_address.upper() if local_address else None
    sink = {}
    try:
        with adapter_lock(adapter):
            return asyncio.run(_probe_async(transport, target, adapter, local_address, sink))
    except KeyboardInterrupt:
        result = sink.get("result") or ProbeResult(target, adapter, transport,
            status="interrupted", cleanup_status="unconfirmed")
        result.status = "interrupted"
        if not sink.get("cleanup_finished"):
            result.cleanup_status = "unconfirmed"
            result.warnings.append("探测被中断，清理状态尚未确认。")
        return result
    except RuntimeError as exc:
        return ProbeResult(target, adapter, transport, status="failed",
            warnings=[safe_display_name(exc, 240)])
