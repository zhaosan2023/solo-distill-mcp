"""复读当前主线A已有物理证据并保存阻塞对账；不重测、不联网、不归档。"""
from __future__ import annotations

import json

from mainline_a_20261029 import ROOT, OUT, PLAN, CAPS, save, sha

COMMAND = "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_reconciliation_v3_20261029.py </dev/null"


def read(name):
    raw = (ROOT / OUT / name).read_bytes()
    return raw, {"path": OUT + "/" + name, "bytes": len(raw),
                 "sha256": sha(raw), "readback": "existing_exact_bytes_read"}


def main():
    plan_raw = (ROOT / PLAN).read_bytes()
    plan_text = plan_raw.decode("utf-8")
    required = ("plan_id: mainline-a-v2-scenario-a-20261029\n", "contract_version: 3\n",
                "status: BLOCKED\n",
                "verification_command_status: BLOCKED_AT_PREFLIGHT_TIMEOUT_AND_HTTP_INPUT_CAPACITY\n")
    if any(item not in plan_text for item in required):
        raise ValueError("active_plan_boundary_conflict")
    health_raw, health_location = read("health.receipt.json")
    diagnostic_raw, diagnostic_location = read("capacity-diagnostic.receipt.json")
    health, diagnostic = json.loads(health_raw), json.loads(diagnostic_raw)
    if (health["status"] != "pass" or health["driver_exit_code"] != 0
            or health["gate_contract_version"] != 3
            or not health["listener_inode_stable"]
            or len(health["diagnostic"]["results"]) != 2
            or any(item["http_status"] != 200 for item in health["diagnostic"]["results"])):
        raise ValueError("health_evidence_conflict")
    if (diagnostic["diagnostic_status"] != "completed_not_business_acceptance"
            or diagnostic["diagnostic_exit_code"] != 0
            or diagnostic["preceding_preflight"]["actual_exit_code"] != 124
            or diagnostic["generation_requests"] != 0
            or diagnostic["mandatory_body_alone_exceeds_http_cap"] is not True
            or diagnostic["http_post_exceeds_hard_cap"] is not True
            or diagnostic["http_post_hard_limit_bytes"] != 393216
            or diagnostic["complete_mandatory_body_utf8_bytes"] <= 393216):
        raise ValueError("capacity_and_timeout_evidence_conflict")
    evidence = [health_location, diagnostic_location]
    for expected in diagnostic["source_receipts"] + [diagnostic["post_evidence"]]:
        prefix = OUT + "/"
        if not expected["path"].startswith(prefix):
            raise ValueError("evidence_scope_conflict")
        name = expected["path"][len(prefix):]
        if "/" in name or name in {".", ".."}:
            raise ValueError("evidence_name_conflict")
        raw, actual = read(name)
        if any(actual[key] != expected[key] for key in ("path", "bytes", "sha256")):
            raise ValueError("saved_evidence_changed")
        if name == "capacity-diagnostic.http-post.json" and len(raw) != diagnostic["http_post_bytes"]:
            raise ValueError("post_byte_evidence_conflict")
        evidence.append(actual)
    warnings = diagnostic["telemetry"]["warnings"]
    if not any(item["metric"] == "body_bytes" and item["level"] == "Warning"
               and item["target"] == 98304 for item in warnings):
        raise ValueError("soft_telemetry_evidence_missing")
    remaining = [
        "真实选择链预检退出124，缺合格预检回执；完整必需正文已超过单次POST物理上限，当前契约内无法消除此阻塞。",
        "指定离线回归未执行：按绑定契约暂停失败预检的依赖，不登记全绿。",
        "专用live及分片接收入口未实现；没有在线E5、模型输出、分片、投递或180秒成功接收证据。",
        "consumer_v2四维独立语义消费未执行；没有可消费的本次在线知识帧。",
        "最终通过对账与归档未完成；活动计划及历史资产保留，不转正ADR、不更新事实基线。",
    ]
    state = {
        "kind": "mainline_a_v3_blocked_reconciliation_not_progress_authority",
        "contract_version": 3, "plan_id": "mainline-a-v2-scenario-a-20261029",
        "task_state": "BLOCKED", "plan_path": PLAN,
        "plan_sha256_at_delivery": sha(plan_raw), "plan_bytes": len(plan_raw),
        "command": COMMAND, "cwd": str(ROOT),
        "delivery_scope": "existing_evidence_readback_only_not_business_reverification",
        "source_receipts": evidence,
        "physical_command_results": [
            {"command": health["command"], "cwd": health["cwd"], "actual_exit_code": 0,
             "provenance": "client_execute_command_result_call_068975c8860f1afc622898c3"},
            diagnostic["preceding_preflight"],
            {"command": diagnostic["command"], "cwd": diagnostic["cwd"], "actual_exit_code": 0,
             "provenance": "client_execute_command_result_call_0bb148212f7e16a4f48893b9",
             "scope": "capacity_diagnostic_not_preflight_or_business_acceptance"},
        ],
        "capacity": {
            "python_files": diagnostic["python_files"],
            "mandatory_unique_owners": diagnostic["mandatory_unique_owners"],
            "mandatory_body_bytes": diagnostic["complete_mandatory_body_utf8_bytes"],
            "source_body_soft_target_bytes": 98304,
            "http_post_shape_bytes": diagnostic["http_post_bytes"],
            "http_post_hard_limit_bytes": 393216,
            "producer_equivalence": diagnostic["producer_equivalence"],
            "snapshot_freshness": "saved_preflight_snapshot_not_final_live_verification",
        },
        "telemetry_warnings": warnings, "transport_contract": CAPS,
        "controller_coverage": "not_proven", "progress_authority": "platform_task_record_only",
        "online_business": "not_started", "generation_requests": 0, "outer_replays": 0,
        "related_offline_tests": "not_run_due_to_preflight_gate",
        "four_dimension_consumption": {key: "not_run_no_online_frame" for key in
            ("query_flow", "state_ownership", "failure_boundaries", "scope_and_verification")},
        "transport_runtime_acceptance": "not_run", "hard_timeout_runtime_acceptance": "not_run",
        "remaining_requirements": remaining,
        "target_write_operations": 0, "target_execution_operations": 0,
        "gateway_restart_operations": 0, "archival_operations": 0,
        "adr_promotion_operations": 0, "fact_baseline_write_operations": 0,
        "business_retested_for_state_save": False,
    }
    card = "\n".join([
        "# 主线 A V3 阻塞对账", "",
        "已纠正96KB假硬门禁，但全生命周期未完成。当前阻塞是真实选择链预检超时，以及完整必需正文超过单次HTTP POST物理上限。", "",
        "## 实际完成", "",
        "- [活动计划](../../_bmad-output/mainline-a-v2-scenario-a-20261029.md:1)契约版本3；98,304字节仅为软遥测目标，状态如实保持BLOCKED。",
        "- [预检驱动](../.scratch/mainline_a_20261029.py:245)已使用实际选择链，不再将96KB作为失败断言；生产源码及既有改动未覆盖。",
        "- [健康回执](health.receipt.json:1)：实际命令退出0，健康和统计HTTP均为200，监听inode快照稳定；不证明进程身份、持续隔离或模型可用性。",
        "- 真实选择链预检实际退出124，未生成合格预检回执；超时的具体热点未取得性能剖析证据。",
        "- [容量诊断回执](capacity-diagnostic.receipt.json:1)：离线诊断实际退出0，仅证明已保存来源快照的容量，不等于预检或模型验收。",
        "- 完整快照有" + str(diagnostic["python_files"]) + "个Python文件，唯一必需正文为" + str(diagnostic["complete_mandatory_body_utf8_bytes"]) + "字节；该正文自身已超过393,216字节物理上限。",
        "- [HTTP形状原字节](capacity-diagnostic.http-post.json:1)为" + str(diagnostic["http_post_bytes"]) + "字节。形状依生产源码推导，不是已完成Scheduler或真实POST运行时证据。",
        "- 98,304字节越界已记录Warning而非失败。生产自适应选择将全部语法注册为必需组；导航分页和下行stdio分片不减少上行完整必需正文。", "",
        "## 全部未完成事项", "",
    ] + ["- " + item for item in remaining] + [
        "", "## 可恢复边界", "",
        "- 在不缩小分母、仅一次完整正文POST、393,216字节硬上限同时成立时，当前全库输入无法满足容量；不能通过剪裁、旧策略降级、增发POST或提高上限伪造成功。",
        "- 继续端到端实施须明确修订至少一项冲突契约，并重验受影响接口与证据；本次不自主扩大该权限。",
        "- 未启动或重启网关，未执行或修改FastCode；未发起模型请求，未继承主线B历史通过。",
        "- [结构化对账](reconciliation.receipt.json:1)引用实际回执和源字节摘要；本卡仅为程序生成的只读证据视图，唯一进度权威是平台任务记录。",
        "- 保存本报告只复读既有证据，不触发业务重测；归档条件未满足，源计划及历史资产均保留。", "",
    ])
    raw = card.encode("utf-8")
    if len(card.splitlines()) > 80 or len(raw) > 12000:
        raise ValueError("bounded_delivery_budget_exhausted")
    if (ROOT / PLAN).read_bytes() != plan_raw:
        raise ValueError("active_plan_changed_during_reconciliation")
    receipt_location = save("reconciliation.receipt.json", state)
    card_location = save("RECONCILIATION.md", raw)
    print(json.dumps({"delivery_status": "saved_fsync_and_exact_readback",
                      "task_status": "BLOCKED", "receipt": receipt_location,
                      "card": card_location, "business_retested": False,
                      "archived": False}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
