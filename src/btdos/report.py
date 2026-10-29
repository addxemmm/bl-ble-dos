"""Report execution, evidence availability, and business conclusions separately."""
import json


def render_report(plan, result):
    simulation = result.get("simulation", False)
    captures = result.get("captures", [])
    stats = result.get("worker_statistics", {})
    heading = "SIMULATION / 合成演示，非实测" if simulation else "现场运行记录 / 非自动合规结论"
    rows = []
    for item in captures:
        rows.append(f"| {item.get('name')} | {item.get('status')} | {item.get('file')} | {item.get('bytes', 0)} |")
    if not rows:
        rows.append("| HCI / air | 未采集 | 无 | 0 |")
    return f"""# Bluetooth 测试证据报告

> **{heading}**

- 工具版本：{result.get('tool_version', 'unknown')}
- 运行状态：`{result.get('status', 'unknown')}`（不是车辆 PASS/FAIL）
- 时间：{result.get('started', '')} → {result.get('finished', '')}
- simulation：`{str(simulation).lower()}`
- 用例：`{plan.get('case')}`；适配器：`{plan.get('adapter')}`
- 目标：`{plan.get('target')}`
- 停止/错误：{result.get('error') or result.get('stop_reason') or '无已记录错误'}

## 证据状态

| 来源 | 状态 | 原始文件 | bytes |
|---|---|---|---|
{chr(10).join(rows)}

- 独立正常业务探针：{'计划声明由外部提供；需另附实际结果' if plan.get('external_business_probe') else '未提供 / not_provided'}
- ECU状态观测：{'计划声明由外部提供；需另附实际结果' if plan.get('external_ecu_observation') else '未提供 / not_provided'}
- 数据包目标关联：`{result.get('packet_verification', 'manual_required')}`
- HCI文件存在/含记录不代表捕获到目标事务；空口文件非空不代表成功跟踪或解密。
- {('本报告所有事件和计数均为合成，不含伪造的真实抓包文件。' if simulation else 'worker收到响应是运行时观测；仍需与原始包的目标、连接生命周期、方向和事务关联。')}

## Worker API 统计（不是空口PDU计数）

```json
{json.dumps(stats, ensure_ascii=False, indent=2)}
```

## 判定边界与人工复核

1. **已发起**：原始包中有目标连接上的真实施压请求，不仅是本机日志。
2. **已到达/已处理**：同一目标连接的匹配SDP TID响应、ATT对应响应、车端RX记录或有效接收证据。
3. **防护有效/业务受影响**：需外部业务、ECU、防护和恢复证据支持；本工具不自动判定。
4. HCI TX、Command Complete、Number Of Completed Packets 不单独证明车端应用接收。
5. BR/EDR通道循环不等于ACL循环；串行BLE循环不等于多连接槽耗尽。
6. ATT响应可能不含Handle；按bearer与请求顺序关联。单dongle不构成独立空口或独立业务探针。

## 计划快照

```json
{json.dumps(plan, ensure_ascii=False, indent=2)}
```

## 待附证据

- 车型/ECU/协议栈与软件版本、配对/加密、PHY/MTU/连接参数。
- 实际SDP/ATT/连接事务索引、目标身份关联与抓包丢失范围。
- 正常业务成功率/时延、车端限流和日志、复位原因、资源与供电。
- 实际负载与批准预算、停止时间、自然恢复和人工干预区别。
- GB 44495适用正文/修改单及TARA映射，UN R155适用措施与试验判据。

`manifest.json` 为文件完整性清单，不是数字签名或来源认证；需受控留存原始包。
"""
