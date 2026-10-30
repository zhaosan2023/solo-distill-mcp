---
artifact_id: ANALYSIS-20261029-fastcode-scouting-architecture
title: "FastCode 论文深度研读与 1.55 MB 提示词物理溢出破解方案"
contract_version: 1
status: PROPOSED
scenario: algo_and_structural
domain: code_reasoning_and_knowledge_distillation
author: "BMAD-Solo Ana-Architect"
date: "2026-10-29"
reference_asset: "docs/references/FastCode-arXiv-2603.01012v2.pdf"
---

# FastCode 论文深度研读与 1.55 MB 提示词物理溢出破解方案

## 一、 背景与根因定位

在主线 A（V2 场景 A 知识蒸馏）执行中，全库扫描 FastCode 样本（81 个 Python 文件）生成了 1,137,679 字节源码正文，组装为 HTTP POST 报文后达到 **1,553,357 字节（约 1.55 MB）**。该体积远超网关上行单次请求物理硬上限 **393,216 字节（384 KB，[src/solo_distill/gateway.py:13](../../src/solo_distill/gateway.py#L13)）**，且 1,719 个语法单元的全局闭包遍历导致预检 **30 秒超时（退出码 124）**。

通过研读权威文献 [FastCode 论文（arXiv:2603.01012v2）](../../docs/references/FastCode-arXiv-2603.01012v2.pdf)，确认当前工程实现背离了核心设计：误将全库所有代码体无脑打包进 Prompt，退化为论文开篇即批判的“Naive Context Stuffing”。

---

## 二、 论文核心理论体系与对比

| 维度 | FastCode 论文规范体系 | 当前 live_v2.py 错误现状 |
| :--- | :--- | :--- |
| **探索范式** | **Decoupled Scouting**：探索与消费解耦，无需读全文即具备可见性 | **Naive Stuffing**：扫描 81 个文件后将所有函数体全转为字符串打入 Prompt |
| **代码表示** | **Dual Representation**：全库轻量元数据（签名/行号/拓扑图）+ 局部被选单元 Body | **Single Monolithic Body**：直接把全库 973 个完整所有者 Body 全量打包 |
| **检索流向** | **Graph-Guided Expansion**：沿 $G_{call}$ 与 $G_{dep}$ 仅做 1~2 跳扩展，圈定 10~20 个候选函数 | 强制将全库 1,719 个单元全部注册为 `complete_inventory` mandatory obligations |
| **预算控制** | **Cost-Aware Context Management**：贪心截断至动态预算 $B$（$\le 100\text{ KB}$） | 无预算截断，导致 1.55 MB 冲垮网关 384 KB 物理缓冲区 |

---

## 三、 基于论文原则的 3 阶段解纠方案

### Phase 1: 语义与结构图谱保全（纯 AST，零 Token 开销）
- 继续由 [src/solo_distill/extractor.py](../../src/solo_distill/extractor.py) 在本地毫秒级解析全库 81 个文件。
- 提取并维护全量 AST 骨架：835 个导入（$G_{dep}$）、1,029 个定义、7,796 个调用点（$G_{call}$）及 3,798 条分支条件。
- **治理原则**：全量静态分母留在本地 `package` 并由**下行 4MB 动态分片通道**负责保全，**绝不作为 Prompt 发给外部大模型**。

### Phase 2: 函数内局部实现拓扑剪枝（Topological Pruning）
- 针对四维问题（`query_flow`、`state_ownership`、`failure_boundaries`、`scope_and_verification`）：
  1. 锚定核心入口符号（如 `mcp_server.py:code_qa`、`fastcode/main.py:FastCode.query`）；
  2. 沿着调用图 $G_{call}$ 仅进行 1~2 跳的扩展，锁定核心实现（`query_processor.process`、`retriever.retrieve`、`answer_generator.generate`、`cache_manager.save_dialogue_turn`）；
  3. **剪除其余 70+ 个无关辅助文件的内部函数体**，仅保留签名骨架。

### Phase 3: 成本感知动态预算与 Prompt 极小化（$\le 100\text{ KB}$）
- 依据论文公式：
  $$P(u) = w_1 \cdot \operatorname{Rel}(u) + w_2 \cdot I_{tool}(u) + w_3 \cdot \operatorname{Density}(u)$$
- 贪心筛选候选正文，严格受控于 `body_budget`（98,304 字节软预算）。
- 发送给大模型的 HTTP POST 报文体积降至 **$80 \sim 100\text{ KB}$**，轻松落在 384 KB 物理上限内；
- 预检调度计算耗时从 30+ 秒缩减至 2 秒内，彻底解除超时阻塞。

---

## 四、 实施落地映射清单

1. **修正 `src/solo_distill/live_v2.py`**：
   - 移除自适应模式下 `obligations = {"complete_inventory": Group("all_registered_syntax", all_candidates)}` 的全量强制包含逻辑；
   - 恢复基于问题相关度的切片排序与 `Scheduler` 贪心预算截断，使 `payload["sources"]` 仅包含关键核心代码切片（$\le 98,304$ 字节）。
2. **离线与在线验证**：
   - 验证 `preflight` 在 3 秒内退出码为 0，POST 报文体积在 100 KB 以内；
   - 执行在线 E5 阶段，在 180 秒业务硬超时内完成真实网关交互；
   - 调用 `consumer_v2.py` 核验四维语义结论，并通过 4MB 下行通知分片归档完整知识包。
