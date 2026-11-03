# solo-distill-mcp 项目事实基线

本页经当前用户授权登记稳定事实及其来源，不保存任务进度、当前 Git 分支或提交推送流水。代码事实以读取的实现为界；历史运行事实以原实例回执为界，不互相替代。

## 工程身份与版本

- 本工程是独立的 Python 结构提取与知识蒸馏工具，不是被扫描的 FastCode 来源项目。依据：[项目说明](../README.md:3)与[协作入口](../AGENTS.md:3)。
- 包元数据声明名称 solo-distill-mcp、包版本 0.1.0、Python 最低版本 3.12，运行时依赖列表为空；可选构建依赖为固定版本 setuptools。依据：[项目元数据](../pyproject.toml:1)。声明不证明当前主机满足运行环境或已经打包。
- Schema 3.0 是独立服务及输入契约的版本，不等于 Python 包版本 3.0。依据：[包版本](../pyproject.toml:7)、[V3 服务定义](../src/solo_distill/server_v3.py:28)与[V3 输入契约](../src/solo_distill/service_v3.py:32)。

## 已检查的接口与配置

- V1、V2、V3 服务分别有独立源码入口；V2 与 V3 分别定义扩展服务类。本页只登记定义存在，不宣称宿主已加载或接口运行通过。依据：[V1 服务](../src/solo_distill/server.py:64)、[V2 服务](../src/solo_distill/server_v2.py:19)与[V3 服务](../src/solo_distill/server_v3.py:28)。
- V1 声明协议版本 2024-11-05；V3 输入契约声明契约版本 3、同一协议版本、场景 A，以及分别名为 preflight 和 live 的操作。依据：[V1 协议声明](../src/solo_distill/server.py:17)与[V3 输入契约](../src/solo_distill/service_v3.py:32)。字段声明不是新的在线执行授权。
- 当前服务端配置只登记 fastcode-sample，来源根目录与读取限额由工程配置给定。依据：[来源配置](../config/projects.json:1)。配置读取、来源执行、来源写入和模型外发是不同权限，不能互相推定。

## 治理与资产职责

- 工具自身的公共入口、治理模块与领域导航分别定位协作纪律、操作规范及实现接口。依据：[协作入口](../AGENTS.md:5)、[治理索引](../specs/governance/00-index.md:13)与[领域导航](../specs/domains/00-index.md:1)。文件存在不证明控制器或宿主自动读取。
- 历史 ADR 与分析由新索引引用旧原件，归档计划和机器凭据继续保留原位置；来源蓝图为独立候选，不作为工具治理根。依据：[ADR 索引](../docs/adrs/README.md:3)、[分析索引](../docs/analysis/README.md:3)与[输出目录说明](README.md:5)。
- 任务薄指针只负责契约定位；当前进度只能使用覆盖已核验的控制器或平台任务记录，不由本事实基线保存。依据：[薄指针职责](../specs/tasks/_active.md:9)与[状态单源规则](../specs/governance/02-lifecycle-routing.md:27)。本轮没有证明控制器覆盖或程序防篡改。
- 通用忽略规则对历史资产和机器证据目录保留可见性例外；可见不等于获准上传或删除。依据：[忽略规则](../.gitignore:24)与[上传边界](README.md:27)。

## 历史收尾事实及限制

- 完成回执记录主线 A 实例 mainline-a-schema-v3-20261030T095713Z 的完成、关联 ADR 接受、计划归档与活动源退休。依据：[完成回执](../verification/mainline-a-schema-v3-20261030T095713Z/phase6-7-closeout-20261102.completion.receipt.json:1)。此事实不代表所有历史任务或全部能力已经完成。
- 同一回执明确人工语义审核未执行／未证明、宿主加载未证明。本页不将结构认证、文档生成、传输或 ADR 状态扩大为这些证据。
- 完成回执关联的[归档计划](archive/plans/20261102T105412Z-mainline-a-v2-scenario-a-20261029.md)、[ADR 原件](adrs/ADR-20261030-harel-fastcode-schema-v3.md)与[最终对账原件](../verification/mainline-a-schema-v3-20261030T095713Z/phase6-7-closeout-20261102.final-reconciliation.receipt.json)已按该回执的最终 SHA256 核对匹配；这仅证明所列文件在校核时点的字节身份，不是重跑历史验收。

## 登记溯源与使用边界

- 登记日期：2026-11-03。生产源码与历史材料的读取基线为提交 565db4c；工具导航来自已审查的治理增量。本页不随每轮状态保存镜像指纹或通过记录。
- 摘要校核依据为客户端命令回执 call_0566b6d55fbab86fc5f68fed，工作目录为本工程根目录，退出码 0；三个原件均报告匹配。完整历史证据仍以原文件为准。
- 当前登记未执行生产测试、来源扫描、在线业务或宿主激活；不继承历史在线许可。实现、契约或环境变化后，只重新核验受影响事实与必要证据。
- 未知会话标识保持未知；性能、人工语义、第二项目与宿主能力不从包元数据、源码定义或本页的存在推导。
