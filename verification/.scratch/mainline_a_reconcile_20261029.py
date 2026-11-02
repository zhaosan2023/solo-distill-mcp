"""仅保存主线 A 已有证据的对账视图；不重跑业务、不联网、不处理归档。"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from mainline_a_20261029 import ROOT, OUT, PLAN, CAPS, save, sha


def main():
    command = "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_reconcile_20261029.py </dev/null"
    evidence = []
    values = {}
    for name in ("health.receipt.json", "listener-visibility.receipt.json"):
        raw = (ROOT / OUT / name).read_bytes()
        values[name] = json.loads(raw)
        evidence.append({"path": OUT + "/" + name, "bytes": len(raw), "sha256": sha(raw)})
    health = values["health.receipt.json"]
    visibility = values["listener-visibility.receipt.json"]
    plan_raw = (ROOT / PLAN).read_bytes()
    plan = plan_raw.decode("utf-8")
    if ("plan_id: mainline-a-v2-scenario-a-20261029\n" not in plan
            or "status: BLOCKED\n" not in plan
            or "verification_command_status: BLOCKED_AT_LISTENER_IDENTITY_GATE\n" not in plan):
        raise ValueError("active_plan_boundary_conflict")
    if (health["driver_exit_code"] != 2 or health["status"] != "blocked"
            or health["listener_after"]["identity_observable"] is not False
            or visibility["actual_exit_code"] != 0
            or visibility["ss_process_identity_returned"] is not False):
        raise ValueError("blocked_evidence_contract_conflict")
    results = health["diagnostic"]["results"]
    if len(results) != 2:
        raise ValueError("health_result_count_conflict")
    state = {
        "receipt_version": 1,
        "kind": "mainline_a_evidence_reconciliation_not_progress_authority",
        "plan_id": "mainline-a-v2-scenario-a-20261029",
        "contract_version": 1,
        "plan_path": PLAN,
        "plan_bytes": len(plan_raw),
        "plan_sha256_at_delivery": sha(plan_raw),
        "task_state": "BLOCKED",
        "review_state": "awaiting_user_review",
        "command": command,
        "cwd": str(ROOT),
        "source_receipts": evidence,
        "health_observation": {"health_http_status": results[0]["http_status"],
                               "health_reports_ok": results[0].get("health_reports_ok"),
                               "stats_http_status": results[1]["http_status"]},
        "blocking_gate": "listener_owner_pid_and_start_ticks_not_observed",
        "listener_inode_observed": health["listener_after"]["listening_inodes"],
        "visibility_evidence_scope": "recorded_client_output_summary_not_independent_process_authentication",
        "transport_contract": CAPS,
        "transport_runtime_acceptance": "not_run",
        "hard_timeout_runtime_acceptance": "not_run",
        "online_business": "not_started_in_this_task",
        "source_preflight": "not_run_due_to_dependency_gate",
        "related_offline_tests": "not_run_due_to_dependency_gate",
        "four_dimension_consumption": {
            "query_flow": "not_run", "state_ownership": "not_run",
            "failure_boundaries": "not_run", "scope_and_verification": "not_run"},
        "invariant_scope": "this_task_requested_no_target_write_execution_or_gateway_restart; no_continuous_isolation_proof",
        "remaining_requirements": [
            "监听拥有者身份门禁缺证据；恢复前须确认可获准的证据方式或经用户明确修订该验收契约，不能提权绕过。",
            "完整来源快照、HTTP 输入容量及非空结构分母预检尚未执行。",
            "动态背压、实际分片、期限和取消等相关离线回归尚未执行。",
            "专用分片接收与线上入口尚未实现；不得使用旧单帧入口冒充兼容。",
            "单次 V2 A 网关集成、180 秒硬超时运行时证据及投递清理尚未取得。",
            "四维全量离线消费、来源支持与独立语义审核尚未完成；真实 Roo 消费未证明。",
            "回执和对账卡已生成后停止，等待用户审核；不归档、不转正、不登记事实基线。"],
        "progress_authority": "platform_task_record_only",
        "controller_coverage": "not_proven",
        "archival_operations": 0,
        "adr_promotion_operations": 0,
        "fact_baseline_write_operations": 0,
        "delivery_scope": "existing_receipt_readback_and_bounded_reconciliation_only_not_business_validation"
    }
    location = save("reconciliation.receipt.json", state)
    card = "\n".join([
        "# 主线 A 对账卡：阻塞，等待审核",
        "",
        "主线 A 实施计划已初始化，但本轮尚未进入线上试跑。健康接口正常不等于安全门禁或知识蒸馏验收通过。",
        "",
        "## 已有证据",
        "",
        "- [实施计划](../../_bmad-output/mainline-a-v2-scenario-a-20261029.md:1)当前为 BLOCKED；主线 B 历史验收不继承为本轮绿灯。",
        "- [健康回执](health.receipt.json:1)：健康及统计接口均返回 200，健康报告正常；综合身份门禁退出码为 2。",
        "- [监听定位回执](listener-visibility.receipt.json:1)：诊断退出码为 0，端口可见，但没有返回拥有者 PID 或启动时间；123 个进程描述符目录读取被拒绝，不能认定其中哪个属于网关。",
        "- 监听 inode 前后均为 12033；这不证明拥有者身份或持续不重启，也不证明已经发生重启。",
        "- [结构化对账回执](reconciliation.receipt.json:1)引用原始文件字节和摘要；本卡为程序生成的证据视图，平台待办才是当前进度权威。控制器覆盖未获证明。",
        "",
        "## 传输契约与未验证边界",
        "",
        "- 计划显式要求 adaptive-backpressure-v1；22,016 字节只作物理帧上限和软遥测目标，不作业务截断门禁。",
        "- 累计传输和解码上限均为 4,194,304 字节，最多 256 个分片，通知分片及唯一最终清单须保留全文证据。",
        "- 上述是本轮计划及驱动的能力声明，不是实际分片运行时通过证据。",
        "- [预检驱动](../.scratch/mainline_a_20261029.py:1)只有健康和预检入口；线上入口与专用分片接收尚未实现。",
        "- 本轮只发起健康读取及只读定位，没有发起生成、来源执行、FastCode 写入、网关修改或重启操作；未取得全树持续隔离证明。",
        "",
        "## 全部未完成事项",
        "",
    ] + ["- " + item for item in state["remaining_requirements"]] + [
        "",
        "## 停止边界",
        "",
        "监听拥有者身份缺证据，未推进其依赖：没有运行预检、相关离线回归、线上业务或四维消费，不登记任何通过。",
        "本轮仅完成接管、计划初始化、健康及身份诊断和阻塞交付；没有完成主线 A。未归档、未转正、未更新事实基线。",
        ""
    ])
    raw = card.encode("utf-8")
    if len(card.splitlines()) > 80 or len(raw) > 12000:
        raise ValueError("bounded_delivery_budget_exhausted")
    card_location = save("RECONCILIATION.md", raw)
    print(json.dumps({"delivery_status": "saved_and_exact_readback",
                      "task_status": "BLOCKED", "review_state": "awaiting_user_review",
                      "receipt": location, "card": card_location,
                      "business_retested": False, "archived": False}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
