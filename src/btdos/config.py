"""Strict, versioned plans. A plan never contains shell commands or credentials."""
from dataclasses import asdict, dataclass, fields
import math
from pathlib import Path
import re

CASES = {
    "br-sdp": ("BR-01", "BR/EDR", "SDP 服务搜索负载"),
    "br-channel": ("BR-02", "BR/EDR", "L2CAP 通道资源回收"),
    "ble-read": ("LE-01", "BLE", "GATT 只读请求负载"),
    "ble-cycle": ("LE-02", "BLE", "LE 连接生命周期"),
    "ble-write-command": ("LE-03", "BLE", "专用fixture无响应写入负载"),
}
TEST_SERVICE_UUID = "9fbb0001-6284-4e2e-9c8e-3b7a2d546f10"
TEST_WRITE_UUID = "9fbb0002-6284-4e2e-9c8e-3b7a2d546f10"
WRITE_BUDGET = dict(rate=100.0, approved_rate=100.0, duration=10.0, max_ops=1000)
MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
DEFAULT_BUDGET = dict(rate=10.0, approved_rate=10.0, duration=10.0, max_ops=100,
                      cycles=5, hold=2.0, gap=3.0, baseline=5.0, recovery=10.0)
QUICK_BUDGET = dict(DEFAULT_BUDGET, rate=1.0, approved_rate=1.0, duration=5.0, max_ops=5, cycles=3,
                    baseline=2.0, recovery=5.0)


def budget_defaults(quick=False):
    """Fresh settings dict; preset changes never mutate shared defaults."""
    return dict(QUICK_BUDGET if quick else DEFAULT_BUDGET)


