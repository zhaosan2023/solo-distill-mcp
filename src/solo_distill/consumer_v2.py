"""Explicit offline consumer: authenticate before use, never activate a host/model.

V1 is passed unchanged to the caller's existing validator. Task evidence is an
independent package, not appended to or indexed into the immutable core tables.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .core_v2 import SealedCore, authenticate_seal, canonical, digest
from .package_v2 import validate_package
from .wire_v2 import PROTOCOL_VERSION, response_id

REFERENCE_TABLES = {"sources", "facts", "relations", "role_proofs"}


def strict_load(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    def reject(_):
        raise ValueError("nonfinite_json")
    try:
        # Legacy reports include finite elapsed-time floats. Apply the restricted
        # V2 canonical domain only after version dispatch, never reinterpret V1.
        text = raw.decode("utf-8") if type(raw) is bytes else raw
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject)
        # Overflowing JSON exponents must also fail before a legacy callback.
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        return value
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid_json") from error


def check_cancel(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise ValueError("offline_consumption_cancelled")


@dataclass(frozen=True)
class Consumed:
    version: int
    status: str
    package_bytes: bytes
    core: SealedCore | None = None

    @property
    def package(self):
        return strict_load(self.package_bytes)

    @property
    def prefix(self):
        return b"" if self.core is None else self.core.prefix


def consume_frame(raw, raw_files, *, protocol_version, request_id, scenario,
                  return_bytes, project_id=None, policy_id=None,
                  legacy_validator=None, cancelled=None, transport=None):
    """Count the received wire, then version-decode and independently authenticate.

    No disk reads, writes, source imports, network, timers or implicit dispatch.
    The legacy validator is explicitly supplied by the existing V1 caller.
    """
    check_cancel(cancelled)
    if protocol_version != PROTOCOL_VERSION or scenario not in {"A", "B"}:
        raise ValueError("unsupported_consumer_protocol_or_scenario")
    response_id(request_id)
    if type(raw) is not bytes or type(return_bytes) is not int or return_bytes < 1:
        raise ValueError("invalid_consumer_input")
    manifest_report = None
    if transport is not None:
        from .adaptive_wire import TransportCapabilities, reassemble
        from .telemetry import POLICY
        if type(transport) is not TransportCapabilities or scenario != "A" or policy_id != POLICY:
            raise ValueError("adaptive_consumer_binding_required")
        raw, manifest_report = reassemble(raw, transport, request_id=request_id,
            frame_budget=return_bytes, cancelled=cancelled)
    # Legacy A keeps 24,576; only explicitly negotiated adaptive A can exceed it.
    elif len(raw) > min(return_bytes, 24576 if scenario == "A" else 12288):
        raise ValueError("wire_over_budget")
    if not raw.endswith(b"\n") or b"\n" in raw[:-1]:
        raise ValueError("invalid_frame_boundary")
    try:
        envelope = strict_load(raw)
        if type(envelope) is not dict or set(envelope) != {"jsonrpc", "id", "result"} or envelope["jsonrpc"] != "2.0" or type(envelope["id"]) is not type(request_id) or envelope["id"] != request_id:
            raise ValueError("response_identity_conflict")
        result = envelope["result"]
        if type(result) is not dict or set(result) != {"content", "isError"} or type(result["isError"]) is not bool or type(result["content"]) is not list or len(result["content"]) != 1:
            raise ValueError("invalid_result")
        content = result["content"][0]
        if type(content) is not dict or set(content) != {"type", "text"} or content["type"] != "text" or type(content["text"]) is not str:
            raise ValueError("invalid_text_content")
        report = strict_load(content["text"])
        if type(report) is not dict or type(report.get("contract_version")) is not int:
            raise ValueError("invalid_version")
        version = report["contract_version"]
        if version == 1:
            if transport is not None:
                raise ValueError("adaptive_transport_cannot_reinterpret_v1")
            if legacy_validator is None:
                raise ValueError("legacy_validator_required")
            legacy_validator(report, raw_files)
            if result["isError"] != (report.get("status") in {"blocked", "failed"}):
                raise ValueError("legacy_status_conflict")
            check_cancel(cancelled)
            legacy_bytes = json.dumps(report, ensure_ascii=False, allow_nan=False,
                                      separators=(",", ":")).encode("utf-8")
            return Consumed(1, report.get("status", "unknown"), legacy_bytes)
        if version != 2:
            raise ValueError("unsupported_version")
        if transport is None and len(raw) > min(return_bytes, 22016 if scenario == "A" else 12288):
            raise ValueError("v2_wire_over_budget")
        if report.get("kind") == "publication_blocked":
            expected = {"contract_version": 2, "kind": "publication_blocked", "status": "blocked",
                        "reason": "required_knowledge_not_delivered", "knowledge_delivered": False}
            if report != expected or report["knowledge_delivered"] is not False or result["isError"] is not True:
                raise ValueError("forged_publication_failure")
            check_cancel(cancelled)
            return Consumed(2, "blocked", canonical(report))
        if set(report) != {"contract_version", "kind", "status", "core", "observations"} or report["kind"] != "sealed_knowledge" or type(report["observations"]) is not dict or len(canonical(report["observations"])) > 512:
            raise ValueError("invalid_publication_fields")
        if type(project_id) is not str or not project_id or type(policy_id) is not str or not policy_id:
            raise ValueError("expected_core_binding_required")
        from .codec_v2 import ENCODING, unpack_core
        packed = report["core"]
        encoding = ENCODING if type(packed) is dict and "encoding" in packed else "json"
        if encoding != "json" and scenario != "A":
            raise ValueError("compact_transport_A_only")
        if transport is not None and encoding != "json":
            raise ValueError("adaptive_transport_requires_json_core")
        core = SealedCore(canonical(unpack_core(packed, strict_load)), encoding)
        value = authenticate_seal(core, raw_files, policy_id=policy_id)
        package = value["package"]
        if package["scenario"] != scenario or package["project_id"] != project_id or report["status"] != package["status"] or result["isError"] != (package["status"] in {"blocked", "failed"}):
            raise ValueError("publication_binding_or_status_conflict")
        if transport is None:
            from .telemetry import POLICY
            if value["policy_id"] == POLICY:
                raise ValueError("adaptive_core_without_transport_negotiation")
        if manifest_report is not None:
            manifest = manifest_report["manifest"]
            expected = {"project_id": package["project_id"], "snapshot_id": package["snapshot_id"],
                        "input_id": value["input_id"], "core_id": value["core_id"],
                        "policy_id": value["policy_id"], "contract_version": 2}
            if (any(type(manifest[key]) is not type(item) or manifest[key] != item
                    for key, item in expected.items()) or manifest_report["status"] != package["status"]):
                raise ValueError("reassembled_core_manifest_conflict")
        if transport is not None:
            from .syntax_v2 import extract_units
            expected_units = {unit["id"] for path, source in raw_files.items()
                              for unit in extract_units(path, source)["units"]}
            if (not expected_units or {unit["id"] for unit in package["units"]} != expected_units
                    or {file["path"] for file in package["files"]} != set(raw_files)):
                raise ValueError("adaptive_structural_denominator_incomplete")
            from .graph_v2 import extract_relations
            expected_relations = extract_relations(package, raw_files)
            if ({item["id"]: item for item in package["relations"]}
                    != {item["id"]: item for item in expected_relations}
                    or len(package["relations"]) != len(expected_relations)):
                raise ValueError("adaptive_relationship_denominator_incomplete")
        check_cancel(cancelled)
        return Consumed(2, package["status"], canonical(package), core)
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("malformed_publication") from error


def reference_paths(package, table, index):
    """All referenced dependencies, not merely a fact's first source, are scoped."""
    pending, seen, paths = [(table, index)], set(), set()
    while pending:
        table, index = pending.pop()
        if (table, index) in seen:
            continue
        seen.add((table, index))
        item = package[table][index]
        if table in {"sources", "units", "owners"}:
            paths.add(package["files"][item["file"]]["path"])
        if table == "sources":
            pending.extend([("units", item["unit"]), ("owners", item["owner"])])
        if table in {"owners", "conditions", "role_proofs"}:
            pending.append(("units", item["unit"]))
        if table == "conditions":
            pending.append(("sources", item["source"]))
        if table == "relations":
            pending.append(("units", item["from_unit"]))
            if item["to_unit"] is not None:
                pending.append(("units", item["to_unit"]))
        for target in ("sources", "relations", "conditions"):
            if target in item and type(item[target]) is list:
                pending.extend((target, i) for i in item[target])
    return paths


