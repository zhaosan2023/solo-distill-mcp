# solo-distill-mcp

独立 BMAD 蒸馏项目的首个离线阶段：使用 Python 标准库读取授权文件并提取结构，不加载 FastCode 引擎、不安装依赖、不请求模型、不激活 Roo。当前不是完整知识蒸馏实现。

## 已落地边界

- [项目元数据](pyproject.toml)声明 Python 3.12 以上，运行时第三方依赖为空。构建依赖仅用于可选打包，本阶段不执行打包或安装。
- [提取器](src/solo_distill/extractor.py)记录文件清单、类／函数、限定名称、来源行号、内容摘要、直接导入和调用表达式。调用目标与运行时执行均保留未知，不将 AST 语法当运行时证明。
- [服务骨架](src/solo_distill/server.py)实现顺序初始化、就绪通知、心跳、工具发现与一次只读工具调用；协议版本固定为 2024-11-05。不宣称完整 SDK、全部 MCP 功能、新版本兼容或真实 Roo 联调通过。
- [服务端配置](config/projects.json)只绑定 FastCode 只读样本。调用者只能选择既有项目并收窄范围，不能传入任意根路径、URL、命令或模型。
- 描述符逐级打开拒绝根目录、父路径与子路径软链接，不读取非普通文件。排除常见凭据路径只是纵深防御，不是完整秘密识别或执行级沙箱；本阶段没有任何网络外发。
- 支持 Python 内容身份；其他语言和资产保留在覆盖分母内并报告未知或跳过。排除目录不下钻，覆盖只针对声明的授权集合，不声称覆盖其内部内容。
- Python 来源在返回前再次读取并校核；未解析文件只保存元数据身份。返回的“当前”仅指本次最后校核时点，不承诺工作树不可继续变化。
- 返回超预算明确部分或阻塞；协议外层转义后再次检查字节预算，无法交付必要内容时报错，不将丢失内容当完整结果。

## V2 风险修复：E0—E4 离线边界

- [合法语法单元](src/solo_distill/syntax_v2.py)与[公共引用契约](src/solo_distill/package_v2.py)显式使用 V2，保存文件身份、完整正文所有权、精确坐标与条件引用；[独立认证器](tests/v2_validator.py)不导入生产解析器或分类器。
- [离线读取适配](src/solo_distill/evidence_v2.py)复用授权快照和描述符读取，不导入目标项目；角色证明只认证所列语法信号，不证明运行时职责或自然语言结论。
- [受保护调度](src/solo_distill/scheduler_v2.py)先预留必需闭包，使用 16／12／4 基准、32 块及 98,304 UTF-8 正文字节上限。只有必需骨架向未预留上下文借槽；父子所有权替换整体重算，多标签不重复扣减。
- [静态关系与拓扑](src/solo_distill/graph_v2.py)区分确定绑定及未知调用，保留调用点、条件、关系层、循环与展示方向；固定骨架后只展开一次确定邻接，不把拓扑称为运行时执行顺序。
- [封存核心与静态切片](src/solo_distill/core_v2.py)保留必需义务及支持闭包，可选组稳定排序、原子试装；同一核心的前缀字节固定，输入／政策变化使相关身份失效，不承诺远端缓存收益。
- [完整帧与唯一写者](src/solo_distill/wire_v2.py)计入身份、嵌套转义、观测与结尾换行；V2 A 上限为 min(22,016, 调用者预算)，B 为 min(12,288, 调用者预算)。离线实际管道捕获、超限、背压、部分写入与取消负控通过；超限不重选核心，部分发送不重放整帧。
- [显式离线消费者](src/solo_distill/consumer_v2.py)先版本解码和来源认证；V1 交给调用者显式提供的既有校验入口，保留有限浮点观测及原 A 预算。任务增量与核心分开引用，校核任务身份、输入与授权范围及跨文件支持依赖。
- [独立消费者与冻结评价](tests/v2_consumer.py)不导入生产认证器、分类器或身份函数；保留 13 个冻结锚点、全部预期语义及独立复读要求。隔离覆盖测试不证明真实 FastCode 13/13 或模型语义通过。
- 新资产保持离线、显式调用，不接入 V1 服务默认路径。E3—E4 仅交付隔离发布、封存与消费者回归；完整真实 B、E5—E6 模型／宿主及第二项目门禁仍未执行，缓存收益未知。
- 当前授权不包含安装、打包、模型外发、宿主激活、ADR 转正、事实登记、提交推送或归档；关联 ADR 保持 PROPOSED，进度仅使用平台任务记录。

## 运行与验证

以下为在本独立项目根目录执行的技术命令。标准输入用于协议消息，仅测试驱动提供封闭输入；不是后台启动或宿主激活授权。

```bash
PYTHONPATH=src python3 -B -m solo_distill.server --config config/projects.json
```

以下为串行、离线验证入口，使用临时夹具，不安装包、不联网、不执行 FastCode 模块。真实样本测试仅从服务端配置读取 FastCode。

```bash
PYTHONPATH=src timeout --kill-after=2s 30s python3 -B -m unittest discover -s tests -p 'test_*.py' -v </dev/null
```

工具身份与调用结构由[服务输入契约](src/solo_distill/server.py:21)定义；输出为结构报告，不冒充提案里的最终场景 A/B 知识包。全样本报告超过返回预算时阻塞是预期负控，不能宣称宿主全库消费通过。

## 未完成门禁

