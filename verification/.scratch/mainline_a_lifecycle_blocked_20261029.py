"""主线 A 物理门禁阻塞对账：只复读既有证据，不联网、不重测、不归档。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from mainline_a_20261029 import ROOT, OUT, PLAN, CAPS, save, sha
from solo_distill.extractor import open_root

COMMAND = "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_lifecycle_blocked_20261029.py </dev/null"


def read_evidence(name):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("unsafe_evidence_name")
    descriptors = [open_root(str(ROOT))]
    try:
        for part in OUT.split("/"):
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=descriptors[-1])
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 33554432:
                raise ValueError("evidence_not_regular_or_over_budget")
            raw = stream.read(33554433)
            if len(raw) != info.st_size:
                raise ValueError("evidence_size_changed")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
    return raw, {"path": OUT + "/" + name, "bytes": len(raw),
                 "sha256": sha(raw), "readback": "existing_bytes_read"}


def main():
    health_raw, health_location = read_evidence("health.receipt.json")
    preflight_raw, preflight_location = read_evidence("preflight.receipt.json")
    health = json.loads(health_raw)
    preflight = json.loads(preflight_raw)
    results = health["diagnostic"]["results"]
    if (health["status"] != "pass" or health["driver_exit_code"] != 0
            or len(results) != 2 or any(item["http_status"] != 200 for item in results)
            or not health["listener_after"]["listening_inodes"]
            or health["listener_before"]["listening_inodes"] != health["listener_after"]["listening_inodes"]
            or health["pid_observation_waived_by_current_user"] is not True):
        raise ValueError("revised_health_evidence_conflict")
    required_failures = {"complete_source_body_exceeds_user_96kib_hard_limit",
                         "complete_gateway_input_exceeds_fixed_http_message_capability"}
    if (preflight["status"] != "blocked" or preflight["driver_exit_code"] != 2
            or set(preflight["failures"]) != required_failures
            or preflight["complete_source_body_bytes"] <= preflight["source_body_hard_limit_bytes"]
            or preflight["post_bytes"] <= preflight["post_hard_limit_bytes"]
            or preflight["source_body_hard_limit_bytes"] != 98304
            or preflight["post_hard_limit_bytes"] != 393216
            or preflight["generation_requests"] != 0
            or preflight["dependencies_not_advanced"] is not True):
        raise ValueError("capacity_block_evidence_conflict")
    plan_raw = (ROOT / PLAN).read_bytes()
    plan_text = plan_raw.decode("utf-8")
    required_plan = ("plan_id: mainline-a-v2-scenario-a-20261029\n",
                     "contract_version: 2\n", "status: BLOCKED\n",
                     "verification_command_status: BLOCKED_AT_COMPLETE_SOURCE_BODY_AND_HTTP_INPUT_CAPACITY_GATE\n")
    if any(item not in plan_text for item in required_plan):
        raise ValueError("active_plan_boundary_conflict")
    evidence = [health_location, preflight_location]
    for key in ("structure_evidence", "sources_evidence", "denominator_evidence"):
        expected = preflight[key]
        prefix = OUT + "/"
        if not expected["path"].startswith(prefix):
            raise ValueError("evidence_path_scope_conflict")
        _, actual = read_evidence(expected["path"][len(prefix):])
        if any(actual[field] != expected[field] for field in ("path", "bytes", "sha256")):
            raise ValueError("saved_preflight_evidence_digest_conflict")
        actual["readback"] = "saved_receipt_digest_and_bytes_match"
        evidence.append(actual)
    remaining = [
        "完整来源正文及完整 HTTP 输入双重容量门禁失败；当前授权内不得截断、缩小来源、增发请求或扩大硬上限。",
        "指定离线单测尚未运行，不登记通过。",
        "专用在线分片接收及 live 入口尚未补齐，不登记兼容或运行时通过。",
        "一次真实 V2 A 集成、180 秒硬截止、原始 stdio、生成输出及投递清理证据尚未取得。",
        "consumer_v2 查询链路、状态所有权、失败边界及来源范围四维独立消费尚未执行。",
        "全计划必需门禁未通过，不归档；保留活动计划、旧回执、来源快照及主线 B 原始资产。"
    ]
    state = {
        "receipt_version": 2,
        "kind": "mainline_a_capacity_block_reconciliation_not_progress_authority",
        "plan_id": "mainline-a-v2-scenario-a-20261029", "contract_version": 2,
        "plan_path": PLAN, "plan_bytes": len(plan_raw), "plan_sha256_at_delivery": sha(plan_raw),
        "task_state": "BLOCKED", "command": COMMAND, "cwd": str(ROOT),
        "driver_exit_code": 0, "delivery_scope": "existing_evidence_readback_only_not_business_validation",
        "physical_command_results": [
            {"command": health["command"], "cwd": health["cwd"], "actual_exit_code": 0,
             "exit_status_provenance": "client_execute_command_success_receipt", "evidence": health_location},
            {"command": preflight["command"], "cwd": preflight["cwd"], "actual_exit_code": 2,
             "exit_status_provenance": "client_execute_command_failure_receipt", "evidence": preflight_location}
        ],
        "source_receipts": evidence,
        "health_gate": {"status": "pass", "http_statuses": [item["http_status"] for item in results],
                        "stable_listener_inodes": health["listener_after"]["listening_inodes"],
                        "pid_observation_waived": True, "process_identity_proven": False,
                        "continuous_isolation_proven": False, "model_availability_proven": False},
        "preflight_gate": {"status": "blocked", "failures": preflight["failures"],
                           "coverage": preflight["coverage"],
                           "complete_source_body_bytes": preflight["complete_source_body_bytes"],
                           "source_body_hard_limit_bytes": preflight["source_body_hard_limit_bytes"],
                           "complete_http_input_bytes": preflight["post_bytes"],
                           "http_input_hard_limit_bytes": preflight["post_hard_limit_bytes"],
                           "full_definition_name_count": preflight["full_definition_name_count"],
                           "full_condition_count": preflight["full_condition_count"],
                           "full_call_expression_distinct_count": preflight["full_call_expression_distinct_count"],
                           "freshness": preflight["freshness"], "blueprint": preflight["blueprint"]},
        "transport_contract": CAPS, "transport_runtime_acceptance": "not_run",
        "online_business": "not_started", "generation_requests": 0, "outer_replays": 0,
        "related_offline_tests": "not_run_due_to_preflight_gate",
        "four_dimension_consumption": {key: "not_run_due_to_preflight_gate" for key in
                                      ("query_flow", "state_ownership", "failure_boundaries", "scope_and_verification")},
        "hard_timeout_runtime_acceptance": "not_run", "remaining_requirements": remaining,
        "progress_authority": "platform_task_record_only", "controller_coverage": "not_proven",
        "archival_operations": 0, "adr_promotion_operations": 0, "fact_baseline_write_operations": 0,
        "target_execution_operations": 0, "target_write_operations": 0,
        "gateway_restart_operations": 0, "privilege_escalation_operations": 0,
        "business_retested_for_state_save": False
    }
    card = "\n".join([
        "# 主线 A 全生命周期对账卡：物理容量门禁阻塞", "",
        "已解除旧 PID 观测阻塞并运行完整来源预检；双重硬容量门禁失败，已停止依赖推进，主线 A 未完成。", "",
        "## 已确认的真实回执", "",
        "- [活动计划](../../_bmad-output/mainline-a-v2-scenario-a-20261029.md:1)契约版本为 2，状态为 BLOCKED，保留原位。",
        "- [健康回执](health.receipt.json:1)：实际命令退出 0，两个 HTTP 接口均为 200，监听 inode 前后稳定；PID 观测已获豁免，不代表进程身份、持续隔离或模型可用性已证明。",
        "- [预检回执](preflight.receipt.json:1)：实际命令退出 2，完整源码正文为 " + str(preflight["complete_source_body_bytes"]) + " 字节，硬上限为 98,304 字节。",
        "- 同一完整请求的 HTTP 编码输入为 " + str(preflight["post_bytes"]) + " 字节，固定硬上限为 393,216 字节；输出 stdio 分片不能解除 HTTP 输入限制。",
        "- 来源枚举 150 项，文件分母 121，解析 81，跳过 40，失败 0；该覆盖为静态扫描，不是业务测试通过。",
        "- [完整来源结构](preflight.structure.json:1)、[原始来源正文快照](preflight.sources.json:1)和[结构分母](preflight.denominators.json:1)已按原回执字节及摘要复读核对。",
        "- 主线 B 蓝图清单 62 项摘要一致，仅验证静态资产完整性，不继承历史运行时绿灯。", "",
        "## 未完成事项", ""
    ] + ["- " + item for item in remaining] + [
        "", "## 边界及证据定位", "",
        "- 本轮生成请求为 0；未截断正文、未缩小分母、未执行或写入 FastCode，未提权、未重启网关。",
        "- [结构化对账回执](reconciliation.receipt.json:1)引用实际命令回执及完整文件摘要；本卡是程序生成的证据视图，不是另一个进度账本。",
        "- 当前唯一进度权威是平台待办；控制器覆盖未证明，不宣称程序防篡改保证。",
        "- 原始证据及旧资产均保留；没有进行计划归档、ADR 转正或事实基线写入。",
        "- 保存本卡只复读既有证据，没有重新运行业务、测试或健康探测。", ""
    ])
    raw = card.encode("utf-8")
    if len(card.splitlines()) > 80 or len(raw) > 12000:
        raise ValueError("bounded_delivery_budget_exhausted")
    receipt_location = save("reconciliation.receipt.json", state)
    card_location = save("RECONCILIATION.md", raw)
    print(json.dumps({"delivery_status": "saved_and_exact_readback", "task_status": "BLOCKED",
                      "receipt": receipt_location, "card": card_location,
                      "business_retested": False, "archived": False}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
