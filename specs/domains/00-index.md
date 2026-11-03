# 工具工程领域导航

本页按工程职责定位源码与相关测试，不是 FastCode 的业务领域契约、运行证明或当前任务账本。修改前继续读取相关完整定义、调用方与副作用边界。

## 1. 授权来源与结构提取

- [来源配置](../../config/projects.json)限定服务端项目及读取范围，调用方只能在获准范围内收窄。
- [提取器](../../src/solo_distill/extractor.py)与[离线测试](../../tests/test_offline.py)定位文件读取、结构提取及旧协议边界。
- [V2 来源适配](../../src/solo_distill/evidence_v2.py)、[V3 来源适配](../../src/solo_distill/evidence_v3.py)与[V3 来源测试](../../tests/test_schema_v3_evidence.py)分别检查适用版本。
- 来源读取不等于导入或执行；语法关系、来源身份和业务语义分别认证。

## 2. 服务入口与问题选择

- [V1 服务](../../src/solo_distill/server.py)、[V2 服务](../../src/solo_distill/server_v2.py:19)与[V3 服务](../../src/solo_distill/server_v3.py:28)保持独立定位，不由新文档替换默认入口。
- [V3 输入契约](../../src/solo_distill/service_v3.py:32)区分离线预检、在线业务、冻结选择与显式能力；接口字段不是执行授权。
- [V3 选择](../../src/solo_distill/scouting_v3.py:47)区分默认规则与显式覆盖版本，不读取参考答案参与生产选择。
- 相关边界见[选择独立测试](../../tests/test_schema_v3_selection_oracle.py)、[覆盖选择测试](../../tests/test_schema_v3_selection_coverage.py)和[stdio 测试](../../tests/test_schema_v3_stdio.py)。测试定位不表示本轮已运行。

## 3. 结构核心、发布与可信消费

- [V3 包构建](../../src/solo_distill/package_v3.py:23)从可信源码及包外核心清单构建宏观范围与局部闭包；不能以包自报分母认证自身。
- [V3 包认证](../../src/solo_distill/package_v3.py:62)是生产复算，不冒充独立 Oracle。
- [V3 核心](../../src/solo_distill/core_v3.py)与[V3 任务证据](../../src/solo_distill/task_v3.py)分开，任务推断不能覆盖结构核心状态。
- [V3 生产消费](../../src/solo_distill/consumer_v3.py:37)对包外可信输入认证完整发布，不联网、不执行来源、不读写磁盘。
- 独立资产分别见[宏观 Oracle](../../tests/schema_v3_macro_oracle.py)、[局部 Oracle](../../tests/schema_v3_local_oracle.py)与[包 Oracle](../../tests/schema_v3_package_oracle.py)；存在资产不证明运行通过。

## 4. 表示、传输与取消

- [V3 传输能力](../../src/solo_distill/wire_v3.py:38)与[物理上限](../../src/solo_distill/wire_v3.py:28)按显式版本适用，不能沿用[项目说明](../../README.md)中旧 V2 示例的更大能力值。
- V3 上限为单帧 22,016 字节、累计 1,572,864 字节、完整解码帧 1,048,576 字节及最多 512 通知分片；调用方可收紧，不能因分片或压缩提高。
- [表示集合](../../src/solo_distill/wire_v3.py:27)包含原 JSON、字典／列式、重复子结构与确定性字节表示；未知协商拒绝，不自动降级。
- [传输测试](../../tests/test_schema_v3_wire.py)、[独立传输测试](../../tests/test_schema_v3_wire_oracle.py)与[运行边界测试](../../tests/test_schema_v3_runtime_gates.py)分开定位来源、重组、取消及退出边界。
- 管道写入、完整接收、来源认证、人工审核和宿主加载是不同证据。

## 5. 治理候选与历史证据

- [旧棕地生成器](../../src/solo_distill/brownfield.py)与[V3 生成器](../../src/solo_distill/brownfield_v3.py:144)定位独立候选输出，不自动安装到来源项目。
- [V3 候选校核](../../src/solo_distill/brownfield_v3.py:105)检查路径、预算、机器资产和薄指针；相关资产见[治理测试](../../tests/test_schema_v3_governance.py)。
- [ADR 索引](../../docs/adrs/README.md)与[分析索引](../../docs/analysis/README.md)定位历史原件，历史执行窗口不作为本轮新操作权限。
- 先读[治理索引](../governance/00-index.md)确定当前任务边界，再选相关接口，不冷加载全部历史。
