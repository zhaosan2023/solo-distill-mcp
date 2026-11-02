"""固定语法恢复支撑：只消费获准新命名空间，不修补或回灌输出。

准入复核不发业务；历史资产只读，真实在线及人工验收另行执行。
"""
from __future__ import annotations

import json

import mainline_a_schema_v3_candidate_20261102 as candidate_support
from mainline_a_schema_v3_coverage_failure_diagnostic_20261102 import sse_output
from schema_v3_candidate_syntax_oracle import SyntaxRejection, parse_candidate

INSTANCE = candidate_support.INSTANCE
PARENT = candidate_support.PARENT
REQUEST = INSTANCE + "-live-syntax-20261102T085352Z"
MARKER = INSTANCE + ".live-syntax-20261102T085352Z"
PREFIX = PARENT + "/phase5-live-candidate-20261102T070153Z."
PATHS = {
    "previous_candidate_marker_sha256": "verification/.scratch/" + candidate_support.MARKER + ".full.json",
    "previous_candidate_receipt_sha256": PREFIX + "receipt.json",
    "previous_candidate_diagnostic_sha256": PARENT + "/phase5-candidate-failure-diagnostic-20261102T080521Z.receipt.json",
    "previous_candidate_ready_sha256": PARENT + "/phase5-candidate-ready-20261102T070153Z.json",
}
REASONS = [candidate_support.REASON, SyntaxRejection.reason]
REGRESSIONS = [PARENT + "/" + name for name in (
    "phase5-syntax-independent-controls.receipt.json",
    "phase5-syntax-recovery-regression.receipt.json",
    "phase5-syntax-admission-regression.receipt.json",
    "phase5-syntax-driver-regression.receipt.json",
    "phase5-syntax-affected-regression.receipt.json",
)]


def verify_preservation(support, bound):
    for key, path in PATHS.items():
        support.require(support.digest(support.read(path, 131072)) == bound.get(key),
                        "syntax_previous_changed:" + key)
    candidate_support.coverage.check_assets(support, bound.get("previous_candidate_assets"), PREFIX)


