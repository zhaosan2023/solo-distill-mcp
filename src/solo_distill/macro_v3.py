"""Schema 3.0 生产宏观索引：全范围符号、保守三图及位点计量。

来源只作解析数据，不执行、不读取参考答案、不导入测试 Oracle。
符号登记与依赖解析分遍处理；仅复用当前操作的私有源码解析树。
宏观完整性不证明局部闭包完整、业务语义或运行时路径。
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import hashlib
import json

from .snapshot_v2 import parsed_tree
from .syntax_v2 import Coordinates

PARSER_VERSION = "python-3.12-ast-v1"
RULE_VERSION = "macro-static-v3-1"
DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
CONTROLS = (ast.If, ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While,
            ast.With, ast.AsyncWith, ast.match_case, ast.ExceptHandler)


def canonical(value):
    """只接受封闭 JSON 域；bool 保留原类型，浮点一律拒绝。"""
    def inspect(item, depth):
        if depth > 64:
            raise ValueError("macro_json_depth")
        if item is None or type(item) in (str, int, bool):
            return
        if type(item) is list:
            for child in item:
                inspect(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                inspect(child, depth + 1)
            return
        raise ValueError("macro_json_type")
    inspect(value, 0)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def identity(namespace, value):
    return hashlib.sha256(namespace.encode("ascii") + b":" + canonical(value)).hexdigest()


def _signature(node):
    if isinstance(node, ast.ClassDef):
        arguments = [ast.unparse(base) for base in node.bases]
        arguments += [(keyword.arg + "=" if keyword.arg is not None else "**")
                      + ast.unparse(keyword.value) for keyword in node.keywords]
        return "(" + ", ".join(arguments) + ")"
    result = "(" + ast.unparse(node.args) + ")"
    if node.returns is not None:
        result += " -> " + ast.unparse(node.returns)
    return result


def _children(node):
    for field, value in ast.iter_fields(node):
        if isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, ast.AST):
                    yield field, (field, index), child
        elif isinstance(value, ast.AST):
            yield field, (field,), value


def _condition_count(node):
    if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While)):
        fields = ("body", "orelse")
    elif isinstance(node, (ast.Try, ast.TryStar)):
        fields = ("body", "orelse", "finalbody")
    elif isinstance(node, (ast.ExceptHandler, ast.With, ast.AsyncWith, ast.match_case)):
        fields = ("body",)
    else:
        return 0
    return sum(bool(getattr(node, field)) for field in fields)


def _arguments(node):
    args = node.args
    return [*args.posonlyargs, *args.args, *args.kwonlyargs,
            *[item for item in (args.vararg, args.kwarg) if item is not None]]


def _module_name(path):
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def build_macro(raw_files, *, project_id, snapshot_id, tick=None):
    """从可信完整源码登记符号，再解析全范围保守依赖；不物化全库 V2 包。"""
    if (type(raw_files) is not dict or not raw_files
            or type(project_id) is not str or not project_id
            or type(snapshot_id) is not str or not snapshot_id):
        raise ValueError("macro_trusted_input_required")
    for path, raw in raw_files.items():
        if (type(path) is not str or not path.endswith(".py") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or type(raw) is not bytes):
            raise ValueError("macro_source_path_or_bytes")
    tick = tick if tick is not None else (lambda: None)
    tick()
    files, symbols, roots, trees = [], [], [], []
    symbol_routes = {}
    modules = defaultdict(list)
    counts = dict(definition_count=0, documentation_count=0, module_count=0,
                  condition_site_count=0, call_site_count=0,
                  import_statement_count=0, import_alias_count=0,
                  inheritance_site_count=0)

    for file_index, path in enumerate(sorted(raw_files)):
        tick()
        raw = raw_files[path]
        coordinates = Coordinates(raw)
        tree = parsed_tree(raw, path)
        trees.append(tree)
        sha = hashlib.sha256(raw).hexdigest()
        file_id = identity("file-v3", [project_id, snapshot_id, path, sha, PARSER_VERSION])
        files.append([file_id, path, sha, coordinates.encoding, "parsed"])
        modules[_module_name(path)].append(file_index)
        end = ([len(coordinates.lines), len(coordinates.lines[-1].encode("utf-8"))]
               if coordinates.lines else [1, 0])

        def register(node, route, parent, kind, name, signature, doc, guarded):
            start = [1, 0] if isinstance(node, ast.Module) else [node.lineno, node.col_offset]
            stop = end if isinstance(node, ast.Module) else [node.end_lineno, node.end_col_offset]
            metadata = [file_index, parent, kind, name, signature, doc,
                        start, stop, list(route), guarded]
            record_id = identity("symbol-v3", [project_id, snapshot_id, file_id,
                                               list(route), kind, metadata])
            index = len(symbols)
            symbols.append([record_id, *metadata])
            symbol_routes[file_index, route] = index
            return index

        root = register(tree, (), None, "module", _module_name(path), None, None, False)
        roots.append(root)
        counts["module_count"] += 1

        def inventory(node, route, owner, names, guarded):
            tick()
            defining = isinstance(node, DEFINITIONS)
            body_owner, body_names = owner, names
            if defining:
                kind = ("class" if isinstance(node, ast.ClassDef) else
                        "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                body_names = [*names, node.name]
                body_owner = register(node, route, owner, kind, ".".join(body_names),
                                      _signature(node), None, guarded)
                counts["definition_count"] += 1
            if isinstance(node, (ast.Module, *DEFINITIONS)) and node.body:
                first = node.body[0]
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and type(first.value.value) is str):
                    register(first, (*route, "body", 0), body_owner, "documentation",
                             None, None, first.value.value, guarded)
                    counts["documentation_count"] += 1
            counts["condition_site_count"] += _condition_count(node)
            for field, suffix, child in _children(node):
                if defining and field == "body":
                    inventory(child, route + suffix, body_owner, body_names, False)
                else:
                    nested_guard = guarded if defining else guarded or isinstance(node, CONTROLS)
                    inventory(child, route + suffix, owner, names, nested_guard)
        inventory(tree, (), root, [], False)

    scopes_by_file = []
    events = []
    for file_index, tree in enumerate(trees):
        tick()
        scopes = {(): {"parent": None, "class": False, "counts": Counter(),
                       "values": {}, "unsafe": set()}}
        scopes_by_file.append(scopes)

        def bind(scope, name, value=None, guarded=False):
            data = scopes[scope]
            data["counts"][name] += 1
            if guarded:
                data["unsafe"].add(name)
            elif value is not None:
                data["values"][name] = value

        def dependencies(node, route, scope, owner, phase, guarded):
            tick()
            defining = isinstance(node, DEFINITIONS)
            inner_owner = owner
            if defining:
                inner_owner = symbol_routes[file_index, route]
                bind(scope, node.name, ("definition", inner_owner), guarded)
                parent = scopes[scope]["parent"] if scopes[scope]["class"] else scope
                scopes[route] = {"parent": parent, "class": isinstance(node, ast.ClassDef),
                                 "counts": Counter(), "values": {}, "unsafe": set()}
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        events.append(("inheritance", file_index, scope, inner_owner,
                                       base, ast.unparse(base), "class_base", guarded))
                        counts["inheritance_site_count"] += 1
                else:
                    for argument in _arguments(node):
                        bind(route, argument.arg)
            elif isinstance(node, ast.Lambda):
                scopes[scope]["unsafe"].update(argument.arg for argument in _arguments(node))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(scope, node.id, guarded=guarded)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                scopes[scope]["unsafe"].update(node.names)
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                bind(scope, node.name, guarded=True)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bind(scope, node.rest, guarded=True)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                counts["import_statement_count"] += 1
                for alias in node.names:
                    local_name = alias.asname or (alias.name.split(".")[0]
                        if isinstance(node, ast.Import) else alias.name)
                    if alias.name == "*":
                        scopes[scope]["unsafe"].add("*")
                    bind(scope, local_name, ("import", node, alias), guarded)
                    single = (ast.Import(names=[alias]) if isinstance(node, ast.Import) else
                              ast.ImportFrom(module=node.module, names=[alias], level=node.level))
                    events.append(("module_dependency", file_index, scope, roots[file_index],
                                   (node, alias), ast.unparse(single), phase, guarded))
                    counts["import_alias_count"] += 1
            if isinstance(node, ast.Call):
                events.append(("call", file_index, scope, owner, node.func,
                               ast.unparse(node.func), phase, guarded))
                counts["call_site_count"] += 1
            for field, suffix, child in _children(node):
                child_scope, child_owner = scope, owner
                child_phase, child_guard = phase, guarded or isinstance(node, CONTROLS)
                if defining:
                    child_guard = guarded
                    if field == "body":
                        child_scope, child_owner = route, inner_owner
                        child_phase = "class_body" if isinstance(node, ast.ClassDef) else "function_body"
                        child_guard = False
                    elif field == "decorator_list":
                        child_phase = "decorator"
                    elif field in {"bases", "keywords"}:
                        child_phase = "class_base"
                    elif field in {"returns", "args", "type_params"}:
                        child_phase = "annotation"
                elif isinstance(node, ast.arguments) and field in {"defaults", "kw_defaults"}:
                    child_phase = "default"
                elif isinstance(node, ast.arg) and field == "annotation":
                    child_phase = "annotation"
                dependencies(child, route + suffix, child_scope, child_owner, child_phase, child_guard)
        dependencies(tree, (), (), roots[file_index], "module", False)

    def lookup(file_index, scope, name):
        while scope is not None:
            data = scopes_by_file[file_index][scope]
            if name in data["unsafe"] or "*" in data["unsafe"]:
                return None, "conditional_or_unsafe_binding"
            count = data["counts"][name]
            if count > 1:
                return None, "rebound_or_ambiguous_binding"
            if count == 1:
                binding = data["values"].get(name)
                return ((binding, None) if binding is not None
                        else (None, "local_or_parameter_binding"))
            scope = data["parent"]
        return None, "unbound_name"

    def import_module(file_index, statement, alias):
        if alias.name == "*":
            # 相对导入越界比星号分派具有更早的失败优先级。
            star = True
        else:
            star = False
        if isinstance(statement, ast.Import):
            module = alias.name
        else:
            module = statement.module or ""
            if statement.level:
                parent = files[file_index][1].split("/")[:-1]
                if statement.level > len(parent):
                    return None, "relative_import_outside_module"
                module = ".".join(parent[:len(parent) - statement.level + 1]
                                  + ([module] if module else []))
        if star:
            return None, "unsupported_star_import"
        if isinstance(statement, ast.ImportFrom) and not statement.module:
            submodule = module + "." + alias.name if module else alias.name
            matches = modules.get(submodule, ())
            if len(matches) == 1:
                return matches[0], None
        matches = modules.get(module, ())
        return ((matches[0], None) if len(matches) == 1
                else (None, "external_or_ambiguous_module"))

    def import_definition(file_index, binding, attribute=None):
        statement, alias = binding[1:]
        if isinstance(statement, ast.ImportFrom):
            if alias.name == "*" or attribute is not None:
                return None, "unsupported_import_dispatch"
            name = alias.name
        else:
            if attribute is None or not alias.asname and "." in alias.name:
                return None, "unsupported_module_dispatch"
            name = attribute
        destination, reason = import_module(file_index, statement, alias)
        if destination is None:
            return None, reason
        target, reason = lookup(destination, (), name)
        if target and target[0] == "definition":
            return target[1], None
        return None, reason or "import_target_not_definition"

    def resolve(file_index, scope, expression):
        if isinstance(expression, ast.Name):
            binding, reason = lookup(file_index, scope, expression.id)
            if binding:
                return ((binding[1], None) if binding[0] == "definition"
                        else import_definition(file_index, binding))
            return None, reason
        if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
            binding, reason = lookup(file_index, scope, expression.value.id)
            if binding and binding[0] == "import":
                return import_definition(file_index, binding, expression.attr)
            return None, reason or "dynamic_attribute_dispatch"
        return None, "unsupported_expression_dispatch"

    expressions = sorted({event[5] for event in events})
    expression_indexes = {expression: index for index, expression in enumerate(expressions)}
    aggregate = Counter()
    for layer, file_index, scope, owner, expression, text, phase, guarded in events:
        tick()
        if layer == "module_dependency":
            destination, reason = import_module(file_index, *expression)
            target = None if destination is None else roots[destination]
        else:
            target, reason = resolve(file_index, scope, expression)
            if layer == "inheritance" and target is not None and symbols[target][3] != "class":
                target, reason = None, "base_not_unique_class"
        aggregate[layer, owner, target, expression_indexes[text], reason, phase, guarded] += 1
    topology = [list(key) + [aggregate[key]]
                for key in sorted(aggregate, key=lambda item: canonical(list(item)))]
    mapping = {**counts, "topology_edge_count": len(topology),
               "unknown_site_count": sum(row[7] for row in topology if row[2] is None),
               "omitted_noncore_micro_details": True}
    tick()
    return {"schema_version": 3, "parser_version": PARSER_VERSION, "rule_version": RULE_VERSION,
            "project_id": project_id, "snapshot_id": snapshot_id, "files": files,
            "symbols": symbols, "expressions": expressions, "topology": topology, "mapping": mapping}


def validate_macro(value, raw_files, *, project_id, snapshot_id, tick=None):
    """生产端重算完整规范结构；与独立测试端复算是两个不同门禁。"""
    expected = build_macro(raw_files, project_id=project_id, snapshot_id=snapshot_id, tick=tick)
    if canonical(value) != canonical(expected):
        raise ValueError("macro_denominator_or_record_conflict")
