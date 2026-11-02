---
plan_id: mainline-a-v2-scenario-a-20261029
contract_version: 5
status: EXECUTING
scenario: algo_and_structural
impact_tier: null
requires_adr: false
adr_file: null
verification_type: controlled_v2_a_gateway_stdio_and_four_dimension_offline_consumption
execution_instance: mainline-a-scouting-v5-20261030T011416Z-capacity
transport_capabilities: {"policy":"adaptive-backpressure-v1","authority":"caller_explicit","max_frame_bytes":22016,"max_transfer_bytes":12582912,"max_decoded_bytes":8388608,"max_chunks":512,"chunking":true}
verification_command:
  - "PYTHONPATH=src:tests timeout -k 2s 105s python3 -B verification/.scratch/mainline_a_offline_20261029.py </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_scouting_20261029.py health </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_scouting_20261029.py preflight </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_scouting_20261029.py preflight </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_scouting_20261029.py preflight </dev/null"
  - "PYTHONPATH=src:tests timeout -s KILL 180s python3 -B verification/.scratch/mainline_a_scouting_20261029.py live </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_scouting_20261029.py consume </dev/null"
  - "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_scouting_20261029.py reconcile </dev/null"
verification_command_status: IMPLEMENTED_PENDING_VERIFICATION
authorization:
  previous_v4: "2026-10-29 当前用户正式批准 ANALYSIS-20261029-mainline-a-scouting-refresh 为唯一技术依据，并一次性授权版本 4 计划修订、生产代码及新驱动和回归落地、三次串行冷预检、一次真实 V2 A 在线接纳、独立四维消费与语义复读、原字节保存复核、全部必需门禁通过后的安全归档。"
  current: "2026-10-30 当前用户确认方案 A 的三阶段工程序列：Phase 1 保全版本 4 计划原字节、修订版本 5、受控驱动独立声明新配额并同步相关验收与回归，运行 105s 离线汇总生成新绑定回执；Phase 2 为 15s 健康绑定及三次串行 30s 冷预检；Phase 3 为一次严格 180s 真实业务、30s 独立四维消费及 13 项锚点语义人工复读、30s 最终对账与安全归档准备。本次仅修订调用方传输契约与驱动及相关回归，不修改生产实现。"
  boundary: "不写入或执行 FastCode、不启动或重启现有 127.0.0.1:8080 网关、不提权、不外层重放、不安装依赖、不恢复其他历史任务；保护既有改动和主线 B 资产。一次真实业务及子进程退出均在外层进程级 180s 严格截止内；唯一 Prompt 完整正文 UTF-8 硬预算 98,304 字节，单次实际 HTTP POST 验收硬限 150,000 字节，网关原 393,216 字节硬限不变。下行单帧 22,016 字节，累计传输 12,582,912 字节，解码原始完整帧 8,388,608 字节，最多 512 个通知分片及另计的唯一最终清单；全配置 81 个解析文件分母不得截断。"
  review: "以本次三阶段授权覆盖历史归档授权：只完成安全归档准备，不实际归档或退休活动源。人工语义复读必须有真实人工证据，不由消费退出码或模型审核代替；缺证据阻塞最终对账。失败不推进依赖，线上失败不自动再发。不授权 ADR 转正或事实基线写入。"
session_id: null
---

# 主线 A：V2 场景 A 知识蒸馏与受控网关集成

## 接管依据与独立边界

