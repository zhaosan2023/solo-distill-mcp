"""固定候选恢复支撑；复核旧证据，不重跑冷预检、不修改冻结输入。

仅由获准在线驱动显式调用。准入不发业务；逐次证据校验不容错修补来源。
"""
from __future__ import annotations

import contextlib
import io
import json
import time

import mainline_a_schema_v3_coverage_20261102 as coverage
from mainline_a_schema_v3_coverage_failure_diagnostic_20261102 import sse_output
from schema_v3_selection_oracle import expected_selection
from schema_v3_selection_ingress_oracle import unpack_selection
from solo_distill.gateway import candidate_json

INSTANCE = coverage.INSTANCE
PARENT = coverage.PARENT
REQUEST = INSTANCE + "-live-candidate-20261102T070153Z"
MARKER = INSTANCE + ".live-candidate-20261102T070153Z"
PREVIOUS_PREFIX = PARENT + "/phase5-live-coverage-20261102T050022Z."
PATHS = {
    "previous_coverage_marker_sha256": "verification/.scratch/" + INSTANCE
        + ".live-coverage-20261102T050022Z.full.json",
    "previous_coverage_receipt_sha256": PREVIOUS_PREFIX + "receipt.json",
    "previous_coverage_diagnostic_sha256": PARENT
        + "/phase5-coverage-failure-diagnostic-20261102T064856Z.receipt.json",
    "previous_coverage_ready_sha256": PARENT + "/phase5-coverage-ready-20261102T050022Z.json",
}
REGRESSION = PARENT + "/phase5-candidate-driver-regression.receipt.json"
REASON = "invalid_or_unsent_source_id"


def controls_valid(value):
    return (type(value) is dict and set(value) == {"temperature", "max_tokens"}
        and type(value["temperature"]) is float and value["temperature"] == 0.2
        and type(value["max_tokens"]) is int and value["max_tokens"] == 3072)


def verify_preservation(support, bound):
    for key, path in PATHS.items():
        support.require(support.digest(support.read(path, 131072)) == bound.get(key),
                        "candidate_previous_changed:" + key)
    coverage.check_assets(support, bound.get("previous_coverage_assets"), PREVIOUS_PREFIX)


