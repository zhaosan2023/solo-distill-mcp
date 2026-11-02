"""Schema 3.0 任务证据：与不可变结构核心及旧任务入口分离。

可信源码认证后复算冻结 POST 中的完整正文及真实包含别名。
任务完整状态只表示必需维度具有合法来源支持，不证明推断语义、
选择独立性、实际网络发送、来源磁盘新鲜度或人工审核。
生产代码不导入独立 Oracle，不执行来源、不访问网关。
"""
from __future__ import annotations

import hashlib
import json
import re

from .core_v3 import authenticate_seal
from .distillation import SYSTEM_PROMPT, utf8_json
from .gateway import encode_request
from .macro_v3 import canonical, identity
from .snapshot_v2 import snapshot_scope

BODY_LIMIT = 98304
POST_LIMIT = 150000
TASK_KEYS = {"evidence_version", "kind", "core_id", "input_id", "selection_id",
             "project_id", "request_id", "question_id", "post_sha256", "summary",
             "facts", "obligations", "failures", "unknowns", "status", "evidence_id"}
FACT_KEYS = {"id", "dimension_id", "text", "level", "sources", "semantic_support"}
REFERENCE_KEYS = {"space", "table", "id"}
DIMENSION_KEYS = {"id", "question", "required", "evidence"}


class InvalidSourceCandidate(ValueError):
    """候选形状全部合法但含未发送来源；整份拒绝，不授权自动恢复。"""

    reason = "invalid_or_unsent_source_id"

    def __init__(self, invalid_references):
        super().__init__("v3_task_invalid_or_unsent_conclusion")
        self.invalid_references = tuple(invalid_references)


def _question_identity(question, dimensions):
    if (type(question) is not str or not question.strip()
            or len(question) > 4096):
        raise ValueError("v3_task_question_required")
    if type(dimensions) is not list or not 1 <= len(dimensions) <= 16:
        raise ValueError("v3_task_dimensions_required")
    seen = set()
    for dimension in dimensions:
        if (type(dimension) is not dict or set(dimension) != DIMENSION_KEYS
                or type(dimension["id"]) is not str or not dimension["id"].strip()
                or len(dimension["id"]) > 64 or dimension["id"] in seen
                or type(dimension["question"]) is not str
                or not dimension["question"].strip() or len(dimension["question"]) > 512
                or type(dimension["required"]) is not bool
                or dimension["evidence"] != "sourced_inference"):
            raise ValueError("v3_task_invalid_dimension")
        seen.add(dimension["id"])
    return identity("question-v3", {"question": question, "dimensions": dimensions})