- 只读消费[主线 B 已归档计划](archive/plans/20261028T145455Z-mainline-b-brownfield-distill-20261028.md)、[正式蓝图](blueprints/mainline-b-20261028/RELEASE.md:1)及[蒸馏配置](../config/distillation.json:1)。主线 B 的五文件静态验收不继承为本轮业务、网关、stdio 或真实 Roo 通过。
- 唯一技术依据为[已获当前用户正式批准的刷新方案](analysis/ANALYSIS-20261029-mainline-a-scouting-refresh.md:83)；其历史文档授权字段不覆盖当前明确实施及在线授权，不擅自转正该文档。
- 主线 A 使用[既有 V2 A 请求入口](../tests/live_v2_driver.py:19)的四维问题和[配置来源](../config/projects.json:4)的完整 include 范围。分离全量侦察索引、既有完整静态知识包、有限问题正文视图；81 个解析文件以及文件、单元、定义、条件和逐调用/导入位点分母保持完整，跳过文件单独披露，不替换为主线 B 五文件范围。
- 当前工作区 Git 回执已有多项修改及未跟踪资产，全部视为接管前既有改动。优先创建专用受控驱动，不覆盖已修改的生产实现；必要接口调整须先核对定义和调用方。
- 默认暂存计划及事实基线未出现在本次目录枚举中，不重复盲读、不恢复其他任务。控制器可用性及本任务覆盖未获证明，唯一临时进度入口是平台待办；对账卡只保存程序生成的证据视图，不另建进度账本，不宣称防篡改保证。
- 本轮集成现有策略，不建立或转正新架构决策；主线 B ADR 保持独立原样。未知影响等级和会话标识保持 null。

## 全文传输契约

- 显式启用 adaptive-backpressure-v1；22,016 字节仅为 stdio 物理帧上限及软遥测目标，不是知识内容截断门禁。
- [显式传输能力](../verification/.scratch/mainline_a_scouting_20261029.py:38)：解码上限 8 MiB（8,388,608 字节），累计传输上限 12 MiB（12,582,912 字节），通知分片上限 512，单帧保持 22,016 字节，分片仍显式启用，策略与调用方显式权限来源不变。解码计量为重组后的完整原始 JSON-RPC 帧；累计计量包含全部通知、Base64、逐片元数据、换行及唯一最终清单。计划头部能力声明与驱动独立能力必须一致，历史驱动和旧回执不改写。
- 大响应必须使用 notifications/solo_distill/chunk-v1 和唯一最终清单；核对顺序、数量、逐片摘要、全量摘要、来源与快照绑定，不截正文、定义、条件、调用、关系或未完成事项。达到硬传输能力则阻塞，不降断言。
- 既有[全局软遥测](../src/solo_distill/telemetry.py:10)及 legacy 调度语义不变；本任务问题正文视图另设 98,304 字节硬预算，只累计实际发送的唯一完整单元转换后 UTF-8 字节，真实包含别名不重复计费，不因方法选择发送整个类。
- 单次 POST 新验收硬限 150,000 字节，包含固定模型、系统提示、问题、四维及全部转义后的请求体；[网关原硬限](../src/solo_distill/gateway.py:13)393,216 字节保持不变。预检与实发使用同一无网络编码入口，实发前再次核验长度及摘要；超限建连前阻塞，不增发、不截函数或条件。
- [实际选择链](../src/solo_distill/live_v2.py:107)保留完整静态包，独立问题选择器按四维标识符、签名、文件名和 docstring 信号、可信 1～2 跳拓扑、分层排序及增量字节成本贪心选择。候选上限 256、扩展边上限 2,048；探索耗尽不等于容量无解，关键义务缺口阻塞。未知实例分派只能标记低置信词法候选，不能伪造已解析边。
- 模型允许引用的来源仅为本次实际发送完整单元及真实完整包含别名；全包存在但未发送、跨快照及伪造来源一律拒绝。预期参考答案仍只供独立消费侧保管，不进入选择或生成。
- 扫描、完整包构建、选择和认证共享一次快照内按内容哈希与解析器/规则版本绑定的语法索引；原始来源秘密检查、变化拒绝、身份及封印认证不能删减。预检阶段分别保存扫描、索引、候选、选择、认证、编码、源复核及保存耗时与解析调用次数。
- 三次新实例串行冷预检全部保存，不挑最快结果：候选发现与选择目标不超过 3 秒，完整预检目标不超过 20 秒，内部门禁 27 秒，外层严格 30 秒；超时、必需项跳过或缺证据阻塞，不放宽窗口。三样本不宣称统计 p95。
- 下行最多 512 个通知分片和另计的一帧唯一最终清单，物理帧数最多 513；解码、累计传输、分片数和单帧四项硬限同时生效。12 MiB 累计配额不保证能填满，8 MiB 解码配额不保证任何内容都可在 512 分片内承载；冷预检、在线接收和独立消费记录实际四项计量并核对清单及摘要。完整静态下行超限仍须阻塞，不删分母、不降低断言、不自动提高配额或引入未经评审的新包版本。
- [旧工程客户端](../src/solo_distill/client.py:82)只接收带身份的单帧，[旧实跑驱动](../tests/live_v2_driver.py:148)仍将22016用作总响应限制，不直接把旧入口当成已兼容本轮分片。使用专用受控接收路径，保留旧入口和旧回执。

