"""Independent offline receiver/evaluator. No production imports or execution.

The frozen anchor matching predicate is unchanged. New source kinds do not
waive expected prose review; coverage never reports semantic acceptance.
"""
from __future__ import annotations

import hashlib
import json

from v2_validator import decode, digest


def dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def load(raw):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = item
        return result
    def reject(_):
        raise ValueError("nonfinite")
    text = raw.decode("utf-8") if type(raw) is bytes else raw
    value = json.loads(text, object_pairs_hook=unique, parse_constant=reject)
    def types(item, depth=0):
        if depth > 64:
            raise ValueError("depth")
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                types(child, depth + 1)
        elif type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                types(child, depth + 1)
        else:
            raise ValueError("json_domain")
    types(value)
    dump(value)
    return value


def restricted_v2(value):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise ValueError("v2_depth")
        if item is None or type(item) in (str, bool, int):
            continue
        if type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict and all(type(key) is str for key in item):
            pending.extend((child, depth + 1) for child in item.values())
        else:
            raise ValueError("v2_json_domain")


def core_check(value, raw_files, project_id, policy_id):
    restricted_v2(value)
    if type(value) is not dict or set(value) != {"render_version", "input_id", "policy_id", "scope", "package", "wire_limit", "core_id"}:
        raise ValueError("core_fields")
    if value["render_version"] != "offline-core-v2-1" or type(policy_id) is not str or not policy_id or value["policy_id"] != policy_id:
        raise ValueError("core_policy_or_renderer")
    unsigned = {key: item for key, item in value.items() if key != "core_id"}
    if value["core_id"] != digest("core-v2", unsigned):
        raise ValueError("core_digest")
    inputs = [[path, hashlib.sha256(raw_files[path]).hexdigest()] for path in sorted(raw_files)]
    if value["scope"] != sorted(raw_files) or value["input_id"] != digest("input-v2", inputs):
        raise ValueError("stale_core")
    package = decode(dump(value["package"]), raw_files)
    if package["contract_version"] != 2 or package["project_id"] != project_id or type(value["wire_limit"]) is not int or value["wire_limit"] != {"A": 22016, "B": 12288}[package["scenario"]]:
        raise ValueError("core_binding")
    if sorted(f["path"] for f in package["files"]) != sorted(raw_files):
        raise ValueError("core_inventory")
    for table in ("files", "units", "owners", "conditions", "role_proofs", "facts", "relations", "obligations"):
        if [item["id"] for item in package[table]] != sorted(item["id"] for item in package[table]):
            raise ValueError("core_registry_order")
    return package


def receive(raw, raw_files, *, request_id, scenario, return_bytes, project_id=None,
            policy_id=None, protocol_version="2024-11-05", legacy_validator=None):
    if protocol_version != "2024-11-05" or type(scenario) is not str or scenario not in {"A", "B"}:
        raise ValueError("protocol")
    if type(request_id) not in (str, int) or len(str(request_id)) > 128 or len(str(request_id).encode()) > 512:
        raise ValueError("identity")
    if type(raw) is not bytes or type(return_bytes) is not int or return_bytes < 1 or len(raw) > min(return_bytes, 24576 if scenario == "A" else 12288):
        raise ValueError("received_wire_budget")
    if not raw.endswith(b"\n") or b"\n" in raw[:-1]:
        raise ValueError("frame_boundary")
    envelope = load(raw)
    if type(envelope) is not dict or set(envelope) != {"jsonrpc", "id", "result"} or envelope["jsonrpc"] != "2.0" or type(envelope["id"]) is not type(request_id) or envelope["id"] != request_id:
        raise ValueError("frame_identity")
    result = envelope["result"]
    if type(result) is not dict or set(result) != {"content", "isError"} or type(result["isError"]) is not bool or type(result["content"]) is not list or len(result["content"]) != 1:
        raise ValueError("result_fields")
    content = result["content"][0]
    if type(content) is not dict or set(content) != {"type", "text"} or content["type"] != "text" or type(content["text"]) is not str:
        raise ValueError("text_content")
    report = load(content["text"])
    if type(report) is not dict or type(report.get("contract_version")) is not int:
        raise ValueError("version")
    if report["contract_version"] == 1:
        if legacy_validator is None:
            raise ValueError("legacy_validator_required")
        legacy_validator(report, raw_files)
        package = report
        core = None
    elif report["contract_version"] == 2:
        restricted_v2(report)
        if len(raw) > min(return_bytes, 22016 if scenario == "A" else 12288):
            raise ValueError("v2_wire_budget")
        if report.get("kind") == "publication_blocked":
            expected = {"contract_version": 2, "kind": "publication_blocked", "status": "blocked",
                        "reason": "required_knowledge_not_delivered", "knowledge_delivered": False}
            if report != expected or report["knowledge_delivered"] is not False or result["isError"] is not True:
                raise ValueError("blocked_not_success")
            return {"version": 2, "package": report, "core": None, "status": "blocked"}
        if set(report) != {"contract_version", "kind", "status", "core", "observations"} or report["kind"] != "sealed_knowledge" or type(report["observations"]) is not dict or len(dump(report["observations"])) > 512:
            raise ValueError("publication_fields")
        if type(project_id) is not str or not project_id or type(policy_id) is not str or not policy_id:
            raise ValueError("expected_binding")
        core = report["core"]
        package = core_check(core, raw_files, project_id, policy_id)
        if package["scenario"] != scenario or package["status"] != report["status"]:
            raise ValueError("publication_status_or_scenario")
    else:
        raise ValueError("unsupported_version")
    if result["isError"] != (package["status"] in {"blocked", "failed"}):
        raise ValueError("is_error_conflict")
    return {"version": report["contract_version"], "package": package, "core": core, "status": package["status"]}