def verify_previous(support, contract):
    current = contract["candidate_recovery_contract"]
    fixed = {
        "request_id": REQUEST, "marker_id": MARKER,
        "recover_from_request_id": support.ORIGINAL_REQUEST,
        "previous_coverage_request_id": coverage.REQUEST,
        "previous_marker": PATHS["previous_coverage_marker_sha256"],
        "previous_receipt": PATHS["previous_coverage_receipt_sha256"],
        "previous_diagnostic": PATHS["previous_coverage_diagnostic_sha256"],
        "previous_ready": PATHS["previous_coverage_ready_sha256"],
        "process_seconds": 600, "soft_target_seconds": 180,
        "return_reserve_seconds": 15, "max_total_retries": 3, "max_http_attempts": 4,
        "candidate_retryable_reason": REASON,
    }
    support.require(type(current) is dict and all(type(current.get(k)) is type(v)
        and current[k] == v for k, v in fixed.items()), "candidate_contract")
    support.require(controls_valid(current.get("generation_controls"))
        and current.get("transport_retryable_reasons") == ["upstream_stream_error", "upstream_timeout"],
        "candidate_controls_and_retries")
    bound = coverage.verify_previous(support, contract)
    raws = {key: support.read(path, 131072) for key, path in PATHS.items()}
    marker, receipt, diagnostic, ready = map(json.loads, raws.values())
    coverage.verify_marker(support, marker, bound)
    support.require(type(receipt) is dict and receipt.get("status") == "blocked"
        and receipt.get("execution_instance") == INSTANCE and receipt.get("request_id") == coverage.REQUEST
        and type(receipt.get("service_exit_code")) is int and receipt["service_exit_code"] == 2
        and receipt.get("service_cleanup") == "confirmed_nonzero_exit"
        and type(receipt.get("business_call_attempts")) is int and receipt["business_call_attempts"] == 1
        and type(ready) is dict and ready.get("status") == "ready_not_online_executed"
        and ready.get("selection_version") == coverage.VERSION
        and type(ready.get("binding")) is dict and ready["binding"] == receipt.get("binding")
        and receipt.get("listener_after") == ready.get("listener_inodes"), "candidate_previous_window")
    support.require(all(ready["binding"].get(key) == value for key, value in bound.items()),
                    "candidate_previous_binding")
    support.require(type(diagnostic) is dict
        and diagnostic.get("status") == "failed_window_diagnosed_not_business_acceptance"
        and diagnostic.get("execution_instance") == INSTANCE
        and diagnostic.get("request_id") == coverage.REQUEST
        and all(type(diagnostic.get(k)) is int and diagnostic[k] == v for k, v in (
            ("actual_online_outer_exit_code", 2), ("service_exit_code", 2), ("actual_diagnostic_exit_code", 0)))
        and diagnostic.get("service_cleanup") == "confirmed_nonzero_exit"
        and diagnostic.get("online_receipt") == PATHS["previous_coverage_receipt_sha256"]
        and diagnostic.get("online_receipt_sha256") == support.digest(raws["previous_coverage_receipt_sha256"])
        and diagnostic.get("failure_reason") == "v3_task_invalid_or_unsent_conclusion"
        and all(type(diagnostic.get(k)) is str and diagnostic[k].strip()
                for k in ("online_tool_result", "diagnostic_tool_result"))
        and diagnostic.get("listener_unchanged") is True
        and diagnostic.get("delivery_saved") is False and diagnostic.get("stdio_saved") is False,
        "candidate_actual_previous_exit")
    assets = receipt.get("assets")
    coverage.check_assets(support, assets, PREVIOUS_PREFIX)
    support.require({"request", "admission", "failure", "transport_attempt_1"} <= set(assets)
        and not {"delivery", "stdio"} & set(assets)
        and type(diagnostic.get("saved_assets_verified")) is int
        and diagnostic["saved_assets_verified"] == len(assets)
        and type(diagnostic.get("saved_asset_bytes")) is int
        and diagnostic["saved_asset_bytes"] == sum(asset["bytes"] for asset in assets.values())
        and support.read(assets["admission"]["path"], 4096) == raws["previous_coverage_marker_sha256"],
        "candidate_previous_assets_binding")
    wire = ready.get("wire_arguments")
    support.require(type(wire) is dict and wire.get("request_id") == coverage.REQUEST
        and wire.get("recover_from_request_id") == support.ORIGINAL_REQUEST
        and wire.get("selection_version") == coverage.VERSION, "candidate_previous_inputs")
    bound.update({key: support.digest(raw) for key, raw in raws.items()})
    bound["previous_coverage_assets"] = assets
    verify_preservation(support, bound)
    return bound


def verify_marker(support, marker, bound):
    fixed = {"request_id": REQUEST, "recover_from_request_id": support.ORIGINAL_REQUEST,
        "previous_coverage_request_id": coverage.REQUEST, "selection_version": coverage.VERSION,
        "automatic_replay": False, "max_retries": 3, "max_total_retries": 3,
        "process_seconds": 600, "candidate_retryable_reason": REASON}
    support.require(type(marker) is dict and all(type(marker.get(k)) is type(v)
        and marker[k] == v for k, v in fixed.items()), "candidate_admission_marker")
    support.require(controls_valid(marker.get("generation_controls"))
        and marker.get("retryable_reasons") == ["upstream_stream_error", "upstream_timeout"],
        "candidate_admission_controls")
    for key in PATHS:
        support.require(type(marker.get(key)) is str and marker[key] == bound.get(key),
                        "candidate_admission_link:" + key)


def require_regression(support, receipt):
    for key in ("actual_exit_code", "tests_run", "failed", "errors", "skipped",
                "adr_revision", "execution_contract_revision"):
        support.require(type(receipt.get(key)) is int, "candidate_regression_type:" + key)
    support.require(receipt["actual_exit_code"] == receipt["failed"] == receipt["errors"]
        == receipt["skipped"] == 0 and receipt["tests_run"] > 0
        and receipt["adr_revision"] == 15 and receipt["execution_contract_revision"] == 16
        and type(receipt.get("tool_results")) is list and bool(receipt["tool_results"])
        and all(type(item) is str and item.strip() for item in receipt["tool_results"]),
        "candidate_regression_not_pass")


