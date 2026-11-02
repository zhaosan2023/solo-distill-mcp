"""Offline delivery degradation, not a runtime FSM or a call-graph resolver.

P0 imports and P1 ALL definitions are atomic mandatory evidence (including
methods/nested definitions). P2 retains explicitly resolved or module-scope
call sites first; other unresolved local expressions are optional P3. A call
site is never promoted to a resolved graph edge merely by its priority.
The table representation factors provenance without losing source records.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

COLUMNS = {
    "symbols": ["source_id", "start_line", "end_line", "kind", "qualified_name", "bases", "conditions"],
    "imports": ["source_id", "start_line", "end_line", "module", "name", "alias", "level", "scope", "conditions"],
    "calls": ["source_id", "start_line", "end_line", "caller", "expression", "target_resolution", "runtime_execution", "conditions"],
}


def expand_entries(report: dict) -> list[dict]:
    """Decode lossless tables; legacy records remain supported, never mutated."""
    encoding = report.get("record_encoding")
    if encoding is None:
        return copy.deepcopy(report.get("entries", []))
    if encoding != {"version": 1, "format": "source_tables_v1", "columns": COLUMNS,
                    "inherit": ["path", "content_sha256"],
                    "evidence_level": "direct_static", "symbol_name": "qualified_name_last_component"}:
        raise ValueError("unsupported_structure_encoding")
    entries = copy.deepcopy(report.get("entries", []))
    for entry in entries:
        if entry.get("status") != "parsed":
            continue
        conditions = entry.pop("condition_table")
        if not isinstance(conditions, list) or not all(isinstance(x, str) for x in conditions):
            raise ValueError("invalid_condition_table")
        for key, columns in COLUMNS.items():
            decoded = []
            for row in entry[key]:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise ValueError("invalid_structure_row")
                record = dict(zip(columns, row))
                refs = record["conditions"]
                if not isinstance(refs, list) or any(type(i) is not int or not 0 <= i < len(conditions) for i in refs):
                    raise ValueError("dangling_condition_reference")
                record["conditions"] = [conditions[i] for i in refs]
                record.update(path=entry["path"], content_sha256=entry["content_sha256"],
                              evidence_level="direct_static")
                if key == "symbols":
                    record["name"] = record["qualified_name"].rsplit(".", 1)[-1]
                    if record["kind"] != "class":
                        record.pop("bases")
                decoded.append(record)
            entry[key] = decoded
    return entries


def _table(report: dict) -> dict:
    value = copy.deepcopy(report)
    value["record_encoding"] = {
        "version": 1, "format": "source_tables_v1", "columns": copy.deepcopy(COLUMNS),
        "inherit": ["path", "content_sha256"], "evidence_level": "direct_static",
        "symbol_name": "qualified_name_last_component"}
    for entry in value["entries"]:
        if entry["status"] != "parsed":
            continue
        conditions: list[str] = []
        for key, columns in COLUMNS.items():
            rows = []
            for record in entry[key]:
                refs = []
                for condition in record["conditions"]:
                    if condition not in conditions:
                        conditions.append(condition)
                    refs.append(conditions.index(condition))
                rows.append([refs if field == "conditions" else record.get(field)
                             for field in columns])
            entry[key] = rows
        entry["condition_table"] = conditions
    return value


def _dependency_graph(report: dict) -> dict:
    """One source-backed declared dependency per import, not name heuristics.

    Only exact module matches within the selected files get a file endpoint.
    External/unselected modules and from-package symbol ambiguity stay null.
    Conditions/scope/provenance are inherited from the referenced import row.
    """
    modules: dict[str, list[int]] = {}
    for index, entry in enumerate(report["entries"]):
        if entry["status"] == "parsed":
            module = entry["path"].removesuffix(".py").replace("/", ".")
            if module == "__init__":
                module = ""
            else:
                module = module.removesuffix(".__init__")
            modules.setdefault(module, []).append(index)
    edges = []
    for index, entry in enumerate(report["entries"]):
        for number, record in enumerate(entry.get("imports", [])):
            module = record["module"] or ""
            level = record["level"]
            if level:
                parent = entry["path"].split("/")[:-1]
                if level <= len(parent):
                    module = ".".join(parent[:len(parent) - level + 1] + ([module] if module else []))
                else:
                    module = None
            destinations = modules.get(module, [])
            # from . import X does not prove whether X is a module or a value.
            target = destinations[0] if len(destinations) == 1 and record["module"] else None
            edges.append([index, number, target])
    return {"kind": "declared_import_dependencies", "direction": "importer_to_module",
            "columns": ["from_entry", "import_record", "to_entry_or_unknown"],
            "edges": edges, "call_graph": "not_resolved",
            "null_target": "external_unselected_or_ambiguous", "evidence": "referenced_import_record"}


def _p2(record: dict) -> bool:
    # Do not use substrings, built-in name lists or alias guesses as resolution.
    return record.get("target_resolution") == "resolved_static" or record.get("caller") == "<module>"


def deliver(report: dict, budget: int, *, measure: Callable[[dict], int],
            hard_limit: int, budget_unit: str) -> dict:
    from .extractor import InputError
    ceiling = min(24576, hard_limit)
    budget = min(budget, ceiling)
    entries = report["entries"]
    counts = {
        "p0": sum(len(e.get("imports", [])) for e in entries),
        "p1": sum(len(e.get("symbols", [])) for e in entries),
        "p2": sum(_p2(r) for e in entries for r in e.get("calls", [])),
        "p3": sum(not _p2(r) for e in entries for r in e.get("calls", [])),
    }
    source_complete = (report["status"] == "complete" and not report["unfinished"]
                       and report["inventory_complete"] is True
                       and report["freshness"] == "current_at_verification")

    def decorate(value, delivered, state, estimate_scope):
        omitted = {key: counts[key] - delivered[key] for key in counts}
        value["delivery"] = {"version": 1, "state": state,
            "source_status": report["status"], "core_complete": source_complete and state != "CORE_BLOCKED",
            "required_counts": {"p0": counts["p0"], "p1": counts["p1"]},
            "delivered_counts": delivered, "omitted_counts": omitted,
            "p1_scope": "all_definitions_including_methods_and_nested"}
        value["partial"] = not source_complete or any(omitted.values())
        value["backpressure"] = {"reason": "return_budget" if any(omitted.values()) else "none",
            "estimated_full_bytes": 0, "estimate_kind": "exact_current_serialization",
            "estimate_scope": estimate_scope, "budget_unit": budget_unit,
            "hard_limit_bytes": ceiling, "omitted_count": sum(omitted.values()),
            "retryable": False, "hard_limit_exceeded": False, "source_status": report["status"]}
        return value

    def estimate(value):
        diagnostic = value["backpressure"]
        for _ in range(8):
            size = measure(value)
            if size == diagnostic["estimated_full_bytes"]:
                return size
            diagnostic["estimated_full_bytes"] = size
        raise InputError("核心响应字节诊断未收敛")

    # Preserve legacy small complete deliveries and all Scanner consumer records.
    full = decorate(copy.deepcopy(report), dict(counts), "FULL", "all_records")
    full["graph"] = _dependency_graph(report)
    full_size = estimate(full)
    if full_size <= budget:
        return full

    compact = _table(report)
    compact["graph"] = _dependency_graph(report)
    candidates = {"p2": [], "p3": []}
    for index, entry in enumerate(entries):
        for number, record in enumerate(entry.get("calls", [])):
            tier = "p2" if _p2(record) else "p3"
            candidates[tier].append((index, number))
    all_rows = {i: entry.get("calls", []) for i, entry in enumerate(compact["entries"])}

    def candidate(n2, n3):
        value = copy.deepcopy(compact)
        chosen = set(candidates["p2"][:n2] + candidates["p3"][:n3])
        for index, entry in enumerate(value["entries"]):
            if entry["status"] == "parsed":
                entry["calls"] = [copy.deepcopy(row) for number, row in enumerate(all_rows[index])
                                  if (index, number) in chosen]
        delivered = {"p0": counts["p0"], "p1": counts["p1"], "p2": n2, "p3": n3}
        pruned = counts["p2"] + counts["p3"] - n2 - n3
        value["unfinished"] = copy.deepcopy(report["unfinished"])
        if pruned:
            value["unfinished"].append({"path": ".", "reason": "optional_call_expressions_pruned", "count": pruned})
        if report["status"] == "complete":
            value["status"] = "partial" if pruned else "complete"
        # Conditions belonging only to discarded P3 sites must not consume the
        # mandatory core. Remap references losslessly for the retained records.
        for entry in value["entries"]:
            if entry["status"] != "parsed":
                continue
            table = entry["condition_table"]
            used = sorted({ref for key in COLUMNS for row in entry[key] for ref in row[-1]})
            mapping = {old: new for new, old in enumerate(used)}
            for key in COLUMNS:
                for row in entry[key]:
                    row[-1] = [mapping[ref] for ref in row[-1]]
            entry["condition_table"] = [table[ref] for ref in used]
        return decorate(value, delivered, "CORE_VALID" if pruned else "FULL", "core_p0_p1")

    core = candidate(0, 0)
    core_size = estimate(core)
    if core_size > budget:
        # P0/P1 are never partially advertised as complete. This is a genuine
        # mandatory-core overflow, not optional call pressure. Never retry business failures.
        diagnostic = dict(core["backpressure"], reason="return_budget", estimated_full_bytes=core_size,
                          retryable=source_complete and budget < ceiling and core_size <= ceiling,
                          hard_limit_exceeded=core_size > ceiling,
                          omitted_count=sum(counts.values()))
        minimal = {key: copy.deepcopy(report[key]) for key in (
            "contract_version", "kind", "project_id", "snapshot_id", "freshness", "inventory_complete")}
        minimal.update(status="blocked", partial=True, backpressure=diagnostic,
            delivery={"version": 1, "state": "CORE_BLOCKED", "core_complete": False,
                      "required_counts": {"p0": counts["p0"], "p1": counts["p1"]},
                      "delivered_counts": {key: 0 for key in counts}, "omitted_counts": counts},
            unfinished=[{"path": ".", "reason": "mandatory_core_budget"}],
            unreturned_entry_count=len(entries), unreturned_obligation_count=len(report["unfinished"]))
        if measure(minimal) > budget:
            # At the 1024-byte floor keep mandatory counts and the explicit
            # non-success state; duplicated per-tier zero/omission maps may go.
            minimal["delivery"].pop("delivered_counts")
            minimal["delivery"].pop("omitted_counts")
            minimal["delivery"].pop("version")
            minimal["unreturned_delivery_field_count"] = 3
        if measure(minimal) > budget:
            raise InputError("返回预算不足以保存核心阻塞诊断")
        return minimal

    # Deterministic lexicographic priority optimization: binary search each tier's
    # prefix. Each predicate measures the actual complete escaped response.
    chosen = {"p2": 0, "p3": 0}
    for tier in ("p2", "p3"):
        if tier == "p3" and chosen["p2"] < len(candidates["p2"]):
            break
        low, high = 0, len(candidates[tier])
        while low < high:
            mid = (low + high + 1) // 2
            trial_counts = {**chosen, tier: mid}
            trial = candidate(trial_counts["p2"], trial_counts["p3"])
            trial["backpressure"]["estimated_full_bytes"] = core_size
            if measure(trial) <= budget:
                low = mid
            else:
                high = mid - 1
        chosen[tier] = low
    result = candidate(chosen["p2"], chosen["p3"])
    result["backpressure"]["estimated_full_bytes"] = core_size
    if not result["partial"]:
        # FULL's estimate describes its current frame rather than the core projection.
        result["backpressure"]["estimate_scope"] = "all_records"
        estimate(result)
    if measure(result) > budget:
        # A state/decimal-width transition can add a few bytes at the boundary.
        # The mandatory core remains a valid measured fallback.
        return core
    return result
