"""Bounded BlueZ discovery with a private, caller-owned D-Bus session.

Importing this module never opens a bus or touches a controller. Device1 metadata
cannot prove which bearer produced a signal: filters from BlueZ clients merge.
"""
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
import sys
import time
import unicodedata

from .supervisor import adapter_lock


DEVICE_IFACE = "org.bluez.Device1"
ADAPTER_IFACE = "org.bluez.Adapter1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
MAX_DEVICES = 500
CALL_TIMEOUT = 5.0
DISCOVERY_KEYS = frozenset({"RSSI", "ManufacturerData", "ServiceData",
    "AdvertisingData", "AdvertisingFlags", "TxPower", "Class", "Name"})
_METADATA_KEYS = frozenset({"Address", "AddressType", "Name", "Alias", "RSSI", "Paired", "UUIDs"})
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
_ADDRESS = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")


def safe_display_name(text, max_length=80):
    """Return single-line terminal-safe text (not Rich markup)."""
    value = _ANSI.sub("", str(text or ""))
    value = "".join(char for char in value
                    if unicodedata.category(char) not in {"Cc", "Cf", "Cs"})
    return value[:max(0, max_length)]


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _unwrap(value):
    return getattr(value, "value", value)


def _metadata(props):
    """Keep only small display metadata; never retain advertising payloads."""
    result = {}
    for key in _METADATA_KEYS.intersection(props):
        value = _unwrap(props[key])
        if key in {"Name", "Alias", "Address", "AddressType"}:
            result[key] = safe_display_name(value)
        elif key == "UUIDs":
            result[key] = [safe_display_name(item) for item in value[:128]] if isinstance(value, (list, tuple)) else []
        elif key == "RSSI":
            result[key] = value if type(value) is int else None
        elif key == "Paired":
            result[key] = value if type(value) is bool else None
    return result


@dataclass
class Device:
    object_path: str
    address: str
    display_name: str
    address_type: str = "unknown"
    rssi: int | None = None
    paired: bool | None = None
    services: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    observation_source: str = "bluez-discovery-signal"
    transport_verified: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class ScanResult:
    requested_transport: str
    adapter: str
    started_at: str
    duration: float
    devices: list[Device]
    shared_discovery_at_start: bool = False
    status: str = "completed"
    cleanup_status: str = "released"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class _Observations:
    """Metadata cache is deliberately separate from this scan's observations."""
    def __init__(self, adapter, warnings):
        self.prefix = f"/org/bluez/{adapter}/"
        self.cache = {}
        self.devices = {}
        self.warnings = warnings
        self.active = False
        self.truncated = False

    def accepts(self, path):
        return isinstance(path, str) and re.fullmatch(
            re.escape(self.prefix) + r"dev_[0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5}", path) is not None

    def _limit(self):
        if not self.truncated:
            self.warnings.append(f"设备元数据/观察列表已达 {MAX_DEVICES} 条上限；后续新设备可能被截断。")
            self.truncated = True

    def update(self, path, props, source=None, invalidated=()):
        if not self.accepts(path) or not isinstance(props, dict):
            return
        if path not in self.cache:
            if len(self.cache) >= MAX_DEVICES:
                self._limit()
                # A fresh observation may still be retained without cached metadata.
                data = {}
            else:
                data = self.cache.setdefault(path, {})
        else:
            data = self.cache[path]
        for key in invalidated:
            data.pop(key, None)
        data.update(_metadata(props))
        qualifies = self.active and source and bool(DISCOVERY_KEYS.intersection(props))
        previous = self.devices.get(path)
        if not qualifies and previous is None:
            return
        if previous is None and len(self.devices) >= MAX_DEVICES:
            self._limit()
            return
        address = data.get("Address") or path.rsplit("dev_", 1)[-1].replace("_", ":")
        if not _ADDRESS.fullmatch(address):
            return
        now = _utc()
        self.devices[path] = Device(path, address.upper(),
            safe_display_name(data.get("Name") or data.get("Alias") or "未命名设备"),
            address_type=data.get("AddressType") or "unknown", rssi=data.get("RSSI"),
            paired=data.get("Paired"), services=list(data.get("UUIDs", [])),
            first_seen=previous.first_seen if previous else now,
            last_seen=now if qualifies else previous.last_seen,
            observation_source=source if qualifies else previous.observation_source)

    def removed(self, path, interfaces):
        if self.accepts(path) and DEVICE_IFACE in interfaces:
            self.cache.pop(path, None)
        # An observation remains evidence even if BlueZ later expires its object.


