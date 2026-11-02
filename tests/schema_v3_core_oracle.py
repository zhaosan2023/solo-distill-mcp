"""Schema 3.0 封印独立期望：只消费可信源码及包外核心清单。

不导入生产模块。摘要仅绑定完整规范输入，不证明来源可信、选择独立、
秘密检查、磁盘新鲜度、任务语义或物理投递；这些门禁分别验收。
"""
from __future__ import annotations

import hashlib

from schema_v3_macro_oracle import canonical, identity
from schema_v3_package_oracle import expected_package
from schema_v3_snapshot_oracle import cached_core, oracle_scope

POLICY = "adaptive-backpressure-v1"
RENDER = "offline-core-v3-1"


@oracle_scope
def expected_core(raw_files, *, project_id, snapshot_id,
                  required_core_unit_ids, selection_id, tick=None):
    check = tick if tick is not None else (lambda: None)
    trusted = canonical({"project_id": project_id, "snapshot_id": snapshot_id,
        "required_core_unit_ids": required_core_unit_ids, "selection_id": selection_id})
    return cached_core(raw_files, trusted, lambda: _build_core(raw_files,
        project_id=project_id, snapshot_id=snapshot_id,
        required_core_unit_ids=required_core_unit_ids, selection_id=selection_id,
        tick=check), check)


def _build_core(raw_files, *, project_id, snapshot_id,
                required_core_unit_ids, selection_id, tick):
    package = expected_package(raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    document = {"render_version": RENDER,
                "input_id": identity("input-v3", [
                    [path, hashlib.sha256(raw).hexdigest()]
                    for path, raw in sorted(raw_files.items())]),
                "policy_id": POLICY, "scope": sorted(raw_files),
                "selection_id": selection_id, "package": package,
                "wire_limit": 22016}
    document["core_id"] = identity("core-v3", document)
    return document


def authenticate_core(value, raw_files, **trusted):
    expected = expected_core(raw_files, **trusted)
    if canonical(value) != canonical(expected):
        raise ValueError("independent_v3_core_or_trusted_input_conflict")
    return {"status": "pass", "core_id": expected["core_id"],
            "input_id": expected["input_id"],
            "proof_scope": "complete_macro_and_external_core_local_structure_binding",
            "selection_independence": "not_checked_by_this_asset",
            "business_semantics": "not_checked_by_this_asset",
            "common_failure_risk": "shared_python_standard_library_ast"}
