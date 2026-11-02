# 五文件离线静态蓝图发布说明

本目录是用户授权的正式静态蓝图资产，不是 FastCode 已加载的规则或运行时业务契约。目录只有在独占发布、完整保存与摘要复读成功后才算正式交付；[发布清单](manifest.json:1)定义发布状态与完整资产集合。

## 阅读入口

- 第一层：[项目阅读指引](AGENTS.md:1)与[宪法候选原文](specs/CONSTITUTION.md:1)。
- 第二层：[模块导航](specs/index-001.md:1)、[静态领域边界](specs/domain-boundaries.md:1)、[治理说明](specs/governance.md:1)和[空闲薄指针](specs/IDLE.md:1)。
- 第三层：[原始候选溯源](analysis/provenance.json:1)、[认证知识包](analysis/knowledge-package.json:1)、[锚点及未知](analysis/review-obligations.json:1)。
- 本次审核：[CS 对账](analysis/CS-RECONCILIATION.md:1)、[SR 审核包](analysis/SR-REVIEW.md:1)、[实际审核回执](analysis/review-evidence.json:1)与[定向测试回执](analysis/offline-tests.receipt.json:1)。

## 正式状态与原始材料

- 接受范围限定为已冻结五文件的离线静态蓝图：核心 163 条不变，完整调用 751 个、条件 395 个、关系位置 811 个；数据与身份详见[审核回执](analysis/review-evidence.json:1)。
- 全部候选内容按原字节保留，包括待审核、提案及旧阶段状态文字；它们描述候选生成时点，不定义本发布目录的当前状态。原始清单另存为[候选清单](analysis/candidate-manifest.json:1)，正式资产集合由本目录[发布清单](manifest.json:1)定义。
- 原候选决策仍是[候选决策原文](analysis/DECISIONS-PROPOSED.md:1)；关联工程 ADR 的接受是独立动作，不能把旧候选原文当成工程 ADR 当前状态。
- 没有覆盖旧样本、新候选、源码、配置、测试、原始回执或事实基线；该目录不自动写入来源项目或触发宿主加载。

## 明确未验收

- 业务语义、实际 stdio 往返、真实 Roo 可见性与消费回执均未验收，按用户修订的契约不作为本次静态发布或关联 ADR 接受门禁。
- 范围外九项事实锚点未读取、未验收；范围内四项只核验静态定位，不登记语义通过。
- 运行时行为、动态调用目标、持久化成功、业务所有权、构建测试入口及其他语言能力仍未知。
- 本地实际认证消费不证明真实宿主能力或普遍容量；工程主线 B 不等于协议场景 B，原协议场景 A 限制不变。
- 控制器覆盖未获证明；当前进度仅用平台待办，不宣称程序防篡改或全树持续隔离。