@dataclass
class Plan:
    case: str
    adapter: str
    target: str
    local_address: str | None = None
    characteristic: str | None = None
    sdp_uuid16: int = 0x110B
    rate: float = DEFAULT_BUDGET["rate"]
    approved_rate: float = DEFAULT_BUDGET["approved_rate"]
    duration: float = DEFAULT_BUDGET["duration"]
    max_ops: int = DEFAULT_BUDGET["max_ops"]
    cycles: int = DEFAULT_BUDGET["cycles"]
    hold: float = DEFAULT_BUDGET["hold"]
    gap: float = DEFAULT_BUDGET["gap"]
    baseline: float = DEFAULT_BUDGET["baseline"]
    recovery: float = DEFAULT_BUDGET["recovery"]
    capture: str = "hci"
    nrf_port: str | None = None
    output_root: str = "runs"
    external_business_probe: bool = False
    external_ecu_observation: bool = False
    schema_version: int = 1
    service_uuid: str | None = None
    write_test_confirmed: bool = False
    ble_interval_ms: float | None = None

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("计划必须是 JSON object")
        unknown = set(value) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"未知计划字段: {', '.join(sorted(unknown))}")
        try:
            plan = cls(**value)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        plan.validate()
        return plan

    def to_dict(self):
        return asdict(self)

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("仅支持 schema_version=1")
        if not isinstance(self.case, str) or self.case not in CASES:
            raise ValueError("未知用例")
        if not isinstance(self.adapter, str) or not re.fullmatch(r"hci[0-9]{1,3}", self.adapter):
            raise ValueError("adapter 必须是 hci0 形式")
        for name in ("target", "local_address"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not MAC.fullmatch(value)):
                raise ValueError(f"{name} 必须是单个 Bluetooth MAC 地址")
        if not self.target or self.target.upper() in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
            raise ValueError("目标不是有效的单设备地址")
        if self.case.startswith("br-") and not self.local_address:
            raise ValueError("BR/EDR 用例需要 local_address")
        if self.case == "ble-read":
            if not isinstance(self.characteristic, str) or not re.fullmatch(
                r"(?:0x[0-9a-fA-F]{1,4}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
                self.characteristic,
            ):
                raise ValueError("characteristic 需要完整 UUID 或 0xHANDLE")
        if type(self.write_test_confirmed) is not bool:
            raise ValueError("write_test_confirmed 必须是布尔值")
        if self.case == "ble-write-command":
            if (self.service_uuid != TEST_SERVICE_UUID or self.characteristic != TEST_WRITE_UUID
                    or self.write_test_confirmed is not True):
                raise ValueError("写入场景仅允许固定测试fixture service/characteristic并需显式确认")
        elif self.service_uuid is not None or self.write_test_confirmed:
            raise ValueError("service_uuid/write_test_confirmed 仅适用于 ble-write-command")
        if self.ble_interval_ms is not None:
            value = self.ble_interval_ms
            try:
                valid = (type(value) in (float, int) and math.isfinite(value)
                         and 7.5 <= value <= 50 and (value / 1.25).is_integer())
            except OverflowError:
                valid = False
            if not self.case.startswith("ble-") or not valid:
                raise ValueError("ble_interval_ms 仅适用于BLE：7.5..50ms，且为1.25ms倍数")
        for name in ("sdp_uuid16", "max_ops", "cycles"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} 必须是整数")
        max_ops_cap = 1000 if self.case == "ble-write-command" else 400
        if not 0 <= self.sdp_uuid16 <= 65535 or not 1 <= self.max_ops <= max_ops_cap or not 1 <= self.cycles <= 10:
            raise ValueError("UUID16/max_ops/cycles 超出范围")
        for name in ("rate", "approved_rate", "duration", "hold", "gap", "baseline", "recovery"):
            value = getattr(self, name)
            try:
                finite = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{name} 需要有限数值")
        rate_cap = 100 if self.case == "ble-write-command" else 20
        duration_cap = 10 if self.case == "ble-write-command" else 60
        if not 0.1 <= self.rate <= min(rate_cap, self.approved_rate):
            raise ValueError(f"rate 需在 0.1..min({rate_cap},approved_rate) operations/s 内")
        if not 1 <= self.duration <= duration_cap or not 1 <= self.hold <= 5 or not 3 <= self.gap <= 30:
            raise ValueError(f"duration 1..{duration_cap}s；hold 1..5s；gap 3..30s")
        if not 0 <= self.baseline <= 60 or not 0 <= self.recovery <= 120:
            raise ValueError("baseline 0..60s；recovery 0..120s")
        if self.capture not in ("hci", "hci+nrf"):
            raise ValueError("capture 必须是 hci 或 hci+nrf")
        if self.capture == "hci+nrf":
            if not self.case.startswith("ble-"):
                raise ValueError("nRF Sniffer 仅适用于 BLE 空口")
            if not isinstance(self.nrf_port, str) or not re.fullmatch(r"/dev/(?:ttyACM|ttyUSB)[0-9]+", self.nrf_port):
                raise ValueError("nrf_port 需要 /dev/ttyACM0 或 /dev/ttyUSB0")
        for name in ("external_business_probe", "external_ecu_observation"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须是布尔值；表示外部证据安排，非自动验证")
        if not isinstance(self.output_root, str) or not self.output_root.strip() or '\x00' in self.output_root:
            raise ValueError("output_root 无效")

    @property
    def worker_budget(self):
        interval_budget = 20 if self.ble_interval_ms is not None and self.case.startswith("ble-") else 0
        if self.case == "ble-cycle":
            return self.cycles * (10 + 10 + self.hold + 5 + self.gap + interval_budget) + 10
        if self.case == "br-channel":
            return self.cycles * (3 + self.hold + self.gap) + 10
        if self.case == "ble-write-command":
            return self.duration + self.baseline + self.recovery + 35 + interval_budget
        return self.duration + (35 if self.case == "ble-read" else 10) + interval_budget

    @property
    def wall_budget(self):
        return math.ceil(self.baseline + self.worker_budget + self.recovery + 30)


def load_plan(path):
    import json
    return Plan.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