class _DBusClient:
    """Thin lazy-import adapter, also the replacement seam for hardware-free tests."""
    def __init__(self):
        from dbus_fast import BusType, Message, MessageType, Variant
        from dbus_fast.aio import MessageBus
        self.Message, self.MessageType, self.Variant = Message, MessageType, Variant
        self.bus = MessageBus(bus_type=BusType.SYSTEM)

    async def connect(self):
        await self.bus.connect()

    async def call(self, destination, path, interface, member, signature="", body=None):
        reply = await self.bus.call(self.Message(destination=destination, path=path,
            interface=interface, member=member, signature=signature, body=body or []))
        if reply is None:
            raise RuntimeError(f"{member}: D-Bus 无响应")
        if reply.message_type == self.MessageType.ERROR:
            detail = safe_display_name(reply.body[0] if reply.body else "", 180)
            raise RuntimeError(f"{reply.error_name}: {detail}")
        return reply.body

    def add_handler(self, handler):
        self.bus.add_message_handler(handler)

    def remove_handler(self, handler):
        self.bus.remove_message_handler(handler)

    def is_signal(self, message):
        return message.message_type == self.MessageType.SIGNAL

    def variant(self, signature, value):
        return self.Variant(signature, value)

    async def wait_for_disconnect(self):
        await self.bus.wait_for_disconnect()

    def disconnect(self):
        self.bus.disconnect()


def _new_client():
    return _DBusClient()


