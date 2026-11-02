"""Offline consumer oracle: mandatory completeness, not merely nonzero counts."""
from solo_distill.priority_v1 import expand_entries


def assess_structure(report, is_error, selected, wire_bytes, budget):
    if wire_bytes > budget or wire_bytes > 24576:
        raise ValueError("wire_budget_exceeded")
    delivery = report["delivery"]
    pressure = report["backpressure"]
    if delivery.get("core_complete") is not True:
        if (delivery.get("state") != "CORE_BLOCKED" or not report["partial"]
                or pressure.get("reason") != "return_budget"
                or pressure.get("source_status") != "complete"
                or pressure.get("estimate_scope") != "core_p0_p1"
                or pressure["estimated_full_bytes"] <= budget):
            raise ValueError("business_failure_not_core_budget_pressure")
        return {"status": "PRESSURE", "delivery_state": "CORE_BLOCKED",
                "delivered_symbols": 0, "delivered_imports": 0,
                "delivered_ast_record_count": 0}
    if (is_error is not False or delivery.get("source_status") != "complete"
            or delivery["state"] not in {"FULL", "CORE_VALID"}
            or report.get("freshness") != "current_at_verification"
            or report.get("inventory_complete") is not True
            or report.get("observations") is None
            or report["observations"].get("model_requests") != 0
            or report["observations"].get("target_writes") != 0):
        raise ValueError("incomplete_or_nonoffline_core")
    entries = expand_entries(report)
    parsed_paths = []
    counts = {"p0": 0, "p1": 0, "p2": 0, "p3": 0}
    source_ids = set()
    for entry in entries:
        if entry.get("status") != "parsed":
            continue
        parsed_paths.append(entry["path"])
        for key in ("symbols", "imports", "calls"):
            for record in entry[key]:
                if (record.get("path") not in selected or record["path"] != entry["path"]
                        or record.get("evidence_level") != "direct_static"
                        or not isinstance(record.get("source_id"), str)
                        or len(record["source_id"]) != 24
                        or record["source_id"] in source_ids
                        or len(record.get("content_sha256", "")) != 64
                        or record["content_sha256"] != entry.get("content_sha256")
                        or type(record.get("start_line")) is not int
                        or type(record.get("end_line")) is not int
                        or not 1 <= record["start_line"] <= record["end_line"] <= entry["line_count"]):
                    raise ValueError("invalid_ast_evidence_record")
                source_ids.add(record["source_id"])
                tier = "p0" if key == "imports" else "p1" if key == "symbols" else (
                    "p2" if record.get("target_resolution") == "resolved_static"
                    or record.get("caller") == "<module>" else "p3")
                counts[tier] += 1
    coverage = report["coverage"]
    if (sorted(parsed_paths) != sorted(selected) or coverage["parsed_files"] != len(selected)
            or coverage["file_denominator"] != len(selected)
            or coverage["skipped_files"] != 0 or coverage["failed_files"] != 0
            or not counts["p0"] or not counts["p1"]
            or counts["p0"] != coverage["imports"] or counts["p1"] != coverage["symbols"]
            or delivery["required_counts"] != {"p0": counts["p0"], "p1": counts["p1"]}
            or delivery["delivered_counts"] != counts
            or delivery["omitted_counts"]["p0"] != 0 or delivery["omitted_counts"]["p1"] != 0):
        raise ValueError("mandatory_core_incomplete")
    omitted = delivery["omitted_counts"]
    if (any(type(n) is not int or n < 0 for n in omitted.values())
            or counts["p2"] + counts["p3"] + omitted["p2"] + omitted["p3"] != coverage["call_expressions"]
            or pressure["omitted_count"] != sum(omitted.values())):
        raise ValueError("invalid_optional_omission_accounting")
    if omitted["p2"] and counts["p3"]:
        raise ValueError("optional_priority_inversion")
    pruned = omitted["p2"] + omitted["p3"]
    expected_obligations = [{"path": ".", "reason": "optional_call_expressions_pruned", "count": pruned}] if pruned else []
    if (report["unfinished"] != expected_obligations or report["partial"] != bool(pruned)
            or report["status"] != ("partial" if pruned else "complete")):
        raise ValueError("business_obligations_or_false_full_delivery")
    if pruned:
        if pressure.get("estimate_scope") != "core_p0_p1" or pressure.get("retryable") is not False:
            raise ValueError("optional_pruning_must_not_request_expansion")
    elif pressure["estimated_full_bytes"] != wire_bytes:
        raise ValueError("contradictory_full_frame_estimate")
    graph = report["graph"]
    expected_refs = {(i, n) for i, entry in enumerate(entries) for n in range(len(entry.get("imports", [])))}
    seen = set()
    for edge in graph["edges"]:
        if not isinstance(edge, list) or len(edge) != 3:
            raise ValueError("invalid_dependency_edge")
        origin, number, target = edge
        if (type(origin) is not int or type(number) is not int
                or (origin, number) not in expected_refs or (origin, number) in seen
                or target is not None and (type(target) is not int or not 0 <= target < len(entries)
                                          or entries[target]["status"] != "parsed")):
            raise ValueError("dangling_dependency_source")
        seen.add((origin, number))
    if (seen != expected_refs or graph["kind"] != "declared_import_dependencies"
            or graph["direction"] != "importer_to_module" or graph["call_graph"] != "not_resolved"):
        raise ValueError("incomplete_or_misrepresented_graph")
    return {"status": "PASS", "delivery_state": delivery["state"],
            "core_complete": True, "delivered_symbols": counts["p1"],
            "delivered_imports": counts["p0"], "dependency_edges": len(seen),
            "delivered_calls": counts["p2"] + counts["p3"],
            "delivered_counts": counts, "omitted_counts": omitted,
            "delivered_ast_record_count": len(source_ids)}