def _request_identity(request_id):
    if (type(request_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", request_id) is None):
        raise ValueError("v3_task_request_identity")


def _decode_post(raw):
    if type(raw) is not bytes:
        raise ValueError("v3_task_post_bytes_required")
    if len(raw) > POST_LIMIT:
        raise ValueError("v3_task_post_budget")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("v3_task_duplicate_json_key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("v3_task_noninteger_json_number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_float=reject, parse_constant=reject)
        canonical(value)
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("v3_task_invalid_post_json") from error


def _prompt_context(core, raw_files, *, project_id, snapshot_id,
                    required_core_unit_ids, selection_id, question, dimensions,
                    frozen_coverage, tick, selection_version="question-scout-v1"):
    tick()
    _question_identity(question, dimensions)
    if type(frozen_coverage) is not dict:
        raise ValueError("v3_task_frozen_scan_coverage_required")
    if (type(selection_version) is not str
            or selection_version not in ("question-scout-v1", "question-scout-v2")):
        raise ValueError("v3_task_unknown_selection_version")
    canonical(frozen_coverage)
    sealed = authenticate_seal(core, raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    local = sealed["package"]["local_closures"]
    syntax = local["compatibility_package"]
    body_by_owner = {body["owner_id"]: body["text"] for body in local["bodies"]}
    sources_by_owner = [[] for _ in syntax["owners"]]
    for source in syntax["sources"]:
        tick()
        sources_by_owner[source["owner"]].append(source)
    records, allowed = [], set()
    body_bytes = 0
    for index, owner in enumerate(syntax["owners"]):
        tick()
        sources = sources_by_owner[index]
        primary = [source for source in sources if source["unit"] == owner["unit"]]
        if len(primary) != 1 or not sources:
            raise ValueError("v3_task_owner_source_conflict")
        unit = syntax["units"][owner["unit"]]
        text = body_by_owner[owner["id"]]
        aliases = sorted(source["id"] for source in sources)
        allowed.update(aliases)
        body_bytes += len(text.encode("utf-8"))
        records.append({"source_id": primary[0]["id"], "alias_source_ids": aliases,
            "path": syntax["files"][owner["file"]]["path"],
            "symbol": unit["qualified_name"], "text": text})
    if not records or body_bytes > BODY_LIMIT:
        raise ValueError("v3_task_complete_body_budget")
    payload = {"operation": "compression", "question": question,
               "dimensions": dimensions, "sources": records, "coverage": frozen_coverage}
    canonical(payload)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": utf8_json(payload).decode("utf-8")}]
    post = encode_request(messages)
    if len(post) > POST_LIMIT:
        raise ValueError("v3_task_post_budget")
    if selection_version == "question-scout-v2":
        # 延迟导入避免选择器与问题身份接口形成模块初始化环。
        # 版本来自包外调用方；重新计算完整选择，不信任核心自报身份。
        from .scouting_v3 import select_prompt_v3
        selected_messages, selected_post, selection = select_prompt_v3(
            raw_files, project_id=project_id, snapshot_id=snapshot_id,
            question=question, dimensions=dimensions, frozen_coverage=frozen_coverage,
            selection_version=selection_version, tick=tick)
        if (selection["selection_id"] != selection_id
                or canonical(selection["core_unit_ids"]) != canonical(required_core_unit_ids)
                or selected_post != post
                or canonical(selected_messages) != canonical(messages)
                or selection["witness"]["allowed_source_ids"] != sorted(allowed)):
            raise ValueError("v3_task_selection_version_or_trusted_input_conflict")
    tick()
    return sealed, messages, post, sorted(allowed)


def _check_frozen_post(post, expected):
    # Only this exact transport suffix may contain a float. The source envelope
    # still uses the restricted integer JSON domain and exact legacy bytes.
    _decode_post(expected)
    controlled = expected[:-1] + b',"temperature":0.2,"max_tokens":3072}'
    if type(post) is bytes and post == controlled:
        return
    value = _decode_post(post)
    if (canonical(value) != canonical(_decode_post(expected))
            or post != expected):
        raise ValueError("v3_task_frozen_post_or_source_conflict")


def _document(candidate, sealed, *, request_id, question_id, dimensions,
              post, allowed, failures, tick):
    if (type(candidate) is not dict or set(candidate) != {"summary", "conclusions", "unknowns"}
            or type(candidate["summary"]) is not str
            or type(candidate["conclusions"]) is not list
            or type(candidate["unknowns"]) is not list
            or any(type(value) is not str for value in candidate["unknowns"])):
        raise ValueError("v3_task_candidate_fields")
    failures = [] if failures is None else failures
    if type(failures) is not list:
        raise ValueError("v3_task_failure_fields")
    for failure in failures:
        tick()
        if (type(failure) is not dict or set(failure) != {"kind", "reason"}
                or type(failure["kind"]) is not str
                or failure["kind"] not in {"blocked", "failed"}
                or type(failure["reason"]) is not str or not failure["reason"].strip()):
            raise ValueError("v3_task_failure_fields")
    by_dimension = {dimension["id"]: [] for dimension in dimensions}
    allowed = frozenset(allowed)
    invalid_references = []
    # 先检查整份形状，避免前面的未知引用掩盖后面的不可重试结构错误。
    # 不构建部分事实，不改写、删除或合并候选中的任何结论。
    for number, conclusion in enumerate(candidate["conclusions"]):
        tick()
        if (type(conclusion) is not dict
                or set(conclusion) != {"dimension_id", "text", "source_ids"}
                or type(conclusion["dimension_id"]) is not str
                or conclusion["dimension_id"] not in by_dimension
                or type(conclusion["text"]) is not str or not conclusion["text"].strip()
                or type(conclusion["source_ids"]) is not list or not conclusion["source_ids"]
                or any(type(key) is not str or not key.strip() for key in conclusion["source_ids"])
                or len(set(conclusion["source_ids"])) != len(conclusion["source_ids"])):
            raise ValueError("v3_task_invalid_or_unsent_conclusion")
        for key in conclusion["source_ids"]:
            tick()
            if key not in allowed:
                invalid_references.append((number, key))
    if invalid_references:
        raise InvalidSourceCandidate(invalid_references)
    facts = []
    for number, conclusion in enumerate(candidate["conclusions"]):
        tick()
        fact_id = identity("task-fact-v3", [request_id, sealed["core_id"],
            question_id, number, conclusion])
        facts.append({"id": fact_id, "dimension_id": conclusion["dimension_id"],
            "text": conclusion["text"], "level": "sourced_inference",
            "sources": [{"space": "local_v3", "table": "sources", "id": key}
                        for key in conclusion["source_ids"]],
            "semantic_support": "requires_independent_review"})
        by_dimension[conclusion["dimension_id"]].append(
            {"space": "task_evidence", "table": "facts", "id": fact_id})
    obligations = []
    for dimension in dimensions:
        tick()
        supports = by_dimension[dimension["id"]]
        obligations.append({"id": identity("task-obligation-v3", [request_id,
            sealed["core_id"], question_id, dimension]), "dimension_id": dimension["id"],
            "required": dimension["required"], "evidence": "sourced_inference",
            "state": "satisfied" if supports else "unknown", "supports": supports,
            "reason": None if supports else "required_semantic_evidence_missing"})
    if any(failure["kind"] == "failed" for failure in failures):
        status = "failed"
    elif failures:
        status = "blocked"
    elif any(obligation["required"] and obligation["state"] != "satisfied"
             for obligation in obligations):
        status = "partial"
    else:
        status = "complete"
    value = {"evidence_version": 1, "kind": "task_evidence",
        "core_id": sealed["core_id"], "input_id": sealed["input_id"],
        "selection_id": sealed["selection_id"],
        "project_id": sealed["package"]["project_id"], "request_id": request_id,
        "question_id": question_id, "post_sha256": hashlib.sha256(post).hexdigest(),
        "summary": candidate["summary"], "facts": facts, "obligations": obligations,
        "failures": failures, "unknowns": candidate["unknowns"], "status": status}
    value["evidence_id"] = identity("task-evidence-v3", value)
    tick()
    return json.loads(canonical(value))


@snapshot_scope
def build_prompt(core, raw_files, *, project_id, snapshot_id,
                 required_core_unit_ids, selection_id, question, dimensions,
                 frozen_coverage, selection_version="question-scout-v1", tick=None):
    """复算全部局部正文并编码；返回值不是实际 HTTP 发送回执。"""
    tick = tick if tick is not None else (lambda: None)
    _, messages, post, allowed = _prompt_context(core, raw_files,
        project_id=project_id, snapshot_id=snapshot_id,
        required_core_unit_ids=required_core_unit_ids, selection_id=selection_id,
        question=question, dimensions=dimensions, frozen_coverage=frozen_coverage,
        selection_version=selection_version, tick=tick)
    return messages, post, allowed


@snapshot_scope
def build_task(candidate, core, raw_files, *, project_id, snapshot_id,
               required_core_unit_ids, selection_id, request_id, question,
               dimensions, post, frozen_coverage, failures=None,
               selection_version="question-scout-v1", tick=None):
    """拒绝非法生成项，不模糊修复引用；保留全部推断和未知文本。"""
    tick = tick if tick is not None else (lambda: None)
    tick()
    _request_identity(request_id)
    question_id = _question_identity(question, dimensions)
    sealed, _, expected_post, allowed = _prompt_context(core, raw_files,
        project_id=project_id, snapshot_id=snapshot_id,
        required_core_unit_ids=required_core_unit_ids, selection_id=selection_id,
        question=question, dimensions=dimensions, frozen_coverage=frozen_coverage,
        selection_version=selection_version, tick=tick)
    _check_frozen_post(post, expected_post)
    return _document(candidate, sealed, request_id=request_id, question_id=question_id,
        dimensions=dimensions, post=post, allowed=allowed, failures=failures, tick=tick)


@snapshot_scope
def validate_task(value, core, raw_files, *, project_id, snapshot_id,
                  required_core_unit_ids, selection_id, request_id, question,
                  dimensions, post, frozen_coverage,
                  selection_version="question-scout-v1", tick=None):
    """从可信上下文复算结构支持及状态，返回与输入隔离的视图。

    没有原始生成输出时只认证现存推断的结构绑定，不证明文本未遭重新
    生成或语义正确。独立验收须另与保存的原始生成输出及真实发送对账。
    """
    tick = tick if tick is not None else (lambda: None)
    tick()
    if (type(value) is not dict or set(value) != TASK_KEYS
            or type(value["evidence_version"]) is not int
            or value["evidence_version"] != 1 or value["kind"] != "task_evidence"
            or type(value["facts"]) is not list):
        raise ValueError("v3_task_fields_or_version")
    encoded = canonical(value)
    conclusions = []
    for fact in value["facts"]:
        tick()
        if (type(fact) is not dict or set(fact) != FACT_KEYS
                or fact["level"] != "sourced_inference"
                or fact["semantic_support"] != "requires_independent_review"
                or type(fact["sources"]) is not list):
            raise ValueError("v3_task_fact_fields_or_level")
        source_ids = []
        for reference in fact["sources"]:
            tick()
            if (type(reference) is not dict or set(reference) != REFERENCE_KEYS
                    or reference["space"] != "local_v3" or reference["table"] != "sources"
                    or type(reference["id"]) is not str or not reference["id"]):
                raise ValueError("v3_task_source_reference_namespace")
            source_ids.append(reference["id"])
        conclusions.append({"dimension_id": fact["dimension_id"], "text": fact["text"],
                            "source_ids": source_ids})
    candidate = {"summary": value["summary"], "conclusions": conclusions,
                 "unknowns": value["unknowns"]}
    expected = build_task(candidate, core, raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, request_id=request_id, question=question,
        dimensions=dimensions, post=post, frozen_coverage=frozen_coverage,
        failures=value["failures"], selection_version=selection_version, tick=tick)
    if encoded != canonical(expected):
        raise ValueError("v3_task_evidence_or_trusted_input_conflict")
    tick()
    return expected
