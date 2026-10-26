"""Authorized foreground stdio--existing-gateway integration, not a Roo substitute.

Print a bounded receipt for native-tool persistence. Never write FastCode, launch
or reconfigure the gateway, retry a failed business, or send acceptance answers.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time

from solo_distill.client import ClientError, StdioClient
from solo_distill.distillation import TOOL_NAME, utf8_json
from solo_distill.extractor import load_projects
from solo_distill.gateway import strict_json

ROOT = Path(__file__).resolve().parents[1]


def request(scenario, request_id, started_ms):
    # Questions are not reference answers. The independent expected fields in
    # fastcode_acceptance.json are used only AFTER generation, never transmitted.
    seeds = [
        {"path": "mcp_server.py", "symbol": "code_qa"},
        {"path": "fastcode/main.py", "symbol": "FastCode.query"},
        {"path": "fastcode/loader.py", "symbol": "RepositoryLoader.load_from_path"},
        {"path": "fastcode/cache.py", "symbol": "CacheManager.save_dialogue_turn"},
        {"path": "fastcode/answer_generator.py", "symbol": "AnswerGenerator.generate"},
        {"path": "fastcode/parser.py", "symbol": "CodeParser.parse_file"},
    ]
    semantic = [
        ("query_flow", "Explain index readiness, query processing branches, retrieval filtering and generation context; distinguish possible execution from static syntax."),
        ("state_ownership", "Explain query/session state, persistence prerequisites, return-value handling and ownership; identify caller and callee evidence."),
        ("failure_boundaries", "Explain query and generation exception handling, input/context limits and whether returned text proves success."),
        ("scope_and_verification", "Explain parser support, local repository loading boundaries, related verification navigation and static blind spots; do not invent tests or runtime evidence."),
    ]
    dimensions = [{"id": identity, "question": question, "required": True,
                   "evidence": "sourced_inference"} for identity, question in semantic]
    for identity, path, expression, question in [
        ("caller_query", "mcp_server.py", "fc.query", "Locate the MCP caller's query expression."),
        ("query_persistence", "fastcode/main.py", "self.cache_manager.save_dialogue_turn", "Locate the query-side persistence call expression."),
        ("loader_side_effect", "fastcode/loader.py", "shutil.copytree", "Locate the loader's filesystem-copy expression."),
    ]:
        dimensions.append({"id": identity, "question": question, "required": True,
                           "evidence": "direct_static", "selector": {
                               "record_kind": "call_expression", "path": path,
                               "expression": expression}})
    return {
        "contract_version": 1, "request_id": request_id,
        "project_id": "fastcode-sample", "scenario": scenario,
        "question": "For the read-only FastCode sample, map the code_qa to FastCode.query path and the caller/callee, state, filesystem, context-limit, error and parser boundaries needed before adapting it. Do not execute FastCode or claim tests ran.",
        "include": list(dict.fromkeys(seed["path"] for seed in seeds)) if scenario == "B" else ["."],
        "seeds": seeds if scenario == "B" else [],
        "dimensions": dimensions,
        "started_at_unix_ms": started_ms,
        "deadline_unix_ms": started_ms + 180000,
        "host_timeout_seconds": 180,
        "return_bytes": 12288 if scenario == "B" else 24576,
    }


def check_sources(package, sample_root):
    sources = package.get("sources", [])
    identities = set()
    for source in sources:
        identity = source["source_id"]
        if identity in identities:
            raise ClientError("duplicate_source")
        identities.add(identity)
        relative = Path(source["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ClientError("source_outside_scope")
        path = sample_root / relative
        if path.is_symlink() or not path.is_file():
            raise ClientError("source_not_regular")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["content_sha256"]:
            raise ClientError("source_version_conflict_in_driver_review")
        tree = ast.parse(raw, filename=source["path"])
        lines = raw.splitlines()
        if not 1 <= source["start_line"] <= source["end_line"] <= len(lines):
            raise ClientError("source_line_range_invalid")
        if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   and node.end_lineno == source["end_line"]
                   and min([node.lineno, *[decorator.lineno for decorator in node.decorator_list]]) == source["start_line"]
                   for node in ast.walk(tree)):
            raise ClientError("source_is_not_complete_definition")
    for conclusion in package.get("conclusions", []):
        if not set(conclusion["source_ids"]) <= identities:
            raise ClientError("conclusion_source_identity_invalid")
        if conclusion["evidence_level"] != "sourced_inference":
            raise ClientError("model_evidence_level_promoted")
    fixture = strict_json((ROOT / "tests" / "fastcode_acceptance.json").read_text())
    checks = []
    for anchor in fixture["anchors"]:
        matches = [source for source in sources
                   if source["path"] == anchor["path"] and source["symbol"] == anchor["symbol"]
                   and source["start_line"] <= anchor["lines"][0]
                   and source["end_line"] >= anchor["lines"][1]]
        checks.append({"id": anchor["id"], "body_coverage": bool(matches),
                       "source_ids": [source["source_id"] for source in matches],
                       "semantic_support": "requires_independent_review"})
    return checks


def check_scratch(package):
    """Verify the persisted full object without injecting it into the receipt."""
    expected = "verification/.scratch/" + package["request_id"] + ".full.json"
    if package.get("full_evidence_trace") != expected:
        raise ClientError("scratch_pointer_invalid")
    path = ROOT
    for part in Path(expected).parts:
        path = path / part
        if path.is_symlink():
            raise ClientError("scratch_symlink")
    if not path.is_file():
        raise ClientError("scratch_not_persisted")
    raw = path.read_bytes()
    saved = strict_json(raw.decode("utf-8"))
    original = saved["report"]
    if (saved["scratch_version"] != 1 or original["request_id"] != package["request_id"]
            or original["scenario"] != package["scenario"] or original["status"] != package["status"]
            or original["full_evidence_trace"] != expected):
        raise ClientError("scratch_identity_or_status_conflict")
    outputs = saved["model_outputs"]
    if "uncertainties" in original:
        declared_unknowns = [unknown for item in outputs if item["stage"] == "compression"
                             for unknown in item.get("candidate", {}).get("unknowns", [])]
        model_unknowns = [item["detail"] for item in original["uncertainties"]
                          if item["reason"] == "model_reported_unknown"]
        if model_unknowns != declared_unknowns:
            raise ClientError("scratch_uncertainties_not_preserved")
        local_unknowns = [item for item in original["uncertainties"]
                          if item["reason"] != "model_reported_unknown"]
        expected_local = [{**item,
                           "detail": "Inventory only; non-Python content was not parsed or read as body evidence."}
                          for item in saved["structure"]["unfinished"]
                          if original["scenario"] == "A"
                          and item["reason"] == "unsupported_language_or_asset"]
        if local_unknowns != expected_local:
            raise ClientError("scratch_inventory_blind_spots_not_preserved")
        if original["scenario"] == "A":
            expected_unfinished = [item for item in saved["structure"]["unfinished"]
                                   if item["reason"] != "unsupported_language_or_asset"]
            if any(item not in original["unfinished"] for item in expected_unfinished):
                raise ClientError("scratch_scan_obligations_not_preserved")
            if original["coverage"] != saved["structure"]["coverage"]:
                raise ClientError("scratch_inventory_denominator_changed")
    if not any(item["stage"] == "compression" and isinstance(item.get("candidate"), dict)
               and isinstance(item["answer"], str) and item["answer"] for item in outputs):
        raise ClientError("scratch_full_proposal_missing")
    if not saved["source_bodies"] or not all("text" in item for item in saved["source_bodies"]):
        raise ClientError("scratch_full_sources_missing")
    if "compaction" in package:
        for key, count in package["compaction"]["omitted_counts"].items():
            if count != len(original[key]) - len(package.get(key, [])):
                raise ClientError("pin_omission_accounting_invalid")
    return {"path": expected, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "full_conclusions": len(original["conclusions"]), "full_sources": len(original["sources"]),
            "full_unfinished": len(original["unfinished"]),
            "full_uncertainties": len(original.get("uncertainties", [])), "model_outputs": len(outputs),
            "status": "full_proposal_and_evidence_persisted"}


def run(scenario):
    started = time.monotonic()
    started_ms = int(time.time() * 1000)
    receipt = {
        "receipt_version": 1, "kind": "live_stdio_engineering_integration",
        "scenario": scenario, "status": "blocked", "request_id": "live-" + scenario.lower() + "-" + str(started_ms),
        "host": "owned_engineering_stdio_client_not_Roo", "host_acceptance": "not_proven",
        "semantic_acceptance": "requires_independent_review", "performance_limit_seconds": 180,
        "outer_business_replays": 0, "sample_writes": 0,
        "controller_coverage": "not_proven_platform_task_record_used",
        "driver_command": [sys.executable, "-B", "-S", "tests/run_live_integration.py", "--scenario", scenario],
        "failure": None, "package": None,
    }
    value = request(scenario, receipt["request_id"], started_ms)
    sample_root = Path(load_projects(str(ROOT / "config" / "projects.json"))["fastcode-sample"].root)
    client = None
    try:
        client = StdioClient(ROOT, online=True, deadline=started + 180)
        discovery = client.initialize()
        if "error" in discovery or TOOL_NAME not in {
            tool["name"] for tool in discovery["result"]["tools"]}:
            raise ClientError("distillation_tool_not_discovered")
        receipt["initialization_seconds"] = round(time.monotonic() - started, 6)
        client.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": TOOL_NAME, "arguments": value}})
        response = client.receive(3)
        receipt["request_to_consumption_seconds"] = round(time.monotonic() - started, 6)
        if "error" in response:
            receipt["protocol_error"] = response["error"]
            raise ClientError("business_result_not_delivered")
        package = strict_json(response["result"]["content"][0]["text"])
        receipt["package"] = package
        receipt["wire_response_bytes"] = len(utf8_json(response)) + 1
        receipt["pin_bytes"] = len(utf8_json(package))
        if (receipt["pin_bytes"] > value["return_bytes"]
                or receipt["wire_response_bytes"] > value["return_bytes"]):
            raise ClientError("pin_or_wire_budget_exceeded")
        if package["request_id"] != value["request_id"] or package["scenario"] != scenario:
            raise ClientError("business_identity_conflict")
        if receipt["request_to_consumption_seconds"] > 180:
            raise ClientError("performance_deadline_exceeded")
        if (package["status"] != "complete" or response["result"]["isError"]
                or package.get("unfinished")):
            receipt["business_failure_reasons"] = [item["reason"] for item in package["unfinished"]
                                                   if "reason" in item]
            raise ClientError("business_not_complete")
        receipt["scratch_check"] = check_scratch(package)
        if package["freshness"] != "current_at_final_verification":
            raise ClientError("final_source_freshness_unproven")
        attempts = package["observations"]["gateway_requests"]
        if not 1 <= len(attempts) <= 2:
            raise ClientError("gateway_request_count_invalid")
        for attempt in attempts:
            if (attempt["request_model"] != "gpt-6-sol"
                    or attempt["endpoint"] != "http://127.0.0.1:8080/v1/chat/completions"
                    or attempt["http_status"] != 200 or attempt["finish_reason"] != "stop"
                    or not attempt["done_marker"] or attempt["outer_replays"] != 0
                    or attempt["client_auto_retries"] != 0):
                raise ClientError("gateway_transport_gate_missing")
        receipt["anchor_checks"] = check_sources(package, sample_root)
        if not all(check["body_coverage"] for check in receipt["anchor_checks"]):
            raise ClientError("required_reference_body_missing")
        receipt["status"] = "transport_structure_sources_pass_semantics_and_Roo_pending"
    except (ClientError, ValueError, TypeError, KeyError, OSError) as exception:
        receipt["failure"] = str(exception) if isinstance(exception, ClientError) else type(exception).__name__
        receipt["status"] = "blocked"
    finally:
        if client is not None:
            client.close()
            receipt["child_exit_code"] = client.exit_code
            receipt["cleanup"] = client.cleanup
            receipt["stderr_bytes"] = len(client.stderr)
            receipt["stderr_sha256"] = hashlib.sha256(client.stderr).hexdigest()
            if client.cleanup != "confirmed_natural_exit" or client.exit_code != 0 or client.stderr:
                receipt["status"] = "blocked"
                receipt["cleanup_gate"] = "not_passed"
        else:
            receipt["cleanup"] = "no_child_observed"
        receipt["total_with_cleanup_seconds"] = round(time.monotonic() - started, 6)
    print(json.dumps(receipt, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
    return 0 if receipt["status"] == "transport_structure_sources_pass_semantics_and_Roo_pending" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="已获准真实网关集成；不是 Roo 验收")
    parser.add_argument("--scenario", choices=["A", "B"], required=True)
    parser.add_argument("--v2", action="store_true", help="显式获准 V2 单次 A；V1 默认不变")
    options = parser.parse_args()
    if options.v2:
        if options.scenario != "A":
            parser.error("本轮 V2 仅授权场景 A")
        from live_v2_driver import run as run_v2
        raise SystemExit(run_v2())
    raise SystemExit(run(options.scenario))