def verify_previous(support, contract):
    current = contract["syntax_recovery_contract"]
    fixed = {
        "request_id": REQUEST, "marker_id": MARKER,
        "recover_from_request_id": support.ORIGINAL_REQUEST,
        "previous_candidate_request_id": candidate_support.REQUEST,
        "previous_marker": PATHS["previous_candidate_marker_sha256"],
        "previous_receipt": PATHS["previous_candidate_receipt_sha256"],
        "previous_diagnostic": PATHS["previous_candidate_diagnostic_sha256"],
        "previous_ready": PATHS["previous_candidate_ready_sha256"],
        "process_seconds": 600, "soft_target_seconds": 180,
        "return_reserve_seconds": 15, "max_total_retries": 3, "max_http_attempts": 4,
    }
    support.require(type(current) is dict and all(type(current.get(k)) is type(v)
        and current[k] == v for k, v in fixed.items()), "syntax_contract")
    support.require(candidate_support.controls_valid(current.get("generation_controls"))
        and current.get("transport_retryable_reasons") == ["upstream_stream_error", "upstream_timeout"]
        and current.get("candidate_retryable_reasons") == REASONS, "syntax_controls_and_retries")
    bound = support.verify_minimal_previous(contract)
    candidate_bound = candidate_support.verify_previous(support, contract)
    support.require(all(bound[key] == value for key, value in candidate_bound.items()
        if key in bound), "syntax_original_chain_binding_conflict")
    bound.update(candidate_bound)
    raws = {key: support.read(path, 131072) for key, path in PATHS.items()}
    marker, receipt, diagnostic, ready = map(json.loads, raws.values())
    candidate_support.verify_marker(support, marker, bound)
    support.require(type(receipt) is dict and receipt.get("status") == "blocked"
        and receipt.get("execution_instance") == INSTANCE
        and receipt.get("request_id") == candidate_support.REQUEST
        and type(receipt.get("service_exit_code")) is int and receipt["service_exit_code"] == 2
        and receipt.get("service_cleanup") == "confirmed_nonzero_exit"
        and type(receipt.get("business_call_attempts")) is int and receipt["business_call_attempts"] == 1
        and type(ready) is dict and ready.get("status") == "ready_not_online_executed"
        and ready.get("selection_version") == candidate_support.coverage.VERSION
        and type(ready.get("binding")) is dict and ready["binding"] == receipt.get("binding")
        and receipt.get("listener_after") == ready.get("listener_inodes"), "syntax_previous_window")
    support.require(all(ready["binding"].get(key) == value for key, value in bound.items()),
                    "syntax_previous_binding")
    support.require(type(diagnostic) is dict
        and diagnostic.get("status") == "failed_window_diagnosed_not_business_acceptance"
        and diagnostic.get("execution_instance") == INSTANCE
        and diagnostic.get("request_id") == candidate_support.REQUEST
        and all(type(diagnostic.get(k)) is int and diagnostic[k] == v for k, v in (
            ("actual_online_outer_exit_code", 2), ("service_exit_code", 2), ("actual_diagnostic_exit_code", 0)))
        and diagnostic.get("service_cleanup") == "confirmed_nonzero_exit"
        and diagnostic.get("online_receipt") == PATHS["previous_candidate_receipt_sha256"]
        and diagnostic.get("online_receipt_sha256") == support.digest(raws["previous_candidate_receipt_sha256"])
        and diagnostic.get("failure_error_type") == "JSONDecodeError"
        and all(type(diagnostic.get(k)) is str and diagnostic[k].strip()
                for k in ("online_tool_result", "diagnostic_tool_result"))
        and diagnostic.get("listener_unchanged") is True
        and diagnostic.get("delivery_saved") is False and diagnostic.get("stdio_saved") is False,
        "syntax_actual_previous_exit")
    assets = receipt.get("assets")
    candidate_support.coverage.check_assets(support, assets, PREFIX)
    support.require({"request", "admission", "failure", "transport_attempt_1", "transport_attempt_2"} <= set(assets)
        and not {"delivery", "stdio"} & set(assets)
        and type(diagnostic.get("saved_assets_verified")) is int
        and diagnostic["saved_assets_verified"] == len(assets)
        and type(diagnostic.get("saved_asset_bytes")) is int
        and diagnostic["saved_asset_bytes"] == sum(item["bytes"] for item in assets.values())
        and support.read(assets["admission"]["path"], 4096) == raws["previous_candidate_marker_sha256"],
        "syntax_previous_assets_binding")
    wire = ready.get("wire_arguments")
    previous = support.load(candidate_support.PATHS["previous_coverage_ready_sha256"])["wire_arguments"]
    support.require(type(wire) is dict and wire.get("request_id") == candidate_support.REQUEST
        and wire.get("recover_from_request_id") == support.ORIGINAL_REQUEST
        and wire.get("selection_version") == candidate_support.coverage.VERSION
        and {k: v for k, v in wire.items() if k not in {"request_id", "started_at_unix_ms", "deadline_unix_ms"}}
            == {k: v for k, v in previous.items() if k not in {"request_id", "started_at_unix_ms", "deadline_unix_ms"}},
        "syntax_previous_inputs")
    bound.update({key: support.digest(raw) for key, raw in raws.items()})
    bound["previous_candidate_assets"] = assets
    support.verify_prior_preservation(bound)
    verify_preservation(support, bound)
    return bound


def verify_marker(support, marker, bound):
    fixed = {"request_id": REQUEST, "recover_from_request_id": support.ORIGINAL_REQUEST,
        "previous_candidate_request_id": candidate_support.REQUEST,
        "selection_version": candidate_support.coverage.VERSION,
        "automatic_replay": False, "max_retries": 3, "max_total_retries": 3,
        "process_seconds": 600, "soft_target_seconds": 180, "return_reserve_seconds": 15}
    support.require(type(marker) is dict and all(type(marker.get(k)) is type(v)
        and marker[k] == v for k, v in fixed.items()), "syntax_admission_marker")
    support.require(candidate_support.controls_valid(marker.get("generation_controls"))
        and marker.get("retryable_reasons") == ["upstream_stream_error", "upstream_timeout"]
        and marker.get("candidate_retryable_reasons") == REASONS, "syntax_admission_controls")
    for key in PATHS:
        support.require(type(marker.get(key)) is str and marker[key] == bound.get(key),
                        "syntax_admission_link:" + key)


