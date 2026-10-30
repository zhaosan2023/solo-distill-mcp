"""复读主线 A 已存阻塞证据；不探测网关、不重测、不推进或归档。

只生成本次证据复读回执及有界对账视图，平台待办仍是唯一进度入口。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[2]
BASE = "verification/mainline-a-lifecycle-20261029"
OUT = "verification/mainline-a-lifecycle-review-20261029T0641"
PLAN = "_bmad-output/mainline-a-v2-scenario-a-20261029.md"
COMMAND = "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_evidence_readback_20261029T0641.py </dev/null"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def directory(path, create=False):
    parts = Path(path).parts
    if not parts or Path(path).is_absolute() or any(p in {".", ".."} for p in parts):
        raise ValueError("unsafe_relative_directory")
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read(path):
    parent, name = str(Path(path).parent), Path(path).name
    fd = directory(parent)
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(handle, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 33554432:
                raise ValueError("evidence_not_regular_or_over_budget")
            raw = stream.read(33554433)
            if len(raw) != info.st_size:
                raise ValueError("evidence_size_changed")
        return raw, {"path": path, "bytes": len(raw), "sha256": digest(raw)}
    finally:
        os.close(fd)


def save(name, raw):
    fd = directory(OUT, create=True)
    try:
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)
    checked, location = read(OUT + "/" + name)
    if checked != raw:
        raise ValueError("delivery_readback_conflict")
    location["readback"] = "exact_bytes_pass"
    return location


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def main():
    reconciliation_raw, reconciliation_location = read(BASE + "/reconciliation.receipt.json")
    recorded = json.loads(reconciliation_raw)
    health_raw, health_location = read(BASE + "/health.receipt.json")
    preflight_raw, preflight_location = read(BASE + "/preflight.receipt.json")
    health, preflight = json.loads(health_raw), json.loads(preflight_raw)
    plan_raw, plan_location = read(PLAN)
    require(recorded["plan_path"] == PLAN and recorded["plan_id"] == "mainline-a-v2-scenario-a-20261029", "plan_binding_conflict")
    require(recorded["contract_version"] == 2 and recorded["plan_sha256_at_delivery"] == digest(plan_raw), "recorded_plan_version_conflict")
    require(recorded["plan_bytes"] == len(plan_raw), "recorded_plan_size_conflict")
    plan_text = plan_raw.decode("utf-8")
    for expected in ("contract_version: 2\n", "status: BLOCKED\n", "verification_command_status: BLOCKED_AT_COMPLETE_SOURCE_BODY_AND_HTTP_INPUT_CAPACITY_GATE\n"):
        require(expected in plan_text, "active_plan_boundary_conflict")
    require(health["status"] == "pass" and health["driver_exit_code"] == 0, "recorded_health_conflict")
    results = health["diagnostic"]["results"]
    require({r["path"]: r["http_status"] for r in results} == {"/health": 200, "/v1/stats": 200}, "recorded_http_conflict")
    require(bool(health["listener_before"]["listening_inodes"]) and health["listener_before"]["listening_inodes"] == health["listener_after"]["listening_inodes"], "recorded_listener_conflict")
    require(health["pid_observation_waived_by_current_user"] is True, "pid_waiver_conflict")
    failures = {"complete_source_body_exceeds_user_96kib_hard_limit", "complete_gateway_input_exceeds_fixed_http_message_capability"}
    require(preflight["status"] == "blocked" and preflight["driver_exit_code"] == 2 and set(preflight["failures"]) == failures, "recorded_capacity_gate_conflict")
    require(preflight["source_body_hard_limit_bytes"] == 98304 and preflight["complete_source_body_bytes"] > 98304, "source_capacity_conflict")
    require(preflight["post_hard_limit_bytes"] == 393216 and preflight["post_bytes"] > 393216, "http_capacity_conflict")
    require(preflight["generation_requests"] == 0 and preflight["dependencies_not_advanced"] is True, "recorded_dependency_boundary_conflict")
    locations = [reconciliation_location, plan_location, health_location, preflight_location]
    for expected in recorded["source_receipts"]:
        require(expected["path"].startswith(BASE + "/"), "recorded_evidence_path_conflict")
        _, actual = read(expected["path"])
        require(all(actual[k] == expected[k] for k in ("path", "bytes", "sha256")), "recorded_evidence_digest_conflict")
        actual["readback"] = "recorded_digest_and_size_match"
        locations.append(actual)
    for key in ("structure_evidence", "sources_evidence", "denominator_evidence"):
        expected = preflight[key]
        require(expected["path"].startswith(BASE + "/"), "preflight_evidence_path_conflict")
        _, actual = read(expected["path"])
        require(all(actual[k] == expected[k] for k in ("path", "bytes", "sha256")), "preflight_evidence_digest_conflict")
    remaining = [
        "完整源码正文和 HTTP 输入双重容量门禁未通过；不得截断、缩小分母、增发请求或扩大硬上限。",
        "指定离线回归尚未取得通过证据，保持阻塞。",
        "专用在线无损分片接收与 live 入口尚未补齐或验证。",
        "一次真实 V2 A 集成、180 秒硬截止、原始 stdio、生成输出及投递清理证据尚未取得。",
        "consumer_v2 四维独立消费尚未完成；真实 Roo 消费也未证明。",
        "全计划门禁未通过，不归档；保留活动计划、来源快照、旧回执及主线 B 资产。",
    ]
    receipt = {
        "kind": "existing_mainline_a_block_evidence_readback_only", "receipt_version": 1,
        "plan_id": recorded["plan_id"], "contract_version": 2, "task_state": "BLOCKED",
        "command": COMMAND, "cwd": str(ROOT), "driver_exit_code": 0,
        "existing_evidence": locations, "recorded_health_gate": "pass_not_retested",
        "recorded_preflight_gate": "blocked_not_retested", "failures": sorted(failures),
        "complete_source_body_bytes": preflight["complete_source_body_bytes"], "source_body_hard_limit_bytes": 98304,
        "complete_http_input_bytes": preflight["post_bytes"], "http_input_hard_limit_bytes": 393216,
        "current_source_freshness": "not_rechecked_readback_is_not_a_new_preflight",
        "current_gateway_health": "not_reprobed_readback_is_not_current_runtime_evidence",
        "current_execution_scope": "artifact_digest_readback_and_stop_delivery_only",
        "current_generation_requests": 0, "current_business_tests": 0, "archival_operations": 0,
        "active_plan_write_operations": 0, "target_write_operations": 0, "target_execution_operations": 0,
        "gateway_restart_operations": 0, "privilege_escalation_operations": 0,
        "adr_promotion_operations": 0, "fact_baseline_write_operations": 0,
        "progress_authority": "platform_task_record_only", "controller_coverage": "not_proven",
        "remaining_requirements": remaining,
    }
    card = "\n".join([
        "# 主线 A 接管复读对账：保持阻塞", "",
        "已复读并核对既存容量阻塞证据；本次没有重新探测、预检或运行业务。依照失败即停止的授权，保留现场，不推进后续依赖。", "",
        "## 既存物理证据", "",
        "- [活动计划](../../" + PLAN + ":1)仍为 BLOCKED，契约版本 2；原字节与既存对账摘要一致，未写为 EXECUTING。",
        "- [既存健康回执](../mainline-a-lifecycle-20261029/health.receipt.json:1)记录两个 HTTP 接口均为 200、监听 inode 稳定，PID 观测已豁免；不是本次的新健康探测。",
        "- [既存预检回执](../mainline-a-lifecycle-20261029/preflight.receipt.json:1)记录退出 2：完整源码正文 " + str(preflight["complete_source_body_bytes"]) + " 字节，上限 98,304；HTTP 输入 " + str(preflight["post_bytes"]) + " 字节，上限 393,216。",
        "- 已复读健康、预检、完整来源结构、正文快照和分母证据，字节及摘要与既存回执一致；没有重验当前来源新鲜度或当前网关状态。",
        "- 输出分片不解除源码总量或 HTTP 输入硬上限；授权未允许缩小来源、截断或增发请求。", "",
        "## 全部未完成事项", "",
    ] + ["- " + item for item in remaining] + [
        "", "## 交付边界", "",
        "- [本次复读回执](readback.receipt.json:1)只证明资产复读核对及当前停止交付，不证明离线单测、集成或语义验收通过。",
        "- 原有计划及其他资产未改写。当前唯一进度入口仍为平台待办；控制器覆盖未证明，不宣称程序防篡改保证。",
        "- 未发起生成、执行 FastCode、提权、重启网关、归档、ADR 转正或事实基线写入。", "",
    ])
    require(len(card.splitlines()) <= 80 and len(card.encode("utf-8")) <= 12000, "delivery_budget_exhausted")
    receipt_location = save("readback.receipt.json", json.dumps(receipt, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    card_location = save("RECONCILIATION.md", card.encode("utf-8"))
    print(json.dumps({"task_status": "BLOCKED", "delivery_status": "saved_and_exact_readback", "receipt": receipt_location, "card": card_location, "source_body_bytes": preflight["complete_source_body_bytes"], "http_input_bytes": preflight["post_bytes"], "business_retested": False, "archived": False}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
