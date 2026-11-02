---
adr_id: ADR-20261028-mainline-b-brownfield-distill
plan_id: mainline-b-brownfield-distill-20261028
contract_version: 3
status: ACCEPTED
scenario: structural
impact_tier: null
requires_adr: true
adr_file: _bmad-output/adrs/ADR-20261028-mainline-b-brownfield-distill.md
verification_type: five_file_offline_static_blueprint_review_publication_readback
verification_command: "采用关联计划契约版本 3 的审核、定向离线夹具、独占发布与复读命令；历史步骤 2—4 证据保持原始版本，不镜像执行记录"
verification_command_status: VERIFIED_FOR_FIVE_FILE_OFFLINE_STATIC_SCOPE
authorization:
  current: "用户批准步骤 5—6，确认 CS／SR 字段、五文件离线静态蓝图验收和 _bmad-output/blueprints/mainline-b-20261028/ 独占发布"
  subsequent: "静态必需门禁及发布复读通过后允许本 ADR ACCEPTED 和当前计划归档；业务语义、stdio 和 Roo 保持未验收且不作为本次接受门禁；不登记事实基线"
session_id: null
---

# 主线 B：棕地样本重扫与独立候选交付

## 来源与状态

本记录按当前用户明确要求初始化，不恢复其他任务，也不继承历史通过状态。指定 ADR 和 Plan 的原生读取均返回不存在；当前工作区没有可读取的契约模板，采用用户提供的必需契约字段初始化。未知会话标识及影响等级保持空值。

本记录接受契约版本 3 的五文件离线静态蓝图决策：用户已批准该范围，本次必需静态门禁、独占发布及完整复读均已取得成功回执。业务语义、实际 stdio 和真实 Roo 仍未验收，不在接受范围内。只保存决策与依据，不保存逐轮进度流水。

## 决策

1. 工程执行根限定为当前独立项目；FastCode 只作为配置绑定的来源数据，不导入或执行，不修改其文件。
2. 用户确认读取集合为 api.py、fastcode/__init__.py、fastcode/agent_tools.py、fastcode/answer_generator.py、fastcode/cache.py；范围外事实锚点保留为未读取、未验收，不静默混用或扩读。
3. 新策略仅通过显式能力声明启用，保留必要结构、条件、关系、来源与未完成事项；本地离线消费者单帧 24576 字节、累计传输 4194304 字节、解码 2097152 字节、最多 256 个分片，允许分片。上限由用户明确确认，实际能力只按本轮真实接收范围证明，不推广为 Roo 宿主能力。
4. 本任务“主线 B”指工程交付主线，不等于协议场景 B。现有自适应封存、传输与消费者限制为协议场景 A；不得以本计划名称为由放宽协议场景 B 的既有契约。
5. 保全旧样本、新候选与原始证据；按用户确认的 CS／SR 字段审核后，只在指定工程内全新目录发布五文件离线静态蓝图。发布不授予来源项目写入、宿主激活或运行权限。
6. 控制器覆盖未获证明时，当前进度只使用平台任务记录；状态卡为会话交付，不另存进度账本，不宣称程序防篡改保证。

## 依据

- [配置](../../config/projects.json:4)绑定 FastCode 来源根，但允许范围为整个根；它本身不是五文件冻结凭证。
- [既有导航](../../verification/brownfield-review/python-mvp-20261027T0344Z/specs/index-001.md:5)列出历史五文件；[既有溯源](../../verification/brownfield-review/python-mvp-20261027T0344Z/analysis/provenance.json:1)记录 79 个导入、84 个定义以及 697 个被剪枝的调用。
- [独立事实规范](../../tests/fastcode_acceptance.json:5)的锚点涉及另一组来源文件，预期语义不得发送给生成模型；[验收边界](../../tests/fastcode_acceptance.json:20)另要求来源、语义、stdio 和真实宿主证据。
- [棕地构建](../../src/solo_distill/brownfield.py:104)已有显式完整消费路径；[命令行入口](../../src/solo_distill/brownfield.py:404)没有接入自适应能力参数。代码存在不等于当前真实五文件验收通过。
- [传输分片](../../src/solo_distill/adaptive_wire.py:76)检查单帧和累计限制；[消费者](../../src/solo_distill/consumer_v2.py:76)限制自适应协议场景，并在[结构与关系核对](../../src/solo_distill/consumer_v2.py:153)处检查分母。
- [独立接收器](../../tests/adaptive_receiver.py:98)要求非空定义与调用预期，并逐调用点核对；本次证据见[CS 对账](../../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/CS-RECONCILIATION.md:1)、[SR 审核](../../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/SR-REVIEW.md:1)、[定向夹具回执](../../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/offline-tests.receipt.json:1)和[发布复读回执](../../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/publication.receipt.json:1)。
- [历史最后一份失败摘要](../../verification/live-b-1792842977905.scratch-pin.retry.failure.json:12)记录退出码 2 和候选或证据结构无效；网关返回成功不能替代业务门禁通过。

## 取舍与未决项

- 独立候选与人工审核避免覆盖来源规则；代价是生成成功不能直接登记为发布完成。
- 完整分片消费不同于旧核心投影；旧候选内容完整保存，不代表全量调用保真。
- 步骤 2—4 的冻结集合、来源身份及完整消费证据保留原始版本。契约版本 3 的 CS／SR 审核核对五文件静态来源、分母、旧记录保全、未知保留及指定位置独占发布与复读；用户明确将业务语义、实际 stdio 往返和真实 Roo 排除本次发布及接受门禁，三者继续标为未验收，不推断为通过。
- 真实宿主、线上模型、历史性能轮次和其他暂停任务不因本次接管自动获得恢复授权。
- 本次接受仅限已验收的五文件离线静态蓝图决策，不接受完整业务契约、线上模型结果或宿主能力。用户已授权当前计划归档；不登记新的事实基线，不改变原始候选决策或历史证据状态。
