# 测试资产导航与执行边界

本页定位测试，不是本轮测试回执，也不授予执行权限。文档治理只审查获准文档差异和引用，不运行测试。

## 按改动范围选择

- 旧入口与提取：[离线测试](test_offline.py)、[背压测试](test_backpressure.py)、[核心优先级测试](test_priority_delivery.py)。
- V2 契约与消费者：[契约测试](test_v2_contract.py)、[消费者测试](test_v2_consumer.py)、[调度测试](test_v2_scheduler.py)。
- V3 宏观与局部：[宏观测试](test_schema_v3_macro.py)、[生产局部测试](test_schema_v3_production_local.py)、[包测试](test_schema_v3_package.py)。
- V3 问题选择：[独立选择测试](test_schema_v3_selection_oracle.py)、[覆盖选择独立测试](test_schema_v3_selection_coverage_oracle.py)、[覆盖选择生产测试](test_schema_v3_selection_coverage.py)。
- V3 传输与服务：[传输测试](test_schema_v3_wire.py)、[独立传输测试](test_schema_v3_wire_oracle.py)、[stdio 测试](test_schema_v3_stdio.py)、[运行边界测试](test_schema_v3_runtime_gates.py)。
- 文档候选：[棕地测试](test_brownfield.py)、[V3 治理测试](test_schema_v3_governance.py)。

## 独立认证与真实业务分离

- 独立期望资产见[宏观 Oracle](schema_v3_macro_oracle.py)、[局部 Oracle](schema_v3_local_oracle.py)、[选择 Oracle](schema_v3_selection_oracle.py)与[传输 Oracle](schema_v3_wire_oracle.py)。
- 独立实现的导入边界和期望来源须按获准改动实际检查，不因文件名宣称独立性已通过。
- [实时集成驱动](run_live_integration.py)、[V2 实时驱动](live_v2_driver.py)与[网关诊断](gateway_readonly_diagnostic.py)只作为定位；运行前单独核对完整实现及网络、来源、费用和保存权限。
- 真实来源、在线驱动和历史验收资产与离线夹具不同，不能从测试目录位置推定全部可安全运行。
- 不默认全库发现，不联网安装依赖，不启动网关，不执行来源业务测试。

## 回执要求

- 依获准契约串行、前台、封闭输入并使用进程级限时，取得实际退出后再推进。
- 报告命令、工作目录、目标、非零命中、退出状态和必需跳过情况，不把命令登记当作通过。
- 缺依赖、超时、零命中、必需跳过、缺证据或清理未知时阻塞相关验证。
- 实现、测试、契约或环境变化后仅重验受影响证据；保存进度不触发业务重测。

范围规则见[验证门禁](../specs/governance/04-verification-gates.md)，接口定位见[领域导航](../specs/domains/00-index.md)。