def validate_task(value, core, raw_files, *, task_id, policy_id):
    """Closed namespaces: a bare index can never silently select a core record."""
    sealed = authenticate_seal(core, raw_files, policy_id=policy_id)
    fields = {"delta_version", "delta_id", "core_id", "input_id", "project_id",
              "task_id", "scope", "evidence", "references"}
    if type(value) is not dict or set(value) != fields or type(value["delta_version"]) is not int or value["delta_version"] != 1:
        raise ValueError("invalid_task_fields")
    if type(task_id) is not str or not task_id or len(task_id.encode()) > 128 or value["task_id"] != task_id:
        raise ValueError("task_identity_conflict")
    if (value["core_id"] != sealed["core_id"] or value["input_id"] != sealed["input_id"] or value["project_id"] != sealed["package"]["project_id"]):
        raise ValueError("task_core_binding_conflict")
    scope = value["scope"]
    if type(scope) is not list or not scope or any(type(path) is not str for path in scope) or scope != sorted(set(scope)) or not set(scope) <= set(sealed["scope"]):
        raise ValueError("task_scope_outside_authorization")
    evidence = value["evidence"]
    files = {path: raw_files[path] for path in scope}
    validate_package(evidence, files)
    if evidence["scenario"] != "B" or evidence["project_id"] != value["project_id"] or sorted(f["path"] for f in evidence["files"]) != scope:
        raise ValueError("task_evidence_scope_or_scenario")
    if type(value["references"]) is not list:
        raise ValueError("invalid_task_references")
    seen = set()
    for ref in value["references"]:
        if type(ref) is not dict or set(ref) != {"space", "table", "index"} or type(ref["space"]) is not str or ref["space"] not in {"core", "task"} or type(ref["table"]) is not str or ref["table"] not in REFERENCE_TABLES or type(ref["index"]) is not int:
            raise ValueError("invalid_reference_namespace")
        table = sealed["package"] if ref["space"] == "core" else evidence
        if not 0 <= ref["index"] < len(table[ref["table"]]):
            raise ValueError("dangling_task_reference")
        if ref["space"] == "core":
            paths = reference_paths(table, ref["table"], ref["index"])
            if not paths or not paths <= set(scope):
                raise ValueError("core_reference_outside_task_scope")
        key = ref["space"], ref["table"], ref["index"]
        if key in seen:
            raise ValueError("duplicate_task_reference")
        seen.add(key)
    unsigned = {key: item for key, item in value.items() if key != "delta_id"}
    if value["delta_id"] != digest("task-v2", unsigned):
        raise ValueError("task_identity_digest_conflict")
    return value