## 获准阶段与门禁

1. Phase 1：保全版本 4 活动计划原字节并复读，保全不是归档或历史绿灯继承；修订版本 5 及专用驱动独立配额，同步必要容量回归和验收。核对配置、只读输入、接口和既有改动；持久化驱动后才调用，草稿路径不构成沙箱。
2. Phase 1：运行[离线汇总入口](../verification/.scratch/mainline_a_offline_20261029.py:19)，外层 105 秒、三个既有子验证各 30 秒串行；验证动态背压、真实管道、分片完整性、硬限负控、错误/取消/期限和来源保全。三个套件均退出码 0、非零命中、零跳过，保存新实例计划及实现绑定回执；旧 143 项通过事实不代替修订后的证据。不运行 FastCode 代码或测试。
3. Phase 2：健康探测仅访问现有127.0.0.1:8080；以 /health=200、/v1/stats=200 及非空监听 inode 前后稳定为准。用户正式豁免非特权 /proc/*/fd 无法读取 PID 所致阻塞；不提权，不把未观测的 PID 或启动时间记为已验证。健康成功不证明模型可用；监听快照不证明持续隔离。
4. Phase 2：三次新进程串行冷预检全部通过；核对完整来源快照、非空结构分母、四维问题、实际 POST 字节及完整分片双消费能力。模型预期答案只供离线核对，不发送生成端；必需前提缺失立即阻塞，不推进依赖、不挑样本或删失败证据。
5. Phase 3：在离线与三次冷预检门禁通过后仅允许一次 V2 A 业务接纳及一次压缩 POST，无外层重试。业务含 stdio 接收及子进程退出硬超时 180 秒，生成及来源阶段保留现有 15 秒返回余量；不把旧 210 秒清理窗口当成通过。后续独立四维消费采用单独 30 秒窗口，不扩展线上业务窗口。
6. Phase 3：全量原始 stdio、生成输出、来源身份、投递、遥测、独立接收与四维对账保存于[新实例证据目录](../verification/mainline-a-scouting-v5-20261030T011416Z-capacity/)；保存后复读。四维为查询链路、状态与持久化所有权、失败与上下文边界、来源范围及验证盲区。逐结论来源及 13 项锚点须取得人工语义复读证据；消费退出码不证明语义真值，真实 Roo 可见性与消费仍单独标记未证明。
7. Phase 3：全部必需物理门禁及人工审核通过后运行 30 秒最终对账，只交付安全归档准备；不实际归档、不退休活动计划、不登记全计划归档完成，不转正 ADR、不更新事实基线。人工审核缺失或失败则阻塞，保留活动源、现场及全部未完成事项；阶段通过不等于全计划完成。

## 执行与交付约束

- 所有终端串行、前台同步、封闭标准输入；探测 15 秒、离线子验证和冷预检及消费对账各 30 秒，离线汇总外层 105 秒仅覆盖三个串行子验证及保存；唯一线上业务 180 秒进程级严格截止。取得实际退出状态及必要回执后才推进下一命令。
- 助手文件创建与修改使用原生工具；授权驱动可独占生成自身回执和原始证据，不覆盖既有文件，安全创建及复读失败不得登记保存成功。
- 回执包含命令、工作目录、退出状态、目标、关键结果和源证据定位；超时、缺依赖、零命中、必需项跳过、清理未知或缺证据进入BLOCKED。普通离线故障可在授权内最小修复；线上业务失败不自动再发。
- 回执和对账卡必须保留全部未完成事项；即使集成阻塞也交付已实际完成部分和具体缺口，不冒充四维或模型验收通过。本次授权止于最终对账和安全归档准备，实际归档须另获授权；主线 B 资产保持原样。
