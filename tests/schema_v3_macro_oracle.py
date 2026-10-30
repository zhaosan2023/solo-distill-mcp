"""Schema 3.0 宏观层独立期望值：仅标准库，不导入任何生产模块。

从调用方冻结的完整源码重新解析，不接受知识包自报文件分母。
共享 Python AST 的共同失效风险仍存在；本模块不证明局部闭包或业务语义。
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import io
import json
import tokenize

PARSER = "python-3.12-ast-v1"
RULE = "macro-static-v3-1"
DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
CONTROL = (ast.If, ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While,
           ast.With, ast.AsyncWith, ast.match_case, ast.ExceptHandler)


def canonical(value):
    def check(item, depth=0):
        if depth > 64:
            raise ValueError("oracle_json_depth")
        if item is None or type(item) in (str, int, bool):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValueError("oracle_json_type")
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def identity(namespace, value):
    return hashlib.sha256(namespace.encode("ascii") + b":" + canonical(value)).hexdigest()


def signature(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        result = "(" + ast.unparse(node.args) + ")"
        if node.returns is not None:
            result += " -> " + ast.unparse(node.returns)
        return result
    if isinstance(node, ast.ClassDef):
        parts = [ast.unparse(base) for base in node.bases]
        parts.extend((keyword.arg + "=" if keyword.arg is not None else "**")
                     + ast.unparse(keyword.value) for keyword in node.keywords)
        return "(" + ", ".join(parts) + ")"
    return None


def condition_branch(node, field, value):
    if not isinstance(value, list) or not value:
        return False
    if isinstance(node, ast.If):
        return field in {"body", "orelse"}
    if isinstance(node, (ast.Try, ast.TryStar)):
        return field in {"body", "orelse", "finalbody"}
    if isinstance(node, ast.ExceptHandler):
        return field == "body"
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return field in {"body", "orelse"}
    return isinstance(node, (ast.With, ast.AsyncWith, ast.match_case)) and field == "body"


def expected_macro(raw_files, *, project_id, snapshot_id, tick=None):
    """独立 AST 前序、作用域计数和事件归约；不读取磁盘或执行源码。"""
    tick = tick or (lambda: None)
    if (type(raw_files) is not dict or not raw_files
            or type(project_id) is not str or not project_id
            or type(snapshot_id) is not str or not snapshot_id):
        raise ValueError("oracle_trusted_input_required")
    files, symbols, module_symbols = [], [], []
    environments, events, module_files = {}, [], {}
    counts = dict(definition_count=0, documentation_count=0, module_count=0,
                  condition_site_count=0, call_site_count=0,
                  import_statement_count=0, import_alias_count=0,
                  inheritance_site_count=0)

    def module_name(path):
        return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")

    for file_index, path in enumerate(sorted(raw_files)):
        tick()
        if (type(path) is not str or not path.endswith(".py") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or type(raw_files[path]) is not bytes):
            raise ValueError("oracle_source_path_or_bytes")
        raw = raw_files[path]
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
        text = raw.decode(encoding)
        codec = "utf-8" if encoding == "utf-8-sig" else encoding
        bom = 3 if encoding == "utf-8-sig" and raw.startswith(b"\xef\xbb\xbf") else 0
        if text.encode(codec) != raw[bom:]:
            raise ValueError("oracle_nonlossless_encoding")
        tree = ast.parse(raw, filename=path, feature_version=(3, 12))
        sha = hashlib.sha256(raw).hexdigest()
        file_id = identity("file-v3", [project_id, snapshot_id, path, sha, PARSER])
        files.append([file_id, path, sha, encoding, "parsed"])
        module_files.setdefault(module_name(path), []).append(file_index)
        physical_lines = io.StringIO(text, newline="").readlines()
        end = ([len(physical_lines), len(physical_lines[-1].encode("utf-8"))]
               if physical_lines else [1, 0])
        local = {(): {"parent": None, "counts": Counter(), "bindings": {},
                      "unsafe": set(), "class": False}}
        environments[file_index] = local

        def add_symbol(kind, name, sig, doc, node, route, parent, guarded):
            start = [1, 0] if isinstance(node, ast.Module) else [node.lineno, node.col_offset]
            stop = end if isinstance(node, ast.Module) else [node.end_lineno, node.end_col_offset]
            metadata = [file_index, parent, kind, name, sig, doc, start, stop,
                        list(route), guarded]
            record_id = identity("symbol-v3", [project_id, snapshot_id, file_id,
                                              list(route), kind, metadata])
            index = len(symbols)
            symbols.append([record_id, *metadata])
            return index

        root_symbol = add_symbol("module", module_name(path), None, None,
                                 tree, (), None, False)
        module_symbols.append(root_symbol)
        counts["module_count"] += 1

        def bind(scope, name, binding=None, guarded=False):
            data = local[scope]
            data["counts"][name] += 1
            if guarded:
                data["unsafe"].add(name)
            elif binding is not None:
                data["bindings"][name] = binding

        def visit(node, route, scope, owner, names, phase, guarded):
            tick()
            defining = isinstance(node, DEFINITIONS)
            inner_owner = owner
            child_names = names
            if defining:
                kind = ("class" if isinstance(node, ast.ClassDef) else
                        "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                child_names = [*names, node.name]
                inner_owner = add_symbol(kind, ".".join(child_names), signature(node), None,
                                         node, route, owner, guarded)
                counts["definition_count"] += 1
                bind(scope, node.name, ("definition", inner_owner), guarded)
                parent_scope = local[scope]["parent"] if local[scope]["class"] else scope
                local[route] = {"parent": parent_scope, "counts": Counter(),
                                "bindings": {}, "unsafe": set(),
                                "class": isinstance(node, ast.ClassDef)}
                if not isinstance(node, ast.ClassDef):
                    for argument in [*node.args.posonlyargs, *node.args.args,
                                     *node.args.kwonlyargs,
                                     *[a for a in (node.args.vararg, node.args.kwarg) if a is not None]]:
                        bind(route, argument.arg)
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        events.append(("inheritance", file_index, scope, inner_owner,
                                       base, ast.unparse(base), "class_base", guarded))
                        counts["inheritance_site_count"] += 1
            elif isinstance(node, ast.Lambda):
                # 保守遮蔽，不把匿名作用域误解析为确定外层目标。
                for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                                 *[a for a in (node.args.vararg, node.args.kwarg) if a is not None]]:
                    local[scope]["unsafe"].add(argument.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(scope, node.id, guarded=guarded)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                local[scope]["unsafe"].update(node.names)
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                bind(scope, node.name, guarded=True)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bind(scope, node.rest, guarded=True)

            if isinstance(node, (ast.Module, *DEFINITIONS)) and node.body:
                first = node.body[0]
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and type(first.value.value) is str):
                    add_symbol("documentation", None, None, first.value.value, first,
                               (*route, "body", 0), inner_owner, guarded)
                    counts["documentation_count"] += 1
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                counts["import_statement_count"] += 1
                for alias in node.names:
                    name = alias.asname or (alias.name.split(".")[0]
                        if isinstance(node, ast.Import) else alias.name)
                    if alias.name == "*":
                        local[scope]["unsafe"].add("*")
                    bind(scope, name, ("import", node, alias), guarded)
                    single = (ast.Import(names=[alias]) if isinstance(node, ast.Import)
                              else ast.ImportFrom(module=node.module, names=[alias], level=node.level))
                    events.append(("module_dependency", file_index, scope, root_symbol,
                                   (node, alias), ast.unparse(single), phase, guarded))
                    counts["import_alias_count"] += 1
            if isinstance(node, ast.Call):
                events.append(("call", file_index, scope, owner, node.func,
                               ast.unparse(node.func), phase, guarded))
                counts["call_site_count"] += 1

            for field, value in ast.iter_fields(node):
                counts["condition_site_count"] += int(condition_branch(node, field, value))
                child_scope, child_owner = scope, owner
                child_phase, child_guarded = phase, guarded or isinstance(node, CONTROL)
                scope_names = names
                if defining:
                    if field == "body":
                        child_scope, child_owner = route, inner_owner
                        child_phase = "class_body" if isinstance(node, ast.ClassDef) else "function_body"
                        child_guarded, scope_names = False, child_names
                    elif field == "decorator_list":
                        child_phase, child_guarded = "decorator", guarded
                    elif field in {"bases", "keywords"}:
                        child_phase, child_guarded = "class_base", guarded
                    elif field in {"returns", "args", "type_params"}:
                        child_phase, child_guarded = "annotation", guarded
                if isinstance(node, ast.arguments) and field in {"defaults", "kw_defaults"}:
                    child_phase = "default"
                if isinstance(node, ast.arg) and field == "annotation":
                    child_phase = "annotation"
                children = enumerate(value) if isinstance(value, list) else [(None, value)]
                for index, child in children:
                    if isinstance(child, ast.AST):
                        visit(child, (*route, field, *(() if index is None else (index,))),
                              child_scope, child_owner, scope_names, child_phase, child_guarded)
        visit(tree, (), (), root_symbol, [], "module", False)

    def find(file_index, scope, name):
        while scope is not None:
            data = environments[file_index][scope]
            if "*" in data["unsafe"] or name in data["unsafe"]:
                return None, "conditional_or_unsafe_binding"
            count = data["counts"][name]
            if count:
                if count != 1:
                    return None, "rebound_or_ambiguous_binding"
                binding = data["bindings"].get(name)
                return (binding, None) if binding is not None else (None, "local_or_parameter_binding")
            scope = data["parent"]
        return None, "unbound_name"

    def imported_module(file_index, statement, alias):
        if isinstance(statement, ast.Import):
            return alias.name, None
        module = statement.module or ""
        if statement.level:
            parent = files[file_index][1].split("/")[:-1]
            if statement.level > len(parent):
                return None, "relative_import_outside_module"
            module = ".".join(parent[:len(parent) - statement.level + 1]
                              + ([module] if module else []))
        return module, None

    def module_target(file_index, statement, alias):
        module, reason = imported_module(file_index, statement, alias)
        if reason:
            return None, reason
        if alias.name == "*":
            return None, "unsupported_star_import"
        if isinstance(statement, ast.ImportFrom) and not statement.module:
            submodule = module + "." + alias.name if module else alias.name
            if len(module_files.get(submodule, [])) == 1:
                return module_files[submodule][0], None
        candidates = module_files.get(module, [])
        return ((candidates[0], None) if len(candidates) == 1
                else (None, "external_or_ambiguous_module"))

    def imported_definition(file_index, binding, attribute=None):
        statement, alias = binding[1:]
        if isinstance(statement, ast.ImportFrom):
            if alias.name == "*" or attribute is not None:
                return None, "unsupported_import_dispatch"
            name = alias.name
        else:
            if attribute is None or (not alias.asname and "." in alias.name):
                return None, "unsupported_module_dispatch"
            name = attribute
        destination, reason = module_target(file_index, statement, alias)
        if destination is None:
            return None, reason
        found, reason = find(destination, (), name)
        if found and found[0] == "definition":
            return found[1], None
        return None, reason or "import_target_not_definition"

    def resolve(file_index, scope, expression):
        if isinstance(expression, ast.Name):
            binding, reason = find(file_index, scope, expression.id)
            if binding:
                return ((binding[1], None) if binding[0] == "definition"
                        else imported_definition(file_index, binding))
            return None, reason
        if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
            binding, reason = find(file_index, scope, expression.value.id)
            if binding and binding[0] == "import":
                return imported_definition(file_index, binding, expression.attr)
            return None, reason or "dynamic_attribute_dispatch"
        return None, "unsupported_expression_dispatch"

    expressions = sorted({event[5] for event in events})
    expression_indexes = {text: index for index, text in enumerate(expressions)}
    grouped = Counter()
    for layer, file_index, scope, origin, expression, text, phase, guarded in events:
        tick()
        if layer == "module_dependency":
            target_file, reason = module_target(file_index, *expression)
            target = None if target_file is None else module_symbols[target_file]
        else:
            target, reason = resolve(file_index, scope, expression)
            if layer == "inheritance" and target is not None and symbols[target][3] != "class":
                target, reason = None, "base_not_unique_class"
        grouped[layer, origin, target, expression_indexes[text], reason, phase, guarded] += 1
    topology = [list(key) + [value] for key, value in
                sorted(grouped.items(), key=lambda item: canonical(list(item[0])))]
    mapping = {**counts, "topology_edge_count": len(topology),
               "unknown_site_count": sum(row[7] for row in topology if row[2] is None),
               "omitted_noncore_micro_details": True}
    return {"schema_version": 3, "parser_version": PARSER, "rule_version": RULE,
            "project_id": project_id, "snapshot_id": snapshot_id, "files": files,
            "symbols": symbols, "expressions": expressions,
            "topology": topology, "mapping": mapping}


def authenticate_macro(value, raw_files, *, project_id, snapshot_id, tick=None):
    """逐完整规范字段对账，区分 bool/int，并拒绝遗漏、重复及未知提升。"""
    expected = expected_macro(raw_files, project_id=project_id,
                              snapshot_id=snapshot_id, tick=tick)
    if canonical(value) != canonical(expected):
        raise ValueError("independent_macro_denominator_or_record_conflict")
    return {"tier": 1, "status": "pass", "files": len(expected["files"]),
            "symbols": len(expected["symbols"]), "mapping": expected["mapping"],
            "proof_scope": "frozen_source_macro_syntax_not_local_or_semantic_acceptance",
            "common_failure_risk": "shared_python_standard_library_ast"}