def require_regression(support, receipt):
    for key in ("actual_exit_code", "tests_run", "failed", "errors", "skipped",
                "adr_revision", "execution_contract_revision"):
        support.require(type(receipt.get(key)) is int, "syntax_regression_type:" + key)
    support.require(receipt["actual_exit_code"] == receipt["failed"] == receipt["errors"]
        == receipt["skipped"] == 0 and receipt["tests_run"] > 0
        and receipt["adr_revision"] == 16 and receipt["execution_contract_revision"] == 17
        and type(receipt.get("tool_results")) is list and bool(receipt["tool_results"])
        and all(type(item) is str and item.strip() for item in receipt["tool_results"]),
        "syntax_regression_not_pass")


def require_regressions(support):
    for path in REGRESSIONS:
        require_regression(support, support.load(path))


def prepare(support):
    return candidate_support.prepare(support, regression_validator=require_regressions)


def verify_attempt(support, receipt, observation, post, captured, guard):
    if observation.get("candidate_error_reason") != SyntaxRejection.reason:
        return candidate_support.verify_attempt(support, receipt, observation, post, captured, guard,
                                                request_id=REQUEST)
    guard.check()
    number = observation["attempt_index"]
    support.require(type(number) is int and 1 <= number <= 4, "syntax_attempt_index")
    identity = REQUEST + ".gateway-attempt-" + str(number)
    raw = support.read("verification/.scratch/" + identity + ".full.json", 16777216)
    attempt = json.loads(raw)
    plain = {k: v for k, v in observation.items()
             if k not in {"candidate_error_reason", "candidate_rejection_id"}}
    support.require(attempt.get("request_id") == REQUEST
        and attempt.get("recover_from_request_id") == support.ORIGINAL_REQUEST
        and attempt.get("status") == "transport_completed_not_business_acceptance"
        and attempt.get("post_utf8", "").encode("utf-8") == post
        and attempt.get("post_sha256") == support.digest(post)
        and attempt.get("observation") == plain, "syntax_transport_record")
    support.require_gateway_completion(observation, captured)
    output, finishes, done, errors = sse_output(captured)
    support.require(output == attempt.get("output") and finishes == ["stop"] and done and not errors
        and observation.get("upstream_error_type") is None, "syntax_original_sse_output")
    try:
        parse_candidate(output)
    except SyntaxRejection as error:
        details = error.details
    else:
        raise ValueError("phase5_syntax_rejection_not_independently_reproduced")
    rejection_id = REQUEST + ".candidate-rejection-" + str(number)
    support.require(observation.get("candidate_rejection_id") == rejection_id, "syntax_typed_rejection")
    rejection_raw = support.read("verification/.scratch/" + rejection_id + ".full.json", 16777216)
    rejection = json.loads(rejection_raw)
    fixed = {"contract_version": 3, "request_id": REQUEST,
        "status": "candidate_rejected_not_business_acceptance", "reason": SyntaxRejection.reason,
        "attempt_index": number, "transport_attempt_id": identity,
        "post_sha256": support.digest(post), "absolute_deadline": observation["absolute_deadline"]}
    support.require(type(rejection) is dict and all(type(rejection.get(k)) is type(v)
        and rejection[k] == v for k, v in fixed.items())
        and rejection.get("output") == output and rejection.get("parse_error") == details
        and not {"candidate", "invalid_references"} & set(rejection), "syntax_whole_rejection_binding")
    for key, name, data in (
        ("transport_attempt_" + str(number), ".transport-attempt-" + str(number) + ".json", raw),
        ("candidate_rejection_" + str(number), ".candidate-rejection-" + str(number) + ".json", rejection_raw),
    ):
        receipt["assets"][key] = support.save_or_verify_asset(receipt, key, support.LIVE + name, data, guard)
    guard.check()
