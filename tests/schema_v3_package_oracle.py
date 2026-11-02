"""Schema 3.0 结构包独立 Oracle；仅使用标准库及独立语法期望。

本门禁组合完整宏观范围与包外指定核心的局部语法，不核验选择独立性、
生成结论、秘密扫描、磁盘来源新鲜度、物理传输或业务语义。
共享标准库 AST 的共同失效风险仍保留。
"""
from __future__ import annotations

from schema_v3_macro_oracle import canonical, expected_macro
from schema_v3_local_oracle import expected_local_syntax


def expected_package(raw_files, *, project_id, snapshot_id,
                     required_core_unit_ids, selection_id, tick=None):
    tick = tick if tick is not None else (lambda: None)
    macro = expected_macro(raw_files, project_id=project_id,
                           snapshot_id=snapshot_id, tick=tick)
    local = expected_local_syntax(raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    compatibility = local["compatibility_package"]
    tick()
    return {"contract_version": 3, "kind": "knowledge_package",
            "project_id": project_id, "snapshot_id": snapshot_id, "scenario": "A",
            "macro": macro, "local_closures": local, "status": "complete",
            "failures": [], "coverage": {
                "scan_files": len(macro["files"]),
                "definition_count": macro["mapping"]["definition_count"],
                "topology_edges": len(macro["topology"]),
                "core_units": len(local["core_unit_ids"]),
                "body_units": len(compatibility["owners"]),
                "semantic_supported": 0}}


def authenticate_package(value, raw_files, **trusted):
    expected = expected_package(raw_files, **trusted)
    if canonical(value) != canonical(expected):
        raise ValueError("independent_v3_package_structure_conflict")
    return {"status": "pass", "contract_version": 3,
            "coverage": expected["coverage"],
            "proof_scope": "macro_and_external_core_local_syntax_only",
            "selection_independence": "not_checked_by_this_asset",
            "business_semantics": "not_checked_by_this_asset",
            "common_failure_risk": "shared_python_standard_library_ast"}
