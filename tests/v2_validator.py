"""Independent test-side version decoding and AST/byte authentication.

No production parser, classifier, identity function or validator is imported.
This proves source syntax, not natural-language semantics or live delivery.
"""
from __future__ import annotations

import ast
import codecs
import hashlib
import io
import json
import tokenize

TABLES = ("files", "units", "owners", "sources", "conditions", "role_proofs",
          "facts", "relations", "obligations")


def digest(namespace, value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((namespace + ":" + encoded).encode()).hexdigest()


def decode(raw, raw_files):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    def reject(_):
        raise ValueError("nonfinite")
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
    if type(value) is not dict or type(value.get("contract_version")) is not int:
        raise ValueError("invalid_version")
    if value["contract_version"] == 2:
        authenticate(value, raw_files)
    elif value["contract_version"] != 1:
        raise ValueError("unsupported_version")
    return value


def binding_resolver(package, raw_files):
    """Independent lexical binding oracle; never imports the production graph."""
    from collections import Counter
    public = {(u["file"], tuple(u["syntax_path"])): i for i, u in enumerate(package["units"])}
    modules = {}
    scopes, sites = {}, {}
    for file_index, file in enumerate(package["files"]):
        module = file["path"].removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        modules.setdefault(module, []).append(file_index)
        tree = ast.parse(raw_files[file["path"]], feature_version=(3, 12))
        local = {(): {"parent": None, "counts": Counter(), "values": {}, "unsafe": set(), "class": False}}

        def record(scope, name, value=None, guarded=False):
            data = local[scope]
            data["counts"][name] += 1
            if guarded:
                data["unsafe"].add(name)
            elif value is not None:
                data["values"][name] = value

        def collect(node, path, scope, guarded=False):
            is_definition = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if is_definition:
                record(scope, node.name, ("definition", public.get((file_index, path))), guarded)
                parent = local[scope]["parent"] if local[scope]["class"] else scope
                local[path] = {"parent": parent, "counts": Counter(), "values": {}, "unsafe": set(),
                               "class": isinstance(node, ast.ClassDef)}
                if not isinstance(node, ast.ClassDef):
                    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                            *[a for a in (node.args.vararg, node.args.kwarg) if a is not None]]
                    for argument in args:
                        record(path, argument.arg)
            elif isinstance(node, ast.Lambda):
                for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                                 *[a for a in (node.args.vararg, node.args.kwarg) if a is not None]]:
                    record(scope, argument.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    if alias.name == "*":
                        local[scope]["unsafe"].add("*")
                    record(scope, name, ("import", node, alias), guarded)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                record(scope, node.id, guarded=guarded)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                local[scope]["unsafe"].update(node.names)
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                record(scope, node.name, guarded=True)
            if isinstance(node, ast.Call):
                sites[file_index, path] = (scope, node)
            for field, value in ast.iter_fields(node):
                for index, child in (enumerate(value) if isinstance(value, list) else [(None, value)]):
                    if isinstance(child, ast.AST):
                        child_scope = path if is_definition and field == "body" else scope
                        conditional = guarded or isinstance(node, (ast.If, ast.Try, ast.TryStar, ast.For,
                            ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.match_case, ast.ExceptHandler))
                        collect(child, path + (field,) + (() if index is None else (index,)), child_scope,
                                False if is_definition else conditional)
        collect(tree, (), ())
        scopes[file_index] = local

    def find(file_index, scope, name):
        while scope is not None:
            data = scopes[file_index][scope]
            if name in data["unsafe"] or "*" in data["unsafe"]:
                return None
            if data["counts"][name]:
                return data["values"].get(name) if data["counts"][name] == 1 else None
            scope = data["parent"]
        return None

    def import_target(file_index, binding, attribute=None):
        statement, alias = binding[1:]
        if isinstance(statement, ast.ImportFrom):
            if alias.name == "*" or attribute is not None:
                return None
            module = statement.module or ""
            if statement.level:
                parent = package["files"][file_index]["path"].split("/")[:-1]
                if statement.level > len(parent):
                    return None
                module = ".".join(parent[:len(parent) - statement.level + 1] + ([module] if module else []))
            name = alias.name
        else:
            if attribute is None or not alias.asname and "." in alias.name:
                return None
            module, name = alias.name, attribute
        targets = modules.get(module, [])
        if len(targets) != 1:
            return None
        binding = find(targets[0], (), name)
        return binding[1] if binding and binding[0] == "definition" else None

    def resolve(file_index, path):
        scope, call = sites[file_index, tuple(path)]
        expression = call.func
        if isinstance(expression, ast.Name):
            binding = find(file_index, scope, expression.id)
            if binding:
                return binding[1] if binding[0] == "definition" else import_target(file_index, binding)
        elif isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
            binding = find(file_index, scope, expression.value.id)
            if binding and binding[0] == "import":
                return import_target(file_index, binding, expression.attr)
        return None
    return resolve


def authenticate(package, raw_files):
    def check(value, reason):
        if not value:
            raise ValueError(reason)
    def shape(value, fields):
        check(type(value) is dict and set(value) == set(fields), "fields")
    def reference(value, table, nullable=False):
        check(nullable and value is None or type(value) is int and 0 <= value < len(package[table]), "reference")
    def references(values, table):
        check(type(values) is list, "reference_list")
        for value in values:
            reference(value, table)
        check(len(values) == len(set(values)), "duplicate_reference")
    def string(value):
        check(type(value) is str and bool(value) and len(value.encode()) <= 4096, "string")
    def level(value):
        check(type(value) is str and value in ("unknown", "sourced_inference", "direct_static"), "level")

    shape(package, set(TABLES) | {"contract_version", "kind", "parser_version", "project_id",
                               "snapshot_id", "scenario", "status", "failures", "coverage"})
    check(type(package["contract_version"]) is int and package["contract_version"] == 2, "version")
    check(package["parser_version"] == "python-3.12-ast-v1" and package["kind"] == "knowledge_package", "parser")
    string(package["project_id"])
    string(package["snapshot_id"])
    check(type(package["scenario"]) is str and package["scenario"] in {"A", "B"}, "scenario")
    seen = set()
    for table in TABLES:
        check(type(package[table]) is list, "table")
        for item in package[table]:
            check(type(item) is dict and "id" in item, "identity")
            string(item["id"])
            check(item["id"] not in seen, "duplicate_identity")
            seen.add(item["id"])
    records = {}
    condition_records = {}
    file_seen = set()
    for file_index, file in enumerate(package["files"]):
        shape(file, {"id", "path", "sha256", "encoding", "parse_status"})
        path = file["path"]
        check(type(path) is str and path in raw_files and path not in file_seen and not path.startswith("/")
              and not any(part in {"..", ".", ""} for part in path.split("/")) and "\\" not in path, "path")
        file_seen.add(path)
        raw = raw_files[path]
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
        text = raw.decode(encoding)
        codec = "utf-8" if encoding == "utf-8-sig" else encoding
        bom = 3 if raw.startswith(codecs.BOM_UTF8) else 0
        check(text.encode(codec) == raw[bom:], "encoding_roundtrip")
        sha = hashlib.sha256(raw).hexdigest()
        check(file == {"id": digest("file-v2", [path, sha, package["parser_version"]]), "path": path,
                       "sha256": sha, "encoding": encoding, "parse_status": "parsed"}, "file_digest")
        import re
        lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]
        def pos(row, col):
            prefix = lines[row - 1].encode()[:col].decode()
            return [row, len(prefix)], bom + len("".join(lines[:row - 1]).encode(codec)) + len(prefix.encode(codec))
        tree = ast.parse(raw, filename=path, feature_version=(3, 12))
        def visit(node, route, scope, conditions, owner, top=False):
            definition = isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            if definition or top and isinstance(node, ast.stmt):
                guard = False
                if isinstance(node, ast.If):
                    expr = node.test
                    if isinstance(expr, ast.Compare) and len(expr.ops) == 1 and isinstance(expr.ops[0], ast.Eq):
                        operands = [expr.left, expr.comparators[0]]
                        guard = any(isinstance(operands[i], ast.Name) and operands[i].id == "__name__"
                            and isinstance(operands[1-i], ast.Constant) and operands[1-i].value == "__main__" for i in (0, 1))
                kind = "definition" if definition else "main_guard" if guard else "statement"
                name = ".".join(scope + [node.name]) if definition else None
                row, col = node.lineno, node.col_offset
                if definition and node.decorator_list:
                    first = sorted(node.decorator_list, key=lambda item: (item.lineno, item.col_offset))[0]
                    row, col = first.lineno, first.col_offset - 1
                char_start, raw_start = pos(row, col)
                char_end, raw_end = pos(node.end_lineno, node.end_col_offset)
                span = {"start": [row, col], "end": [node.end_lineno, node.end_col_offset],
                        "char_start": char_start, "char_end": char_end, "raw_start": raw_start, "raw_end": raw_end}
                unit_id = digest("unit-v2", [file["id"], route, kind, name, span])
                owner = unit_id
                records[unit_id] = ({"id": unit_id, "kind": kind, "ast_type": type(node).__name__,
                    "syntax_path": route, "qualified_name": name, **span, "file": file_index}, conditions, node)
            local_scope = scope + [node.name] if definition else scope
            for field, children in ast.iter_fields(node):
                label = None
                if isinstance(children, list) and children:
                    if isinstance(node, ast.If):
                        label = {"body": "if", "orelse": "else"}.get(field)
                    elif isinstance(node, (ast.Try, ast.TryStar)):
                        label = {"body": "try", "orelse": "else", "finalbody": "finally"}.get(field)
                    elif isinstance(node, ast.ExceptHandler) and field == "body":
                        label = "except"
                    elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                        label = {"body": "loop", "orelse": "loop_else"}.get(field)
                    elif isinstance(node, (ast.With, ast.AsyncWith)) and field == "body":
                        label = "with"
                    elif isinstance(node, ast.match_case) and field == "body":
                        label = "case"
                active = conditions
                if label:
                    cid = digest("condition-v2", [file["id"], route, field, label])
                    condition_records[cid] = {"id": cid, "syntax_path": route, "field": field,
                                             "branch": label, "scope": ".".join(local_scope), "origin": owner}
                    active = conditions + [cid]
                values = enumerate(children) if isinstance(children, list) else [(None, children)]
                for index, child in values:
                    if isinstance(child, ast.AST):
                        visit(child, route + [field] + ([] if index is None else [index]), local_scope,
                              active, owner, isinstance(node, ast.Module) and field == "body")
        visit(tree, [], [], [], None)
    for unit in package["units"]:
        shape(unit, {"id", "kind", "ast_type", "syntax_path", "qualified_name", "start", "end",
                     "char_start", "char_end", "raw_start", "raw_end", "conditions", "file"})
        reference(unit["file"], "files")
        references(unit["conditions"], "conditions")
        check(unit["id"] in records, "unit_id")
        expected, active, _ = records[unit["id"]]
        for key in ("raw_start", "raw_end"):
            check(type(unit[key]) is int, "byte_type")
        for key in ("start", "end", "char_start", "char_end"):
            check(type(unit[key]) is list and len(unit[key]) == 2 and all(type(part) is int for part in unit[key]), "position_type")
        check(type(unit["syntax_path"]) is list and all(type(part) in {str, int} for part in unit["syntax_path"]), "route_type")
        check({key: value for key, value in unit.items() if key != "conditions"} == expected, "unit_syntax")
        check([package["conditions"][i]["id"] for i in unit["conditions"]] == active, "condition_closure")
    owner_units = set()
    for owner in package["owners"]:
        shape(owner, {"id", "unit", "file", "raw_start", "raw_end", "body_bytes", "complete"})
        reference(owner["unit"], "units")
        reference(owner["file"], "files")
        check(owner["unit"] not in owner_units, "duplicate_body")
        owner_units.add(owner["unit"])
        unit = package["units"][owner["unit"]]
        file = package["files"][owner["file"]]
        check(owner["complete"] is True and all(type(owner[key]) is int for key in ("body_bytes", "raw_start", "raw_end")), "owner_type")
        check(unit["file"] == owner["file"] and unit["raw_start"] == owner["raw_start"]
              and unit["raw_end"] == owner["raw_end"] and owner["id"] == digest("owner-v2", unit["id"]), "owner_range")
        codec = "utf-8" if file["encoding"] == "utf-8-sig" else file["encoding"]
        body = raw_files[file["path"]][owner["raw_start"]:owner["raw_end"]].decode(codec)
        check(owner["body_bytes"] == len(body.encode()), "body_cost")
    check(len(package["units"]) == len(package["sources"]), "source_cardinality")
    for i, source in enumerate(package["sources"]):
        shape(source, {"id", "file", "unit", "owner", "conditions"})
        for key, table in (("file", "files"), ("unit", "units"), ("owner", "owners")):
            reference(source[key], table)
        references(source["conditions"], "conditions")
        unit, owner = package["units"][source["unit"]], package["owners"][source["owner"]]
        parent = package["units"][owner["unit"]]
        check(source["unit"] == i and source["file"] == unit["file"] == parent["file"]
              and source["conditions"] == unit["conditions"] and parent["raw_start"] <= unit["raw_start"]
              and unit["raw_end"] <= parent["raw_end"] and unit["syntax_path"][:len(parent["syntax_path"])] == parent["syntax_path"]
              and source["id"] == digest("source-v2", unit["id"]), "source_alias")
    for condition in package["conditions"]:
        shape(condition, {"id", "syntax_path", "field", "branch", "scope", "unit", "source"})
        reference(condition["unit"], "units")
        reference(condition["source"], "sources")
        check(condition["id"] in condition_records, "condition_id")
        expected = condition_records[condition["id"]]
        check(all(condition[key] == expected[key] for key in {"id", "syntax_path", "field", "branch", "scope"})
              and expected["origin"] == package["units"][condition["unit"]]["id"]
              and package["sources"][condition["source"]]["unit"] == condition["unit"], "condition_origin")
    for proof in package["role_proofs"]:
        shape(proof, {"id", "unit", "role", "rule", "level", "sources"})
        reference(proof["unit"], "units")
        references(proof["sources"], "sources")
        string(proof["rule"])
        level(proof["level"])
        check(type(proof["role"]) is str and proof["role"] in {"entry", "assembly", "state", "persistence", "boundary"}, "role")
        check(all(package["sources"][i]["unit"] == proof["unit"] for i in proof["sources"]), "role_source")
        if proof["level"] == "direct_static":
            unit = package["units"][proof["unit"]]
            node = records[unit["id"]][2]
            nodes = list(ast.walk(node))
            signals = set()
            if unit["kind"] == "main_guard":
                signals.add(("entry", "main-guard-equality-v1"))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute) and d.func.attr in {"route", "command"} for d in node.decorator_list):
                signals.add(("entry", "registration-decorator-v1"))
            for expr in nodes:
                if isinstance(expr, ast.Call):
                    if isinstance(expr.func, ast.Name) and expr.func.id[:1].isupper():
                        signals.add(("assembly", "constructor-expression-v1"))
                    if isinstance(expr.func, ast.Name) and expr.func.id == "open" or isinstance(expr.func, ast.Attribute) and expr.func.attr in {"write", "write_text", "write_bytes", "commit"}:
                        signals.add(("persistence", "io-expression-v1"))
                if isinstance(expr, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = expr.targets if isinstance(expr, ast.Assign) else [expr.target]
                    if any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self" for t in targets):
                        signals.add(("state", "self-attribute-assignment-v1"))
            if not signals:
                signals.add(("boundary", "no-lifecycle-signal-v1"))
            check((proof["role"], proof["rule"]) in signals, "forged_role")
    for fact in package["facts"]:
        shape(fact, {"id", "text", "level", "sources", "relations", "conditions"})
        string(fact["text"])
        level(fact["level"])
        check(fact["level"] != "direct_static", "promoted_prose")
        for table in ("sources", "relations", "conditions"):
            references(fact[table], table)
    resolve_binding = binding_resolver(package, raw_files) if package["relations"] else None
    for relation in package["relations"]:
        shape(relation, {"id", "from_unit", "to_unit", "layer", "direction", "conditions", "sources", "reason", "site"})
        shape(relation["site"], {"syntax_path", "start", "end", "expression"})
        site = relation["site"]
        check(type(site["syntax_path"]) is list and all(type(part) in {str, int}
              for part in site["syntax_path"]), "site_path")
        for key in ("start", "end"):
            check(type(site[key]) is list and len(site[key]) == 2 and all(type(part) is int for part in site[key]), "site_position")
        string(site["expression"])
        reference(relation["from_unit"], "units")
        reference(relation["to_unit"], "units", True)
        references(relation["sources"], "sources")
        references(relation["conditions"], "conditions")
        check(type(relation["layer"]) is str and relation["layer"] in {"call", "import", "inheritance"}
              and relation["direction"] == "caller_to_dependency" and bool(relation["sources"]), "relation")
        if relation["to_unit"] is None:
            string(relation["reason"])
        else:
            check(relation["reason"] is None, "relation_reason")
        origin = package["units"][relation["from_unit"]]
        file = package["files"][origin["file"]]
        node = ast.parse(raw_files[file["path"]], feature_version=(3, 12))
        try:
            for part in site["syntax_path"]:
                node = node[part] if type(part) is int else getattr(node, part)
        except (AttributeError, IndexError, TypeError):
            raise ValueError("forged_site_path") from None
        check(isinstance(node, ast.Call) if relation["layer"] == "call" else
              isinstance(node, (ast.Import, ast.ImportFrom)), "site_kind")
        check(site["start"] == [node.lineno, node.col_offset] and
              site["end"] == [node.end_lineno, node.end_col_offset] and
              site["expression"] == ast.unparse(node.func if isinstance(node, ast.Call) else node), "site_syntax")
        check(origin["syntax_path"] == site["syntax_path"][:len(origin["syntax_path"])] and
              relation["sources"] == [relation["from_unit"]], "site_source")
        active = list(origin["conditions"])
        for index, condition in enumerate(package["conditions"]):
            condition_unit = package["units"][condition["unit"]]
            prefix = condition["syntax_path"] + [condition["field"]]
            if condition_unit["file"] == origin["file"] and site["syntax_path"][:len(prefix)] == prefix and index not in active:
                active.append(index)
        check(relation["conditions"] == active, "relation_condition_closure")
        if relation["layer"] == "call":
            check(relation["to_unit"] == resolve_binding(origin["file"], site["syntax_path"]), "forged_binding_target")
        else:
            check(relation["to_unit"] is None, "unsupported_known_import_edge")
        target = None if relation["to_unit"] is None else package["units"][relation["to_unit"]]["id"]
        check(relation["id"] == digest("relation-v2", [origin["id"], site, relation["layer"], target,
                                                     relation["conditions"], relation["reason"]]), "relation_identity")
    ranks = {"unknown": 0, "sourced_inference": 1, "direct_static": 2}
    for obligation in package["obligations"]:
        shape(obligation, {"id", "required", "evidence", "role", "state", "supports", "reason"})
        check(type(obligation["required"]) is bool, "required_type")
        level(obligation["evidence"])
        check(obligation["role"] is None or type(obligation["role"]) is str and obligation["role"] in {"entry", "assembly", "state", "persistence", "boundary"}, "obligation_role")
        check(type(obligation["state"]) is str and obligation["state"] in {"satisfied", "unknown", "unread", "blocked"}, "obligation_state")
        check(type(obligation["supports"]) is list, "supports")
        satisfied, support_seen = False, set()
        for support in obligation["supports"]:
            shape(support, {"table", "index"})
            check(type(support["table"]) is str and support["table"] in {"facts", "role_proofs"}, "support_type")
            reference(support["index"], support["table"])
            key = support["table"], support["index"]
            check(key not in support_seen, "duplicate_support")
            support_seen.add(key)
            item = package[support["table"]][support["index"]]
            satisfied |= bool(item["sources"] and ranks[item["level"]] > 0 and ranks[item["level"]] >= ranks[obligation["evidence"]]
                and (obligation["role"] is None or support["table"] == "role_proofs" and item["role"] == obligation["role"]))
        check((obligation["state"] == "satisfied") == satisfied, "satisfaction")
        if satisfied:
            check(obligation["reason"] is None, "satisfied_reason")
        else:
            string(obligation["reason"])
    check(type(package["failures"]) is list, "failures")
    for failure in package["failures"]:
        shape(failure, {"kind", "reason"})
        string(failure["reason"])
        check(type(failure["kind"]) is str and failure["kind"] in {"failed", "blocked"}, "failure_kind")
    states = [o["state"] for o in package["obligations"] if o["required"]]
    expected = "failed" if any(f["kind"] == "failed" for f in package["failures"]) else "blocked" if package["failures"] or "blocked" in states or not package["sources"] else "partial" if any(s != "satisfied" for s in states) else "complete"
    check(package["status"] == expected, "status")
    shape(package["coverage"], {"scan_files", "body_units", "semantic_supported"})
    check(all(type(value) is int and value >= 0 for value in package["coverage"].values()), "coverage_type")
    check(package["coverage"] == {"scan_files": len(raw_files), "body_units": len(package["owners"]),
        "semantic_supported": sum(o["state"] == "satisfied" and o["evidence"] == "sourced_inference" for o in package["obligations"])}, "coverage")
    return package
