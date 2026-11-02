"""问题选择的独立核验资产；仅标准库和独立 Oracle，不导入生产模块。

独立复算 question-scout-v1 的候选、义务、完整正文预算和确定选择。
全范围微观导航仅用于本独立资产，不是生产 V3 下传格式；共享标准库
AST 的共同失效风险仍存在。选择一致不证明业务语义或真实人工审核。
"""
from __future__ import annotations

import ast
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import re

from schema_v3_local_oracle import _contains, _inventory, _relations
from schema_v3_macro_oracle import canonical, identity
from schema_v3_task_oracle import MODEL, SYSTEM_PROMPT, _questions

VERSION = "question-scout-v1"
COVERAGE_VERSION = "question-scout-v2"
BODY_LIMIT = 98304
POST_LIMIT = 150000
MAX_CANDIDATES = 256
MAX_EDGES = 2048


def tokens(text):
    words = set(re.findall(r"[a-z][a-z0-9]*", text.lower()))
    for family in ({"query", "question", "search"},
                   {"retrieve", "retrieval", "retriever"},
                   {"generate", "generation", "generator", "answer"},
                   {"parse", "parser", "parsing"}, {"load", "loading", "loader"},
                   {"persist", "persistence", "save", "saving"},
                   {"error", "errors", "exception", "exceptions"},
                   {"filter", "filters", "filtering"},
                   {"repo", "repository", "repositories"},
                   {"interface", "interfaces"}):
        if words & family:
            words.update(family)
    return words


