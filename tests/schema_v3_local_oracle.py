"""Schema 3.0 局部语法独立期望：仅标准库，不导入生产模块。

从可信源码及包外必需核心清单复算完整局部单位、条件、关系与正文。
本资产不核验问题选择的独立性，不证明业务语义或完成整个 Tier 2。
与其他 AST 检查共享标准库解析器的共同失效风险仍存在。
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import hashlib
import io
import tokenize

from schema_v3_macro_oracle import canonical, identity

DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
CONTROLS = (ast.If, ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While,
            ast.With, ast.AsyncWith, ast.match_case, ast.ExceptHandler)
TABLES = ("files", "units", "owners", "sources", "conditions", "role_proofs",
          "facts", "relations", "obligations")


def _main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    operands = (test.left, test.comparators[0])
    return any(isinstance(operands[i], ast.Name) and operands[i].id == "__name__"
               and isinstance(operands[1-i], ast.Constant)
               and operands[1-i].value == "__main__" for i in (0, 1))


def _inventory(path, raw, tick):
    encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
    text = raw.decode(encoding)
    codec = "utf-8" if encoding == "utf-8-sig" else encoding
    bom = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
    if text.encode(codec) != raw[bom:]:
        raise ValueError("local_oracle_encoding_roundtrip")
    lines = io.StringIO(text, newline="").readlines()
    offsets, offset = [], bom
    for line in lines:
        offsets.append(offset)
        offset += len(line.encode(codec))
    sha = hashlib.sha256(raw).hexdigest()
    file = {"id": identity("file-v2", [path, sha, "python-3.12-ast-v1"]),
            "path": path, "sha256": sha, "encoding": encoding, "parse_status": "parsed"}
    tree = ast.parse(raw, filename=path, feature_version=(3, 12))
    units, conditions, nodes = [], [], {}

    def position(row, col):
        prefix = lines[row - 1].encode("utf-8")[:col].decode("utf-8")
        return [row, len(prefix)], offsets[row - 1] + len(prefix.encode(codec))

    def visit(node, route, names, active, owner, top=False):
        tick()
        defining = isinstance(node, DEFINITIONS)
        if defining or top and isinstance(node, ast.stmt):
            kind = "definition" if defining else "main_guard" if _main_guard(node) else "statement"
            name = ".".join([*names, node.name]) if defining else None
            row, col = node.lineno, node.col_offset
            if defining and node.decorator_list:
                first = min(node.decorator_list, key=lambda d: (d.lineno, d.col_offset))
                row, col = first.lineno, first.col_offset - 1
            char_start, raw_start = position(row, col)
            char_end, raw_end = position(node.end_lineno, node.end_col_offset)
            span = {"start": [row, col], "end": [node.end_lineno, node.end_col_offset],
                    "char_start": char_start, "char_end": char_end,
                    "raw_start": raw_start, "raw_end": raw_end}
            unit_id = identity("unit-v2", [file["id"], route, kind, name, span])
            owner = len(units)
            units.append({"id": unit_id, "kind": kind, "ast_type": type(node).__name__,
                          "syntax_path": route, "qualified_name": name, **span,
                          "conditions": list(active)})
            nodes[unit_id] = node
        local_names = [*names, node.name] if defining else names
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            branch = None
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While)) and children:
                branch = ({"body": "if", "orelse": "else"} if isinstance(node, ast.If)
                          else {"body": "loop", "orelse": "loop_else"}).get(field)
            elif isinstance(node, (ast.Try, ast.TryStar)) and children:
                branch = {"body": "try", "orelse": "else", "finalbody": "finally"}.get(field)
            elif field == "body":
                if isinstance(node, ast.ExceptHandler):
                    branch = "except"
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    branch = "with"
                elif isinstance(node, ast.match_case):
                    branch = "case"
            nested = active
            if branch is not None:
                if owner is None:
                    raise ValueError("local_oracle_condition_without_origin")
                conditions.append({"id": identity("condition-v2", [file["id"], route, field, branch]),
                                   "syntax_path": route, "field": field, "branch": branch,
                                   "scope": ".".join(local_names), "unit": owner})
                nested = [*active, len(conditions) - 1]
            for index, child in enumerate(children):
                if isinstance(child, ast.AST):
                    visit(child, route + [field] + ([index] if isinstance(value, list) else []),
                          local_names, nested, owner, isinstance(node, ast.Module) and field == "body")
    visit(tree, [], [], [], None)
    return file, units, conditions, nodes, tree


def _contains(parent, child):
    prefix = parent["syntax_path"]
    return (child["syntax_path"][:len(prefix)] == prefix
            and parent["raw_start"] <= child["raw_start"]
            and child["raw_end"] <= parent["raw_end"])


def _roles(unit, node):
    signals = []
    if unit["kind"] == "main_guard":
        signals.append(("entry", "main-guard-equality-v1"))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"route", "command"} for d in node.decorator_list):
        signals.append(("entry", "registration-decorator-v1"))
    walked = list(ast.walk(node))
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id[:1].isupper() for n in walked):
        signals.append(("assembly", "constructor-expression-v1"))
    if any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(
            isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])) for n in walked):
        signals.append(("state", "self-attribute-assignment-v1"))
    if any(isinstance(n, ast.Call) and (isinstance(n.func, ast.Name) and n.func.id == "open"
           or isinstance(n.func, ast.Attribute) and n.func.attr in {"write", "write_text", "write_bytes", "commit"})
           for n in walked):
        signals.append(("persistence", "io-expression-v1"))
    return signals or [("boundary", "no-lifecycle-signal-v1")]


def _relations(package, inventories, tick):
    units = package["units"]
    public = {(u["file"], tuple(u["syntax_path"])): i for i, u in enumerate(units)}
    modules, scopes, events = defaultdict(list), {}, []
    for fi, file in enumerate(package["files"]):
        modules[file["path"].removesuffix(".py").replace("/", ".").removesuffix(".__init__")].append(fi)
        tree = inventories[file["path"]][4]
        env = {(): {"parent": None, "class": False, "counts": Counter(), "values": {}, "unsafe": set()}}
        scopes[fi] = env

        def bind(scope, name, value=None, guarded=False):
            item = env[scope]
            item["counts"][name] += 1
            if guarded:
                item["unsafe"].add(name)
            elif value is not None:
                item["values"][name] = value

        def visit(node, route, scope, owner, guarded=False):
            tick()
            if (fi, route) in public:
                owner = public[fi, route]
            defining = isinstance(node, DEFINITIONS)
            if defining:
                bind(scope, node.name, ("definition", public.get((fi, route))), guarded)
                env[route] = {"parent": env[scope]["parent"] if env[scope]["class"] else scope,
                              "class": isinstance(node, ast.ClassDef), "counts": Counter(),
                              "values": {}, "unsafe": set()}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                            *[a for a in (args.vararg, args.kwarg) if a is not None]]:
                    bind(route if defining else scope, arg.arg)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    if alias.name == "*":
                        env[scope]["unsafe"].add("*")
                    bind(scope, name, ("import", node, alias), guarded)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(scope, node.id, guarded=guarded)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                env[scope]["unsafe"].update(node.names)
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                bind(scope, node.name, guarded=True)
            if owner is not None and isinstance(node, (ast.Call, ast.Import, ast.ImportFrom)):
                events.append((fi, node, route, scope, owner))
            for field, value in ast.iter_fields(node):
                children = enumerate(value) if isinstance(value, list) else [(None, value)]
                for index, child in children:
                    if isinstance(child, ast.AST):
                        nested_scope = route if defining and field == "body" else scope
                        visit(child, route + (field,) + (() if index is None else (index,)), nested_scope,
                              owner, False if defining else guarded or isinstance(node, CONTROLS))
        visit(tree, (), (), None)

    def lookup(fi, scope, name):
        while scope is not None:
            item = scopes[fi][scope]
            if "*" in item["unsafe"] or name in item["unsafe"]:
                return None, "conditional_or_unsafe_binding"
            if item["counts"][name]:
                if item["counts"][name] != 1:
                    return None, "rebound_or_ambiguous_binding"
                value = item["values"].get(name)
                return (value, None) if value is not None else (None, "local_or_parameter_binding")
            scope = item["parent"]
        return None, "unbound_name"

    def imported(fi, binding, attribute=None):
        statement, alias = binding[1:]
        if isinstance(statement, ast.ImportFrom):
            if alias.name == "*" or attribute is not None:
                return None, "unsupported_import_dispatch"
            module, name = statement.module or "", alias.name
            if statement.level:
                parent = package["files"][fi]["path"].split("/")[:-1]
                if statement.level > len(parent):
                    return None, "relative_import_outside_module"
                module = ".".join(parent[:len(parent) - statement.level + 1] + ([module] if module else []))
        else:
            if attribute is None or not alias.asname and "." in alias.name:
                return None, "unsupported_module_dispatch"
            module, name = alias.name, attribute
        matches = modules.get(module, [])
        if len(matches) != 1:
            return None, "external_or_ambiguous_module"
        target, reason = lookup(matches[0], (), name)
        if target and target[0] == "definition" and target[1] is not None:
            return target[1], None
        return None, reason or "import_target_not_selected"

    prefixes = defaultdict(list)
    for index, condition in enumerate(package["conditions"]):
        fi = units[condition["unit"]]["file"]
        prefixes[fi, tuple(condition["syntax_path"] + [condition["field"]])].append(index)
    result = []
    for fi, node, route, scope, owner in events:
        tick()
        target, reason = None, "dynamic_attribute_dispatch"
        if isinstance(node, ast.Call):
            expression, layer = node.func, "call"
            if isinstance(expression, ast.Name):
                binding, reason = lookup(fi, scope, expression.id)
                if binding:
                    if binding[0] == "definition":
                        target = binding[1]
                        reason = None if target is not None else "target_not_selected"
                    else:
                        target, reason = imported(fi, binding)
            elif isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
                binding, local_reason = lookup(fi, scope, expression.value.id)
                if binding and binding[0] == "import":
                    target, reason = imported(fi, binding, expression.attr)
                elif local_reason:
                    reason = local_reason
        else:
            expression, layer, reason = node, "import", "module_import_not_definition_edge"
        site = {"syntax_path": list(route), "start": [node.lineno, node.col_offset],
                "end": [node.end_lineno, node.end_col_offset], "expression": ast.unparse(expression)}
        active = list(units[owner]["conditions"])
        matched = set()
        for length in range(len(route) + 1):
            matched.update(prefixes.get((fi, route[:length]), ()))
        active.extend(c for c in sorted(matched) if c not in active)
        rid = identity("relation-v2", [units[owner]["id"], site, layer,
                       None if target is None else units[target]["id"], active, reason])
        result.append({"id": rid, "from_unit": owner, "to_unit": target, "layer": layer,
                       "direction": "caller_to_dependency", "conditions": active,
                       "sources": [owner], "reason": reason, "site": site})
    return sorted(result, key=lambda r: r["id"])


def expected_local_syntax(raw_files, *, project_id, snapshot_id,
                          required_core_unit_ids, selection_id, tick=None):
    """外部核心清单必须另经选择核验；本函数只建立其完整语法期望。"""
    tick = tick if tick is not None else (lambda: None)
    if (type(raw_files) is not dict or not raw_files
            or any(type(v) is not str or not v for v in (project_id, snapshot_id, selection_id))
            or type(required_core_unit_ids) is not list or not required_core_unit_ids
            or any(type(v) is not str or not v for v in required_core_unit_ids)
            or len(set(required_core_unit_ids)) != len(required_core_unit_ids)):
        raise ValueError("local_oracle_trusted_inputs")
    inventories, available = {}, {}
    for path, raw in sorted(raw_files.items()):
        tick()
        if (type(path) is not str or not path.endswith(".py") or "\\" in path
                or any(p in {"", ".", ".."} for p in path.split("/")) or type(raw) is not bytes):
            raise ValueError("local_oracle_source_path_or_bytes")
        value = _inventory(path, raw, tick)
        inventories[path] = value
        for index, unit in enumerate(value[1]):
            available[unit["id"]] = path, index
    wanted = set(required_core_unit_ids)
    if not wanted <= available.keys():
        raise ValueError("local_oracle_missing_external_core")
    pending = list(wanted)
    while pending:
        key = pending.pop()
        path, index = available[key]
        _, units, conditions, _, _ = inventories[path]
        for c in units[index]["conditions"]:
            origin = units[conditions[c]["unit"]]["id"]
            if origin not in wanted:
                wanted.add(origin)
                pending.append(origin)
    wanted_by_path = defaultdict(set)
    for key in wanted:
        path, index = available[key]
        wanted_by_path[path].add(index)
    owners = []
    containment = {}
    for path, selected in wanted_by_path.items():
        units = inventories[path][1]
        outer = [i for i in selected if not any(j != i and _contains(units[j], units[i]) for j in selected)]
        owners.extend((path, i) for i in outer)
        for i, unit in enumerate(units):
            parent = next((j for j in outer if _contains(units[j], unit)), None)
            if parent is not None:
                containment[path, i] = parent
    owners.sort(key=lambda pair: (pair[0], inventories[pair[0]][1][pair[1]]["raw_start"]))
    package = {"contract_version": 2, "kind": "knowledge_package", "parser_version": "python-3.12-ast-v1",
               "project_id": project_id, "snapshot_id": snapshot_id, "scenario": "A",
               "status": "complete", **{table: [] for table in TABLES}, "failures": [],
               "coverage": {"scan_files": len(wanted_by_path), "body_units": len(owners), "semantic_supported": 0}}
    file_map, unit_map, owner_map = {}, {}, {}
    for path in sorted(wanted_by_path):
        file_map[path] = len(package["files"])
        package["files"].append(inventories[path][0])
        for i, unit in enumerate(inventories[path][1]):
            if (path, i) in containment:
                unit_map[path, i] = len(package["units"])
                package["units"].append({**unit, "file": file_map[path], "conditions": []})
    bodies = []
    for path, i in owners:
        unit = inventories[path][1][i]
        codec = inventories[path][0]["encoding"]
        codec = "utf-8" if codec == "utf-8-sig" else codec
        text = raw_files[path][unit["raw_start"]:unit["raw_end"]].decode(codec)
        owner_id = identity("owner-v2", unit["id"])
        owner_map[path, i] = len(package["owners"])
        package["owners"].append({"id": owner_id, "unit": unit_map[path, i], "file": file_map[path],
                                  "raw_start": unit["raw_start"], "raw_end": unit["raw_end"],
                                  "body_bytes": len(text.encode("utf-8")), "complete": True})
        bodies.append({"owner_id": owner_id, "text": text,
                       "utf8_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    condition_map = {}
    needed = {(path, c) for path, i in unit_map for c in inventories[path][1][i]["conditions"]}
    needed.update((path, c) for path in wanted_by_path for c, item in enumerate(inventories[path][2])
                  if (path, item["unit"]) in unit_map)
    for path, c in sorted(needed):
        condition = inventories[path][2][c]
        origin = unit_map[path, condition["unit"]]
        condition_map[path, c] = len(package["conditions"])
        package["conditions"].append({**condition, "unit": origin, "source": origin})
    for (path, i), public in unit_map.items():
        unit = inventories[path][1][i]
        active = [condition_map[path, c] for c in unit["conditions"]]
        package["units"][public]["conditions"] = active
        package["sources"].append({"id": identity("source-v2", unit["id"]), "file": file_map[path],
                                   "unit": public, "owner": owner_map[path, containment[path, i]],
                                   "conditions": active})
        for role, rule in _roles(unit, inventories[path][3][unit["id"]]):
            package["role_proofs"].append({"id": identity("role-v2", [unit["id"], role, rule]),
                                           "unit": public, "role": role, "rule": rule,
                                           "level": "direct_static", "sources": [public]})
    package["relations"] = _relations(package, inventories, tick)
    if sum(len(b["text"].encode("utf-8")) for b in bodies) > 98304:
        raise ValueError("local_oracle_complete_body_budget")
    tick()
    return {"selection_id": selection_id, "core_unit_ids": sorted(required_core_unit_ids),
            "compatibility_package": package, "bodies": bodies}


def authenticate_local_syntax(local, raw_files, **trusted):
    expected = expected_local_syntax(raw_files, **trusted)
    if canonical(local) != canonical(expected):
        raise ValueError("local_oracle_denominator_body_or_record_conflict")
    package = expected["compatibility_package"]
    return {"status": "pass", "proof_scope": "external_core_complete_local_syntax_only",
            "selection_independence": "not_checked_by_this_asset",
            "business_semantics": "not_checked_by_this_asset",
            "shared_parser_risk": "python_standard_library_ast",
            "files": len(package["files"]), "units": len(package["units"]),
            "owners": len(package["owners"]), "conditions": len(package["conditions"]),
            "relations": len(package["relations"])}