def dependency_paths(package, table, index):
    reached = {(table, index)}
    previous = -1
    while len(reached) != previous:
        previous = len(reached)
        for name, position in list(reached):
            item = package[name][position]
            if name == "sources":
                reached.update({("units", item["unit"]), ("owners", item["owner"])})
            if name in {"conditions", "owners", "role_proofs"}:
                reached.add(("units", item["unit"]))
            if name == "conditions":
                reached.add(("sources", item["source"]))
            if name == "relations":
                reached.add(("units", item["from_unit"]))
                if item["to_unit"] is not None:
                    reached.add(("units", item["to_unit"]))
            for target in ("sources", "relations", "conditions"):
                if type(item.get(target)) is list:
                    reached.update((target, i) for i in item[target])
    return {package["files"][package[name][position]["file"]]["path"]
            for name, position in reached if name in {"units", "sources", "owners"}}


def receive_task(raw, core, raw_files, *, task_id, scope, policy_id="lifecycle-v2-1"):
    if type(raw) is not bytes or len(raw) > 12288:
        raise ValueError("task_budget")
    value = load(raw)
    restricted_v2(value)
    if dump(value) != raw or type(value) is not dict or set(value) != {"delta_version", "delta_id", "core_id", "input_id", "project_id", "task_id", "scope", "evidence", "references"} or type(value["delta_version"]) is not int or value["delta_version"] != 1:
        raise ValueError("task_fields")
    package = core_check(core, raw_files, value["project_id"], policy_id)
    if type(task_id) is not str or not task_id or len(task_id.encode()) > 128 or value["task_id"] != task_id or value["core_id"] != core["core_id"] or value["input_id"] != core["input_id"]:
        raise ValueError("task_binding")
    if type(scope) is not list or not scope or any(type(path) is not str for path in scope) or scope != sorted(set(scope)) or value["scope"] != scope or not set(scope) <= set(core["scope"]):
        raise ValueError("task_scope")
    evidence = decode(dump(value["evidence"]), {path: raw_files[path] for path in scope})
    if evidence["contract_version"] != 2 or evidence["scenario"] != "B" or evidence["project_id"] != value["project_id"] or sorted(f["path"] for f in evidence["files"]) != scope:
        raise ValueError("task_evidence")
    if type(value["references"]) is not list:
        raise ValueError("references")
    seen = set()
    for ref in value["references"]:
        if type(ref) is not dict or set(ref) != {"space", "table", "index"} or type(ref["space"]) is not str or ref["space"] not in {"core", "task"} or type(ref["table"]) is not str or ref["table"] not in {"sources", "facts", "relations", "role_proofs"} or type(ref["index"]) is not int:
            raise ValueError("reference_namespace")
        source = package if ref["space"] == "core" else evidence
        if not 0 <= ref["index"] < len(source[ref["table"]]):
            raise ValueError("reference_index")
        key = ref["space"], ref["table"], ref["index"]
        if key in seen:
            raise ValueError("duplicate_reference")
        seen.add(key)
        if ref["space"] == "core":
            paths = dependency_paths(source, ref["table"], ref["index"])
            if not paths or not paths <= set(scope):
                raise ValueError("cross_scope_dependency")
    if value["delta_id"] != digest("task-v2", {key: item for key, item in value.items() if key != "delta_id"}):
        raise ValueError("delta_digest")
    return value


def evaluate_frozen(received, frozen):
    """Same path/symbol/containing-lines rule as the frozen V1 driver.

    Complete statements may be evaluated when an anchor actually names them.
    They do not satisfy definition anchors by source-kind exemptions. expected
    prose remains evaluation-only, intact, and never yields automatic semantics.
    """
    if type(frozen) is not dict or frozen.get("version") != 1 or type(frozen.get("anchors")) is not list:
        raise ValueError("frozen_asset")
    package = received["package"]
    if received["core"] is None and received["version"] == 2:
        return {"status": "publication_blocked", "checks": [], "semantic_acceptance": "not_proven"}
    if received["version"] == 1:
        sources = [{"id": s["source_id"], "path": s["path"], "symbol": s["symbol"],
                    "start": s["start_line"], "end": s["end_line"]} for s in package["sources"]]
    else:
        sources = []
        for s in package["sources"]:
            unit = package["units"][s["unit"]]
            sources.append({"id": s["id"], "path": package["files"][s["file"]]["path"],
                            "symbol": unit["qualified_name"], "start": unit["start"][0], "end": unit["end"][0]})
    checks = []
    for anchor in frozen["anchors"]:
        matches = [s for s in sources if s["path"] == anchor["path"] and s["symbol"] == anchor["symbol"]
                   and s["start"] <= anchor["lines"][0] and s["end"] >= anchor["lines"][1]]
        checks.append({"id": anchor["id"], "body_coverage": bool(matches),
                       "source_ids": [s["id"] for s in matches], "expected": anchor["expected"],
                       "semantic_support": "requires_independent_review"})
    return {"status": "coverage_only_semantics_pending", "checks": checks,
            "semantic_acceptance": "requires_independent_review"}
