"""获准 V3 冷预检调用链；不联网、不执行来源，不自动运行真实三样本。

可信来源由共用准备链冻结，实际 stdio 由生产和独立消费端对账。
六份证据保存与复读共用软观测起点；真实调用方窗口不超过 60 秒。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from schema_v3_selection_oracle import expected_selection
from schema_v3_wire_oracle import receive as independent_receive
from schema_v3_snapshot_oracle import oracle_metrics, oracle_scope
from solo_distill.snapshot_v2 import snapshot_metrics, snapshot_scope
from solo_distill.client import StdioClient
from solo_distill.distillation import utf8_json
from solo_distill.extractor import load_projects, open_root, open_relative
from solo_distill.gateway import encode_request
from solo_distill.preflight_v3 import (
    PARENT, PreflightBlocked, PreflightGuard, _save_exact, save_preflight_artifacts,
    PREFLIGHT_SAMPLES, COVERAGE_PREFLIGHT_SAMPLES, validate_preflight_sample,
)
from solo_distill.preparation_v3 import prepare_snapshot


class ExitGuard:
    def __init__(self, guard, *, deadline=None):
        self.guard = guard
        self.deadline = min(guard.started + 60, deadline) if deadline is not None else guard.started + 60

    def check(self):
        self.guard.check()
        if time.monotonic() >= self.deadline:
            raise PreflightBlocked("v3_preflight_final_receipt_or_exit_deadline")


def operation_deadline(arguments, guard):
    started, end, window = (arguments[key] for key in (
        "started_at_unix_ms", "deadline_unix_ms", "host_timeout_seconds"))
    now = time.time() * 1000
    if (any(type(value) is not int for value in (started, end, window))
            or not 1 <= window <= 60 or started <= 0
            or not 0 < end - started <= window * 1000
            or started > now or end <= now):
        raise ValueError("v3_cold_chain_host_window")
    return min(guard.started + 60, time.monotonic() + (end - now) / 1000)


def reject_existing(root, sample_id, *, selection_version="question-scout-v1"):
    validate_preflight_sample(sample_id, selection_version=selection_version)
    workspace = open_root(str(root))
    try:
        parent = open_relative(workspace, PARENT, directory=True)
        try:
            try:
                os.stat(sample_id, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise FileExistsError(sample_id)
        finally:
            os.close(parent)
    finally:
        os.close(workspace)


def preflight_wire_arguments(arguments, external_selection):
    """仅离线请求不携带完整见证；包外核验、双消费和服务回执仍使用全文。

    已传入的冻结选择必须与本次独立复算完整相等，不允许静默丢弃冲突。
    live 不走此入口；不改变服务端 live 的建连前冻结选择要求或输入上限。
    """
    if arguments.get("contract_version") != 3 or arguments.get("operation") != "preflight":
        raise ValueError("v3_cold_chain_offline_request_required")
    if ("frozen_selection" in arguments
            and arguments["frozen_selection"] != external_selection):
        raise ValueError("v3_cold_chain_caller_frozen_selection_conflict")
    wire = json.loads(utf8_json(arguments))
    wire.pop("frozen_selection", None)
    encoded = utf8_json({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "solo_distill_knowledge_v3", "arguments": wire}}) + b"\n"
    if len(encoded) > 65536:
        raise ValueError("v3_cold_chain_request_wire_budget")
    return wire, len(encoded)


@snapshot_scope
@oracle_scope
def run_preflight(root, arguments, sample_id, *, guard=None):
    guard = PreflightGuard() if guard is None else guard
    guard.check()
    root = Path(root).absolute()
    arguments = json.loads(utf8_json(arguments))
    selection_version = arguments.get("selection_version", "question-scout-v1")
    reject_existing(root, sample_id, selection_version=selection_version)
    if arguments.get("contract_version") != 3 or arguments.get("operation") != "preflight":
        raise ValueError("v3_cold_chain_offline_request_required")
    deadline = operation_deadline(arguments, guard)
    operation_guard = ExitGuard(guard, deadline=deadline)
    operation_guard.check()
    projects = load_projects(str(root / "config/projects.json"))
    project = projects[arguments["project_id"]]
    prepared = prepare_snapshot(project=project,
        include=tuple(arguments.get("include", project.include)),
        question=arguments["question"], dimensions=arguments["dimensions"],
        selection_version=selection_version,
        deadline=deadline, cancelled=guard.cancelled, tick=operation_guard.check)
    raw_files, report, selection = prepared.raw_files, prepared.report, prepared.selection
    if encode_request(prepared.messages) != prepared.post:
        raise ValueError("v3_cold_chain_exact_post_message_order_conflict")
    guard.observe_selection(prepared.metrics["stage_seconds"]["selection"])
    selection_started = time.monotonic()
    messages, post, external_selection = expected_selection(raw_files,
        project_id=project.project_id, snapshot_id=report["snapshot_id"],
        question=arguments["question"], dimensions=arguments["dimensions"],
        selection_version=selection_version,
        frozen_coverage=report["coverage"], tick=operation_guard.check)
    if post != prepared.post or selection != external_selection or messages != prepared.messages:
        raise ValueError("v3_cold_chain_independent_selection_conflict")
    independent_selection_seconds = time.monotonic() - selection_started
    wire_arguments, request_wire_bytes = preflight_wire_arguments(arguments, external_selection)
    candidate = {"summary": "", "conclusions": [], "unknowns": []}
    trusted = {"project_id": project.project_id, "snapshot_id": report["snapshot_id"],
        "required_core_unit_ids": external_selection["core_unit_ids"],
        "selection_id": external_selection["selection_id"],
        "request_id": arguments["request_id"], "question": arguments["question"],
        "dimensions": arguments["dimensions"], "post": post,
        "selection_version": selection_version,
        "frozen_coverage": report["coverage"], "failures": []}
    client = StdioClient(root, online=False, v3=True, deadline=deadline)
    try:
        tools = client.initialize()
        if [tool["name"] for tool in tools["result"]["tools"]] != ["solo_distill_knowledge_v3"]:
            raise ValueError("v3_cold_chain_registration")
        call_started = time.monotonic()
        production = client.call_v3(wire_arguments, raw_files=raw_files,
            candidate=candidate, tick=operation_guard.check, **trusted)
        service_and_production_consume_seconds = time.monotonic() - call_started
        guard.check()
        oracle_started = time.monotonic()
        independent = independent_receive(client.last_frame, raw_files, arguments["transport"],
            protocol_version=arguments["protocol_version"], response_id=3,
            return_bytes=arguments["return_bytes"], candidate=candidate,
            wire_encoding=wire_arguments.get("wire_encoding", "json"),
            tick=operation_guard.check, **trusted)
        independent_consume_seconds = time.monotonic() - oracle_started
        if production.publication != independent["publication"]:
            raise ValueError("v3_cold_chain_consumers_conflict")
        prepared.verify_sources()
    finally:
        client.close()
    guard.check()
    if client.exit_code != 0 or client.cleanup != "confirmed_natural_exit" or client.stderr:
        raise PreflightBlocked("v3_cold_chain_service_exit_or_cleanup")
    trace_path = root / "verification/.scratch" / (arguments["request_id"] + ".full.json")
    trace = json.loads(trace_path.read_bytes())
    if (trace["gateway_requests"] or trace["model_outputs"] or trace["candidate"] != candidate
            or trace["post_utf8"].encode("utf-8") != post or trace["selection"] != external_selection):
        raise ValueError("v3_cold_chain_service_trace_conflict")
    prepared.verify_sources()
    guard.check()
    publication = production.publication
    oracle_receipt = {"status": "pass", "selection_id": external_selection["selection_id"],
        "wire_encoding": wire_arguments.get("wire_encoding", "json"),
        "production_and_independent_equal": True,
        "task_status": production.status,
        "decoded_bytes": production.decoded_bytes, "transfer_bytes": production.transfer_bytes,
        "physical_frames": production.physical_frames,
        "notification_chunks": production.notification_chunks,
        "shared_parser_risk": "python_standard_library_ast",
        "syntax_reuse": {"production_driver": snapshot_metrics(),
                         "independent_oracle": oracle_metrics()},
        "semantic_acceptance": "not_proven"}
    frozen = {"project_id": project.project_id, "report": report,
        "selection": external_selection, "question": arguments["question"],
        "dimensions": arguments["dimensions"], "source_root": project.root,
        "include": list(arguments.get("include", project.include)),
        "raw_files_base64": {path: base64.b64encode(raw).decode("ascii")
                             for path, raw in sorted(raw_files.items())}}
    if selection_version == "question-scout-v2":
        frozen["selection_version"] = selection_version
        oracle_receipt["selection_version"] = selection_version
    artifacts = {"frozen-source-and-selection.json": utf8_json(frozen),
        "knowledge-package.json": utf8_json(publication["core"]["package"]),
        "sealed-core.json": utf8_json(publication["core"]),
        "transport.jsonl": client.last_frame, "exact-http-post.json": post,
        "independent-oracle.receipt.json": utf8_json(oracle_receipt)}
    save_started = time.monotonic()
    saved = save_preflight_artifacts(artifact_root=root, sample_id=sample_id,
                                     artifacts=artifacts, guard=guard,
                                     selection_version=selection_version)
    guard.check()
    result = {"receipt_version": 1, "status": "pass", "sample_id": sample_id,
        "wire_encoding": wire_arguments.get("wire_encoding", "json"),
        "request_id": arguments["request_id"], "request_wire_bytes": request_wire_bytes,
        "frozen_selection_bytes": len(utf8_json(external_selection)),
        "selection_binding": "full_independent_witness_and_consumers_and_service_trace_equal",
        "sample_kind": "caller_selected_not_automatically_actual_source",
        "service_exit_code": client.exit_code, "service_cleanup": client.cleanup,
        "gateway_requests": 0, "task_status": production.status, "save": saved,
        "post_sha256": hashlib.sha256(post).hexdigest(), "metrics": prepared.metrics,
        "syntax_reuse": {"production_driver": snapshot_metrics(),
                         "independent_oracle": oracle_metrics(),
                         "service_preparation": trace["metrics"]["syntax"]},
        "independent_selection_seconds": independent_selection_seconds,
        "service_and_production_consume_seconds": service_and_production_consume_seconds,
        "independent_consume_seconds": independent_consume_seconds,
        "save_and_readback_seconds": time.monotonic() - save_started,
        "internal_elapsed_seconds": guard.check(),
        "process_exit_evidence": "requires_outer_command_actual_exit_receipt",
        "not_proven": ["online_generation", "human_semantics", "host_loading"]}
    if selection_version == "question-scout-v2":
        result["selection_version"] = selection_version
    final_guard = operation_guard
    workspace = open_root(str(root))
    try:
        leaf = open_relative(workspace, PARENT + "/" + sample_id, directory=True)
        try:
            _save_exact(leaf, "preflight.receipt.json", utf8_json(result), final_guard)
            os.fsync(leaf)
            final_guard.check()
        finally:
            os.close(leaf)
    finally:
        os.close(workspace)
    final_guard.check()
    return result


def main():
    parser = argparse.ArgumentParser(description="显式请求与样本；不会自动推进三次真实预检")
    parser.add_argument("--request", required=True)
    parser.add_argument("--sample", choices=PREFLIGHT_SAMPLES + COVERAGE_PREFLIGHT_SAMPLES,
                        required=True)
    args = parser.parse_args()
    path = Path(args.request)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("request_must_be_workspace_relative")
    guard = PreflightGuard()
    result = run_preflight(Path.cwd(), json.loads(path.read_bytes()), args.sample, guard=guard)
    print(json.dumps({"status": result["status"], "sample_id": args.sample,
                      "internal_elapsed_seconds": result["internal_elapsed_seconds"]}), flush=True)
    ExitGuard(guard).check()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exception:
        print(json.dumps({"status": "blocked", "error": type(exception).__name__,
                          "reason": str(exception)[:512]}), file=sys.stderr, flush=True)
        raise SystemExit(2)
