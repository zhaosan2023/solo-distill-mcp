"""已退出候选恢复窗口的有界离线诊断；不联网、不重发、不修改历史资产。

问题：已保存的原始事件流正常终止后，候选为何无法解析？
范围：固定候选窗口的回执、准备证据、保存资产及至多四次传输。
前提：客户端 call_e4ee077d6e7c04efbf8530c3 已报告外层和服务退出2。
验收：复核原字节、请求及事件流绑定；报告解析位置和有界上下文，
保留缺口，不修补输出、不创建业务投递。标准输出不超过8192字节。
"""
from __future__ import annotations

import base64
import json

from mainline_a_schema_v3_coverage_failure_diagnostic_20261102 import (
    BASE, candidate, digest, inspect_candidate, parse, read, require, sse_output,
)

PREFIX = "phase5-live-candidate-20261102T070153Z"
REQUEST = "mainline-a-schema-v3-20261030T095713Z-live-candidate-20261102T070153Z"
READY = BASE / "phase5-candidate-ready-20261102T070153Z.json"


def main():
    receipt_path = BASE / (PREFIX + ".receipt.json")
    receipt_raw = read(receipt_path, 65536)
    receipt = parse(receipt_raw)
    ready = parse(read(READY, 131072))
    require(receipt.get("status") == "blocked"
            and receipt.get("request_id") == REQUEST
            and type(receipt.get("service_exit_code")) is int
            and receipt["service_exit_code"] == 2
            and receipt.get("service_cleanup") == "confirmed_nonzero_exit",
            "failed_candidate_window_binding")
    require(receipt.get("binding") == ready.get("binding")
            and ready.get("status") == "ready_not_online_executed",
            "ready_receipt_binding")
    evidence = receipt.get("assets")
    require(type(evidence) is dict and 0 < len(evidence) <= 32,
            "asset_count_budget")
    assets, total = {}, 0
    for key, item in evidence.items():
        require(type(item) is dict and type(item.get("path")) is str
                and item["path"].startswith(str(BASE) + "/" + PREFIX + ".")
                and item.get("exact_readback") is True
                and type(item.get("bytes")) is int
                and 0 <= item["bytes"] <= 16777216,
                "asset_contract")
        raw = read(item["path"], item["bytes"])
        require(len(raw) == item["bytes"] and digest(raw) == item.get("sha256"),
                "saved_asset_changed:" + key)
        total += len(raw)
        require(total <= 33554432, "asset_total_budget")
        assets[key] = raw
    require({"request", "admission", "failure", "transport_attempt_1"} <= set(assets),
            "required_failed_assets")
    admission = parse(assets["admission"])
    require(admission.get("request_id") == REQUEST
            and admission.get("process_seconds") == 600
            and admission.get("max_total_retries") == 3,
            "candidate_admission_binding")
    attempts = []
    numbers = sorted(int(key.removeprefix("transport_attempt_"))
                     for key in assets if key.startswith("transport_attempt_"))
    require(numbers == list(range(1, len(numbers) + 1)) and 1 <= len(numbers) <= 4,
            "contiguous_bounded_attempts")
    for number in numbers:
        item = parse(assets["transport_attempt_" + str(number)])
        require(item.get("request_id") == REQUEST, "attempt_identity")
        observation = item["observation"]
        post = item["post_utf8"].encode("utf-8")
        require(digest(post) == item["post_sha256"] == ready["post_sha256"]
                and len(post) == ready["post_bytes"] == observation["message_bytes"]
                and digest(post) == observation["message_sha256"], "attempt_post_binding")
        require(observation["attempt_index"] == number
                and observation["client_auto_retries"] == number - 1, "attempt_index")
        sse = base64.b64decode(observation["raw_sse_base64"], validate=True)
        require(len(sse) == observation["raw_sse_bytes"]
                and digest(sse) == observation["raw_sse_sha256"], "sse_capture_binding")
        output, finishes, done, errors = sse_output(sse)
        envelope = parse(post)
        require(envelope.get("temperature") == 0.2 and envelope.get("max_tokens") == 3072,
                "generation_controls_binding")
        result = {"attempt": number, "saved_status": item["status"],
                  "error_reason": observation.get("error_reason"),
                  "upstream_error_type": observation.get("upstream_error_type"),
                  "finish_reasons": finishes, "done": done, "sse_error_types": errors,
                  "http_status": observation["http_status"], "post_bytes": len(post),
                  "output_characters": len(output), "output_sha256": digest(output.encode("utf-8")),
                  "absolute_deadline": observation["absolute_deadline"]}
        if item.get("output") is not None:
            require(output == item["output"] and finishes == ["stop"] and done
                    and not errors and observation.get("error_reason") is None,
                    "completed_output_sse_conflict")
            try:
                value = candidate(output)
            except json.JSONDecodeError as error:
                result["candidate_parse"] = {
                    "status": "invalid_json", "message": error.msg,
                    "position": error.pos, "line": error.lineno, "column": error.colno,
                    "parsed_text_characters": len(error.doc),
                    "context_start": max(0, error.pos - 100),
                    "context_repr": repr(error.doc[max(0, error.pos - 100):error.pos + 100]),
                }
            except ValueError:
                result["candidate_parse"] = {"status": "invalid_fence_or_strict_json"}
            else:
                payload = parse(envelope["messages"][-1]["content"])
                dimensions = {entry["id"] for entry in payload["dimensions"]}
                allowed = {key for entry in payload["sources"] for key in entry["alias_source_ids"]}
                try:
                    result["candidate_structure"] = inspect_candidate(value, dimensions, allowed)
                except ValueError:
                    result["candidate_structure"] = {"status": "invalid_candidate_fields"}
        else:
            require(item["status"] == "transport_failed_not_business_acceptance",
                    "failed_transport_status")
        rejection_key = "candidate_rejection_" + str(number)
        if rejection_key in assets:
            rejection = parse(assets[rejection_key])
            require(rejection.get("request_id") == REQUEST
                    and rejection.get("attempt_index") == number
                    and rejection.get("post_sha256") == digest(post), "rejection_binding")
            result["saved_candidate_rejection_reason"] = rejection.get("reason")
        attempts.append(result)
    require(len({item["absolute_deadline"] for item in attempts}) == 1,
            "shared_absolute_deadline")
    failure = parse(assets["failure"])
    report = {
        "status": "failed_window_diagnosed_not_business_acceptance",
        "execution_instance": receipt["execution_instance"], "request_id": REQUEST,
        "online_tool_result": "call_e4ee077d6e7c04efbf8530c3",
        "actual_online_outer_exit_code": 2, "service_exit_code": 2,
        "service_cleanup": receipt["service_cleanup"],
        "online_receipt": str(receipt_path), "online_receipt_sha256": digest(receipt_raw),
        "saved_assets_verified": len(assets), "saved_asset_bytes": total,
        "failure_error_type": failure.get("error_type"), "failure_reason": failure.get("reason"),
        "attempts": attempts, "soft_target_met": receipt.get("soft_target_met"),
        "listener_unchanged": receipt.get("listener_after") == ready.get("listener_inodes"),
        "delivery_saved": "delivery" in assets, "stdio_saved": "stdio" in assets,
        "network_operations": 0, "historical_asset_writes": 0,
        "not_proven": ["legal_final_candidate", "trusted_dual_consumption",
                       "four_dimensions_and_13_anchors", "human_review", "governance",
                       "ADR_acceptance", "archival"],
    }
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    require(len(encoded) + 1 <= 8192, "diagnostic_output_budget")
    print(encoded.decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
