"""当前唯一语法恢复产物的可信双消费与定位；不重发、不冒充人工。

消费与对账分别使用获准的90秒独立窗口，保留旧minimal验收入口与全部旧证据。
版本由当前获准调用方固定，不从发布包自报值取得；参考锚点仅在对账读取。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time

import mainline_a_schema_v3_online_20261101 as support
import mainline_a_schema_v3_syntax_20261102 as syntax_support
from mainline_a_schema_v3_minimal_acceptance_20261102 import (
    checked_asset, locate_anchors, reports_equal_except_scan_elapsed,
)
from schema_v3_candidate_syntax_oracle import parse_candidate
from schema_v3_selection_oracle import expected_selection
from schema_v3_selection_ingress_oracle import unpack_selection
from schema_v3_snapshot_oracle import oracle_scope
from schema_v3_wire_oracle import receive as independent_receive
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
from solo_distill.extractor import load_projects
from solo_distill.gateway import candidate_json, strict_json
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.wire_v3 import Admission

PREFIX = "phase5-syntax-acceptance-20261102T085352Z"
VERSION = "question-scout-v2"
LIVE_RECEIPT_SHA256 = "14a6bdcafd7afcec358f6a852a0ae9a6ccbaf6331a060d454322bc7051aaac9e"
ONLINE_EXIT_TOOL_RESULT = "call_9e2e7276cc8f9a23e9e7fa1e"
ONLINE_EXIT_CODE = 0


def require(condition, reason):
    if not condition:
        raise ValueError("syntax_acceptance_" + reason)


def load(path):
    return strict_json(support.read(path).decode("utf-8"))


def binding():
    """消费已退出窗口的冻结绑定，不调用准入、不产生下一在线标记。"""
    require(support.SYNTAX and support.REQUEST_ID == syntax_support.REQUEST
            and support.FROZEN_SAMPLE == "preflight-coverage-20261102T050022Z-1",
            "explicit_syntax_namespace")
    raw = support.read(support.PARENT + "/" + support.LIVE + ".receipt.json")
    require(support.digest(raw) == LIVE_RECEIPT_SHA256, "live_receipt_changed")
    receipt = strict_json(raw.decode("utf-8"))
    require(receipt.get("status") == "online_transport_saved_trusted_consumption_pending"
            and receipt.get("failures") == []
            and receipt.get("execution_instance") == support.INSTANCE
            and receipt.get("request_id") == syntax_support.REQUEST
            and type(receipt.get("service_exit_code")) is int
            and receipt["service_exit_code"] == 0
            and receipt.get("service_cleanup") == "confirmed_natural_exit"
            and type(receipt.get("business_call_attempts")) is int
            and receipt["business_call_attempts"] == 1
            and ONLINE_EXIT_CODE == 0 and bool(ONLINE_EXIT_TOOL_RESULT), "live_not_complete")
    bound = receipt["binding"]
    require(type(bound.get("plan_contract_version")) is int
            and bound["plan_contract_version"] == 6
            and type(bound.get("adr_revision")) is int and bound["adr_revision"] == 16
            and type(bound.get("execution_contract_revision")) is int
            and bound["execution_contract_revision"] == 17
            and bound.get("session_id") is None, "binding_version")
    paths = {"plan_sha256": support.PLAN, "execution_contract_sha256": support.CONTRACT,
             "adr_sha256": "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md",
             "projects_sha256": "config/projects.json", "policy_sha256": "config/distillation.json"}
    for key, path in paths.items():
        require(support.digest(support.read(path)) == bound[key], "binding_changed:" + key)
    support.verify_prior_preservation(bound)
    syntax_support.verify_marker(support, load(support.RECOVERY_MARKER), bound)
    return receipt


def verify_attempts(receipt, assets, trace, post, guard):
    """复核共享计数、期限、原SSE及已保存整份拒收；不新写或修改旧产物。"""
    attempts = trace["gateway_requests"]
    require(type(attempts) is list and 1 <= len(attempts) <= 4, "bounded_attempts")
    require(type(receipt.get("actual_http_attempts")) is int
            and receipt["actual_http_attempts"] == len(attempts)
            and type(receipt.get("client_auto_retries")) is int
            and receipt["client_auto_retries"] == len(attempts) - 1, "shared_retry_count")
    for index, attempt in enumerate(attempts):
        guard.check()
        raw = base64.b64decode(attempt["raw_sse_base64"], validate=True)
        require(raw == assets["sse_attempt_" + str(index + 1)]
                and len(raw) == attempt["raw_sse_bytes"]
                and support.digest(raw) == attempt["raw_sse_sha256"]
                and attempt["message_bytes"] == len(post)
                and attempt["message_sha256"] == support.digest(post)
                and attempt["absolute_deadline"] == attempts[0]["absolute_deadline"]
                and type(attempt["attempt_index"]) is int and attempt["attempt_index"] == index + 1
                and type(attempt["client_auto_retries"]) is int
                and attempt["client_auto_retries"] == index, "attempt_binding")
        # verify_attempt只可复读本窗口已保存副本；缺少副本时先拒绝，避免隐式写入。
        require("transport_attempt_" + str(index + 1) in receipt["assets"], "transport_copy_missing")
        if attempt.get("candidate_error_reason") is not None:
            require("candidate_rejection_" + str(index + 1) in receipt["assets"], "rejection_copy_missing")
        syntax_support.verify_attempt(support, receipt, attempt, post, raw, guard)
        if index < len(attempts) - 1:
            require(support.retry_allowed(attempt), "nonretryable_attempt")
    require(assets["sse"] == assets["sse_attempt_" + str(len(attempts))], "final_sse_conflict")
    support.require_gateway_completion(attempts[-1], assets["sse"])
    require(attempts[-1].get("candidate_error_reason") is None
            and "candidate_rejection_id" not in attempts[-1], "final_candidate_rejected")
    return attempts


def fresh_context(arguments, trace, frozen, guard):
    require(arguments.get("selection_version") == VERSION, "explicit_selection_version")
    project = load_projects("config/projects.json")[arguments["project_id"]]
    prepared = prepare_snapshot(project=project, include=tuple(frozen["include"]),
        question=frozen["question"], dimensions=frozen["dimensions"],
        deadline=guard.deadline, cancelled=threading.Event(),
        frozen_selection=frozen["selection"], selection_version=VERSION, tick=guard.check)
    require(arguments["question"] == frozen["question"]
            and arguments["dimensions"] == frozen["dimensions"]
            and arguments["include"] == frozen["include"]
            and reports_equal_except_scan_elapsed(prepared.report, frozen["report"])
            and reports_equal_except_scan_elapsed(prepared.report, trace["report"])
            and prepared.selection == frozen["selection"] == trace["selection"]
            and prepared.core.document == trace["core"], "fresh_source_or_structure_conflict")
    frozen_files = {path: base64.b64decode(raw, validate=True)
                    for path, raw in frozen["raw_files_base64"].items()}
    require(prepared.raw_files == frozen_files, "complete_frozen_source_changed")
    messages, legacy_post, selection = expected_selection(prepared.raw_files,
        project_id=project.project_id, snapshot_id=prepared.report["snapshot_id"],
        question=frozen["question"], dimensions=frozen["dimensions"],
        frozen_coverage=prepared.report["coverage"], selection_version=VERSION, tick=guard.check)
    require(selection == prepared.selection and messages == prepared.messages
            and legacy_post == prepared.post, "independent_selection_conflict")
    require(unpack_selection(arguments["compressed_frozen_selection"], tick=guard.check) == selection,
            "independent_ingress_conflict")
    return project, prepared, selection, legacy_post[:-1] + b',"temperature":0.2,"max_tokens":3072}'


def dual_receive(raw, raw_files, candidate, trusted):
    require(trusted.get("selection_version") == VERSION, "dual_explicit_selection_version")
    admission = Admission("2024-11-05", "A", 3, 22016,
        TransportCapabilities.from_dict(support.CAPS), wire_encoding="lz-json-v3-1")
    begin = time.monotonic()
    production = consume_frame(raw, raw_files, admission=admission, candidate=candidate, **trusted)
    production_seconds = time.monotonic() - begin
    begin = time.monotonic()
    independent = independent_receive(raw, raw_files, support.CAPS,
        protocol_version="2024-11-05", response_id=3, return_bytes=22016,
        wire_encoding="lz-json-v3-1", candidate=candidate, **trusted)
    independent_seconds = time.monotonic() - begin
    require(production.publication == independent["publication"]
            and production.status == independent["task_status"] == "complete",
            "dual_consumers_or_required_task_status")
    return production.publication, production_seconds, independent_seconds


@snapshot_scope
@oracle_scope
def consume():
    guard = support.Guard(90)
    receipt = binding()
    assets = {key: checked_asset(value) for key, value in receipt["assets"].items()}
    arguments = strict_json(assets["request"].decode("utf-8"))
    trace = strict_json(assets["trace"].decode("utf-8"))
    candidate = strict_json(assets["candidate"].decode("utf-8"))
    outputs = strict_json(assets["model_outputs"].decode("utf-8"))
    require(arguments["request_id"] == trace["request_id"] == syntax_support.REQUEST
            and arguments["operation"] == trace["operation"] == "live"
            and trace["failures"] == [] and type(outputs) is list and len(outputs) == 1
            and type(outputs[0]) is str and bool(outputs[0].strip())
            and outputs == trace["model_outputs"]
            and parse_candidate(outputs[0]) == candidate_json(outputs[0]) == candidate == trace["candidate"],
            "external_generation_or_request_conflict")
    require(arguments["transport"] == support.CAPS
            and arguments["wire_encoding"] == "lz-json-v3-1"
            and arguments["return_bytes"] == 22016, "transport_contract")
    frozen = load(support.PARENT + "/" + support.FROZEN_SAMPLE + "/frozen-source-and-selection.json")
    project, prepared, selection, post = fresh_context(arguments, trace, frozen, guard)
    require(post == assets["post"] == trace["post_utf8"].encode("utf-8")
            and support.digest(post) == trace["post_sha256"], "external_post_conflict")
    attempts = verify_attempts(receipt, assets, trace, post, guard)
    trusted = {"project_id": project.project_id, "snapshot_id": prepared.report["snapshot_id"],
        "required_core_unit_ids": selection["core_unit_ids"], "selection_id": selection["selection_id"],
        "selection_version": VERSION, "request_id": syntax_support.REQUEST,
        "question": frozen["question"], "dimensions": frozen["dimensions"], "post": post,
        "frozen_coverage": prepared.report["coverage"], "failures": [], "tick": guard.check}
    publication, production_seconds, independent_seconds = dual_receive(
        assets["stdio"], prepared.raw_files, candidate, trusted)
    delivery = strict_json(assets["delivery"].decode("utf-8"))
    require(delivery["written"] == len(assets["stdio"])
            and delivery["frame_sha256"] == support.digest(assets["stdio"])
            and delivery["status"] == "frame_written_not_host_consumption", "delivery_binding")
    prepared.verify_sources()
    require(binding() == receipt, "binding_changed_during_consumption")
    saved = {key: support.save(PREFIX + "." + suffix, value, guard)
        for key, suffix, value in (
            ("publication", "publication.json", publication),
            ("core", "sealed-core.json", publication["core"]),
            ("package", "knowledge-package.json", publication["core"]["package"]))}
    prepared.verify_sources()
    result = {"status": "dual_consumption_pass_semantics_pending",
        "execution_instance": support.INSTANCE, "request_id": syntax_support.REQUEST,
        "binding": receipt["binding"], "selection_version": VERSION,
        "live_receipt_sha256": LIVE_RECEIPT_SHA256,
        "actual_online_exit_tool_result": ONLINE_EXIT_TOOL_RESULT, "actual_online_outer_exit_code": ONLINE_EXIT_CODE,
        "selection_independently_recomputed": True, "production_and_independent_publication_equal": True,
        "task_status": publication["status"], "source_files": len(prepared.raw_files),
        "production_seconds": production_seconds, "independent_seconds": independent_seconds,
        "physical_limits": receipt["physical_limits"], "actual_http_attempts": len(attempts),
        "attempt_reasons": [item.get("candidate_error_reason") or item.get("error_reason") for item in attempts],
        "assets": saved, "elapsed_seconds": time.monotonic() - guard.started,
        "human_review": "not_proven", "host_loading": "not_proven",
        "shared_parser_risk": "python_standard_library_ast",
        "outer_process_exit": "requires_actual_client_command_receipt"}
    evidence = support.save(PREFIX + ".consume.receipt.json", result, guard)
    print(json.dumps({key: result[key] for key in (
        "status", "task_status", "source_files", "actual_http_attempts", "attempt_reasons",
        "production_seconds", "independent_seconds", "elapsed_seconds")} | {"receipt": evidence},
        ensure_ascii=False), flush=True)
    return 0


def reconcile():
    guard = support.Guard(90)
    live = binding()
    consumed = load(support.PARENT + "/" + PREFIX + ".consume.receipt.json")
    require(consumed["status"] == "dual_consumption_pass_semantics_pending"
            and consumed["binding"] == live["binding"] and consumed["selection_version"] == VERSION
            and consumed["live_receipt_sha256"] == LIVE_RECEIPT_SHA256
            and consumed["actual_online_exit_tool_result"] == ONLINE_EXIT_TOOL_RESULT
            and consumed["production_and_independent_publication_equal"] is True, "consumption_evidence")
    publication = strict_json(checked_asset(consumed["assets"]["publication"]).decode("utf-8"))
    require(publication["core"] == strict_json(checked_asset(consumed["assets"]["core"]).decode("utf-8"))
            and publication["core"]["package"] == strict_json(
                checked_asset(consumed["assets"]["package"]).decode("utf-8")), "consumption_assets")
    frozen = load(support.PARENT + "/" + support.FROZEN_SAMPLE + "/frozen-source-and-selection.json")
    raw_files = support.current_source(frozen, guard)
    candidate = strict_json(checked_asset(live["assets"]["candidate"]).decode("utf-8"))
    task = publication["task_evidence"]
    require(task["status"] == publication["status"] == "complete"
            and task["summary"] == candidate["summary"] and task["unknowns"] == candidate["unknowns"]
            and len(task["facts"]) == len(candidate["conclusions"]), "task_preservation")
    syntax = publication["core"]["package"]["local_closures"]["compatibility_package"]
    anchor_path = "tests/fastcode_acceptance.json"
    anchor_raw = support.read(anchor_path)
    anchors = strict_json(anchor_raw.decode("utf-8"))
    require(anchors["project_id"] == frozen["project_id"], "anchor_project")
    sources, checks = locate_anchors(syntax, anchors)
    by_id = {source["id"]: source for source in sources}
    conclusions = []
    for index, (fact, item) in enumerate(zip(task["facts"], candidate["conclusions"])):
        guard.check()
        require(fact["text"] == item["text"] and fact["dimension_id"] == item["dimension_id"]
                and [reference["id"] for reference in fact["sources"]] == item["source_ids"], "conclusion_preservation")
        excerpts = []
        for key in item["source_ids"]:
            source = by_id[key]
            lines = raw_files[source["path"]].decode("utf-8").splitlines(keepends=True)
            require(1 <= source["start"] <= source["end"] <= len(lines), "source_line_range")
            excerpts.append({**source, "source_excerpt": "".join(lines[source["start"] - 1:source["end"]]),
                "source_file_sha256": support.digest(raw_files[source["path"]])})
        conclusions.append({"index": index + 1, "fact_id": fact["id"], **item,
                            "located_sources": excerpts, "human_verdict": "pending"})
    dimensions = []
    require(len(frozen["dimensions"]) == 4, "four_dimension_contract")
    for dimension in frozen["dimensions"]:
        obligations = [item for item in task["obligations"] if item["dimension_id"] == dimension["id"]]
        require(len(obligations) == 1 and obligations[0]["state"] == "satisfied"
                and bool(obligations[0]["supports"]), "dimension_missing")
        dimensions.append({"id": dimension["id"], "required": dimension["required"],
            "structural_state": "satisfied", "conclusion_indices": [item["index"] for item in conclusions
                if item["dimension_id"] == dimension["id"]], "semantic_acceptance": "pending_real_human_review"})
    missing = [item["id"] for item in checks if not item["body_coverage"]]
    for check in checks:
        check["conclusion_indices"] = [item["index"] for item in conclusions
            if set(item["source_ids"]) & set(check["source_ids"])]
    require(support.read(anchor_path) == anchor_raw, "frozen_anchors_changed")
    require(support.current_source(frozen, guard) == raw_files and binding() == live,
            "source_or_binding_changed_during_reconciliation")
    review = {"kind": "human_review_inputs_not_human_review_receipt",
        "execution_instance": support.INSTANCE, "request_id": syntax_support.REQUEST,
        "binding": consumed["binding"], "candidate_sha256": live["assets"]["candidate"]["sha256"],
        "source_snapshot_id": frozen["report"]["snapshot_id"], "frozen_anchor_path": anchor_path,
        "frozen_anchor_sha256": support.digest(anchor_raw), "summary": candidate["summary"],
        "unknowns": candidate["unknowns"], "conclusions": conclusions, "four_dimensions": dimensions,
        "anchor_checks": checks, "required_checks": anchors["required_checks"],
        "reviewer_kind": "not_recorded", "human_semantic_review": "pending",
        "boundary": "定位与结构通过不是语义审核，模型不能冒充真实人工。"}
    review_asset = support.save(PREFIX + ".human-review-inputs.json", review, guard)
    result = {"status": "blocked_required_anchor_body_missing" if missing else
        "four_dimensions_and_13_anchor_locations_pass_human_review_pending",
        "execution_instance": support.INSTANCE, "request_id": syntax_support.REQUEST,
        "binding": consumed["binding"], "selection_version": VERSION,
        "four_dimensions": dimensions, "anchor_checks": checks, "conclusions": len(conclusions),
        "anchors_located": len(checks) - len(missing), "required_anchors": len(checks),
        "missing_anchor_ids": missing, "review_inputs": review_asset,
        "human_semantic_review": "not_proven", "host_loading": "not_proven",
        "adr_promotions": 0, "archive_operations": 0, "elapsed_seconds": time.monotonic() - guard.started,
        "outer_process_exit": "requires_actual_client_command_receipt"}
    evidence = support.save(PREFIX + ".reconciliation.receipt.json", result, guard)
    print(json.dumps({key: result[key] for key in (
        "status", "conclusions", "anchors_located", "required_anchors", "missing_anchor_ids",
        "human_semantic_review", "elapsed_seconds")} | {"receipt": evidence, "review_inputs": review_asset},
        ensure_ascii=False), flush=True)
    return 2 if missing else 0


def main():
    parser = argparse.ArgumentParser(description="仅当前语法窗口双消费或对账，不准入、不重发")
    parser.add_argument("phase", choices=["consume", "reconcile"])
    arguments = parser.parse_args()
    support.configure_syntax()
    return consume() if arguments.phase == "consume" else reconcile()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
            "reason": str(error)[:240]}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(2)