def prepare(support, *, regression_validator=None):
    guard = support.Guard(30)
    bound = support.binding()
    if regression_validator is None:
        require_regression(support, support.load(REGRESSION))
    else:
        regression_validator(support)
    previous = support.load(PATHS["previous_coverage_ready_sha256"])
    samples, frozen = [], None
    for sample in coverage.SAMPLES:
        _, assets, item = coverage.sample_assets(support, sample, guard)
        audit_path = PARENT + "/" + sample + ".phase5-audit.receipt.json"
        audit = support.load(audit_path)
        support.require(audit.get("status") == "pass"
            and type(audit.get("actual_exit_code")) is int and audit["actual_exit_code"] == 0
            and audit.get("binding") == previous["binding"]
            and audit.get("sample_id") == sample and audit.get("selection_version") == coverage.VERSION
            and type(audit.get("actual_exit_tool_result")) is str and audit["actual_exit_tool_result"].strip()
            and audit.get("artifact_readback") == assets, "candidate_original_cold_evidence")
        if frozen is not None:
            support.require({k: v for k, v in item.items() if k != "report"}
                == {k: v for k, v in frozen.items() if k != "report"}
                and {k: v for k, v in item["report"].items() if k != "observations"}
                == {k: v for k, v in frozen["report"].items() if k != "observations"},
                "candidate_inter_sample_conflict")
        else:
            frozen = item
        samples.append({"sample_id": sample, "audit_path": audit_path, "artifact_readback": assets})
    raw_files = support.current_source(frozen, guard)
    old = support.load(PARENT + "/preflight-1/frozen-source-and-selection.json")
    support.require(frozen["question"] == old["question"] and frozen["dimensions"] == old["dimensions"]
        and frozen["raw_files_base64"] == old["raw_files_base64"]
        and {k: v for k, v in frozen["report"].items() if k != "observations"}
        == {k: v for k, v in old["report"].items() if k != "observations"}, "candidate_original_source")
    _, post, selection = expected_selection(raw_files, project_id=frozen["project_id"],
        snapshot_id=frozen["report"]["snapshot_id"], question=frozen["question"],
        dimensions=frozen["dimensions"], frozen_coverage=frozen["report"]["coverage"],
        selection_version=coverage.VERSION, tick=guard.check)
    support.require(selection == frozen["selection"]
        and post == support.read(PARENT + "/" + coverage.SAMPLES[0] + "/exact-http-post.json"),
        "candidate_independent_frozen_selection")
    post = post[:-1] + b',"temperature":0.2,"max_tokens":3072}'
    support.require(post == support.expected_live_post() and len(post) == previous["post_bytes"]
        and support.digest(post) == previous["post_sha256"] and 0 < len(post) <= 150000
        and selection["witness"]["unique_body_bytes"] <= 98304, "candidate_exact_post")
    wire = dict(previous["wire_arguments"])
    support.require(unpack_selection(wire["compressed_frozen_selection"], tick=guard.check) == selection,
                    "candidate_independent_ingress")
    wire.update(request_id=support.REQUEST_ID, started_at_unix_ms=1, deadline_unix_ms=600001)
    request = support.utf8_json({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "solo_distill_knowledge_v3", "arguments": wire}}) + b"\n"
    support.require(len(request) <= 65536, "candidate_complete_request_budget")
    captured = io.StringIO()
    before = support.listener()
    with contextlib.redirect_stdout(captured):
        code = support.gateway_readonly_diagnostic.run()
    health = json.loads(captured.getvalue())
    after = support.listener()
    support.require(code == 0 and bool(before) and before == after == previous["listener_inodes"]
        and health["results"][0].get("health_reports_ok") is True
        and all(item.get("http_status") == 200 for item in health["results"]), "candidate_gateway_health")
    support.require(support.binding() == bound, "candidate_prepare_binding_changed")
    ready = {**previous, "binding": bound, "wire_arguments": wire, "current_samples": samples,
        "listener_inodes": after, "health": health, "request_wire_bytes": len(request),
        "business_posts": 0, "cold_preflight_runs": 0, "prior_test_runs": 0,
        "controller_coverage": "not_proven_platform_task_record_used",
        "elapsed_seconds": time.monotonic() - guard.started}
    evidence = support.save(support.READY, ready, guard)
    print(json.dumps({"status": ready["status"], "evidence": evidence,
        "post_bytes": len(post), "request_wire_bytes": len(request),
        "elapsed_seconds": time.monotonic() - guard.started}), flush=True)
    return 0


