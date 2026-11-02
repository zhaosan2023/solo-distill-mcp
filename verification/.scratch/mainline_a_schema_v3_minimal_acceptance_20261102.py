"""本次真实在线产物的包外双消费与锚点定位；不重发、不冒充人工。

consume 与 reconcile 分别运行于独立 30 秒窗口。只读取获准来源，
独占保存本实例验收证据；不启动服务、联网、转正、归档或写事实基线。
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import threading
import time

import mainline_a_schema_v3_online_20261101 as support
from schema_v3_selection_oracle import expected_selection
from schema_v3_snapshot_oracle import oracle_scope
from schema_v3_wire_oracle import receive as independent_receive
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.consumer_v3 import consume_frame
from solo_distill.extractor import load_projects
from solo_distill.gateway import candidate_json, strict_json
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.wire_v3 import Admission

PREFIX = "phase5-minimal-acceptance-20261102T020451Z"
LIVE_RECEIPT_SHA256 = "e66b72055bea30e124062032e99a196dcf4c763b796a5a8023ba72dc9a57c315"


def require(condition, reason):
    if not condition:
        raise ValueError("minimal_acceptance_" + reason)


def load(path):
    return strict_json(support.read(path).decode("utf-8"))


def checked_asset(asset):
    require(type(asset) is dict and asset.get("exact_readback") is True
            and type(asset.get("path")) is str
            and asset["path"].startswith(support.PARENT + "/")
            and type(asset.get("bytes")) is int
            and 0 <= asset["bytes"] <= 33554432, "asset_fields")
    raw = support.read(asset["path"], maximum=asset["bytes"])
    require(len(raw) == asset["bytes"] and support.digest(raw) == asset.get("sha256"),
            "asset_changed")
    return raw


def binding():
    """已消耗在线接纳不再调用准入入口；复核真实回执的原绑定。"""
    raw = support.read(support.PARENT + "/" + support.LIVE + ".receipt.json")
    require(support.digest(raw) == LIVE_RECEIPT_SHA256, "live_receipt_changed")
    receipt = strict_json(raw.decode("utf-8"))
    require(receipt["status"] == "online_transport_saved_trusted_consumption_pending"
            and receipt["failures"] == []
            and receipt["execution_instance"] == support.INSTANCE
            and receipt["request_id"] == support.REQUEST_ID
            and type(receipt["service_exit_code"]) is int
            and receipt["service_exit_code"] == 0
            and receipt["service_cleanup"] == "confirmed_natural_exit",
            "live_not_complete")
    bound = receipt["binding"]
    paths = {"plan_sha256": support.PLAN, "execution_contract_sha256": support.CONTRACT,
             "adr_sha256": "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md",
             "projects_sha256": "config/projects.json", "policy_sha256": "config/distillation.json"}
    for key, path in paths.items():
        require(support.digest(support.read(path)) == bound[key], "binding_changed:" + key)
    require(bound["plan_contract_version"] == 6 and bound["adr_revision"] == 13
            and bound["execution_contract_revision"] == 14
            and bound["session_id"] is None, "binding_version")
    support.verify_prior_preservation(bound)
    return receipt


def reports_equal_except_scan_elapsed(left, right):
    """仅允许独立扫描计时变化；不排除其他观察、字段或来源。"""
    if type(left) is not dict or type(right) is not dict:
        return False
    a, b = left.get("observations"), right.get("observations")
    if type(a) is not dict or type(b) is not dict:
        return False
    for observation in (a, b):
        elapsed = observation.get("elapsed_seconds")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            return False
    return ({k: v for k, v in left.items() if k != "observations"}
            == {k: v for k, v in right.items() if k != "observations"}
            and {k: v for k, v in a.items() if k != "elapsed_seconds"}
            == {k: v for k, v in b.items() if k != "elapsed_seconds"})


@snapshot_scope
@oracle_scope
def consume():
    guard = support.Guard(30)
    receipt = binding()
    assets = {key: checked_asset(value) for key, value in receipt["assets"].items()}
    guard.check()
    arguments = strict_json(assets["request"].decode("utf-8"))
    trace = strict_json(assets["trace"].decode("utf-8"))
    candidate = strict_json(assets["candidate"].decode("utf-8"))
    outputs = strict_json(assets["model_outputs"].decode("utf-8"))
    require(arguments["request_id"] == trace["request_id"] == support.REQUEST_ID
            and arguments["operation"] == trace["operation"] == "live"
            and trace["failures"] == []
            and type(outputs) is list and len(outputs) == 1
            and type(outputs[0]) is str and bool(outputs[0].strip())
            and outputs == trace["model_outputs"]
            and candidate_json(outputs[0]) == candidate == trace["candidate"],
            "external_generation_or_request_conflict")
    require(arguments["transport"] == support.CAPS
            and arguments["wire_encoding"] == "lz-json-v3-1"
            and arguments["return_bytes"] == 22016, "transport_contract")
    frozen = load(support.PARENT + "/preflight-1/frozen-source-and-selection.json")
    project = load_projects("config/projects.json")[arguments["project_id"]]
    prepared = prepare_snapshot(project=project, include=tuple(frozen["include"]),
        question=frozen["question"], dimensions=frozen["dimensions"],
        deadline=guard.deadline, cancelled=threading.Event(),
        frozen_selection=frozen["selection"], tick=guard.check)
    require(arguments["question"] == frozen["question"]
            and arguments["dimensions"] == frozen["dimensions"]
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
        frozen_coverage=prepared.report["coverage"], tick=guard.check)
    require(selection == prepared.selection and messages == prepared.messages
            and legacy_post == prepared.post, "independent_selection_conflict")
    post = legacy_post[:-1] + b',"temperature":0.2,"max_tokens":3072}'
    require(post == assets["post"] == trace["post_utf8"].encode("utf-8")
            and support.digest(post) == trace["post_sha256"], "external_post_conflict")
    attempts = trace["gateway_requests"]
    require(1 <= len(attempts) <= 4, "bounded_attempts")
    for index, attempt in enumerate(attempts):
        guard.check()
        raw = base64.b64decode(attempt["raw_sse_base64"], validate=True)
        require(raw == assets["sse_attempt_" + str(index + 1)]
                and len(raw) == attempt["raw_sse_bytes"]
                and support.digest(raw) == attempt["raw_sse_sha256"]
                and attempt["message_bytes"] == len(post)
                and attempt["message_sha256"] == support.digest(post)
                and attempt["absolute_deadline"] == attempts[0]["absolute_deadline"]
                and attempt["attempt_index"] == index + 1
                and attempt["client_auto_retries"] == index, "attempt_binding")
        if index < len(attempts) - 1:
            require(support.retry_allowed(attempt), "nonretryable_attempt")
    require(assets["sse"] == assets["sse_attempt_" + str(len(attempts))], "final_sse_conflict")
    support.require_gateway_completion(attempts[-1], assets["sse"])
    trusted = {"project_id": project.project_id, "snapshot_id": prepared.report["snapshot_id"],
        "required_core_unit_ids": selection["core_unit_ids"], "selection_id": selection["selection_id"],
        "request_id": support.REQUEST_ID, "question": frozen["question"],
        "dimensions": frozen["dimensions"], "post": post,
        "frozen_coverage": prepared.report["coverage"], "failures": [], "tick": guard.check}
    admission = Admission("2024-11-05", "A", 3, 22016,
        TransportCapabilities.from_dict(support.CAPS), wire_encoding="lz-json-v3-1")
    begin = time.monotonic()
    production = consume_frame(assets["stdio"], prepared.raw_files,
        admission=admission, candidate=candidate, **trusted)
    production_seconds = time.monotonic() - begin
    begin = time.monotonic()
    independent = independent_receive(assets["stdio"], prepared.raw_files, support.CAPS,
        protocol_version="2024-11-05", response_id=3, return_bytes=22016,
        wire_encoding="lz-json-v3-1", candidate=candidate, **trusted)
    independent_seconds = time.monotonic() - begin
    publication = production.publication
    require(publication == independent["publication"]
            and production.status == independent["task_status"] == "complete",
            "dual_consumers_or_required_task_status")
    delivery = strict_json(assets["delivery"].decode("utf-8"))
    require(delivery["written"] == len(assets["stdio"])
            and delivery["frame_sha256"] == support.digest(assets["stdio"])
            and delivery["status"] == "frame_written_not_host_consumption", "delivery_binding")
    prepared.verify_sources()
    require(binding() == receipt, "binding_changed_during_consumption")
    saved = {}
    for key, suffix, value in (
            ("publication", "publication.json", publication),
            ("core", "sealed-core.json", publication["core"]),
            ("package", "knowledge-package.json", publication["core"]["package"])):
        saved[key] = support.save(PREFIX + "." + suffix, value, guard)
    prepared.verify_sources()
    result = {"status": "dual_consumption_pass_semantics_pending",
        "execution_instance": support.INSTANCE, "request_id": support.REQUEST_ID,
        "binding": receipt["binding"], "live_receipt_sha256": LIVE_RECEIPT_SHA256,
        "actual_online_exit_tool_result": "call_c2cfad3e04b3355f3e6814da",
        "selection_independently_recomputed": True,
        "production_and_independent_publication_equal": True,
        "task_status": production.status, "source_files": len(prepared.raw_files),
        "production_seconds": production_seconds, "independent_seconds": independent_seconds,
        "physical_limits": receipt["physical_limits"], "actual_http_attempts": len(attempts),
        "assets": saved, "elapsed_seconds": time.monotonic() - guard.started,
        "human_review": "not_proven", "host_loading": "not_proven",
        "shared_parser_risk": "python_standard_library_ast",
        "outer_process_exit": "requires_actual_client_command_receipt"}
    evidence = support.save(PREFIX + ".consume.receipt.json", result, guard)
    print(json.dumps({"status": result["status"], "receipt": evidence,
        "task_status": result["task_status"], "actual_http_attempts": len(attempts),
        "source_files": result["source_files"], "production_seconds": production_seconds,
        "independent_seconds": independent_seconds,
        "elapsed_seconds": time.monotonic() - guard.started}, ensure_ascii=False), flush=True)
    return 0


def locate_anchors(syntax, frozen):
    require(type(frozen) is dict and frozen.get("version") == 1
            and type(frozen.get("anchors")) is list and len(frozen["anchors"]) == 13
            and len({item["id"] for item in frozen["anchors"]}) == 13, "frozen_anchor_contract")
    sources = []
    for source in syntax["sources"]:
        unit = syntax["units"][source["unit"]]
        sources.append({"id": source["id"], "path": syntax["files"][source["file"]]["path"],
            "symbol": unit["qualified_name"], "start": unit["start"][0], "end": unit["end"][0]})
    checks = []
    for anchor in frozen["anchors"]:
        matches = [source for source in sources
            if source["path"] == anchor["path"] and source["symbol"] == anchor["symbol"]
            and source["start"] <= anchor["lines"][0] and source["end"] >= anchor["lines"][1]]
        checks.append({**anchor, "body_coverage": bool(matches),
            "source_ids": [source["id"] for source in matches],
            "semantic_support": "requires_real_human_review"})
    return sources, checks


def reconcile():
    guard = support.Guard(30)
    live = binding()
    consumed = load(support.PARENT + "/" + PREFIX + ".consume.receipt.json")
    require(consumed["status"] == "dual_consumption_pass_semantics_pending"
            and consumed["binding"] == live["binding"]
            and consumed["live_receipt_sha256"] == LIVE_RECEIPT_SHA256
            and consumed["production_and_independent_publication_equal"] is True,
            "consumption_evidence")
    publication = strict_json(checked_asset(consumed["assets"]["publication"]).decode("utf-8"))
    require(publication["core"] == strict_json(checked_asset(consumed["assets"]["core"]).decode("utf-8"))
            and publication["core"]["package"] == strict_json(
                checked_asset(consumed["assets"]["package"]).decode("utf-8")), "consumption_assets")
    frozen = load(support.PARENT + "/preflight-1/frozen-source-and-selection.json")
    raw_files = support.current_source(frozen, guard)
    candidate = strict_json(checked_asset(live["assets"]["candidate"]).decode("utf-8"))
    task = publication["task_evidence"]
    require(task["status"] == publication["status"] == "complete"
            and task["summary"] == candidate["summary"]
            and task["unknowns"] == candidate["unknowns"]
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
                and [reference["id"] for reference in fact["sources"]] == item["source_ids"],
                "conclusion_preservation")
        located = [by_id[key] for key in item["source_ids"]]
        excerpts = []
        for source in located:
            lines = raw_files[source["path"]].decode("utf-8").splitlines(keepends=True)
            require(1 <= source["start"] <= source["end"] <= len(lines), "source_line_range")
            text = "".join(lines[source["start"] - 1:source["end"]])
            excerpts.append({**source, "source_excerpt": text,
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
            "structural_state": "satisfied", "conclusion_indices": [item["index"]
                for item in conclusions if item["dimension_id"] == dimension["id"]],
            "semantic_acceptance": "pending_real_human_review"})
    # 失败也保存完整定位证据；必需锚点缺失仍返回非零，不推进后续阶段。
    missing_anchors = [item["id"] for item in checks if not item["body_coverage"]]
    located_count = sum(bool(item["body_coverage"]) for item in checks)
    for check in checks:
        check["conclusion_indices"] = [item["index"] for item in conclusions
            if set(item["source_ids"]) & set(check["source_ids"])]
    require(support.read(anchor_path) == anchor_raw, "frozen_anchors_changed")
    require(support.current_source(frozen, guard) == raw_files and binding() == live,
            "source_or_binding_changed_during_reconciliation")
    review = {"kind": "human_review_inputs_not_human_review_receipt",
        "execution_instance": support.INSTANCE, "request_id": support.REQUEST_ID,
        "binding": consumed["binding"], "candidate_sha256": live["assets"]["candidate"]["sha256"],
        "source_snapshot_id": frozen["report"]["snapshot_id"],
        "frozen_anchor_path": anchor_path, "frozen_anchor_sha256": support.digest(anchor_raw),
        "summary": candidate["summary"], "unknowns": candidate["unknowns"],
        "conclusions": conclusions, "four_dimensions": dimensions, "anchor_checks": checks,
        "required_checks": anchors["required_checks"],
        "reviewer_kind": "not_recorded", "human_semantic_review": "pending",
        "boundary": "定位和结构核对不证明语义；逐结论及13项锚点须真实人工复读。"}
    review_asset = support.save(PREFIX + ".human-review-inputs.json", review, guard)
    status = ("blocked_required_anchor_body_missing" if missing_anchors else
              "four_dimensions_and_13_anchor_locations_pass_human_review_pending")
    result = {"status": status,
        "execution_instance": support.INSTANCE, "request_id": support.REQUEST_ID,
        "binding": consumed["binding"], "four_dimensions": dimensions,
        "anchor_checks": checks, "conclusions": len(conclusions), "anchors_located": located_count,
        "required_anchors": len(checks), "missing_anchor_ids": missing_anchors,
        "prior_failed_command_result": "call_c364db234b810108645d0835",
        "review_inputs": review_asset, "human_semantic_review": "not_proven",
        "host_loading": "not_proven", "adr_promotions": 0, "archive_operations": 0,
        "elapsed_seconds": time.monotonic() - guard.started,
        "outer_process_exit": "requires_actual_client_command_receipt"}
    evidence = support.save(PREFIX + ".reconciliation.receipt.json", result, guard)
    print(json.dumps({"status": result["status"], "receipt": evidence,
        "review_inputs": review_asset, "dimensions": len(dimensions), "anchors_located": located_count,
        "required_anchors": len(checks), "missing_anchors": [
            {key: item[key] for key in ("id", "path", "symbol", "lines")}
            for item in checks if not item["body_coverage"]],
        "conclusions": len(conclusions), "human_semantic_review": "not_proven",
        "elapsed_seconds": time.monotonic() - guard.started}, ensure_ascii=False), flush=True)
    return 2 if missing_anchors else 0


def main():
    parser = argparse.ArgumentParser(description="显式选择双消费或锚点定位；不自动串联后续阶段")
    parser.add_argument("phase", choices=["consume", "reconcile"])
    arguments = parser.parse_args()
    support.configure_minimal()
    return consume() if arguments.phase == "consume" else reconcile()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
            "reason": str(error)[:240]}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(2)
