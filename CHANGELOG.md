# Changelog

本文件记录面向使用者的重要变更。未发布功能不表示已完成硬件验证。

## [0.1.0] — Unreleased

### Added

- Bash 单入口与 Python 包结构；面向 Ubuntu / Kali 的 Rich TUI 和向导。
- 环境诊断、适配器枚举、限时发现与 BLE 特征信息查询接口。
- `br-sdp`、`br-channel`、`ble-read`、`ble-cycle` 四种正常协议有界场景。
- 版本化 JSON 计划、显式目标确认及速率/时长/操作数/循环上限。
- `btmon` HCI 采集先就绪的监督执行，以及可选 Nordic BLE sniffer 接口。
- 运行计划快照、JSONL 事件、Markdown 报告及 SHA-256 清单验证。
- 不接触硬件、不伪造捕获的合成 demo。
- 中文主文档、英文概览、硬件/证据/开发说明与贡献和安全策略。

### Scope and validation

- 真实蓝牙运行面向 Linux；Windows 用于仓库管理与离线开发。
- HCI 不等于完整空口；nRF52840 的可选空口采集仅覆盖 BLE。
- 不将连接循环称为并发连接槽位耗尽，不将信道交互称为 ACL 周期测试。
- 不以工具退出成功代替目标业务可用、ECU 正常或法规合格结论。
- 尚无已验证硬件兼容性结论；实际验证范围随可复核记录更新。
