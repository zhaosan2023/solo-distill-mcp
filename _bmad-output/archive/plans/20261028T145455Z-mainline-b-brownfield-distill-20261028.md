---
plan_id: mainline-b-brownfield-distill-20261028
contract_version: 3
status: COMPLETED
scenario: structural
impact_tier: null
requires_adr: true
adr_file: _bmad-output/adrs/ADR-20261028-mainline-b-brownfield-distill.md
verification_type: five_file_offline_static_blueprint_review_publication_readback
verification_command:
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_b_steps56_20261028.py review </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B -m unittest -v test_adaptive_backpressure.ReadingTests test_adaptive_backpressure.SchedulingTests test_adaptive_backpressure.TelemetryTests test_adaptive_backpressure.TransportTests test_adaptive_backpressure.IntegrationTests.test_adaptive_brownfield_pages_all_definitions_and_authenticated_relations test_brownfield </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_b_steps56_20261028.py publish </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_b_steps56_20261028.py readback </dev/null"
verification_command_status: VERIFIED_FOR_FIVE_FILE_OFFLINE_STATIC_SCOPE
authorization:
  current: "用户已审阅步骤 2—4，批准继续步骤 5—6；确认 CS／SR 字段及五文件离线静态蓝图验收契约"
  subsequent: "仅推进当前计划，不恢复其他任务；不登记事实基线"
  publication: "审核确认无破坏后，独占创建 _bmad-output/blueprints/mainline-b-20261028/；发布五文件静态蓝图资产及关联审核材料，保全旧候选、新候选和原始回执"
  adr_promotion: "本次离线静态必需门禁全部通过且发布复读成功后，将关联 ADR 转正为 ACCEPTED；不证明业务语义、stdio 或真实 Roo"
  archiving: "当前计划全部获准步骤完成后，完整保存至全新时间戳及 plan_id 命名的归档目标，复核源标识和内容版本后处理活动源"
session_id: null
---

# 主线 B：棕地样本重扫与独立候选交付

## 初始化依据与契约边界

指定 Plan 与 ADR 原生读取返回不存在。本计划依当前用户给出的六步目标、必需契约字段及现有物理实现初始化，不补造历史计划、会话或通过记录；没有发现当前工作区内可读取的契约模板。影响等级及会话标识未知，保持空值。

本计划保存目标、授权、阶段与验收契约，不镜像逐轮进度。当前任务进度以平台任务记录为唯一临时入口；控制器真实可用性与本任务覆盖未获证明，不宣称已有程序防篡改保证。

## 工作区、读取与输出边界

- 工程执行根必须为当前独立项目；不得在方法论仓库执行，不恢复其他暂停任务。
- [来源配置](../config/projects.json:4)绑定 FastCode；配置允许整个根，不等于已冻结五文件读取范围。来源代码仅作数据，不导入、不执行、不修改。
- [旧候选导航](../verification/brownfield-review/python-mvp-20261027T0344Z/specs/index-001.md:5)所列五文件作为待确认候选范围，不静默替换、缩小或扩大。
- [独立事实规范](../tests/fastcode_acceptance.json:5)涉及六种文件路径，其中仅缓存与答案生成模块和旧五文件集合重叠；后续必须明确范围外锚点的处理，不把未读取锚点登记通过。
- 仅允许在本工程内全新独立候选目录交付后续审核材料；既有候选、源码改动及原始回执必须保全，不原地覆盖。
- 主线 B 是工程场景水印，不等于协议场景 B。[现有自适应消费者](../src/solo_distill/consumer_v2.py:76)仅支持显式协议场景 A，禁止静默放宽旧协议。

## 六步目标与验收

1. 核对工作区、源码读取授权及候选输出边界；检查指定契约、四个相关源码、旧候选物理完整性与时间戳，以及历史失败回执。交付有界状态对账卡、六步完成比例和平台待办，然后停止等待确认。
2. 获准后读取目标规范、旧溯源、配置与离线引擎声明；冻结五文件具体名单及非空定义、导入、条件、关系／调用点和必要未完成事项集合。范围、来源身份与验收字段不能留作隐含假设。
3. 获准后离线重扫，验证软背压、完整分片消费及机械保真；单个实际传输帧不超过 24576 字节，核对通知、唯一最终清单、顺序、数量、摘要、完整结构与关系分母。累计传输、解码及分片数量能力须明确确认，不能直接采用示例值当能力证明。
4. 获准后生成独立新候选：宪法／指引、领域与治理模块、决策及分析、空闲薄指针。生成前明确文件清单与内容边界，保存后复读；语法声明不得冒充业务不变量、运行时证明或已接受决策。
5. 获准后完成候选差异审查与 CS 对账，提交独立 SR 审核包。CS／SR 的具体字段、审核对象与通过标准须先确认；来源外锚点、未知项和未通过项必须显式保留。未批准前不发布。
6. 仅用户批准具体发布范围后实施发布与复读，保全旧样本和原始回执；发布成功不自动授权 ADR 转正、事实登记或计划归档。

## 阶段交接与验证约束