def verify_attempt(support, receipt, observation, post, captured, guard, *, request_id=None):
    """认证原传输与整份拒收记录；保存副本，不把拒收候选视为成功。"""
    request_id = REQUEST if request_id is None else request_id
    guard.check()
    number = observation["attempt_index"]
    identity = request_id + ".gateway-attempt-" + str(number)
    raw = support.read("verification/.scratch/" + identity + ".full.json", 16777216)
    attempt = json.loads(raw)
    plain = {k: v for k, v in observation.items()
             if k not in {"candidate_error_reason", "candidate_rejection_id"}}
    support.require(attempt.get("request_id") == request_id
        and attempt.get("recover_from_request_id") == support.ORIGINAL_REQUEST
        and attempt.get("post_utf8", "").encode("utf-8") == post
        and attempt.get("post_sha256") == support.digest(post)
        and attempt.get("observation") == plain, "candidate_transport_record")
    receipt["assets"]["transport_attempt_" + str(number)] = support.save_or_verify_asset(
        receipt, "transport_attempt_" + str(number), support.LIVE + ".transport-attempt-"
        + str(number) + ".json", raw, guard)
    rejected = observation.get("candidate_error_reason")
    if observation.get("error_reason") is not None:
        support.require(rejected is None and attempt.get("output") is None
            and attempt.get("status") == "transport_failed_not_business_acceptance",
            "candidate_failed_transport_shape")
        return
    support.require(attempt.get("status") == "transport_completed_not_business_acceptance",
                    "candidate_completed_transport_shape")
    support.require_gateway_completion(observation, captured)
    output, finishes, done, errors = sse_output(captured)
    support.require(output == attempt.get("output") and finishes == ["stop"] and done and not errors,
                    "candidate_original_sse_output")
    if rejected is None:
        support.require("candidate_rejection_id" not in observation, "candidate_untyped_rejection")
        return
    rejection_id = request_id + ".candidate-rejection-" + str(number)
    support.require(rejected == REASON and observation.get("candidate_rejection_id") == rejection_id,
                    "candidate_typed_rejection")
    rejection_raw = support.read("verification/.scratch/" + rejection_id + ".full.json", 16777216)
    rejection = json.loads(rejection_raw)
    candidate = candidate_json(output)
    payload = json.loads(json.loads(post)["messages"][-1]["content"])
    dimensions = {item["id"] for item in payload["dimensions"]}
    allowed = {key for item in payload["sources"] for key in item["alias_source_ids"]}
    support.require(type(candidate) is dict and set(candidate) == {"summary", "conclusions", "unknowns"}
        and type(candidate["summary"]) is str and type(candidate["conclusions"]) is list
        and type(candidate["unknowns"]) is list
        and all(type(item) is str for item in candidate["unknowns"]), "candidate_rejected_fields")
    invalid = []
    for index, conclusion in enumerate(candidate["conclusions"]):
        guard.check()
        support.require(type(conclusion) is dict and set(conclusion) == {"dimension_id", "text", "source_ids"}
            and type(conclusion["dimension_id"]) is str and conclusion["dimension_id"] in dimensions
            and type(conclusion["text"]) is str and conclusion["text"].strip()
            and type(conclusion["source_ids"]) is list and bool(conclusion["source_ids"])
            and all(type(key) is str and key.strip() for key in conclusion["source_ids"])
            and len(set(conclusion["source_ids"])) == len(conclusion["source_ids"]),
            "candidate_nonretryable_shape")
        invalid.extend([[index, key] for key in conclusion["source_ids"] if key not in allowed])
    fixed = {"contract_version": 3, "request_id": request_id,
        "status": "candidate_rejected_not_business_acceptance", "reason": REASON,
        "attempt_index": number, "transport_attempt_id": identity,
        "post_sha256": support.digest(post), "absolute_deadline": observation["absolute_deadline"]}
    support.require(type(rejection) is dict and all(type(rejection.get(k)) is type(v)
        and rejection[k] == v for k, v in fixed.items())
        and bool(invalid) and rejection.get("candidate") == candidate
        and rejection.get("invalid_references") == invalid, "candidate_whole_rejection_binding")
    receipt["assets"]["candidate_rejection_" + str(number)] = support.save_or_verify_asset(
        receipt, "candidate_rejection_" + str(number), support.LIVE + ".candidate-rejection-"
        + str(number) + ".json", rejection_raw, guard)
    guard.check()
