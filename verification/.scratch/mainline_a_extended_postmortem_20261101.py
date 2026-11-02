"""有界核验已退出的在线失败现场；不联网、不重发、不运行成功消费门禁。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path.cwd()
spec = importlib.util.spec_from_file_location(
    "extended_online_evidence", ROOT / "verification/.scratch/mainline_a_schema_v3_online_20261101.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
PARENT = "verification/mainline-a-schema-v3-20261030T095713Z"
INSTANCE = "mainline-a-schema-v3-20261030T095713Z"
PREFIX = PARENT + "/phase5-live-extended-20261101T233105Z."
REQUEST = INSTANCE + "-live-extended-20261101T233105Z"
RECEIPT = PREFIX + "receipt.json"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def inspect():
    driver.configure_extended()
    raw = driver.read(RECEIPT, 16384)
    receipt = json.loads(raw)
    require(receipt.get("status") == "blocked" and receipt.get("request_id") == REQUEST
            and receipt.get("execution_instance") == INSTANCE, "receipt_identity")
    require(type(receipt.get("service_exit_code")) is int
            and receipt["service_exit_code"] == 0
            and receipt.get("service_cleanup") == "confirmed_natural_exit", "service_exit")
    assets = receipt["assets"]
    require(type(assets) is dict and 0 < len(assets) <= 32, "asset_count")
    total = 0
    verified = []
    for name, asset in assets.items():
        require(type(asset) is dict and type(asset.get("path")) is str
                and asset["path"].startswith(PREFIX)
                and type(asset.get("bytes")) is int and 0 <= asset["bytes"] <= 16777216
                and asset.get("exact_readback") is True, "asset_contract:" + name)
        total += asset["bytes"]
        require(total <= 67108864, "asset_total_budget")
        content = driver.read(asset["path"], asset["bytes"])
        require(len(content) == asset["bytes"]
                and driver.digest(content) == asset.get("sha256"), "asset_changed:" + name)
        verified.append(name)
    require(all(name in assets for name in (
        "trace", "post", "model_outputs", "candidate", "stdio", "request_wire", "delivery", "admission")),
        "required_saved_assets")
    trace = json.loads(driver.read(assets["trace"]["path"], 16777216))
    require(trace.get("request_id") == REQUEST, "trace_identity")
    attempts = trace["gateway_requests"]
    require(type(attempts) is list and 1 <= len(attempts) <= 4, "attempt_count")
    require(receipt.get("actual_http_attempts") == len(attempts)
            and receipt.get("client_auto_retries") == len(attempts) - 1, "receipt_attempt_count")
    post = driver.read(assets["post"]["path"], 150000)
    require(post == trace["post_utf8"].encode("utf-8")
            and post == driver.read(PARENT + "/preflight-1/exact-http-post.json", 150000), "exact_post")
    observations = []
    for index, attempt in enumerate(attempts, 1):
        require(attempt.get("attempt_index") == index
                and attempt.get("client_auto_retries") == index - 1
                and attempt.get("absolute_deadline") == attempts[0].get("absolute_deadline")
                and attempt.get("message_bytes") == len(post)
                and attempt.get("message_sha256") == driver.digest(post), "attempt_binding")
        key = "sse_attempt_" + str(index)
        require(key in assets, "missing_sse:" + key)
        stream = driver.read(assets[key]["path"], 16777216)
        require(len(stream) == attempt.get("raw_sse_bytes")
                and driver.digest(stream) == attempt.get("raw_sse_sha256"), "sse_binding")
        errors = []
        done = 0
        for line in stream.splitlines():
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                done += 1
                continue
            if not data:
                continue
            value = json.loads(data)
            if type(value) is dict and "error" in value:
                error = value["error"]
                item = {"error_object": type(error) is dict}
                if type(error) is dict:
                    for field in ("type", "code"):
                        value = error.get(field)
                        item[field] = value[:96] if type(value) is str else value if type(value) is int else None
                    message = error.get("message")
                    item["message_utf8_bytes"] = len(message.encode("utf-8")) if type(message) is str else None
                    item["message_terms"] = [term for term in (
                        "timeout", "timed out", "deadline", "stream", "exceeded", "cancel", "rate limit")
                        if type(message) is str and term in message.lower()]
                errors.append(item)
        require(len(errors) <= 8, "error_output_budget")
        if index < len(attempts):
            require(attempt.get("error_reason") == "upstream_stream_error"
                    and attempt.get("upstream_error_type") == "upstream_stream_error", "retry_classification")
        observations.append({field: attempt.get(field) for field in (
            "attempt_index", "http_status", "error_reason", "upstream_error_type",
            "finish_reason", "done_marker", "elapsed_seconds", "ttft_seconds", "raw_sse_bytes")})
        observations[-1].update(raw_error_objects=errors, raw_done_markers=done)
    require(trace.get("model_outputs") == []
            and json.loads(driver.read(assets["model_outputs"]["path"], 1048576)) == [], "no_success_output")
    driver.verify_prior_preservation(receipt["binding"])
    admission = json.loads(driver.read(assets["admission"]["path"], 16384))
    marker = "verification/.scratch/" + INSTANCE + ".live-extended-20261101T233105Z.full.json"
    require(driver.read(marker, 16384) == driver.read(assets["admission"]["path"], 16384)
            and admission.get("request_id") == REQUEST
            and admission.get("automatic_replay") is False, "consumed_admission")
    delivery = json.loads(driver.read(assets["delivery"]["path"], 16384))
    require(delivery.get("written") == assets["stdio"]["bytes"]
            and delivery.get("frame_sha256") == assets["stdio"]["sha256"], "delivery_binding")
    result = {
        "status": "failure_evidence_verified_not_online_acceptance",
        "execution_instance": INSTANCE, "request_id": REQUEST,
        "original_receipt": RECEIPT, "original_receipt_bytes": len(raw),
        "original_receipt_sha256": driver.digest(raw),
        "service_exit_code": receipt["service_exit_code"], "service_cleanup": receipt["service_cleanup"],
        "business_call_attempts": receipt.get("business_call_attempts"),
        "actual_http_attempts": len(attempts), "client_auto_retries": len(attempts) - 1,
        "failures": receipt.get("failures"), "trace_failures": trace.get("failures"),
        "gateway_observations": observations, "model_outputs_count": 0,
        "verified_asset_names": sorted(verified), "verified_asset_bytes": total,
        "exact_post_bytes": len(post), "exact_post_matches_frozen": True,
        "prior_evidence_preserved": True, "new_admission_consumed": True,
        "saved_delivery_matches_received_stdio": True,
        "live_timing": trace.get("live_timing"),
        "listener_after": receipt.get("listener_after"),
        "source_writes": receipt.get("source_writes"),
        "gateway_restart_operations": receipt.get("gateway_restart_operations"),
        "outer_replays": receipt.get("outer_replays"),
        "online_acceptance": "blocked", "root_cause": "not_proven",
        "business_posts_by_this_inspection": 0,
        "success_consumption_gates_run": 0,
        "scope": "核验已保存失败证据；不证明外部网关600秒配置、失败根因或成功物理验收。原始错误消息保留在SSE资产，不全文回灌。"
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    require(len(encoded.encode("utf-8")) <= 8192, "summary_budget")
    print(encoded, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(inspect())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "reason": str(error)[:200]}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(2)
