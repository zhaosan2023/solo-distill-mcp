"""显式新选择恢复的支撑入口；旧窗口和来源只读，证据独占保存。

cold 每次仅启动一个新离线样本；audit 只在收到真实退出回执后调用。
prepare 不启动业务；在线仍由已有驱动单独执行，人工门禁不放宽。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import time

from schema_v3_selection_oracle import expected_selection
from schema_v3_selection_ingress_oracle import unpack_selection
from solo_distill.distillation import utf8_json
from solo_distill.snapshot_v2 import snapshot_scope
from schema_v3_snapshot_oracle import oracle_scope

INSTANCE = "mainline-a-schema-v3-20261030T095713Z"
PARENT = "verification/" + INSTANCE
SAMPLES = tuple("preflight-coverage-20261102T050022Z-" + str(i) for i in range(1, 4))
VERSION = "question-scout-v2"
REQUEST = INSTANCE + "-live-coverage-20261102T050022Z"
MINIMAL_REQUEST = INSTANCE + "-live-minimal-20261102T020451Z"
MINIMAL_MARKER = "verification/.scratch/" + INSTANCE + ".live-minimal-20261102T020451Z.full.json"
MINIMAL_PREFIX = PARENT + "/phase5-live-minimal-20261102T020451Z."
ACCEPT_PREFIX = PARENT + "/phase5-minimal-acceptance-20261102T020451Z."
MINIMAL_RECEIPT = MINIMAL_PREFIX + "receipt.json"
CONSUME_RECEIPT = ACCEPT_PREFIX + "consume.receipt.json"
RECONCILE_RECEIPT = ACCEPT_PREFIX + "reconciliation.receipt.json"
REGRESSION = PARENT + "/phase5-coverage-driver-regression.receipt.json"


def require_regression(support, receipt):
    for key in ("actual_exit_code", "tests_run", "failed", "errors", "skipped",
                "adr_revision", "execution_contract_revision"):
        support.require(type(receipt.get(key)) is int, "coverage_regression_type:" + key)
    support.require(receipt["actual_exit_code"] == receipt["failed"] == receipt["errors"]
                    == receipt["skipped"] == 0 and receipt["tests_run"] > 0
                    and receipt["adr_revision"] == 14
                    and receipt["execution_contract_revision"] == 15,
                    "coverage_regression_not_pass")


def check_assets(support, assets, prefix):
    from solo_distill.extractor import relative_path
    support.require(type(assets) is dict and 0 < len(assets) <= 32, "coverage_saved_assets")
    total = 0
    for asset in assets.values():
        support.require(type(asset) is dict and type(asset.get("path")) is str
                        and asset["path"].startswith(prefix)
                        and relative_path(asset["path"]) == asset["path"]
                        and type(asset.get("bytes")) is int and 0 <= asset["bytes"] <= 16777216
                        and asset.get("exact_readback") is True, "coverage_asset_fields")
        total += asset["bytes"]
        support.require(total <= 33554432, "coverage_assets_budget")
        raw = support.read(asset["path"], asset["bytes"])
        support.require(len(raw) == asset["bytes"] and support.digest(raw) == asset.get("sha256"),
                        "coverage_asset_changed")


def verify_preservation(support, bound):
    paths = {"previous_minimal_marker_sha256": MINIMAL_MARKER,
             "previous_minimal_receipt_sha256": MINIMAL_RECEIPT,
             "previous_consumption_receipt_sha256": CONSUME_RECEIPT,
             "previous_reconciliation_receipt_sha256": RECONCILE_RECEIPT}
    for key, path in paths.items():
        support.require(support.digest(support.read(path)) == bound.get(key),
                        "coverage_previous_changed:" + key)
    for key, prefix in (("previous_minimal_assets", MINIMAL_PREFIX),
                        ("previous_consumption_assets", ACCEPT_PREFIX),
                        ("previous_review_assets", ACCEPT_PREFIX)):
        check_assets(support, bound.get(key), prefix)


def verify_previous(support, contract):
    current = contract["selection_coverage_recovery_contract"]
    fixed = {"request_id": REQUEST,
             "marker_id": INSTANCE + ".live-coverage-20261102T050022Z",
             "recover_from_request_id": support.ORIGINAL_REQUEST,
             "previous_minimal_request_id": MINIMAL_REQUEST,
             "previous_minimal_receipt": MINIMAL_RECEIPT,
             "previous_consumption_receipt": CONSUME_RECEIPT,
             "previous_reconciliation_receipt": RECONCILE_RECEIPT,
             "process_seconds": 600, "soft_target_seconds": 180,
             "return_reserve_seconds": 15, "max_retries": 3, "max_http_attempts": 4}
    support.require(type(current) is dict and all(type(current.get(k)) is type(v)
                    and current[k] == v for k, v in fixed.items()), "coverage_contract")
    controls = current.get("generation_controls")
    support.require(type(controls) is dict and set(controls) == {"temperature", "max_tokens"}
                    and type(controls["temperature"]) is float and controls["temperature"] == 0.2
                    and type(controls["max_tokens"]) is int and controls["max_tokens"] == 3072
                    and current.get("retryable_reasons") == ["upstream_stream_error", "upstream_timeout"]
                    and current.get("selection_contract", {}).get("explicit_version") == VERSION,
                    "coverage_generation_retry_version")
    marker_raw = support.read(MINIMAL_MARKER, 4096)
    receipt_raw = support.read(MINIMAL_RECEIPT, 65536)
    consume_raw = support.read(CONSUME_RECEIPT, 65536)
    reconcile_raw = support.read(RECONCILE_RECEIPT, 65536)
    marker, receipt, consumed, reconciled = map(json.loads,
        (marker_raw, receipt_raw, consume_raw, reconcile_raw))
    support.require(marker.get("request_id") == MINIMAL_REQUEST
                    and marker.get("recover_from_request_id") == support.ORIGINAL_REQUEST
                    and marker.get("previous_extended_request_id") == INSTANCE + "-live-extended-20261101T233105Z"
                    and marker.get("automatic_replay") is False
                    and type(marker.get("max_retries")) is int and marker["max_retries"] == 3
                    and type(marker.get("process_seconds")) is int and marker["process_seconds"] == 600
                    and marker.get("generation_controls") == controls
                    and marker.get("retryable_reasons") == current["retryable_reasons"],
                    "coverage_previous_marker")
    support.require(receipt.get("execution_instance") == INSTANCE
                    and receipt.get("request_id") == MINIMAL_REQUEST
                    and receipt.get("status") == "online_transport_saved_trusted_consumption_pending"
                    and receipt.get("failures") == []
                    and type(receipt.get("service_exit_code")) is int and receipt["service_exit_code"] == 0
                    and receipt.get("service_cleanup") == "confirmed_natural_exit"
                    and type(receipt.get("business_call_attempts")) is int
                    and receipt["business_call_attempts"] == 1, "coverage_previous_exit")
    old = receipt.get("binding")
    support.require(type(old) is dict
                    and all(item.get("execution_instance") == INSTANCE
                            and item.get("request_id") == MINIMAL_REQUEST
                            and item.get("binding") == old for item in (consumed, reconciled))
                    and consumed.get("status") == "dual_consumption_pass_semantics_pending"
                    and consumed.get("production_and_independent_publication_equal") is True
                    and consumed.get("task_status") == "complete"
                    and consumed.get("live_receipt_sha256") == support.digest(receipt_raw)
                    and reconciled.get("status") == "blocked_required_anchor_body_missing"
                    and type(reconciled.get("missing_anchor_ids")) is list
                    and bool(reconciled["missing_anchor_ids"])
                    and reconciled.get("human_semantic_review") == "not_proven",
                    "coverage_previous_acceptance")
    support.verify_prior_preservation(old)
    for key in ("previous_extended_marker_sha256", "previous_extended_receipt_sha256",
                "previous_extended_postmortem_sha256"):
        support.require(marker.get(key) == old.get(key) and type(old.get(key)) is str,
                        "coverage_previous_extended_link")
    bound = {k: v for k, v in old.items() if k.startswith("original_") or k.startswith("previous_")}
    bound.update(previous_minimal_marker_sha256=support.digest(marker_raw),
                 previous_minimal_receipt_sha256=support.digest(receipt_raw),
                 previous_consumption_receipt_sha256=support.digest(consume_raw),
                 previous_reconciliation_receipt_sha256=support.digest(reconcile_raw),
                 previous_minimal_assets=receipt.get("assets"),
                 previous_consumption_assets=consumed.get("assets"),
                 previous_review_assets={"review": reconciled.get("review_inputs")})
    verify_preservation(support, bound)
    return bound


def verify_marker(support, marker, bound):
    fixed = {"request_id": REQUEST, "recover_from_request_id": support.ORIGINAL_REQUEST,
             "previous_minimal_request_id": MINIMAL_REQUEST, "selection_version": VERSION,
             "automatic_replay": False, "max_retries": 3, "process_seconds": 600}
    support.require(all(type(marker.get(k)) is type(v) and marker[k] == v
                        for k, v in fixed.items()), "coverage_admission_marker")
    for key in ("previous_minimal_marker_sha256", "previous_minimal_receipt_sha256",
                "previous_consumption_receipt_sha256", "previous_reconciliation_receipt_sha256"):
        support.require(type(marker.get(key)) is str and marker[key] == bound.get(key),
                        "coverage_admission_link:" + key)
    controls = marker.get("generation_controls")
    support.require(type(controls) is dict and set(controls) == {"temperature", "max_tokens"}
                    and type(controls["temperature"]) is float and controls["temperature"] == 0.2
                    and type(controls["max_tokens"]) is int and controls["max_tokens"] == 3072
                    and marker.get("retryable_reasons") == ["upstream_stream_error", "upstream_timeout"],
                    "coverage_admission_controls")


def sample_assets(support, sample, guard):
    from solo_distill.preflight_v3 import ARTIFACT_NAMES
    receipt = support.load(PARENT + "/" + sample + "/preflight.receipt.json")
    support.require(receipt.get("status") == "pass" and receipt.get("sample_id") == sample
                    and receipt.get("selection_version") == VERSION
                    and type(receipt.get("service_exit_code")) is int and receipt["service_exit_code"] == 0
                    and receipt.get("service_cleanup") == "confirmed_natural_exit"
                    and receipt.get("gateway_requests") == 0
                    and receipt.get("wire_encoding") == "lz-json-v3-1"
                    and receipt.get("save", {}).get("exact_readback") is True,
                    "coverage_cold_receipt")
    assets = {}
    for name in ARTIFACT_NAMES:
        guard.check()
        raw = support.read(PARENT + "/" + sample + "/" + name)
        support.require(support.digest(raw) == receipt["save"]["artifact_sha256"].get(name),
                        "coverage_cold_asset_changed")
        assets[name] = {"bytes": len(raw), "sha256": support.digest(raw)}
    frozen = support.load(PARENT + "/" + sample + "/frozen-source-and-selection.json")
    oracle = support.load(PARENT + "/" + sample + "/independent-oracle.receipt.json")
    support.require(frozen.get("selection_version") == VERSION
                    and frozen["selection"].get("selection_version") == VERSION
                    and oracle.get("selection_version") == VERSION
                    and oracle.get("production_and_independent_equal") is True
                    and oracle.get("status") == "pass", "coverage_cold_version_or_consumers")
    return receipt, assets, frozen


@snapshot_scope
@oracle_scope
def prepare(support):
    guard = support.Guard(30)
    bound = support.binding()
    regression = support.load(REGRESSION)
    require_regression(support, regression)
    support.require(type(regression.get("tool_results")) is list and bool(regression["tool_results"]),
                    "coverage_regression_actual_results_missing")
    samples = []
    frozen = None
    for sample in SAMPLES:
        receipt, assets, item = sample_assets(support, sample, guard)
        audit_path = PARENT + "/" + sample + ".phase5-audit.receipt.json"
        audit = support.load(audit_path)
        support.require(audit.get("status") == "pass" and type(audit.get("actual_exit_code")) is int
                        and audit["actual_exit_code"] == 0 and audit.get("binding") == bound
                        and audit.get("sample_id") == sample and audit.get("selection_version") == VERSION
                        and type(audit.get("actual_exit_tool_result")) is str
                        and bool(audit["actual_exit_tool_result"])
                        and audit.get("artifact_readback") == assets,
                        "coverage_sample_actual_exit_or_binding")
        if frozen is None:
            frozen = item
        else:
            support.require({k: v for k, v in item.items() if k != "report"}
                            == {k: v for k, v in frozen.items() if k != "report"}
                            and {k: v for k, v in item["report"].items() if k != "observations"}
                            == {k: v for k, v in frozen["report"].items() if k != "observations"},
                            "coverage_inter_sample_conflict")
        samples.append({"sample_id": sample, "audit_path": audit_path, "artifact_readback": assets})
    raw_files = support.current_source(frozen, guard)
    old = support.load(PARENT + "/preflight-1/frozen-source-and-selection.json")
    support.require(frozen["question"] == old["question"] and frozen["dimensions"] == old["dimensions"]
                    and frozen["raw_files_base64"] == old["raw_files_base64"]
                    and {k: v for k, v in frozen["report"].items() if k != "observations"}
                    == {k: v for k, v in old["report"].items() if k != "observations"},
                    "coverage_original_source_or_question_changed")
    messages, post, selection = expected_selection(raw_files, project_id=frozen["project_id"],
        snapshot_id=frozen["report"]["snapshot_id"], question=frozen["question"],
        dimensions=frozen["dimensions"], frozen_coverage=frozen["report"]["coverage"],
        selection_version=VERSION, tick=guard.check)
    support.require(selection == frozen["selection"]
                    and post == support.read(PARENT + "/" + SAMPLES[0] + "/exact-http-post.json"),
                    "coverage_independent_selection")
    post = post[:-1] + b',"temperature":0.2,"max_tokens":3072}'
    support.require(post == support.expected_live_post() and 0 < len(post) <= 150000
                    and selection["witness"]["unique_body_bytes"] <= 98304, "coverage_post_body_budget")
    arguments = {"contract_version": 3, "protocol_version": "2024-11-05", "operation": "live",
        "request_id": REQUEST, "project_id": frozen["project_id"],
        "recover_from_request_id": support.ORIGINAL_REQUEST, "selection_version": VERSION,
        "scenario": "A", "question": frozen["question"], "dimensions": frozen["dimensions"],
        "include": frozen["include"], "started_at_unix_ms": 1, "deadline_unix_ms": 600001,
        "host_timeout_seconds": 600, "return_bytes": 22016, "transport": support.CAPS,
        "wire_encoding": "lz-json-v3-1", "frozen_selection": selection}
    wire, _ = support.live_wire_arguments(arguments, selection, tick=guard.check)
    packed = wire.pop("frozen_selection_lz")
    support.require(unpack_selection(packed, tick=guard.check) == selection, "coverage_independent_ingress")
    wire["compressed_frozen_selection"] = packed
    request = utf8_json({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "solo_distill_knowledge_v3", "arguments": wire}}) + b"\n"
    support.require(len(request) <= 65536, "coverage_complete_request_budget")
    captured = io.StringIO()
    before = support.listener()
    with contextlib.redirect_stdout(captured):
        code = support.gateway_readonly_diagnostic.run()
    health = json.loads(captured.getvalue())
    after = support.listener()
    original_listener = support.load(PARENT + "/health.unattended-20261031T1513Z.receipt.json")["listener_after"]
    support.require(code == 0 and bool(before) and before == after == original_listener
                    and health["results"][0].get("health_reports_ok") is True
                    and all(item.get("http_status") == 200 for item in health["results"]),
                    "coverage_current_gateway_health")
    support.require(support.binding() == bound, "coverage_binding_changed_during_prepare")
    ready = {"status": "ready_not_online_executed", "binding": bound,
        "selection_version": VERSION, "current_samples": samples, "wire_arguments": wire,
        "snapshot_id": frozen["report"]["snapshot_id"], "selection_id": selection["selection_id"],
        "post_bytes": len(post), "post_sha256": support.digest(post),
        "body_utf8_bytes": selection["witness"]["unique_body_bytes"],
        "compressed_selection_bytes": len(utf8_json(packed)), "request_wire_bytes": len(request),
        "listener_inodes": after, "health": health, "business_posts": 0,
        "controller_coverage": "not_proven_platform_task_record_used",
        "elapsed_seconds": time.monotonic() - guard.started}
    evidence = support.save(support.READY, ready, guard)
    print(json.dumps({"status": ready["status"], "evidence": evidence,
        "post_bytes": len(post), "body_utf8_bytes": ready["body_utf8_bytes"],
        "request_wire_bytes": len(request), "elapsed_seconds": time.monotonic() - guard.started}), flush=True)
    return 0


def cold(support, index):
    from mainline_a_schema_v3_cold_chain_20261030 import run_preflight
    from solo_distill.preflight_v3 import PreflightGuard
    guard = PreflightGuard()
    sample = SAMPLES[index - 1]
    request = support.load(PARENT + "/preflight-1.unattended-20261031T1513Z.request.json")
    support.binding()
    now = int(time.time() * 1000)
    request.update(request_id=INSTANCE + "-" + sample, selection_version=VERSION,
                   started_at_unix_ms=now, deadline_unix_ms=now + 60000, host_timeout_seconds=60)
    request_asset = support.save(sample + ".request.json", request, guard)
    result = run_preflight(support.ROOT, request, sample, guard=guard)
    print(json.dumps({"status": result["status"], "sample_id": sample,
        "selection_version": result["selection_version"], "request": request_asset,
        "internal_elapsed_seconds": result["internal_elapsed_seconds"],
        "service_exit_code": result["service_exit_code"], "service_cleanup": result["service_cleanup"]}), flush=True)
    return 0


def audit(support, index, exit_tool_result):
    guard = support.Guard(15)
    bound = support.binding()
    sample = SAMPLES[index - 1]
    receipt, assets, frozen = sample_assets(support, sample, guard)
    support.current_source(frozen, guard)
    support.require(support.binding() == bound, "coverage_audit_binding_changed")
    result = {"status": "pass", "sample_id": sample, "selection_version": VERSION,
        "binding": bound, "actual_exit_code": 0, "actual_exit_tool_result": exit_tool_result,
        "artifact_readback": assets, "internal_elapsed_seconds": receipt["internal_elapsed_seconds"],
        "boundary": "引用客户端实际退出回执；审计保存不重新运行冷预检，不证明在线或人工。"}
    evidence = support.save(sample + ".phase5-audit.receipt.json", result, guard)
    print(json.dumps({"status": "pass", "sample_id": sample, "evidence": evidence}), flush=True)
    return 0


def main():
    import mainline_a_schema_v3_online_20261101 as support
    parser = argparse.ArgumentParser(description="每次仅执行一个新冷样本或其已退出证据审计")
    parser.add_argument("phase", choices=("cold", "audit"))
    parser.add_argument("--sample", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--exit-tool-result")
    args = parser.parse_args()
    if args.phase == "audit" and not args.exit_tool_result:
        parser.error("audit requires an actual successful client exit tool result")
    support.configure_coverage()
    return cold(support, args.sample) if args.phase == "cold" else audit(support, args.sample, args.exit_tool_result)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        import sys
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
                          "reason": str(error)[:256]}), file=sys.stderr, flush=True)
        raise SystemExit(2)
