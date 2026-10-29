# 贡献指南

感谢帮助改进 bl-ble-dos。项目欢迎小而明确、可复现、保留能力边界的贡献。

## 开始前

1. 阅读 [README](README.md)、[开发指南](docs/development.md) 和[证据说明](docs/evidence.md)。
2. 对行为、CLI 或计划格式的较大改变先提交 issue，说明目标、边界和验收方式。
3. 保持四个现有场景的正常协议、有界工作负载定位；改变运行上限或权限模型需明确论证与复核。

## 本地检查

```bash
./scripts/install.sh --dev
.venv/bin/python -m unittest discover -s tests -v
./bt-dos demo --output runs/contribution-demo
# 使用 demo 输出的实际子目录替换 RUN_DIR
./bt-dos verify RUN_DIR
```

测试按修改风险增加失败与边界用例；默认测试不依赖设备、root 或无线交互。真实硬件试验与离线测试分别报告。提交时注明实际执行的命令、结果以及未覆盖范围。

## 报告问题

提供提交版本、发行版、Python/内核/BlueZ 版本、命令与去标识化计划、期望行为、实际行为及相关错误片段。请将 `TARGET`、`LOCAL_ADDRESS` 等占位符用于公开示例。原始捕获可能含敏感资料，不直接附到公开 issue。

兼容性记录还需包含适配器 VID/PID、固件、直通方式、目标类别/版本、场景、参数和证据缺口。不要仅凭 `doctor` 或 demo 将硬件标为已验证。

疑似安全漏洞按 [SECURITY.md](SECURITY.md) 处理，不在公开问题中发布敏感运行包。

## Pull request

- 说明问题、实现边界、测试结果及文档变更；关联相关 issue。
- 尽量保持单一主题，避免无关格式化和生成文件。
- 行为改变添加测试，CLI / schema / 报告改变同步修改说明。
- 不提交 `.venv/`、`runs/`、密钥、设备标识、真实捕获或个人凭据。
- 保留明确的 simulation 标识、采集失败和证据不足状态。
- 贡献按本仓库 [MIT 许可证](LICENSE) 提供；确认所提交材料的分发许可。

项目尚处开发预览阶段，维护者不承诺固定审查或修复时限。
