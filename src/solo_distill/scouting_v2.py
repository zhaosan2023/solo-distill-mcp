"""Local question view, separate from the complete authenticated V2 package.

No reference-answer assets, network, source execution or global quota changes.
Lexical supplements are explicitly not resolved call targets. Scores are counts,
not probabilities; bounded greedy failure is never a proof of capacity infeasibility.
"""
from __future__ import annotations

import ast
import hashlib
import time
from collections import defaultdict
from fractions import Fraction

from .distillation import SYSTEM_PROMPT, utf8_json
from .evidence import EvidenceBlocked
from .gateway import encode_request
from .package_v2 import contains, containing_selected
from .snapshot_v2 import parsed_tree
from .syntax_v2 import body_text

BODY_LIMIT = 98304
POST_LIMIT = 150000
MAX_CANDIDATES = 256
MAX_EDGES = 2048
VERSION = "question-scout-v1"


def messages_for(question, dimensions, sources, coverage):
    payload = {"operation": "compression", "question": question,
               "dimensions": dimensions, "sources": sources, "coverage": coverage}
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": utf8_json(payload).decode("utf-8")}]


def node_at(tree, route):
    node = tree
    for part in route:
        node = node[part] if type(part) is int else getattr(node, part)
    return node


def select_prompt(package, raw_files, question, dimensions, coverage, *, tick=None,
                  body_limit=BODY_LIMIT, post_limit=POST_LIMIT):
    """Return complete selected bodies and a local, auditable selection receipt.

    Every required question gets direct body evidence. Question-derived state,
    failure and caller/callee duties reserve candidate groups before optional fill.
    This is a syntactic sufficiency precheck, not independent semantic acceptance.
    """
    from .live_v2 import query_tokens
    tick = tick or (lambda: None)
    if not 0 < body_limit <= BODY_LIMIT or not 0 < post_limit <= POST_LIMIT:
        raise ValueError("invalid_question_budget")
    started = time.monotonic()
    units = package["units"]
    source_by_unit = {s["unit"]: s for s in package["sources"]}
    dimensions_by_id = {d["id"]: d for d in dimensions}
    tokens = {d["id"]: query_tokens(d["question"]) for d in dimensions}
    common = query_tokens(question)
    positions = {i: (package["files"][u["file"]]["path"], u["raw_start"],
                     u["raw_end"], u["id"]) for i, u in enumerate(units)}
    metadata, bodies, by_method = {}, {}, defaultdict(list)
    for i, unit in enumerate(units):
        tick()
        file = package["files"][unit["file"]]
        raw = raw_files[file["path"]]
        node = node_at(parsed_tree(raw, file["path"]), unit["syntax_path"])
        name = query_tokens(unit["qualified_name"] or "")
        signature = (ast.unparse(node.args) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else " ".join(ast.unparse(base) for base in node.bases)
                     if isinstance(node, ast.ClassDef) else "")
        doc = ast.get_docstring(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
        # Only syntax of this complete unit; do not use hidden line anchors.
        walked = list(ast.walk(node))
        roles = set()
        if any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(
                isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target])) for n in walked):
            roles.add("state")
        if any(isinstance(n, (ast.Try, ast.TryStar, ast.Raise, ast.ExceptHandler)) for n in walked):
            roles.add("failure")
        if unit["kind"] == "main_guard":
            roles.add("entry")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in {"route", "command"} for n in node.decorator_list):
                roles.add("entry")
            by_method[node.name].append(i)
        signals = {"identifier": name, "signature": query_tokens(signature),
                   "filename": query_tokens(file["path"].rsplit("/", 1)[-1]),
                   "docstring": query_tokens(doc or "")}
        hits = {}
        for d, terms in tokens.items():
            # Common question can break ties, but cannot manufacture a dimension hit.
            matched = {key: sorted(value & terms) for key, value in signals.items()}
            exact = set(matched["identifier"]) | set(matched["signature"])
            weak = set(matched["filename"]) | set(matched["docstring"])
            if exact or weak:
                hits[d] = {"tier": 0 if exact else 2, "signals": matched,
                           "count": len(exact | weak), "hop": 0, "path": []}
        metadata[i] = {"hits": hits, "roles": sorted(roles), "signature": signature,
                       "docstring": doc, "common_hits": len(name & common)}
        bodies[i] = body_text(raw, file, unit)
    index_seconds = time.monotonic() - started
    # Do not let class-wide metadata win merely because one method is relevant.
    eligible = {i for i, u in enumerate(units) if u["ast_type"] != "ClassDef"}
    if not eligible:
        eligible = set(range(len(units)))
    outgoing, incoming, lexical = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in package["relations"]:
        a, b = r["from_unit"], r["to_unit"]
        if b is not None:
            outgoing[a].append((b, r["id"]))
            incoming[b].append((a, r["id"]))
        elif r["layer"] == "call":
            expression = r["site"]["expression"]
            if "." in expression:
                method = expression.rsplit(".", 1)[-1]
                for candidate in by_method.get(method, ()):
                    if candidate != a:
                        lexical[a].append((candidate, r["id"], expression))
    def rank(i, d):
        hit = metadata[i]["hits"][d]
        return (hit["tier"], -hit["count"], -metadata[i]["common_hits"],
                hit["hop"], positions[i])
    candidates = set()
    seeds = {}
    edge_visits = 0
    exhausted = False
    for d in dimensions_by_id:
        tick()
        direct = sorted((i for i in eligible if d in metadata[i]["hits"]), key=lambda i: rank(i, d))
        if not direct and dimensions_by_id[d]["required"]:
            raise EvidenceBlocked("question_obligation_has_no_direct_body:" + d)
        # Fixed fair share reserves strong per-dimension discoveries before broad fill.
        seeds[d] = direct[:8]
        candidates.update(seeds[d])
    frontier = [(i, d, []) for d, seed in seeds.items() for i in seed]
    for hop in (1, 2):
        next_frontier = []
        for a, d, route in frontier:
            tick()
            needs_caller = bool(tokens[d] & {"caller", "callee", "ownership", "state", "return"})
            edges = [(b, rid, "resolved_call") for b, rid in outgoing[a]]
            if needs_caller:
                edges += [(b, rid, "resolved_caller") for b, rid in incoming[a]]
            edges += [(b, rid, "lexical_unresolved:" + expr) for b, rid, expr in lexical[a]]
            for b, rid, kind in sorted(edges, key=lambda e: (e[2].startswith("lexical"), positions[e[0]], e[1])):
                edge_visits += 1
                if edge_visits > MAX_EDGES or len(candidates) >= MAX_CANDIDATES and b not in candidates:
                    exhausted = True
                    break
                if b not in eligible:
                    continue
                candidates.add(b)
                if d not in metadata[b]["hits"]:
                    path = route + [{"from": units[a]["id"], "to": units[b]["id"],
                                     "relation_id": rid, "basis": kind}]
                    metadata[b]["hits"][d] = {"tier": 3 if kind.startswith("lexical") else 1,
                        "signals": {}, "count": 1, "hop": hop, "path": path}
                    next_frontier.append((b, d, path))
            if edge_visits > MAX_EDGES:
                break
        frontier = next_frontier
        if edge_visits > MAX_EDGES:
            break
    # Remaining direct metadata candidates are considered deterministically and bounded.
    for d in dimensions_by_id:
        for i in sorted((i for i in eligible if d in metadata[i]["hits"]), key=lambda i: rank(i, d)):
            if len(candidates) >= MAX_CANDIDATES and i not in candidates:
                exhausted = True
                continue
            candidates.add(i)
    discovery_seconds = time.monotonic() - started - index_seconds
    file_units = defaultdict(list)
    for i, u in enumerate(units):
        file_units[u["file"]].append(i)
    def closure(selected):
        selected, pending = set(selected), list(selected)
        while pending:
            i = pending.pop()
            for c in units[i]["conditions"]:
                origin = package["conditions"][c]["unit"]
                if origin not in selected:
                    selected.add(origin)
                    pending.append(origin)
        return selected
    def materialize(selected):
        selected = closure(selected)
        owners = []
        for f, indexes in file_units.items():
            local = [units[i] for i in indexes]
            selected_local = [n for n, i in enumerate(indexes) if i in selected]
            mapped = containing_selected(local, selected_local)
            owners.extend(indexes[n] for n in selected_local if mapped[n] == n)
        owners.sort(key=lambda i: positions[i])
        records, allowlist = [], set()
        for i in owners:
            u, s = units[i], source_by_unit[i]
            aliases = sorted(source_by_unit[j]["id"] for j in file_units[u["file"]]
                             if contains(u, units[j]))
            allowlist.update(aliases)
            records.append({"source_id": s["id"], "alias_source_ids": aliases,
                "path": positions[i][0], "symbol": u["qualified_name"], "text": bodies[i]})
        messages = messages_for(question, dimensions, records, coverage)
        post = encode_request(messages)
        cost = sum(len(record["text"].encode("utf-8")) for record in records)
        return selected, owners, records, allowlist, messages, post, cost
    empty = materialize(set())
    if len(empty[5]) > post_limit:
        raise EvidenceBlocked("fixed_question_metadata_over_post_budget")
    chosen, mandatory, duties, omissions = set(), set(), [], []
    def fits(result):
        return result[6] <= body_limit and len(result[5]) <= post_limit
    for d, dimension in dimensions_by_id.items():
        tick()
        options = sorted((i for i in candidates if d in metadata[i]["hits"]), key=lambda i: rank(i, d))
        if not options:
            continue
        duties_for_dimension = [("direct_question", [i for i in options if metadata[i]["hits"][d]["hop"] == 0])]
        if tokens[d] & {"state", "ownership", "persist", "persistence"}:
            duties_for_dimension.append(("state_write", [i for i in options if "state" in metadata[i]["roles"]]))
        if tokens[d] & {"failure", "error", "exception", "exceptions"}:
            duties_for_dimension.append(("failure_boundary", [i for i in options if "failure" in metadata[i]["roles"]]))
        for duty, alternatives in duties_for_dimension:
            if not alternatives:
                if dimension["required"]:
                    raise EvidenceBlocked("required_question_duty_unresolved:" + d + ":" + duty)
                continue
            admitted = None
            for i in alternatives[:32]:
                tick()
                group = {i}
                if tokens[d] & {"caller", "callee"}:
                    neighbors = [(b, rid) for b, rid in outgoing[i] + incoming[i] if b in candidates]
                    if neighbors:
                        group.add(min(neighbors, key=lambda e: positions[e[0]])[0])
                result = materialize(chosen | group)
                if fits(result):
                    admitted = result
                    duties.append({"dimension_id": d, "duty": duty,
                                   "units": sorted(units[j]["id"] for j in group),
                                   "semantic_acceptance": "requires_independent_review"})
                    mandatory.update(group)
                    break
            if admitted is None:
                raise EvidenceBlocked("required_group_search_exhausted:" + d + ":" + duty)
            chosen = admitted[0]
    current = materialize(chosen)
    if not current[2]:
        raise EvidenceBlocked("zero_question_body_hits")
    # Round-robin dimensions, positive signal / exact incremental unique cost.
    considered = set(chosen)
    while True:
        progressed = False
        for d in dimensions_by_id:
            tick()
            queue = []
            for i in candidates - considered:
                if d not in metadata[i]["hits"]:
                    continue
                hit = metadata[i]["hits"][d]
                u = units[i]
                covered = any(units[o]["file"] == u["file"] and contains(units[o], u) for o in current[1])
                increment = 0 if covered else len(bodies[i].encode("utf-8"))
                queue.append((hit["tier"], 0 if covered else 1,
                              -Fraction(hit["count"], max(1, increment)), hit["hop"], positions[i], i))
            for *_, i in sorted(queue):
                considered.add(i)
                proposed = materialize(chosen | {i})
                if fits(proposed):
                    chosen, current = proposed[0], proposed
                    progressed = True
                    break
                omissions.append({"unit_id": units[i]["id"], "reason": "complete_group_exceeds_body_or_post_budget"})
        if not progressed:
            break
    tick()
    selected, owners, records, allowlist, messages, post, cost = current
    receipt = {"version": VERSION, "snapshot_id": package["snapshot_id"],
        "candidate_count": len(candidates), "edge_visits": min(edge_visits, MAX_EDGES),
        "exploration_incomplete": exhausted, "proof_scope": "bounded_candidates_not_capacity_proof",
        "unique_body_bytes": cost, "body_limit": body_limit, "post_bytes": len(post),
        "post_limit": post_limit, "post_sha256": hashlib.sha256(post).hexdigest(),
        "owners": [units[i]["id"] for i in owners], "allowed_source_ids": sorted(allowlist),
        "duties": duties, "omissions": omissions,
        "selected_evidence": [{"unit_id": units[i]["id"], "path": positions[i][0],
             "hits": metadata[i]["hits"], "roles": metadata[i]["roles"]} for i in sorted(selected, key=lambda i: positions[i])],
        "elapsed_seconds": {"index": index_seconds, "candidate_discovery": discovery_seconds,
                            "selection_and_encoding": time.monotonic() - started - index_seconds - discovery_seconds},
        "unknown_dispatch": "lexical_candidates_are_not_resolved_targets",
        "semantic_acceptance": "requires_independent_four_dimension_review"}
    return messages, post, receipt
