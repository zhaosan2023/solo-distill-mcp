"""审计已退出的获准诊断窗口，不发送请求、不重置标记、不修改旧现场。

问题：实际 HTTP 次数、逐次错误和结束符，以及两项本地诊断失败的来源。
范围：只读当前窗口保存的回执、trace、SSE、POST、投递及关联标记。
前提：现有标准库；进程30秒、输出8192字节，不启动任何子进程或联网。
验收：有界原字节/摘要复核与事实摘要；审计退出0不代表在线业务通过。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import time

ROOT = Path.cwd()
INSTANCE = "mainline-a-schema-v3-20261030T095713Z"
PARENT = "verification/" + INSTANCE
LIVE = "phase5-live-diagnostic-20261101T133705Z"
START = time.monotonic()


def read(path, maximum=16777216):
    if time.monotonic() - START >= 27:
        raise TimeoutError("audit_window_exhausted")
    target = ROOT / path
    if not target.resolve().is_relative_to(ROOT.resolve()) or target.is_symlink():
        raise ValueError("audit_path_escape")
    with target.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("audit_read_budget")
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    receipt = json.loads(read(PARENT + "/" + LIVE + ".receipt.json"))
    verified = []
    for key, asset in receipt["assets"].items():
        raw = read(asset["path"])
        if len(raw) != asset["bytes"] or sha(raw) != asset["sha256"]:
            raise ValueError("saved_asset_changed:" + key)
        verified.append(key)
    trace = json.loads(read(receipt["assets"]["trace"]["path"]))
    attempts = []
    for observation in trace["gateway_requests"]:
        raw = base64.b64decode(observation["raw_sse_base64"], validate=True)
        number = observation["attempt_index"]
        saved = read(PARENT + "/" + LIVE + ".gateway-attempt-" + str(number) + ".sse")
        if (raw != saved or len(raw) != observation["raw_sse_bytes"]
                or sha(raw) != observation["raw_sse_sha256"]):
            raise ValueError("attempt_capture_changed")
        item = {key: observation.get(key) for key in (
            "attempt_index", "client_auto_retries", "http_status", "error_reason",
            "upstream_error_type", "finish_reason", "done_marker", "elapsed_seconds",
            "message_bytes", "message_sha256", "response_bytes", "raw_sse_bytes")}
        item["contains_done_line"] = any(line.strip() == b"data: [DONE]" for line in raw.splitlines())
        attempts.append(item)
    binding = receipt["binding"]
    original = INSTANCE + "-live-20261101"
    checks = {
        "original_admission": sha(read("verification/.scratch/" + INSTANCE + ".live-admission.full.json")) == binding["original_admission_sha256"],
        "original_failure": sha(read("verification/.scratch/" + original + ".full.json")) == binding["original_trace_sha256"],
        "previous_marker": sha(read("verification/.scratch/" + INSTANCE + ".live-recovery-20261101.full.json")) == binding["previous_marker_sha256"],
        "previous_receipt": sha(read(PARENT + "/phase5-live-recovery-20261101.receipt.json")) == binding["previous_receipt_sha256"],
        "same_frozen_post": read(receipt["assets"]["post"]["path"]) == read(PARENT + "/preflight-1/exact-http-post.json"),
        "one_absolute_deadline": len({item["absolute_deadline"] for item in trace["gateway_requests"]}) == 1,
    }
    if not all(checks.values()):
        raise ValueError("preservation_or_post_binding_failed")
    output = {
        "status": "audit_completed_not_online_acceptance", "receipt_status": receipt["status"],
        "local_failures": receipt["failures"], "trace_failures": trace["failures"],
        "actual_http_attempts": len(attempts), "attempts": attempts,
        "model_outputs_count": len(trace["model_outputs"]), "verified_assets": verified,
        "preservation_checks": checks, "service_exit_code": receipt["service_exit_code"],
        "service_cleanup": receipt["service_cleanup"], "listener_after": receipt["listener_after"],
        "total_before_final_receipt_seconds": receipt["total_before_final_receipt_seconds"],
        "no_network_or_replay": True,
    }
    text = json.dumps(output, ensure_ascii=False)
    if len(text.encode("utf-8")) > 8192:
        raise ValueError("audit_output_budget")
    print(text, flush=True)


if __name__ == "__main__":
    main()
