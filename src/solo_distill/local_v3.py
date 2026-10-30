"""Schema 3.0 的完整局部语法见证；不改变旧 V2 入口或全库分母。

包外核心清单须由调用方冻结并另经独立选择核验。本模块只核验
给定清单的局部完整性，不证明问题选择、业务语义或宿主消费。
源码作为字节数据解析，不导入、执行、联网或写入来源项目。
"""
from __future__ import annotations

import hashlib

from .core_v2 import canonical
from .evidence_v2 import attach_roles
from .graph_v2 import extract_relations
from .package_v2 import build_package, validate_package
from .snapshot_v2 import snapshot_scope
from .syntax_v2 import body_text, extract_units

BODY_LIMIT = 98304
LOCAL_KEYS = {"selection_id", "core_unit_ids", "compatibility_package", "bodies"}


def _trusted_inputs(raw_files, project_id, snapshot_id, selection_id, core_ids):
    if type(raw_files) is not dict or not raw_files:
        raise ValueError("local_v3_empty_or_invalid_trusted_scope")
    for path, raw in raw_files.items():
        if (type(path) is not str or not path.endswith(".py") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or type(raw) is not bytes):
            raise ValueError("local_v3_source_path_or_bytes")
    for value in (project_id, snapshot_id, selection_id):
        if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
            raise ValueError("local_v3_missing_trusted_identity")
    if (type(core_ids) is not list or not core_ids
            or any(type(value) is not str or not value for value in core_ids)
            or len(set(core_ids)) != len(core_ids)):
        raise ValueError("local_v3_invalid_external_core")


@snapshot_scope
def build_local_syntax(raw_files, *, project_id, snapshot_id,
                       required_core_unit_ids, selection_id, tick=None):
    """从可信完整范围定位核心，仅物化其所属文件中的必需闭包。

    外层条件来源由旧局部语法格式的完整正文规则补齐，包含别名
    只来自实际完整所有者。局部文件数不是全授权范围的分母。
    全库宏观分母在 V3 的独立宏观层核验，不在此冒充 V2 全库包。
    """
    _trusted_inputs(raw_files, project_id, snapshot_id, selection_id,
                    required_core_unit_ids)
    tick = tick if tick is not None else (lambda: None)
    tick()
    wanted = set(required_core_unit_ids)
    found = set()
    local_files = {}
    for path, raw in sorted(raw_files.items()):
        tick()
        units = extract_units(path, raw)["units"]
        matches = wanted.intersection(unit["id"] for unit in units)
        if matches:
            local_files[path] = raw
            found.update(matches)
    if found != wanted:
        raise ValueError("local_v3_missing_external_core")
    tick()
    package = build_package(project_id, snapshot_id, local_files,
                            sorted(required_core_unit_ids), scenario="A")
    tick()
    package = attach_roles(package, local_files)
    tick()
    package["relations"] = extract_relations(package, local_files)
    tick()
    validate_package(package, local_files)
    bodies = []
    body_bytes = 0
    for owner in package["owners"]:
        tick()
        file = package["files"][owner["file"]]
        unit = package["units"][owner["unit"]]
        text = body_text(local_files[file["path"]], file, unit)
        encoded = text.encode("utf-8")
        body_bytes += len(encoded)
        if body_bytes > BODY_LIMIT:
            raise ValueError("local_v3_complete_body_budget")
        bodies.append({"owner_id": owner["id"], "text": text,
                       "utf8_sha256": hashlib.sha256(encoded).hexdigest()})
    if not bodies:
        raise ValueError("local_v3_zero_complete_bodies")
    tick()
    return {"selection_id": selection_id,
            "core_unit_ids": sorted(required_core_unit_ids),
            "compatibility_package": package, "bodies": bodies}


@snapshot_scope
def validate_local_syntax(local, raw_files, *, project_id, snapshot_id,
                          required_core_unit_ids, selection_id, tick=None):
    """生产认证重新从可信源码复算，不相信包自报的核心或正文摘要。

    独立消费端必须使用独立实现；本函数不是独立测试 Oracle。
    规范字节比较同时拒绝重复、遗漏、额外字段、错误类型及顺序。
    """
    tick = tick if tick is not None else (lambda: None)
    tick()
    if type(local) is not dict or set(local) != LOCAL_KEYS:
        raise ValueError("local_v3_invalid_fields")
    expected = build_local_syntax(raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    if canonical(local) != canonical(expected):
        raise ValueError("local_v3_denominator_body_or_record_conflict")
    tick()