- 当前用户授权覆盖步骤 5—6、指定工程内目录发布、关联 ADR 转正与当前计划归档；步骤 2—4 的历史证据保留原始契约版本 2。契约版本 3 明确缩定为五文件离线静态蓝图，不将业务语义、实际 stdio 往返或真实 Roo 消费列为本次发布及接受门禁，三者仍保留未验收；这不是补造通过或放宽来源运行权限。
- CS 字段为审核项、旧候选、新候选、差异、证据、破坏性判断、未决项；SR 字段为范围、逐项结论、必需门禁、阻塞项、发布结论。必需门禁为新旧清单及摘要完整、五文件来源身份与静态分母一致、旧必需记录及已保留调用无丢失、新全量调用／条件／关系认证消费及负控制、相关离线夹具通过、未知及范围外锚点不消失、来源及既有资产保全、正式资产独占保存及全量复读、链接在新位置有效。失败或缺证据不发布、不转正、不归档。
- 发布位置固定为 _bmad-output/blueprints/mainline-b-20261028/；允许项目内原生工具创建专用部署驱动，部署程序仅独占创建指定目录及其清单、复读校验，不修改生产代码、配置、测试、来源或候选，不启动宿主或联网。控制器覆盖未获证明，使用本契约批准的串行受控命令与平台任务记录。
- 冻结读取范围为 api.py、fastcode/__init__.py、fastcode/agent_tools.py、fastcode/answer_generator.py、fastcode/cache.py；配置绑定 FastCode 根，仅作为来源数据，不导入、执行或修改。独立事实规范的范围外锚点逐项保留为未读取、未验收；范围内只核对静态定位，不提前登记步骤 5 的业务语义通过。
- 本地离线消费者显式上限：单帧 24576 字节、累计传输 4194304 字节、解码 2097152 字节、最多 256 个分片，允许通知分片及唯一最终清单；仅以本轮实际消费验证，不证明真实 Roo 宿主能力。
- 受控入口为项目内全新草稿驱动；freeze 阶段先独立枚举来源身份、全部定义、导入、条件分支、调用点及未完成集合，并冻结候选文件名和内容边界；generate 阶段调用现有显式自适应构建与独立接收器，验收通过后由既有独占导出程序保存新候选并复读。必需集合非空，所有相关分母须完整核对，不降低断言。
- 相关离线夹具验证仅覆盖读取、调度、遥测、传输与棕地候选保存边界，不启动网关或宿主、不安装依赖、不运行来源项目测试。上述命令在工程根串行执行，取得退出回执后才推进；草稿和冻结材料不构成沙箱或第二份进度账本。
- [棕地构建接口](../src/solo_distill/brownfield.py:104)已有完整认证消费路径，但[命令行入口](../src/solo_distill/brownfield.py:404)没有自适应参数；后续应确认受控调用入口，不能直接运行旧命令冒充新版验收。
- [独立接收器](../tests/adaptive_receiver.py:98)要求非空定义与调用预期并逐调用点核对；测试夹具存在不等于真实五文件通过。
- 后续获准终端遵循串行、前台同步、封闭标准输入和进程级超时：探测 15 秒、验证 30 秒、构建 600 秒；文件写入使用原生工具，复杂驱动先在获准草稿区持久化。
- 缺依赖、超时、零命中、必需项跳过或缺证据阻塞相应门禁；失败不推进依赖、不降低断言、不凭空增加重试授权。
- 接管、契约变化与阶段边界复核路径、标识、契约和关联 ADR；不逐修改同步指纹。状态保存不触发业务重测。
- 相关 ADR 仅在本次全部静态必需门禁、独占发布及复读通过并取得转正授权后接受；接受不证明业务语义、stdio 或真实 Roo。归档只处理本计划，不恢复或归档其他任务。

## 证据定位

- [旧候选清单](../verification/brownfield-review/python-mvp-20261027T0344Z/manifest.json:1)用于物理保存完整性核对，不证明来源当前新鲜度或业务审核。
- [旧溯源](../verification/brownfield-review/python-mvp-20261027T0344Z/analysis/provenance.json:1)保留核心完整与调用剪枝边界，不满足新版全量调用保真。
- [历史核心烟测回执](../verification/priority-core-20261026T235609Z.receipt.json:36)仅证明当时核心交付，不继承为本轮绿灯。
- [历史最后一份失败摘要](../verification/live-b-1792842977905.scratch-pin.retry.failure.json:12)记录业务失败及结构缺口；缺少具体异常证据时不推断更精确根因，不自动恢复线上任务。
- [关联 ADR](adrs/ADR-20261028-mainline-b-brownfield-distill.md:1)保存决策及取舍；当前计划不登记新的项目事实基线。
- [CS 对账](../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/CS-RECONCILIATION.md:1)、[SR 审核](../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/SR-REVIEW.md:1)、[定向夹具回执](../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/offline-tests.receipt.json:1)及[发布复读回执](../verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR/publication.receipt.json:1)提供本次静态范围的溯源；正式入口为[蓝图发布说明](blueprints/mainline-b-20261028/RELEASE.md:1)。
