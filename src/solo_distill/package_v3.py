"""Schema 3.0 结构包：完整宏观范围及显式局部兼容语法。

仅新入口调用；不改变 V1/V2 解码、全库微观分母或主线 B。
调用方必须提供可信完整源码及包外核心清单。结构通过不证明
选择独立性、业务语义、秘密扫描、磁盘新鲜度或宿主加载。
生成的任务证据不得写入这里的不可变结构核心。
"""
from __future__ import annotations

import json

from .local_v3 import _trusted_inputs, build_local_syntax
from .macro_v3 import PARSER_VERSION, RULE_VERSION, build_macro, canonical
from .snapshot_v2 import RULE_VERSION as SNAPSHOT_RULE_VERSION
from .snapshot_v2 import cached_structure, snapshot_scope

PACKAGE_KEYS = {"contract_version", "kind", "project_id", "snapshot_id",
                "scenario", "macro", "local_closures", "status", "failures",
                "coverage"}


@snapshot_scope
def build_package(raw_files, *, project_id, snapshot_id,
                  required_core_unit_ids, selection_id, tick=None):
    """从可信输入构建双层结构，不从包内取分母或核心期望。

    必需范围或闭包缺失抛异常，不制造完整状态或缩小源码范围。
    此入口只构建成功的结构包；失败由调用边界另行保存和报告。
    """
    tick = tick if tick is not None else (lambda: None)
    tick()
    _trusted_inputs(raw_files, project_id, snapshot_id, selection_id,
                    required_core_unit_ids)
    trusted_bytes = canonical({"project_id": project_id, "snapshot_id": snapshot_id,
        "selection_id": selection_id, "required_core_unit_ids": required_core_unit_ids,
        "parser_version": PARSER_VERSION, "macro_rule_version": RULE_VERSION,
        "snapshot_rule_version": SNAPSHOT_RULE_VERSION, "package_rule_version": "package-v3-1"})

    def build():
        macro = build_macro(raw_files, project_id=project_id,
                            snapshot_id=snapshot_id, tick=tick)
        local = build_local_syntax(raw_files, project_id=project_id,
            snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
            selection_id=selection_id, tick=tick)
        compatibility = local["compatibility_package"]
        coverage = {"scan_files": len(macro["files"]),
                    "definition_count": macro["mapping"]["definition_count"],
                    "topology_edges": len(macro["topology"]),
                    "core_units": len(local["core_unit_ids"]),
                    "body_units": len(compatibility["owners"]),
                    "semantic_supported": 0}
        tick()
        return {"contract_version": 3, "kind": "knowledge_package",
                "project_id": project_id, "snapshot_id": snapshot_id,
                "scenario": "A", "macro": macro, "local_closures": local,
                "status": "complete", "failures": [], "coverage": coverage}

    return cached_structure("package-v3", raw_files, trusted_bytes, build, tick)


@snapshot_scope
def validate_package(value, raw_files, *, project_id, snapshot_id,
                     required_core_unit_ids, selection_id, tick=None):
    """生产认证复算全部规范字段；不是独立接收器 Oracle。

    完整规范比较拒绝重复、遗漏、额外字段、bool/int 混淆和
    跨层版本冲突；局部 V2 格式仅在声明的兼容层内有效。
    """
    tick = tick if tick is not None else (lambda: None)
    tick()
    if (type(value) is not dict or set(value) != PACKAGE_KEYS
            or type(value["contract_version"]) is not int
            or value["contract_version"] != 3):
        raise ValueError("v3_package_fields_or_version")
    actual = canonical(value)
    expected = build_package(raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    if actual != canonical(expected):
        raise ValueError("v3_package_structure_or_trusted_input_conflict")
    tick()


def decode_package(raw, raw_files, **trusted):
    """显式 V3 解码；重复键、浮点及非有限值均拒绝，不降级。"""
    if type(raw) is not bytes:
        raise ValueError("v3_package_bytes_required")

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("v3_duplicate_json_key")
            result[key] = item
        return result

    def reject(value):
        raise ValueError("v3_noninteger_json_number")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                       parse_float=reject, parse_constant=reject)
    validate_package(value, raw_files, **trusted)
    return value
