"""Offline harness policy and frame checks; no target imports or model calls."""
import json
import subprocess
import time

HARD_LIMIT = 24576
MAX_ATTEMPTS = 3


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    def reject(value):
        raise ValueError("non_finite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)


def next_budget(current, pressure, attempt_number, hard_limit=HARD_LIMIT):
    """Only explicit return-budget pressure may change a caller's quota."""
    ceiling = min(HARD_LIMIT, hard_limit)
    if (pressure.get("reason") != "return_budget" or pressure.get("retryable") is not True
            or pressure.get("source_status") != "complete" or current >= ceiling):
        return None
    estimate = pressure.get("estimated_full_bytes")
    proposed = estimate if type(estimate) is int and estimate > current else current * 2
    # The third/final attempt must reach the authorized ceiling if needed.
    if attempt_number == MAX_ATTEMPTS - 1:
        proposed = ceiling
    return min(ceiling, max(current + 1, proposed))


def adaptive_loop(call, initial_budget, hard_limit=HARD_LIMIT):
    """At most three foreground calls; never retry an operational/business failure."""
    ceiling = min(HARD_LIMIT, hard_limit)
    if type(initial_budget) is not int or not 1024 <= initial_budget <= ceiling:
        raise ValueError("initial_budget_outside_authorized_limits")
    attempts = []
    budget = initial_budget
    snapshot = None
    for number in range(1, MAX_ATTEMPTS + 1):
        attempt = call(budget, number)
        attempts.append(attempt)
        if attempt["status"] == "BLOCKED":
            return attempts, None, "attempt_failed"
        if snapshot is not None and attempt.get("snapshot_id") != snapshot:
            attempt["status"] = "BLOCKED"
            attempt.setdefault("reasons", []).append("snapshot_changed_between_attempts")
            return attempts, None, "snapshot_changed_between_attempts"
        snapshot = attempt.get("snapshot_id")
        if not isinstance(snapshot, str) or not snapshot:
            return attempts, None, "missing_snapshot_evidence"
        if attempt["status"] == "PASS":
            return attempts, {"verified_success_budget": budget,
                               "observed_delivery_frame_bytes": attempt["wire_bytes"],
                               "delivered_symbols": attempt.get("delivered_symbols"),
                               "delivered_imports": attempt.get("delivered_imports"),
                               "delivery_state": attempt.get("delivery_state"),
                               "snapshot_id": snapshot,
                               "scope": "mandatory core for this snapshot; optional calls may be pruned; not a global minimum"}, None
        proposed = next_budget(budget, attempt.get("backpressure", {}), number, ceiling)
        if proposed is None:
            return attempts, None, "hard_limit_cannot_fit" if budget == ceiling else "nonretryable_pressure"
        if number == MAX_ATTEMPTS:
            return attempts, None, "attempt_limit_exhausted"
        budget = proposed
    raise AssertionError("unreachable")


def probe(root, selected, budget, attempt_number):
    arguments = {"contract_version": 1, "project_id": "fastcode-sample", "include": selected,
                 "max_seconds": 5, "return_bytes": budget}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "offline-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "solo_extract_structure_v1", "arguments": arguments}},
    ]
    command = ["timeout", "--kill-after=1s", "7s", "/usr/bin/python3", "-B", "-m",
               "solo_distill.server", "--config", "config/projects.json"]
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                   "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    process = subprocess.run(command, cwd=root, env=environment,
                             input=b"".join(encode(message) + b"\n" for message in messages),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    elapsed = time.monotonic() - started
    attempt = {"attempt": attempt_number, "budget": budget, "status": "BLOCKED",
               "command": command, "exit_code": process.returncode,
               "elapsed_seconds": round(elapsed, 6), "stderr_bytes": len(process.stderr),
               "stdout_bytes": len(process.stdout), "reasons": []}
    reasons = attempt["reasons"]
    if process.returncode != 0 or elapsed >= 7:
        reasons.append("nonzero_or_timeout")
    if process.stderr:
        reasons.append("stderr_not_empty")
    if reasons:
        return attempt
    try:
        lines = process.stdout.splitlines(keepends=True)
        if len(lines) != 3:
            raise ValueError("stdout_not_three_clean_rpc_frames")
        responses = [decode(line) for line in lines]
        for expected, response in enumerate(responses, 1):
            if (set(response) != {"jsonrpc", "id", "result"}
                    or response["jsonrpc"] != "2.0" or type(response["id"]) is not int
                    or response["id"] != expected or encode(response) + b"\n" != lines[expected - 1]):
                raise ValueError("invalid_or_impure_rpc_response")
        if responses[0]["result"]["protocolVersion"] != "2024-11-05":
            raise ValueError("protocol_mismatch")
        if [tool["name"] for tool in responses[1]["result"]["tools"]] != ["solo_extract_structure_v1"]:
            raise ValueError("unexpected_tools")
        attempt["wire_bytes"] = len(lines[2])
        if len(lines[2]) > budget or len(lines[2]) > HARD_LIMIT:
            raise ValueError("wire_budget_exceeded")
        result = responses[2]["result"]
        content = result["content"]
        if len(content) != 1 or content[0]["type"] != "text":
            raise ValueError("invalid_tool_content")
        report = decode(content[0]["text"])
        from step4_core_validation_20261026 import assess_structure
        pressure = report["backpressure"]
        attempt.update(report_status=report["status"], partial=report["partial"],
                       backpressure=pressure, snapshot_id=report["snapshot_id"],
                       coverage=report.get("coverage"), observations=report.get("observations"),
                       unfinished=report.get("unfinished", []))
        if report["kind"] != "repository_structure" or report["project_id"] != "fastcode-sample":
            raise ValueError("invalid_structure_identity")
        if (pressure.get("budget_unit") != "jsonrpc_utf8_bytes_including_newline"
                or pressure.get("estimate_kind") != "exact_current_serialization"
                or type(pressure.get("estimated_full_bytes")) is not int
                or pressure["estimated_full_bytes"] <= 0
                or type(pressure.get("omitted_count")) is not int
                or pressure["omitted_count"] < 0 or type(report["partial"]) is not bool):
            raise ValueError("invalid_backpressure_signal")
        if report.get("freshness") != "current_at_verification" or report.get("inventory_complete") is not True:
            raise ValueError("source_freshness_unverified")
        observations = report.get("observations")
        if observations is not None:
            if (observations.get("model_requests") != 0 or observations.get("target_writes") != 0
                    or not isinstance(observations.get("elapsed_seconds"), (int, float))
                    or not 0 <= observations["elapsed_seconds"] <= 5):
                raise ValueError("nonoffline_or_deadline_observations")
        attempt.update(assess_structure(report, result.get("isError"), selected,
                                        len(lines[2]), budget))
    except (KeyError, TypeError, ValueError) as exception:
        reasons.append(str(exception))
        attempt["status"] = "BLOCKED"
    return attempt
