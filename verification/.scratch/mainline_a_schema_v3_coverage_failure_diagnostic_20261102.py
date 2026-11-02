"""阶段5已退出窗口的有界离线诊断；不联网、不重发、不改历史资产。

问题：非法结论具体违反哪些字段、维度或真实发送来源约束？
范围：仅固定覆盖恢复的回执、准入和保存的逐次传输资产。
前提：客户端call_56bef3cb69e5f224d67bf6bf已报告退出2，服务退出2。
验收：原字节摘要复核、逐次传输终止对账、具体候选违规及证据缺口；
不创建投递产物、不修补生成引用、不登记业务通过。输出上限8192字节。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

BASE = Path("verification/mainline-a-schema-v3-20261030T095713Z")
PREFIX = "phase5-live-coverage-20261102T050022Z"
REQUEST = "mainline-a-schema-v3-20261030T095713Z-live-coverage-20261102T050022Z"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read(path, limit=16777216):
    path = Path(path)
    require(not path.is_absolute() and ".." not in path.parts, "relative_asset_required")
    require(not path.is_symlink(), "symlink_asset_rejected")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "asset_read_budget")
    return raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def candidate(text):
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        require(lines[0] in {"```", "```json"} and lines[-1] == "```", "candidate_fence")
        value = "\n".join(lines[1:-1])
    return parse(value)


def sse_output(raw):
    events, lines = [], []
    for line in raw.splitlines():
        if not line:
            if lines:
                events.append(b"\n".join(lines))
                lines = []
        elif line.startswith(b"data:"):
            lines.append(line[5:].lstrip(b" "))
    require(not lines, "unterminated_sse_event")
    contents, finishes, errors = [], [], []
    done = False
    for event in events:
        require(not done, "sse_data_after_done")
        if event == b"[DONE]":
            done = True
            continue
        value = parse(event)
        if "error" in value:
            errors.append(value["error"].get("type") if type(value["error"]) is dict else None)
        for choice in value.get("choices", []):
            text = choice.get("delta", {}).get("content")
            if text is not None:
                require(type(text) is str, "sse_content_type")
                contents.append(text)
            if choice.get("finish_reason") is not None:
                finishes.append(choice["finish_reason"])
    return "".join(contents), finishes, done, errors


def inspect_candidate(value, dimensions, allowed):
    require(type(value) is dict and set(value) == {"summary", "conclusions", "unknowns"},
            "candidate_fields")
    require(type(value["summary"]) is str and type(value["conclusions"]) is list
            and type(value["unknowns"]) is list, "candidate_field_types")
    require(all(type(item) is str for item in value["unknowns"]), "candidate_unknown_types")
    invalid, valid = [], 0
    for index, item in enumerate(value["conclusions"]):
        reasons, unknown = [], []
        if type(item) is not dict:
            reasons.append("conclusion_not_object")
        else:
            if set(item) != {"dimension_id", "text", "source_ids"}:
                reasons.append("conclusion_field_set")
            dimension = item.get("dimension_id")
            if type(dimension) is not str or dimension not in dimensions:
                reasons.append("unknown_or_invalid_dimension")
            text = item.get("text")
            if type(text) is not str or not text.strip():
                reasons.append("empty_or_invalid_text")
            ids = item.get("source_ids")
            if type(ids) is not list or not ids:
                reasons.append("empty_or_invalid_source_ids")
            else:
                unknown = [key for key in ids if type(key) is not str or key not in allowed]
                if unknown:
                    reasons.append("invalid_or_unsent_source_id")
                if all(type(key) is str for key in ids) and len(set(ids)) != len(ids):
                    reasons.append("duplicate_source_ids")
        if reasons:
            entry = {"index": index, "reasons": reasons,
                     "invalid_source_id_count": len(unknown),
                     "invalid_source_ids_preview": [repr(key)[:160] for key in unknown[:6]]}
            if type(item) is dict:
                entry["dimension_id_preview"] = repr(item.get("dimension_id"))[:96]
                entry["text_preview"] = str(item.get("text", ""))[:160]
            invalid.append(entry)
        else:
            valid += 1
    return {"conclusions": len(value["conclusions"]), "valid_conclusions": valid,
            "invalid_conclusions": len(invalid), "invalid_preview": invalid[:12],
            "preview_truncated": len(invalid) > 12}


def main():
    receipt_raw = read(BASE / (PREFIX + ".receipt.json"), 65536)
    receipt = parse(receipt_raw)
    require(receipt["status"] == "blocked" and receipt["request_id"] == REQUEST
            and receipt["service_exit_code"] == 2
            and receipt["service_cleanup"] == "confirmed_nonzero_exit", "failed_window_binding")
    ready = parse(read(BASE / "phase5-coverage-ready-20261102T050022Z.json", 131072))
    require(receipt["binding"] == ready["binding"], "ready_receipt_binding")
    assets, total = {}, 0
    require(0 < len(receipt["assets"]) <= 32, "asset_count_budget")
    for key, evidence in receipt["assets"].items():
        require(evidence["path"].startswith(str(BASE) + "/" + PREFIX + ".")
                and evidence.get("exact_readback") is True, "asset_path_or_readback")
        raw = read(evidence["path"])
        require(len(raw) == evidence["bytes"] and digest(raw) == evidence["sha256"], "saved_asset_changed")
        total += len(raw)
        require(total <= 33554432, "asset_total_budget")
        assets[key] = raw
    attempts = []
    for number in range(1, 5):
        key = "transport_attempt_" + str(number)
        if key not in assets:
            break
        item = parse(assets[key])
        require(item["request_id"] == REQUEST, "attempt_identity")
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
        text, finishes, done, errors = sse_output(sse)
        result = {"attempt": number, "saved_status": item["status"],
                  "error_reason": observation.get("error_reason"),
                  "upstream_error_type": observation.get("upstream_error_type"),
                  "finish_reasons": finishes, "done": done, "sse_error_types": errors,
                  "http_status": observation["http_status"], "post_bytes": len(post),
                  "temperature": parse(post).get("temperature"),
                  "max_tokens": parse(post).get("max_tokens"),
                  "absolute_deadline": observation["absolute_deadline"]}
        if item["output"] is not None:
            require(text == item["output"] and finishes == ["stop"] and done
                    and not errors and observation.get("error_reason") is None,
                    "completed_output_sse_conflict")
            payload = parse(parse(post)["messages"][-1]["content"])
            dimensions = {entry["id"] for entry in payload["dimensions"]}
            allowed = {key for entry in payload["sources"] for key in entry["alias_source_ids"]}
            require(all(entry["source_id"] in allowed for entry in payload["sources"]), "primary_alias_binding")
            result["candidate"] = inspect_candidate(candidate(text), dimensions, allowed)
            result["allowed_dimensions"] = sorted(dimensions)
            result["allowed_source_ids_count"] = len(allowed)
            result["allowed_source_id_preview"] = sorted(allowed)[:3]
        attempts.append(result)
    require(bool(attempts) and len({entry["absolute_deadline"] for entry in attempts}) == 1,
            "attempt_shared_deadline")
    failure = parse(assets["failure"])
    report = {"status": "failed_window_diagnosed_not_business_acceptance",
              "online_tool_result": "call_56bef3cb69e5f224d67bf6bf",
              "actual_outer_exit_code": 2, "service_exit_code": 2,
              "service_cleanup": receipt["service_cleanup"],
              "receipt_sha256": digest(receipt_raw),
              "saved_assets_verified": len(assets), "saved_asset_bytes": total,
              "failure_reason": failure["reason"], "attempts": attempts,
              "soft_target_met": receipt["soft_target_met"],
              "listener_unchanged": receipt["listener_after"] == ready["listener_inodes"],
              "delivery_saved": "delivery" in assets, "stdio_saved": "stdio" in assets,
              "network_operations": 0, "historical_asset_writes": 0,
              "not_proven": ["legal_candidate", "trusted_dual_consumption",
                             "four_dimension_and_13_anchor_acceptance", "human_review",
                             "governance", "ADR_acceptance", "archival"]}
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    require(len(encoded) + 1 <= 8192, "diagnostic_output_budget")
    print(encoded.decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