- 本地正文选择、证据语义校核、统一业务截止与最终知识包仍待实现。
- gpt-6-sol／8080 集成、正文外发集合、供应商与费用授权、有效网关策略及故障夹具未落定，不自动请求网关。
- 正式 SDK 或更完整协议契约、真实 Roo 工具可见性、取消与宿主调用窗口尚未验收；本顺序骨架不承诺运行中异步取消。
- 至少十个预建跨模块事实锚点、场景 B／A 各三轮完整往返、第二项目复验及 Rust/Go 替换仍待相关阶段授权与证据。
- 控制器覆盖未证明，当前进度只使用平台任务记录；本文件不是另一套任务账本。
- 旧 FastCode 安装与其他计划保持独立，不恢复、不修改。当前阶段不转正提案、不创建 ADR、不更新原工作区事实基线、不归档未完成全提案。

## Python 棕地文档 MVP：独立待审核输出

本增量使用[离线文档入口](src/solo_distill/brownfield.py:1)，不改变既有 MCP 服务与模型蒸馏路径，运行时仍然零新增依赖。

- 读取范围由既有服务端配置限定；调用者只能收窄范围，不导入或执行来源代码。
- 复用 P0 全部导入、P1 全部定义（含方法与嵌套定义）的核心保留；P2/P3 调用按现有优先级和完整帧预算剪枝。
- 输出三层材料：[项目指引](src/solo_distill/brownfield.py:172)、按模块分页的静态契约和完整溯源。单页不足 150 行且不超过 24,576 UTF-8 字节；该文档分页预算不等于整个项目的上下文预算。
- 完整结构帧计入嵌套转义与结尾换行，上限为调用者预算与服务端上限的较小值，最多 24,576 字节。核心超限、过期来源或零命中阻塞，不靠文档分页伪装结构帧通过。
- 输出只允许工程内独立新目录，拒绝已有目标和软链接；保存后复读并生成内容清单。多文件发布不是整体原子事务，失败可能留下不完整目录，不能原地重试覆盖。
- 输出均为待审核材料，不自动发布到来源项目，不生成猜测的业务不变量、构建命令、已接受 ADR 或另一份活动计划。来源未完成项和剪枝项分别保留；存在覆盖缺口时返回部分审核状态，命令退出 2。
- 本阶段只闭环 Python 静态结构到三层待审核文档；其他七种语言、模型语义、真实宿主与业务文档人工接受均不在本轮验收范围内。

以下示例在本工程根目录前台、离线执行，只读配置绑定的五文件样本。输出名称必须全新；命令不会安装依赖或请求网关。

```bash
PYTHONPATH=src timeout --kill-after=2s 30s python3 -B -m solo_distill.brownfield --project-id fastcode-sample --include api.py fastcode/__init__.py fastcode/agent_tools.py fastcode/answer_generator.py fastcode/cache.py --output verification/brownfield-review/python-five-file-review </dev/null
```

相关验收由[文档特性测试](tests/test_brownfield.py:1)及原有核心交付、背压和协议回归覆盖，测试仅使用离线夹具。

```bash
PYTHONPATH=src:tests timeout --kill-after=2s 30s python3 -B -m unittest test_brownfield test_priority_delivery test_backpressure test_offline.ExtractorTests test_offline.ProtocolTests -v </dev/null
```

## 显式动态背压策略

上述固定配额说明适用于旧默认路径。新策略仅由调用者显式选择；旧 V1、旧 V2 默认策略及场景 B 不静默改义。

- [预算遥测](src/solo_distill/telemetry.py:15)记录阶段耗时、唯一正文所有者、骨架、核心、编码、精确单帧及累计字节。32 块、98,304 正文字节、240,000 骨架字节与 22,016 帧字节是软目标，越界产生去重 Warning，不删除必需证据；未测量及宿主消费结果保持未知。
- [新版选择链](src/solo_distill/live_v2.py:107)完整登记授权范围内的语法单元，再稳定分页；必需组不因 16/12/4 借槽冲突拒绝，核心定义、条件和关系进入限定保护集。可选探索仍受控，静态条件不等于已确认的业务语义。
- [传输能力](src/solo_distill/adaptive_wire.py:26)必须显式声明单帧、累计传输、解码与分片数量硬上限。大帧在能力允许时完整发布；否则以已声明的通知分片和唯一最终清单交付，不追加多个最终响应。来源、权限、取消、截止、实际写出故障及不完整关系仍是硬门禁。
- [消费者](src/solo_distill/consumer_v2.py:153)核验完整结构与关系集合；[独立离线接收器](tests/adaptive_receiver.py:35)另行重组并认证来源、非空预期定义和逐调用点覆盖。发送成功不等于真实宿主消费。
- [棕地入口](src/solo_distill/brownfield.py:80)在显式新策略下从完整认证知识包渲染分页文档与关系，不从旧受限帧投影恢复内容。输出仍为独立待审核材料，不自动发布。

调用新 V2 A 路径或棕地构建接口时，在各自既有请求中明确提供以下字段；能力值由实际调用者确认，不能把示例当宿主能力证明。仅设置传输字段会被旧策略拒绝。

```json
{
  "budget_policy": "adaptive-backpressure-v1",
  "return_bytes": 22016,
  "transport": {
    "policy": "adaptive-backpressure-v1",
    "authority": "caller_explicit",
    "max_frame_bytes": 22016,
    "max_transfer_bytes": 8388608,
    "max_decoded_bytes": 6291456,
    "max_chunks": 4096,
    "chunking": true
  }
}
```

[定向测试](tests/test_adaptive_backpressure.py:1)仅使用离线夹具、假网关和托管本地管道，不授权真实模型外发、宿主激活或依赖安装。

```bash
timeout --signal=TERM --kill-after=2s 30s env PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_adaptive_backpressure </dev/null
```
