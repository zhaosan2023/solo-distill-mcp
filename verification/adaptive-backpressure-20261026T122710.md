# 离线结构抽取动态背压：实现与实测交付

机制已实现，30 项相关回归通过。真实五文件样本自动从 4096 扩至 24576 字节后仍不能无截断交付，最终为 BLOCKED；没有成功预算可登记。

## 方案与边界

- [提取器](../src/solo_distill/extractor.py:472)在缩减前测量完整响应，诊断包含完整所需字节、省略记录数、预算单位、硬顶、是否可重试和原始业务状态。估值包含诊断自身，以最多八轮固定点求解；对当前序列化是精确值，不保证下一次扫描字节相同。
- [服务入口](../src/solo_distill/server.py:131)传入完整 JSON-RPC 帧计量器，包含嵌套文本转义、请求标识与末尾换行，消除原先内部 JSON 与外层帧分别截断的预算错位。
- [自适应策略](.scratch/step4_adaptive_support_20261026.py:32)仅允许原始业务完整、显式返回预算压力的响应重试；按完整需求跳步，缺有效估值时指数扩额，最后一次必要时到授权硬顶。最多三次，业务失败、超时、快照变化或写入监测异常均停止。
- [烟测驱动](.scratch/step4_offline_smoke_20261026.py)保留原五文件、五秒扫描、每次七秒进程级超时和封闭协议输入；全轮三十秒验证超时。初始预算为可配置参数，默认 4096。每次尝试同步结束后复核全树元数据并读取变更事件，之后才决定重试。
- 成功须业务完整、未省略记录、实际交付 AST 记录大于零、五文件范围一致及安全观测满足；仅解除截断不代表业务完整。首次成功预算只称为已验证成功配额，不称全局最优。

## 关键代码差异摘录

以下为局部实际改动，省略未展示的上下文，不是可直接应用的完整补丁。完整新实现见上述链接；烟测驱动原本未跟踪，不将其整文件新增的 Git 视图冒充历史差异。

### 服务完整帧计量

```diff
-                    report = extract(self.projects, params["arguments"])
+                    # Bound exactly once against the complete JSON-RPC frame,
+                    # including nested text escaping, request ID and final newline.
+                    report = extract(
+                        self.projects, params["arguments"],
+                        measure=lambda value: len(encode(tool_response(value, request_id))) + 1,
+                        budget_unit="jsonrpc_utf8_bytes_including_newline")
```

### 提取器缩减前诊断

```diff
-def bounded_result(report: dict[str, Any], budget: int) -> dict[str, Any]:
-    if serialized_size(report) <= budget:
-        return report
+def bounded_result(report: dict[str, Any], budget: int, *,
+                   measure: Callable[[dict[str, Any]], int] = serialized_size,
+                   hard_limit: int = MAX_RETURN_BYTES,
+                   budget_unit: str = "report_json_bytes") -> dict[str, Any]:
```

新增完整响应诊断和预算压力诊断的实际片段：

```python
full = {**report, "partial": report["status"] != "complete"}
diagnostic = {"reason": "none", "estimated_full_bytes": 0,
              "estimate_kind": "exact_current_serialization", "budget_unit": budget_unit,
              "hard_limit_bytes": ceiling, "omitted_count": 0, "retryable": False}
full["backpressure"] = diagnostic
```

```python
omitted_count = sum(len(entry.get(key, [])) for entry in report["entries"]
                    for key in ("symbols", "imports", "calls"))
pressure = {**diagnostic, "reason": "return_budget", "omitted_count": omitted_count,
            "retryable": report["status"] == "complete" and budget < ceiling,
            "hard_limit_exceeded": measured > ceiling,
            "source_status": report["status"]}
```

### 驱动预算策略替代固定单次预算

旧驱动只提交一次固定 4096 字节请求；现在由以下策略产生下一次预算，交给最多三次的串行循环。实际策略片段：

```python
ceiling = min(HARD_LIMIT, hard_limit)
if (pressure.get("reason") != "return_budget" or pressure.get("retryable") is not True
        or pressure.get("source_status") != "complete" or current >= ceiling):
    return None
estimate = pressure.get("estimated_full_bytes")
proposed = estimate if type(estimate) is int and estimate > current else current * 2
if attempt_number == MAX_ATTEMPTS - 1:
    proposed = ceiling
return min(ceiling, max(current + 1, proposed))
```

## 真实验证

[证据回执](adaptive-backpressure-20261026T122710.receipt.json)为客户端命令输出的有界转录，不是重跑结果。

- 相关回归：30 项，0.207 秒，退出码 0，无跳过项。覆盖精确预算及差一字节边界、Unicode／转义请求标识、硬顶负控、原始业务失败不重试、快照变化停止、三次上限、隔离夹具真实扫描收敛及既有提取器／协议回归。
- 真实探测：驱动退出码 1，原因是硬顶不能容纳；两次服务退出码均为 0，stderr 均为空。
- 差异审查：Git 差异空白检查退出码 0；生产变更仅涉及提取器和服务入口，新增相关测试与驱动辅助模块。上轮宿主配置草稿及握手脚本未修改。

| 尝试 | 请求预算／字节 | 服务耗时／秒 | 扫描耗时／秒 | 实际工具帧／字节 | 完整帧需求／字节 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| 1 | 4096 | 0.259775 | 0.063781 | 3387 | 378376 | 预算压力，可扩额 |
| 2 | 24576 | 0.199770 | 0.052688 | 3388 | 378376 | 已到硬顶，停止 |

两次均解析五文件，统计 84 个符号、79 个导入、751 个调用表达式，总计 914 条记录；省略记录为 914，交付 AST 记录为 0。前后快照标识相同。

零写入观测：211 个条目、52 个目录，两次全树元数据摘要前后一致，新增／修改／删除和变更事件均为 0。结论仅覆盖此次观测窗口，不含访问时间，不扩展为系统级永久防写保证。未提供模型授权配置，报告模型请求数为 0，未修改 FastCode。

## 基准结论

- 当前五文件与现有完整帧表示在 24576 字节硬顶内没有已验证的无截断预算；不得将硬顶登记为成功基准。
- 378376 字节是此次完整响应的序列化需求，不是可执行配置，也不是未来或全局最优预算。未提升硬顶，未修改项目授权配置。
- 后续调用可沿用“默认初始 4096、最多三次、反馈跳步、上限 24576”的策略，但该五文件完整单帧交付仍 BLOCKED。
- 若必须在同一硬顶下保留全部记录，后续需要另行设计有明确快照／完整性契约的分页或多帧交付，或改变表示格式；本轮未实施，不静默缩小样本或删除记录冒充成功。

当前进度仍只使用平台任务记录，控制器覆盖未证明；本文与回执是交付证据，不作为另一份执行账本。未接入宿主、模型、在线蒸馏、ADR 转正或归档。