def _encoded(value):
    canonical(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _navigation(raw_files, tick):
    if type(raw_files) is not dict or not raw_files:
        raise ValueError("independent_selection_trusted_scope_required")
    inventories = {}
    view = {key: [] for key in ("files", "units", "conditions", "sources")}
    for path, raw in sorted(raw_files.items()):
        tick()
        if (type(path) is not str or not path.endswith(".py") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or type(raw) is not bytes):
            raise ValueError("independent_selection_source_path_or_bytes")
        inventory = _inventory(path, raw, tick)
        inventories[path] = inventory
        file_index = len(view["files"])
        unit_offset = len(view["units"])
        condition_offset = len(view["conditions"])
        view["files"].append(inventory[0])
        for unit in inventory[1]:
            view["units"].append({**unit, "file": file_index,
                "conditions": [condition_offset + c for c in unit["conditions"]]})
            view["sources"].append({"id": identity("source-v2", unit["id"]),
                                    "unit": len(view["units"]) - 1})
        for condition in inventory[2]:
            view["conditions"].append({**condition,
                "unit": unit_offset + condition["unit"],
                "source": unit_offset + condition["unit"]})
    view["relations"] = _relations(view, inventories, tick)
    return view, inventories


def _coverage_roles(node, tick):
    """公开 V2 子义务判据；仅本可调用正文，不借用嵌套定义或注释。

    生成入口：函数自身名称具有生成词，且正文实际调用具有生成词或
    completion/complete 词的表达式。限额：同一入口含比较，其标识符
    同时具有 token/context/input 与 limit/budget/max/available 信号。
    异常：同一入口有带 handler 的 try，且 try 主体含上述生成调用。
    分派：函数自身名称具有解析词，正文至少两个比较分支以及至少
    两个直接 return-call。以上均为可审查语法，不证明运行或语义。
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    scoped = []
    pending = list(reversed(node.body))
    while pending:
        tick()
        item = pending.pop()
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        scoped.append(item)
        pending.extend(reversed(list(ast.iter_child_nodes(item))))
    generation = {"generate", "generation", "generator", "answer"}

    def generating_call(item):
        return (isinstance(item, ast.Call)
                and bool(tokens(ast.unparse(item.func)) &
                         (generation | {"completion", "complete"})))

    roles = set()
    if tokens(node.name) & generation and any(generating_call(item) for item in scoped):
        roles.add("generation_entry")
        for item in scoped:
            if isinstance(item, ast.Compare):
                words = tokens(" ".join(
                    child.id if isinstance(child, ast.Name) else child.attr
                    for child in ast.walk(item) if isinstance(child, (ast.Name, ast.Attribute))))
                if (words & {"token", "tokens", "context", "input"}
                        and words & {"limit", "budget", "max", "available"}):
                    roles.add("generation_input_limit")
        for item in scoped:
            if isinstance(item, (ast.Try, ast.TryStar)) and item.handlers:
                body_items = []
                work = list(item.body)
                while work:
                    tick()
                    child = work.pop()
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                        continue
                    body_items.append(child)
                    work.extend(ast.iter_child_nodes(child))
                if any(generating_call(child) for child in body_items):
                    roles.add("generation_failure")
    if (tokens(node.name) & {"parse", "parser", "parsing"}
            and sum(isinstance(item, ast.If) and isinstance(item.test, ast.Compare)
                    for item in scoped) >= 2
            and sum(isinstance(item, ast.Return) and isinstance(item.value, ast.Call)
                    for item in scoped) >= 2):
        roles.add("parser_dispatch")
    return roles


def _coverage_duties(wanted):
    """仅维度问题启用；可选维度不引入必需子义务。"""
    duties = []
    if wanted & {"generate", "generation", "generator", "answer"}:
        duties.append("generation_entry")
        if wanted & {"limit", "limits", "budget", "context", "input", "token", "tokens"}:
            duties.append("generation_input_limit")
        if wanted & {"failure", "error", "errors", "exception", "exceptions"}:
            duties.append("generation_failure")
    if (wanted & {"parse", "parser", "parsing"}
            and wanted & {"support", "route", "routing", "dispatch", "language", "languages", "boundaries"}):
        duties.append("parser_dispatch")
    return duties


def expected_selection(raw_files, *, project_id, snapshot_id, question,
                       dimensions, frozen_coverage, body_limit=BODY_LIMIT,
                       post_limit=POST_LIMIT, selection_version=VERSION, tick=None):
    """由包外可信输入复算冻结选择；耗时不进入身份或确定性比较。"""
    tick = tick if tick is not None else (lambda: None)
    tick()
    question_id = _questions(question, dimensions)
    if type(selection_version) is not str or selection_version not in (VERSION, COVERAGE_VERSION):
        raise ValueError("independent_selection_unknown_version")
    if (any(type(value) is not str or not value for value in (project_id, snapshot_id))
            or type(frozen_coverage) is not dict
            or type(body_limit) is not int or not 0 < body_limit <= BODY_LIMIT
            or type(post_limit) is not int or not 0 < post_limit <= POST_LIMIT):
        raise ValueError("independent_selection_trusted_contract")
    canonical(frozen_coverage)
    view, inventories = _navigation(raw_files, tick)
    units = view["units"]
    source_ids = [source["id"] for source in view["sources"]]
    by_dimension = {d["id"]: d for d in dimensions}
    terms = {d["id"]: tokens(d["question"]) for d in dimensions}
    common = tokens(question)
    positions, bodies, metadata = {}, {}, {}
    methods = defaultdict(list)
    file_units = defaultdict(list)
    for index, unit in enumerate(units):
        tick()
        path = view["files"][unit["file"]]["path"]
        file_units[unit["file"]].append(index)
        positions[index] = (path, unit["raw_start"], unit["raw_end"], unit["id"])
        node = inventories[path][3][unit["id"]]
        codec = inventories[path][0]["encoding"]
        codec = "utf-8" if codec == "utf-8-sig" else codec
        bodies[index] = raw_files[path][unit["raw_start"]:unit["raw_end"]].decode(codec)
        name = tokens(unit["qualified_name"] or "")
        signature = (ast.unparse(node.args) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else " ".join(ast.unparse(base) for base in node.bases)
                     if isinstance(node, ast.ClassDef) else "")
        doc = ast.get_docstring(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
        walked = list(ast.walk(node))
        roles = set()
        for item in walked:
            if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                if any(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                       and target.value.id == "self" for target in targets):
                    roles.add("state")
            if isinstance(item, (ast.Try, ast.TryStar, ast.Raise, ast.ExceptHandler)):
                roles.add("failure")
        if unit["kind"] == "main_guard":
            roles.add("entry")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[node.name].append(index)
            if any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                   and d.func.attr in {"route", "command"} for d in node.decorator_list):
                roles.add("entry")
        if selection_version == COVERAGE_VERSION:
            roles.update(_coverage_roles(node, tick))
        signals = {"identifier": name, "signature": tokens(signature),
                   "filename": tokens(path.rsplit("/", 1)[-1]),
                   "docstring": tokens(doc or "")}
        hits = {}
        for key, wanted in terms.items():
            matched = {signal: sorted(value & wanted) for signal, value in signals.items()}
            strong = set(matched["identifier"]) | set(matched["signature"])
            weak = set(matched["filename"]) | set(matched["docstring"])
            if strong or weak:
                hits[key] = {"tier": 0 if strong else 2, "signals": matched,
                             "count": len(strong | weak), "hop": 0, "path": []}
        metadata[index] = {"hits": hits, "roles": sorted(roles),
                           "common_hits": len(name & common)}
    eligible = {i for i, unit in enumerate(units) if unit["ast_type"] != "ClassDef"}
    eligible = eligible or set(range(len(units)))
    outgoing, incoming, lexical = defaultdict(list), defaultdict(list), defaultdict(list)
    for relation in view["relations"]:
        origin, target = relation["from_unit"], relation["to_unit"]
        if target is not None:
            outgoing[origin].append((target, relation["id"]))
            incoming[target].append((origin, relation["id"]))
        elif relation["layer"] == "call":
            expression = relation["site"]["expression"]
            if "." in expression:
                for target in methods.get(expression.rsplit(".", 1)[-1], ()):
                    if target != origin:
                        lexical[origin].append((target, relation["id"], expression))

    def rank(index, dimension):
        hit = metadata[index]["hits"][dimension]
        return (hit["tier"], -hit["count"], -metadata[index]["common_hits"],
                hit["hop"], positions[index])

    candidates, seeds = set(), {}
    edge_visits, exhausted = 0, False
    for key, dimension in by_dimension.items():
        tick()
        direct = sorted((i for i in eligible if key in metadata[i]["hits"]),
                        key=lambda i: rank(i, key))
        if not direct and dimension["required"]:
            raise ValueError("question_obligation_has_no_direct_body:" + key)
        seeds[key] = direct[:8]
        candidates.update(seeds[key])
    if selection_version == COVERAGE_VERSION:
        # 必需语法见证先于邻接探索保留候选；不被词法填充耗尽配额。
        for key, dimension in by_dimension.items():
            if not dimension["required"]:
                continue
            required = _coverage_duties(terms[key])
            generation_required = set(required) - {"parser_dispatch"}
            for duty in required:
                alternatives = sorted((i for i in eligible
                    if key in metadata[i]["hits"] and metadata[i]["hits"][key]["hop"] == 0
                    and duty in metadata[i]["roles"]
                    and (duty == "parser_dispatch" or generation_required <= set(metadata[i]["roles"]))),
                    key=lambda i: rank(i, key))
                if not alternatives:
                    raise ValueError("required_question_duty_unresolved:" + key + ":" + duty)
                candidates.add(alternatives[0])
                if len(candidates) > MAX_CANDIDATES:
                    raise ValueError("required_coverage_candidate_budget")
    frontier = [(i, key, []) for key, seed in seeds.items() for i in seed]
    for hop in (1, 2):
        next_frontier = []
        for origin, key, route in frontier:
            tick()
            edges = [(target, rid, "resolved_call") for target, rid in outgoing[origin]]
            if terms[key] & {"caller", "callee", "ownership", "state", "return"}:
                edges += [(target, rid, "resolved_caller") for target, rid in incoming[origin]]
            edges += [(target, rid, "lexical_unresolved:" + expr)
                      for target, rid, expr in lexical[origin]]
            for target, rid, kind in sorted(edges, key=lambda e: (
                    e[2].startswith("lexical"), positions[e[0]], e[1])):
                edge_visits += 1
                if edge_visits > MAX_EDGES or len(candidates) >= MAX_CANDIDATES and target not in candidates:
                    exhausted = True
                    break
                if target not in eligible:
                    continue
                candidates.add(target)
                if key not in metadata[target]["hits"]:
                    path = route + [{"from": units[origin]["id"], "to": units[target]["id"],
                                     "relation_id": rid, "basis": kind}]
                    metadata[target]["hits"][key] = {
                        "tier": 3 if kind.startswith("lexical") else 1,
                        "signals": {}, "count": 1, "hop": hop, "path": path}
                    next_frontier.append((target, key, path))
            if edge_visits > MAX_EDGES:
                break
        frontier = next_frontier
        if edge_visits > MAX_EDGES:
            break
    for key in by_dimension:
        for index in sorted((i for i in eligible if key in metadata[i]["hits"]),
                            key=lambda i: rank(i, key)):
            if len(candidates) >= MAX_CANDIDATES and index not in candidates:
                exhausted = True
            else:
                candidates.add(index)

    def materialize(selected):
        selected = set(selected)
        pending = list(selected)
        while pending:
            index = pending.pop()
            for condition in units[index]["conditions"]:
                origin = view["conditions"][condition]["unit"]
                if origin not in selected:
                    selected.add(origin)
                    pending.append(origin)
        owners = sorted((i for i in selected if not any(j != i
            and units[i]["file"] == units[j]["file"] and _contains(units[j], units[i])
            for j in selected)), key=lambda i: positions[i])
        records, allowed = [], set()
        for index in owners:
            aliases = sorted(source_ids[j] for j in file_units[units[index]["file"]]
                             if _contains(units[index], units[j]))
            allowed.update(aliases)
            records.append({"source_id": source_ids[index], "alias_source_ids": aliases,
                "path": positions[index][0], "symbol": units[index]["qualified_name"],
                "text": bodies[index]})
        payload = {"operation": "compression", "question": question,
                   "dimensions": dimensions, "sources": records, "coverage": frozen_coverage}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _encoded(payload).decode("utf-8")}]
        post = _encoded({"model": MODEL, "messages": messages, "stream": True})
        cost = sum(len(record["text"].encode("utf-8")) for record in records)
        return selected, owners, records, allowed, messages, post, cost

    def fits(result):
        return result[6] <= body_limit and len(result[5]) <= post_limit

    if len(materialize(set())[5]) > post_limit:
        raise ValueError("fixed_question_metadata_over_post_budget")
    chosen, duties, omissions = set(), [], []
    for key, dimension in by_dimension.items():
        tick()
        options = sorted((i for i in candidates if key in metadata[i]["hits"]),
                         key=lambda i: rank(i, key))
        if not options:
            continue
        requirements = [("direct_question", [i for i in options if metadata[i]["hits"][key]["hop"] == 0])]
        if terms[key] & {"state", "ownership", "persist", "persistence"}:
            requirements.append(("state_write", [i for i in options if "state" in metadata[i]["roles"]]))
        if terms[key] & {"failure", "error", "exception", "exceptions"}:
            requirements.append(("failure_boundary", [i for i in options if "failure" in metadata[i]["roles"]]))
        if selection_version == COVERAGE_VERSION and dimension["required"]:
            coverage_duties = _coverage_duties(terms[key])
            generation_required = set(coverage_duties) - {"parser_dispatch"}
            for duty in coverage_duties:
                requirements.append((duty, [i for i in options
                    if metadata[i]["hits"][key]["hop"] == 0
                    and duty in metadata[i]["roles"]
                    and (duty == "parser_dispatch" or generation_required <= set(metadata[i]["roles"]))]))
        generation_owner = None
        for duty, alternatives in requirements:
            if duty.startswith("generation_") and generation_owner is not None:
                alternatives = [generation_owner]
            if not alternatives:
                if dimension["required"]:
                    raise ValueError("required_question_duty_unresolved:" + key + ":" + duty)
                continue
            admitted = None
            for index in alternatives[:32]:
                tick()
                group = {index}
                if terms[key] & {"caller", "callee"}:
                    neighbors = [(target, rid) for target, rid in outgoing[index] + incoming[index]
                                 if target in candidates]
                    if neighbors:
                        group.add(min(neighbors, key=lambda e: positions[e[0]])[0])
                result = materialize(chosen | group)
                if fits(result):
                    admitted = result
                    if duty == "generation_entry":
                        generation_owner = index
                    duties.append({"dimension_id": key, "duty": duty,
                        "units": sorted(units[j]["id"] for j in group),
                        "semantic_acceptance": "requires_independent_review"})
                    break
            if admitted is None:
                raise ValueError("required_group_search_exhausted:" + key + ":" + duty)
            chosen = admitted[0]
    current = materialize(chosen)
    if not current[2]:
        raise ValueError("zero_question_body_hits")
    considered = set(chosen)
    while True:
        progressed = False
        for key in by_dimension:
            tick()
            queue = []
            for index in candidates - considered:
                if key not in metadata[index]["hits"]:
                    continue
                hit, unit = metadata[index]["hits"][key], units[index]
                covered = any(units[owner]["file"] == unit["file"]
                              and _contains(units[owner], unit) for owner in current[1])
                increment = 0 if covered else len(bodies[index].encode("utf-8"))
                queue.append((hit["tier"], 0 if covered else 1,
                    -Fraction(hit["count"], max(1, increment)), hit["hop"], positions[index], index))
            for *_, index in sorted(queue):
                considered.add(index)
                proposed = materialize(chosen | {index})
                if fits(proposed):
                    chosen, current = proposed[0], proposed
                    progressed = True
                    break
                omissions.append({"unit_id": units[index]["id"],
                                  "reason": "complete_group_exceeds_body_or_post_budget"})
        if not progressed:
            break
    tick()
    selected, owners, records, allowed, messages, post, cost = current
    witness = {"version": selection_version, "snapshot_id": snapshot_id,
        "candidate_count": len(candidates), "edge_visits": min(edge_visits, MAX_EDGES),
        "exploration_incomplete": exhausted, "proof_scope": "bounded_candidates_not_capacity_proof",
        "unique_body_bytes": cost, "body_limit": body_limit, "post_bytes": len(post),
        "post_limit": post_limit, "post_sha256": hashlib.sha256(post).hexdigest(),
        "owners": [units[i]["id"] for i in owners], "allowed_source_ids": sorted(allowed),
        "duties": duties, "omissions": omissions,
        "selected_evidence": [{"unit_id": units[i]["id"], "path": positions[i][0],
            "hits": metadata[i]["hits"], "roles": metadata[i]["roles"]}
            for i in sorted(selected, key=lambda i: positions[i])],
        "unknown_dispatch": "lexical_candidates_are_not_resolved_targets",
        "semantic_acceptance": "requires_independent_four_dimension_review"}
    frozen = {"selection_version": selection_version, "project_id": project_id,
        "snapshot_id": snapshot_id,
        "input_id": identity("input-v3", [[path, hashlib.sha256(raw).hexdigest()]
                                          for path, raw in sorted(raw_files.items())]),
        "question_id": question_id, "core_unit_ids": sorted(units[i]["id"] for i in selected),
        "witness": witness}
    frozen["selection_id"] = identity("selection-v3", frozen)
    tick()
    return messages, post, frozen


def authenticate_selection(value, raw_files, **trusted):
    """完整比较包外冻结见证，拒绝只认证生产者自报的现存核心。"""
    _, _, expected = expected_selection(raw_files, **trusted)
    if canonical(value) != canonical(expected):
        raise ValueError("independent_selection_witness_or_trusted_input_conflict")
    scope = ("deterministic_question_scout_v1_only" if expected["selection_version"] == VERSION
             else "deterministic_question_scout_v2_syntax_only")
    return {"status": "pass", "proof_scope": scope,
            "core_units": len(expected["core_unit_ids"]),
            "candidate_count": expected["witness"]["candidate_count"],
            "business_semantics": "not_checked_by_this_asset",
            "shared_parser_risk": "python_standard_library_ast"}
