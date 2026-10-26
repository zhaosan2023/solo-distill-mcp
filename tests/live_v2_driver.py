"""Opt-in independent V2 A driver; frozen answers are read only after consumption."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

from solo_distill.client import ClientError, StdioClient
from solo_distill.distillation import load_policy
from solo_distill.extractor import load_projects, open_root, Scanner
from v2_consumer import evaluate_frozen, load, receive

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAME = "solo_distill_knowledge_v2"


def request(request_id, started_ms):
    # General evaluation questions, not expected answers, line anchors or seeds.
    questions = [
        ("query_flow", "Explain index readiness, query processing branches, retrieval filtering and generation context; distinguish possible execution from static syntax."),
        ("state_ownership", "Explain query/session state, persistence prerequisites, return-value handling and ownership; identify caller and callee evidence."),
        ("failure_boundaries", "Explain query and generation exception handling, input/context limits and whether returned text proves success."),
        ("scope_and_verification", "Explain parser support, local repository loading boundaries, related verification navigation and static blind spots; do not invent tests or runtime evidence."),
    ]
    return {"contract_version": 2, "request_id": request_id,
            "project_id": "fastcode-sample", "scenario": "A", "include": ["."], "seeds": [],
            "question": "For the read-only sample, map the query path and the caller/callee, state, filesystem, context-limit, error and parser boundaries needed before adapting it. Do not execute target code or claim tests ran.",
            "dimensions": [{"id": key, "question": question, "required": True,
                            "evidence": "sourced_inference"} for key, question in questions],
            "started_at_unix_ms": started_ms, "deadline_unix_ms": started_ms + 180000,
            "host_timeout_seconds": 180, "return_bytes": 22016}


def read_trace(root, request_id, suffix=""):
    import os
    descriptor = open_root(str(root))
    try:
        for part in ("verification", ".scratch"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        filename = request_id + suffix + ".full.json"
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            import stat
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 33554432:
                raise ClientError("scratch_not_regular_or_over_budget")
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 33554432:
                    raise ClientError("scratch_read_budget")
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(descriptor)
    return load(raw), {"path": "verification/.scratch/" + filename,
                       "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def check_trace(trace, raw, arguments):
    if (trace.get("scratch_version") != 2 or trace.get("request_id") != arguments["request_id"]
            or trace.get("arguments") != arguments or trace.get("outer_replays") != 0
            or trace.get("target_writes") != 0 or trace.get("frozen_frame_bytes") != len(raw)
            or trace.get("frozen_frame_sha256") != hashlib.sha256(raw).hexdigest()):
        raise ClientError("scratch_or_actual_frame_conflict")
    attempts = trace["gateway_requests"]
    if type(attempts) is not list or len(attempts) > 2:
        raise ClientError("gateway_request_count_invalid")
    for attempt in attempts:
        if (attempt.get("request_model") != "gpt-6-sol"
                or attempt.get("endpoint") != "http://127.0.0.1:8080/v1/chat/completions"
                or attempt.get("outer_replays") != 0 or attempt.get("client_auto_retries") != 0):
            raise ClientError("gateway_transport_boundary_missing")
    return attempts


def source_inputs(project, arguments, trace, deadline):
    import os
    structure = trace.get("structure")
    if (type(structure) is not dict or not structure.get("inventory_complete")
            or structure.get("freshness") != "current_at_verification"):
        raise ClientError("snapshot_not_ready")
    expected = sorted(item["path"] for item in structure["entries"] if item["status"] == "parsed")
    if trace.get("scope") != expected or not expected:
        raise ClientError("python_inventory_denominator_conflict")
    scanner = Scanner(project, tuple(arguments["include"]), project.max_seconds)
    scanner.deadline = deadline
    checked = scanner.run()
    if (checked["snapshot_id"] != structure["snapshot_id"]
            or checked["freshness"] != "current_at_verification"
            or checked["coverage"] != structure["coverage"]):
        raise ClientError("snapshot_changed_in_independent_review")
    descriptor = open_root(project.root)
    raw_files = {}
    try:
        for path in expected:
            raw, _ = scanner.read_bytes(descriptor, path, project.max_file_bytes)
            entry = next(item for item in checked["entries"] if item["path"] == path)
            if hashlib.sha256(raw).hexdigest() != entry["content_sha256"]:
                raise ClientError("source_changed_in_independent_read")
            raw_files[path] = raw
    finally:
        os.close(descriptor)
    return raw_files


def run():
    started = time.monotonic()
    started_ms = int(time.time() * 1000)
    request_id = "live-v2-a-" + str(started_ms)
    arguments = request(request_id, started_ms)
    receipt = {"receipt_version": 2, "scenario": "A", "request_id": request_id,
               "status": "blocked", "failure": None, "outer_business_replays": 0,
               "sample_writes": 0, "semantic_acceptance": "requires_independent_review",
               "host": "owned_engineering_stdio_client_not_Roo", "host_acceptance": "not_proven",
               "controller_coverage": "not_proven_platform_task_record_used", "business_seconds_limit": 180,
               "total_seconds_limit": 210, "gateway_requests": [],
               "driver_command": [sys.executable, "-B", "-S", "tests/run_live_integration.py", "--scenario", "A", "--v2"]}
    client = None
    consumed = None
    try:
        # Both configs must be valid before an online child can be launched.
        projects = load_projects(str(ROOT / "config/projects.json"))
        policy = load_policy(str(ROOT / "config/distillation.json"))
        if arguments["project_id"] not in policy["authorized_project_ids"]:
            raise ClientError("project_not_authorized")
        project = projects[arguments["project_id"]]
        client = StdioClient(ROOT, online=True, deadline=started + 180, v2=True)
        discovery = client.initialize()
        if "error" in discovery or TOOL_NAME not in {item["name"] for item in discovery["result"]["tools"]}:
            raise ClientError("v2_tool_not_discovered")
        receipt["negotiated_protocol"] = client.negotiated_protocol
        client.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": TOOL_NAME, "arguments": arguments}})
        response = client.receive(3)
        raw = client.last_frame
        consumed = time.monotonic()
        receipt["request_to_consumption_seconds"] = round(consumed - started, 6)
        if type(raw) is not bytes or len(raw) > 22016 or consumed >= started + 180:
            raise ClientError("actual_wire_or_business_deadline")
        receipt["wire_response_bytes"] = len(raw)
        receipt["wire_response_sha256"] = hashlib.sha256(raw).hexdigest()
        if "error" in response:
            raise ClientError("business_result_not_delivered")
        trace, location = read_trace(ROOT, request_id)
        receipt["scratch_check"] = location
        receipt["gateway_requests"] = check_trace(trace, raw, arguments)
        receipt["business_failure"] = trace.get("failure")
        report = load(response["result"]["content"][0]["text"])
        if report.get("kind") == "publication_blocked":
            blocked = receive(raw, {}, request_id=3, scenario="A", return_bytes=22016)
            if blocked["status"] != "blocked":
                raise ClientError("blocked_frame_conflict")
            raise ClientError("required_knowledge_not_delivered")
        inputs = source_inputs(project, arguments, trace, started + 180)
        received = receive(raw, inputs, request_id=3, scenario="A", return_bytes=22016,
                           project_id=arguments["project_id"], policy_id="lifecycle-v2-1",
                           protocol_version=client.negotiated_protocol)
        if (received["core"] != trace["core"] or trace.get("freshness") != "current_at_final_verification"
                or received["package"]["snapshot_id"] != trace["structure"]["snapshot_id"]):
            raise ClientError("seal_snapshot_or_trace_conflict")
        receipt["core_id"] = received["core"]["core_id"]
        receipt["input_id"] = received["core"]["input_id"]
        receipt["snapshot_id"] = received["package"]["snapshot_id"]
        # Evaluation-only asset loaded after real frame receipt; never imported by generation.
        frozen = load((ROOT / "tests/fastcode_acceptance.json").read_bytes())
        evaluation = evaluate_frozen(received, frozen)
        receipt["anchor_checks"] = [{key: item[key] for key in ("id", "body_coverage", "source_ids", "semantic_support")}
                                     for item in evaluation["checks"]]
        if len(evaluation["checks"]) != 13 or not all(item["body_coverage"] for item in evaluation["checks"]):
            raise ClientError("required_reference_body_missing")
        if not receipt["gateway_requests"] or any(item.get("http_status") != 200
                or item.get("finish_reason") != "stop" or item.get("done_marker") is not True
                for item in receipt["gateway_requests"]):
            raise ClientError("gateway_normal_completion_not_proven")
        if received["status"] != "complete" or trace["status"] != "complete":
            raise ClientError("business_not_complete")
        if time.monotonic() >= started + 180:
            raise ClientError("independent_validation_deadline")
        receipt["status"] = "transport_structure_sources_pass_semantics_pending"
    except (ClientError, ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        receipt["failure"] = str(error) if isinstance(error, ClientError) else type(error).__name__
    finally:
        if client is not None:
            client.close()
            receipt.update(child_exit_code=client.exit_code, cleanup=client.cleanup,
                           stderr_bytes=len(client.stderr), stderr_sha256=hashlib.sha256(client.stderr).hexdigest())
            if client.cleanup != "confirmed_natural_exit" or client.exit_code != 0 or client.stderr:
                receipt["status"] = "blocked"
                receipt["cleanup_gate"] = "not_passed"
            if consumed is not None and "wire_response_sha256" in receipt:
                try:
                    delivery, location = read_trace(ROOT, request_id, ".delivery")
                    receipt["delivery_check"] = location
                    if (delivery.get("written") != receipt["wire_response_bytes"]
                            or delivery.get("frame_sha256") != receipt["wire_response_sha256"]
                            or delivery.get("request_id") != request_id or delivery.get("rpc_id") != 3
                            or delivery.get("status") != "frame_written_not_host_consumption"):
                        raise ClientError("write_receipt_not_actual_consumption")
                except (ClientError, ValueError, TypeError, OSError):
                    receipt["status"] = "blocked"
                    receipt["delivery_gate"] = "not_passed"
        else:
            receipt["cleanup"] = "no_child_observed"
        receipt["total_with_cleanup_seconds"] = round(time.monotonic() - started, 6)
        if time.monotonic() >= started + 210:
            receipt["status"] = "blocked"
            receipt["total_window_gate"] = "not_passed"
    print(json.dumps(receipt, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
    return 0 if receipt["status"] == "transport_structure_sources_pass_semantics_pending" else 2
