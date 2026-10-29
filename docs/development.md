# 开发指南

[返回首页](../README.md) · [贡献说明](../CONTRIBUTING.md) · [证据设计](evidence.md)

## 环境与入口

Python 3.10+，Linux 是真实蓝牙运行平台。源码、文档、单元测试和纯合成 demo 可与无线设备解耦；硬件结果需在记录版本的 Linux 环境单独验证。

```bash
./scripts/install.sh --dev
.venv/bin/python -m unittest discover -s tests -v
./bt-dos --help
./bt-dos demo --output runs/dev-demo
# 使用 demo 输出的实际子目录替换 RUN_DIR
./bt-dos verify RUN_DIR
```

仓库采用 Bash `./bt-dos` 作为统一入口，Python 包位于 `src/btdos`。展示使用 Rich，不需要 Web 服务或浏览器。安装脚本管理项目本地 `.venv`，不要求修改全局 Python 环境。

## 逻辑边界

```text
Bash entry → CLI / Rich wizard → validated plan
                                  ↓
                              supervisor
                              ├─ capture: btmon (+ optional nRF)
                              ├─ bounded worker
                              └─ events / report / manifest
```

- **CLI / 向导**：输入与展示，选择场景并确认目标；不在 UI 中执行特权采集。
- **计划校验**：统一验证 schema、场景依赖、目标、范围与运行限值；命令行与向导共享边界。
- **supervisor**：负责采集先就绪、启动顺序、超时、信号和子进程回收；采集异常不静默降级。
- **worker**：执行一个场景的正常协议有界工作；不接受任意命令或无限负载。
- **报告 / 清单**：从本次保存的数据生成输出；保留 simulation 和证据缺口，不补造通过结论。

具体模块名称以当前源码为准。修改任何跨层接口时同时检查 CLI、向导、demo 与真实运行路径，避免只在其中一条路径生效。

## 不变条件

1. 请求速率默认 1 req/s，最高 20，且不高于 `approved_rate`。
2. 请求型负载 `duration` 不超过 60 s、`max_ops` 不超过 400；信道/连接循环不超过 10，hold 为 1–5 s，gap 为 3–30 s，并有监督预算。全程不以 60 s 作为通用上限。
3. `run` 的显式确认目标应与计划一致；BR/EDR 应记录对应本地适配器地址。
4. HCI 采集必选，就绪后启动 worker；可选 nRF 不替代 HCI。
5. 普通用户运行 UI / worker；只为 `btmon` 子进程使用 `sudo -n`。不自动安装 sudoers、不设置 capabilities。
6. 信号、超时、采集失败与 worker 失败均应触发受控清理，保留已取得的证据。
7. `demo` 不调用硬件，不生成伪造捕获，显著标识 simulation。
8. 外部业务/ECU 声明不是观测数据；证据缺口不得变成自动 PASS。
9. 不将 `br-channel` 标为 ACL 生命周期压力，不将 `ble-cycle` 标为并发槽位耗尽。
10. 用户输入不得以 shell 字符串拼接执行；子进程参数与文件边界应独立校验。

## 测试策略

单元测试默认使用临时目录与替身，不依赖 root、蓝牙控制器或无线目标。为修改覆盖相关的正常、边界和失败路径，优先验证：

- 非法 schema、缺失字段、超限参数和目标确认不一致；
- BR/EDR 本地地址、BLE 可读特征及采集参数依赖；
- 捕获就绪前不启动 worker、采集提前退出和子进程错误；
- Ctrl+C、超时、正常结束的清理与事件完整性；
- demo 无硬件副作用、报告显著 simulation；
- 清单缺失、文件缺失、内容篡改与有效包校验；
- 缺少空口、业务探针和 ECU 数据时的结论边界。

完整硬件实验需另行执行并保存环境、计划与运行包。测试日志中将“单元测试通过”“demo 校验通过”和“硬件实验通过”分开陈述，不用单元测试替代设备兼容性声明。

## 修改与发布检查

- 提交粒度保持明确；计划格式、CLI 和报告语义改变时同步更新文档与测试。
- 修改上限、采集就绪判断、进程清理或证据判定时进行定向复核。
- 更新 [CHANGELOG.md](../CHANGELOG.md) 的 Unreleased 项，不预填未执行的验证结果。
- 检查许可证、敏感数据、依赖和示例占位符；不要把 `runs/` 或原始捕获提交到 Git。
- 发布标签与发布说明应描述实际完成的验证范围；CI 徽章只在工作流存在且状态可核对后使用。

维护者应在公开发布前完成代码与文档审阅；本指南不自动触发发布、上传或权限修改。