async def _scan_async(transport, adapter, seconds, on_progress, _result_sink=None):
    result = ScanResult(transport, adapter, _utc(), 0.0, [], cleanup_status="not_started", warnings=[
        "BlueZ 多客户端发现过滤器会合并；设备传输类型未经核实，结果不保证严格区分 BR/EDR 与 LE。"])
    if _result_sink is not None:
        _result_sink.update(result=result, cleanup_finished=False)
    observed = _Observations(adapter, result.warnings)
    client = None
    disconnected = None
    handler = None
    started = False
    start_attempted = False
    begun = None
    owner = None
    owner_lost = False
    interrupted = None
    adapter_path = f"/org/bluez/{adapter}"

    async def invoke(destination, path, interface, member, signature="", body=None):
        return await asyncio.wait_for(client.call(destination, path, interface, member, signature, body), CALL_TIMEOUT)

    async def bluez(member, signature="", body=None):
        # Use the resolved unique owner; a restarted daemon must not inherit calls.
        return await invoke(owner, adapter_path, ADAPTER_IFACE, member, signature, body)

    try:
        client = _new_client()
        result.cleanup_status = "unconfirmed"
        disconnected = asyncio.create_task(client.wait_for_disconnect())
        await asyncio.wait_for(client.connect(), CALL_TIMEOUT)
        body = await invoke("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", "s", ["org.bluez"])
        owner = body[0]

        def handle(message):
            nonlocal owner_lost
            if not client.is_signal(message):
                return
            body = message.body
            if (message.sender == "org.freedesktop.DBus" and
                    message.interface == "org.freedesktop.DBus" and message.member == "NameOwnerChanged" and
                    message.path == "/org/freedesktop/DBus" and len(body) == 3 and body[0] == "org.bluez" and
                    body[1] == owner and body[2] != owner):
                owner_lost = True
                return
            if message.sender != owner:
                return
            if message.interface == OBJECT_MANAGER and message.path == "/":
                if message.member == "InterfacesAdded" and len(body) == 2:
                    props = body[1].get(DEVICE_IFACE)
                    if props is not None:
                        observed.update(body[0], props, "bluez-interfaces-added")
                elif message.member == "InterfacesRemoved" and len(body) == 2:
                    observed.removed(body[0], body[1])
            elif (message.interface == PROPERTIES and message.member == "PropertiesChanged" and
                    len(body) == 3 and body[0] == DEVICE_IFACE):
                observed.update(message.path, body[1], "bluez-properties-changed", body[2])

        handler = handle
        client.add_handler(handler)
        rules = [f"type='signal',sender='{owner}',interface='{OBJECT_MANAGER}',path='/'",
            f"type='signal',sender='{owner}',interface='{PROPERTIES}',path_namespace='{adapter_path}'",
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'"]
        for rule in rules:
            await invoke("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s", [rule])
        objects = (await invoke(owner, "/", OBJECT_MANAGER, "GetManagedObjects"))[0]
        adapter_props = objects.get(adapter_path, {}).get(ADAPTER_IFACE)
        if adapter_props is None:
            raise RuntimeError(f"{adapter} 未发现 BlueZ 适配器")
        result.shared_discovery_at_start = bool(_unwrap(adapter_props.get("Discovering", False)))
        if result.shared_discovery_at_start:
            result.warnings.append("扫描开始前已有其他发现会话；结束时仅释放本连接的会话。")
        for path, interfaces in objects.items():
            if DEVICE_IFACE in interfaces:
                observed.update(path, interfaces[DEVICE_IFACE])
        del objects
        await bluez("SetDiscoveryFilter", "a{sv}", [{"Transport": client.variant("s", transport),
                                                    "DuplicateData": client.variant("b", True)}])
        observed.active = True
        start_attempted = True
        begun = time.monotonic()
        await bluez("StartDiscovery")
        started = True
        while True:
            elapsed = time.monotonic() - begun
            if owner_lost:
                raise RuntimeError("BlueZ 服务所有者已变化，扫描中断")
            if disconnected.done():
                disconnected.result()
                raise RuntimeError("系统 D-Bus 连接已断开，扫描中断")
            if on_progress:
                on_progress(min(elapsed, seconds), len(observed.devices))
            if elapsed >= seconds:
                break
            await asyncio.sleep(min(0.1, seconds - elapsed))
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        interrupted = exc
        result.status = "interrupted"
    except Exception as exc:
        result.status = "failed"
        result.warnings.append(f"扫描失败：{safe_display_name(str(exc) or type(exc).__name__, 240)}")
    finally:
        observed.active = False
        if begun is not None:
            result.duration = round(max(0.0, time.monotonic() - begun), 3)
        result.devices = sorted(observed.devices.values(), key=lambda item: (
            item.rssi is None, -(item.rssi or 0), item.address))
        cleanup_error = False
        # Never StopDiscovery after an uncertain Start reply: closing this unique
        # sender releases its session, without affecting sessions of other clients.
        if started and client is not None and not owner_lost:
            try:
                await bluez("StopDiscovery")
            except BaseException as exc:
                cleanup_error = True
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    interrupted = exc
                result.warnings.append(f"StopDiscovery 未确认：{safe_display_name(str(exc) or type(exc).__name__, 200)}；释放独立连接。")
        elif start_attempted and not started:
            result.warnings.append("StartDiscovery 未确认；通过关闭独立连接释放可能已创建的会话。")
        if client is not None:
            if handler is not None:
                try:
                    client.remove_handler(handler)
                except Exception:
                    pass  # Handler is private to the connection about to close.
            try:
                client.disconnect()
                if disconnected is not None:
                    await asyncio.wait_for(asyncio.shield(disconnected), CALL_TIMEOUT)
                result.cleanup_status = "connection_closed" if cleanup_error or (start_attempted and not started) else "released"
            except BaseException as exc:
                cleanup_error = True
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    interrupted = exc
                result.cleanup_status = "unconfirmed"
                result.warnings.append(f"独立 D-Bus 连接关闭未确认：{safe_display_name(str(exc) or type(exc).__name__, 200)}")
            finally:
                if disconnected is not None and not disconnected.done():
                    disconnected.cancel()
                    await asyncio.gather(disconnected, return_exceptions=True)
        if cleanup_error:
            result.status = "failed" if interrupted is None else "interrupted"
        if _result_sink is not None:
            _result_sink["cleanup_finished"] = True
    return result


def scan_devices(transport, adapter="hci0", seconds=5, on_progress=None):
    """Scan explicitly for 1..15 seconds, returning cancellation cleanup evidence.

    Never connect, pair, or resolve GATT. Cancellation returns ``interrupted``;
    callers must inspect cleanup_status and warnings even after Ctrl+C.
    """
    if transport not in {"le", "bredr"}:
        raise ValueError("transport 必须是 le 或 bredr")
    if not isinstance(adapter, str) or not re.fullmatch(r"hci[0-9]{1,3}", adapter):
        raise ValueError("adapter 必须是 hciN，例如 hci0")
    if type(seconds) is not int or not 1 <= seconds <= 15:
        raise ValueError("扫描时长必须是 1..15 秒的整数")
    if sys.platform != "linux":
        raise RuntimeError("蓝牙设备扫描需要 Linux 和 BlueZ；手动地址输入不需要扫描")
    result_sink = {}
    try:
        with adapter_lock(adapter):
            return asyncio.run(_scan_async(transport, adapter, seconds, on_progress,
                                           _result_sink=result_sink))
    except KeyboardInterrupt:
        # Python 3.10 can raise SIGINT outside the coroutine. asyncio.run's
        # shutdown cancels/drains the task; retain that task's cleanup evidence.
        result = result_sink.get("result") or ScanResult(
            transport, adapter, _utc(), 0.0, [], status="interrupted",
            cleanup_status="unconfirmed")
        result.status = "interrupted"
        if not result_sink.get("cleanup_finished", False):
            result.cleanup_status = "unconfirmed"
            result.warnings.append("扫描被中断；独立发现会话的清理状态尚未确认。")
        return result
    except RuntimeError as exc:
        # Lock contention is an operational failure, not malformed user input.
        return ScanResult(transport, adapter, _utc(), 0.0, [], status="failed",
            cleanup_status="not_started", warnings=[safe_display_name(exc, 240)])
