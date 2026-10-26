"""Conservative static bindings, layered relations and iterative SCC display.

Known edges require a unique unconditional lexical binding. Attribute dispatch,
rebinding, conditional definitions and external imports remain explicit unknowns.
The graph is static dependency navigation, never a runtime execution schedule.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import heapq

from .syntax_v2 import Coordinates, identity

DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def extract_relations(package: dict, raw_files: dict[str, bytes]) -> list[dict]:
    units = package["units"]
    routes = {(unit["file"], tuple(unit["syntax_path"])): index for index, unit in enumerate(units)}
    module_files = defaultdict(list)
    for index, file in enumerate(package["files"]):
        module = file["path"].removesuffix(".py").replace("/", ".")
        module = module.removesuffix(".__init__")
        module_files[module].append(index)
    file_records = {}
    records = []
    for file_index, file in enumerate(package["files"]):
        tree = ast.parse(Coordinates(raw_files[file["path"]]).text, feature_version=(3, 12))
        scopes = {(): {"parent": None, "counts": Counter(), "bindings": {}, "unsafe": set()}}
        records_for_file = []

        def bind(scope, name, value=None, conditional=False):
            item = scopes[scope]
            item["counts"][name] += 1
            if value is not None and not conditional:
                item["bindings"][name] = value
            if conditional:
                item["unsafe"].add(name)

        def walk(node, route, scope, owner, conditional=False):
            public = routes.get((file_index, tuple(route)))
            if public is not None:
                owner = public
            definition = isinstance(node, DEFINITIONS)
            if definition:
                bind(scope, node.name, ("definition", public), conditional)
                nested = tuple(route)
                # Class scopes are not lexical parents of method bodies.
                parent = scopes[scope]["parent"] if isinstance(tree_node(scope), ast.ClassDef) else scope
                scopes[nested] = {"parent": parent, "counts": Counter(), "bindings": {}, "unsafe": set()}
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                    args += [item for item in (node.args.vararg, node.args.kwarg) if item is not None]
                    for arg in args:
                        bind(nested, arg.arg)
            elif isinstance(node, ast.Lambda):
                # Lambda parameters shadow outer names. Conservatively mark
                # them in the enclosing analysis scope, never create false edges.
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args += [item for item in (node.args.vararg, node.args.kwarg) if item is not None]
                for arg in args:
                    bind(scope, arg.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    if alias.name == "*":
                        scopes[scope]["unsafe"].add("*")
                    bind(scope, name, ("import", node, alias), conditional)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(scope, node.id, conditional=conditional)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                scopes[scope]["unsafe"].update(node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bind(scope, node.name, conditional=True)
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                bind(scope, node.name, conditional=True)
            if owner is not None and isinstance(node, (ast.Call, ast.Import, ast.ImportFrom)):
                records_for_file.append((node, route, scope, owner))
            for field, value in ast.iter_fields(node):
                # Lambda and comprehension names conservatively contaminate this
                # scope instead of inventing a dispatch binding for their bodies.
                values = enumerate(value) if isinstance(value, list) else [(None, value)]
                for index, child in values:
                    if not isinstance(child, ast.AST):
                        continue
                    child_route = route + [field] + ([] if index is None else [index])
                    child_scope = tuple(route) if definition and field == "body" else scope
                    guarded = conditional or isinstance(node, (ast.If, ast.Try, ast.TryStar, ast.For,
                        ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.match_case, ast.ExceptHandler))
                    walk(child, child_route, child_scope, owner, guarded if not definition else False)

        def tree_node(route):
            node = tree
            for part in route:
                node = node[part] if type(part) is int else getattr(node, part)
            return node

        walk(tree, [], (), None)
        file_records[file_index] = scopes
        records.extend((file_index, *record) for record in records_for_file)

    def lookup(file_index, scope, name):
        scopes = file_records[file_index]
        while scope is not None:
            item = scopes[scope]
            if "*" in item["unsafe"] or name in item["unsafe"]:
                return None, "conditional_or_unsafe_binding"
            if item["counts"][name]:
                if item["counts"][name] != 1:
                    return None, "rebound_or_ambiguous_binding"
                binding = item["bindings"].get(name)
                return (binding, None) if binding else (None, "local_or_parameter_binding")
            scope = item["parent"]
        return None, "unbound_name"

    def imported_target(file_index, node, alias, attribute=None):
        file = package["files"][file_index]
        if isinstance(node, ast.ImportFrom):
            if alias.name == "*" or attribute is not None:
                return None, "unsupported_import_dispatch"
            module = node.module or ""
            if node.level:
                parent = file["path"].split("/")[:-1]
                if node.level > len(parent):
                    return None, "relative_import_outside_module"
                base = parent[:len(parent) - node.level + 1]
                module = ".".join(base + ([module] if module else []))
            name = alias.name
        else:
            if attribute is None or not alias.asname and "." in alias.name:
                return None, "unsupported_module_dispatch"
            module, name = alias.name, attribute
        destinations = module_files.get(module, [])
        if len(destinations) != 1:
            return None, "external_or_ambiguous_module"
        binding, reason = lookup(destinations[0], (), name)
        if binding and binding[0] == "definition" and binding[1] is not None:
            return binding[1], None
        return None, reason or "import_target_not_selected"

    relations = []
    for file_index, node, route, scope, owner in records:
        target, reason = None, "dynamic_attribute_dispatch"
        layer = "call" if isinstance(node, ast.Call) else "import"
        if isinstance(node, ast.Call):
            expression = node.func
            if isinstance(expression, ast.Name):
                binding, reason = lookup(file_index, scope, expression.id)
                if binding:
                    if binding[0] == "definition":
                        target = binding[1]
                        reason = None if target is not None else "target_not_selected"
                    else:
                        target, reason = imported_target(file_index, binding[1], binding[2])
            elif isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
                binding, local_reason = lookup(file_index, scope, expression.value.id)
                if binding and binding[0] == "import":
                    target, reason = imported_target(file_index, binding[1], binding[2], expression.attr)
                elif local_reason:
                    reason = local_reason
            text = ast.unparse(expression)
        else:
            # One import statement can name multiple targets; module imports do
            # not identify a single complete definition, and stay unknown.
            text = ast.unparse(node)
            reason = "module_import_not_definition_edge"
        site = {"syntax_path": route, "start": [node.lineno, node.col_offset],
                "end": [node.end_lineno, node.end_col_offset], "expression": text}
        conditions = list(units[owner]["conditions"])
        # Calls inside a selected complete unit inherit lexical branch origins.
        for index, condition in enumerate(package["conditions"]):
            origin = units[condition["unit"]]
            if origin["file"] != file_index:
                continue
            branch_route = condition["syntax_path"] + [condition["field"]]
            if route[:len(branch_route)] == branch_route and index not in conditions:
                conditions.append(index)
        relation = {"id": identity("relation-v2", [units[owner]["id"], site, layer,
                     None if target is None else units[target]["id"], conditions, reason]),
                    "from_unit": owner, "to_unit": target, "layer": layer,
                    "direction": "caller_to_dependency", "conditions": conditions,
                    "sources": [owner], "reason": reason, "site": site}
        relations.append(relation)
    return sorted(relations, key=lambda item: item["id"])


def determined_graph(package: dict, *, layer: str = "call") -> dict[str, set[str]]:
    if layer not in {"call", "import", "inheritance"}:
        raise ValueError("unsupported_relation_layer")
    graph = {unit["id"]: set() for unit in package["units"]}
    for relation in package["relations"]:
        if relation["layer"] == layer and relation["to_unit"] is not None:
            graph[package["units"][relation["from_unit"]]["id"]].add(
                package["units"][relation["to_unit"]]["id"])
    return graph


def dependency_display(graph: dict[str, set[str]], *, direction: str = "caller_first") -> dict:
    """Iterative Kosaraju plus stable heap topology; retains cycles and direction."""
    if direction not in {"caller_first", "dependency_first"}:
        raise ValueError("invalid_display_direction")
    if any(type(key) is not str or type(values) is not set or not values <= graph.keys()
           for key, values in graph.items()):
        raise ValueError("invalid_graph")
    reverse = {key: set() for key in graph}
    for key, neighbors in graph.items():
        for neighbor in neighbors:
            reverse[neighbor].add(key)
    seen, finished = set(), []
    for root in sorted(graph):
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(sorted(graph[root])))]
        while stack:
            node, iterator = stack[-1]
            child = next(iterator, None)
            if child is None:
                finished.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(sorted(graph[child]))))
    seen, components = set(), []
    for root in reversed(finished):
        if root in seen:
            continue
        seen.add(root)
        pending, component = [root], []
        while pending:
            node = pending.pop()
            component.append(node)
            for child in sorted(reverse[node], reverse=True):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        components.append(tuple(sorted(component)))
    components.sort()
    membership = {node: index for index, component in enumerate(components) for node in component}
    edges = set()
    for node, neighbors in graph.items():
        for neighbor in neighbors:
            a, b = membership[node], membership[neighbor]
            if a != b:
                edges.add((a, b) if direction == "caller_first" else (b, a))
    outgoing = {index: set() for index in range(len(components))}
    indegree = Counter()
    for a, b in edges:
        outgoing[a].add(b)
        indegree[b] += 1
    ready = [index for index in outgoing if not indegree[index]]
    heapq.heapify(ready)
    order = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for child in sorted(outgoing[node]):
            indegree[child] -= 1
            if not indegree[child]:
                heapq.heappush(ready, child)
    cyclic = [index for index, component in enumerate(components)
              if len(component) > 1 or component[0] in graph[component[0]]]
    return {"components": components, "edges": tuple(sorted(edges)), "order": tuple(order),
            "cyclic_components": tuple(cyclic), "direction": direction,
            "meaning": "static_dependency_not_runtime_execution"}