@dataclass(frozen=True)
class TaskDelta:
    payload: bytes

    @property
    def document(self):
        return strict_load(self.payload)


def make_task(core, raw_files, *, task_id, scope, evidence, references, policy_id="lifecycle-v2-1"):
    sealed = authenticate_seal(core, raw_files, policy_id=policy_id)
    value = {"delta_version": 1, "core_id": sealed["core_id"], "input_id": sealed["input_id"],
             "project_id": sealed["package"]["project_id"], "task_id": task_id,
             "scope": scope, "evidence": evidence, "references": references}
    value["delta_id"] = digest("task-v2", value)
    validate_task(value, core, raw_files, task_id=task_id, policy_id=policy_id)
    payload = canonical(value)
    if len(payload) > 12288:
        raise ValueError("offline_task_over_budget")
    return TaskDelta(payload)


def consume_task(delta, core, raw_files, *, task_id, scope, policy_id="lifecycle-v2-1", cancelled=None):
    check_cancel(cancelled)
    if type(delta) is not TaskDelta or type(delta.payload) is not bytes or len(delta.payload) > 12288:
        raise ValueError("invalid_task_delta")
    value = strict_load(delta.payload)
    if canonical(value) != delta.payload:
        raise ValueError("noncanonical_task")
    if type(scope) is not list or value.get("scope") != scope:
        raise ValueError("expected_task_scope_conflict")
    validate_task(value, core, raw_files, task_id=task_id, policy_id=policy_id)
    check_cancel(cancelled)
    return value
