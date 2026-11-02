"""显式 Schema 3.0 问题选择；默认旧版，新增覆盖规则须显式指定。

导航是操作内语法视图，不是完整 V2 知识包，不形成 V2 封印或发布。
暂态条件及位点只用于确定选择见证，V3 宏观／局部输出分别认证。
不读取参考答案或独立 Oracle，不执行源码，不联网或写入来源。
"""
from __future__ import annotations

from .core_v3 import input_identity
from .graph_v2 import extract_relations
from .macro_v3 import canonical, identity
from .scouting_v2 import BODY_LIMIT, POST_LIMIT, VERSION, select_prompt
from .scouting_coverage_v3 import COVERAGE_VERSION
from .snapshot_v2 import snapshot_scope
from .syntax_v2 import extract_units
from .task_v3 import _question_identity


def _navigation(raw_files, tick):
    """构建仅含选择所需的语法导航；不调用全库知识包构建器。"""
    view = {key: [] for key in ("files", "units", "conditions", "sources")}
    for path, raw in sorted(raw_files.items()):
        tick()
        inventory = extract_units(path, raw)
        file_index = len(view["files"])
        unit_offset = len(view["units"])
        condition_offset = len(view["conditions"])
        view["files"].append(inventory["file"])
        for unit in inventory["units"]:
            view["units"].append({**unit, "file": file_index,
                "conditions": [condition_offset + index for index in unit["conditions"]]})
            view["sources"].append({"id": identity("source-v2", unit["id"]),
                                    "unit": len(view["units"]) - 1})
        for condition in inventory["conditions"]:
            view["conditions"].append({**condition,
                "unit": unit_offset + condition["unit"],
                "source": unit_offset + condition["unit"]})
    tick()
    # 使用既有保守绑定规则，禁止将词法候选提升为确定调用。
    # 这些暂态位点不进入全库 V2 知识包，也不作为 V3 宏观期望。
    view["relations"] = extract_relations(view, raw_files, tick=tick)
    tick()
    return view


@snapshot_scope
def select_prompt_v3(raw_files, *, project_id, snapshot_id, question,
                     dimensions, frozen_coverage, body_limit=BODY_LIMIT,
                     post_limit=POST_LIMIT, selection_version=VERSION, tick=None):
    """从可信完整源码冻结选择；身份不包含非确定的耗时记录。

    对照独立 Oracle 的运行属于单独门禁。本生产认证不冒充独立性、
    人工语义或来源磁盘新鲜度认证；调用方必须另行冻结及复核来源。
    """
    tick = tick if tick is not None else (lambda: None)
    tick()
    question_id = _question_identity(question, dimensions)
    if (type(selection_version) is not str
            or selection_version not in (VERSION, COVERAGE_VERSION)):
        raise ValueError("v3_selection_unknown_version")
    if (any(type(value) is not str or not value for value in (project_id, snapshot_id))
            or type(frozen_coverage) is not dict
            or type(body_limit) is not int or not 0 < body_limit <= BODY_LIMIT
            or type(post_limit) is not int or not 0 < post_limit <= POST_LIMIT):
        raise ValueError("v3_selection_trusted_contract")
    canonical(frozen_coverage)
    input_id = input_identity(raw_files)
    navigation = _navigation(raw_files, tick)
    navigation["snapshot_id"] = snapshot_id
    messages, post, measured = select_prompt(navigation, raw_files, question,
        dimensions, frozen_coverage, tick=tick, body_limit=body_limit,
        post_limit=post_limit, selection_version=selection_version)
    witness = {key: value for key, value in measured.items() if key != "elapsed_seconds"}
    frozen = {"selection_version": selection_version, "project_id": project_id,
        "snapshot_id": snapshot_id, "input_id": input_id,
        "question_id": question_id,
        "core_unit_ids": sorted(row["unit_id"] for row in witness["selected_evidence"]),
        "witness": witness}
    frozen["selection_id"] = identity("selection-v3", frozen)
    tick()
    return messages, post, frozen


@snapshot_scope
def authenticate_selection(value, raw_files, **trusted):
    """从可信输入复算全部冻结见证，不接受重新签名的缩小核心。"""
    _, _, expected = select_prompt_v3(raw_files, **trusted)
    if canonical(value) != canonical(expected):
        raise ValueError("v3_selection_witness_or_trusted_input_conflict")
    return expected
