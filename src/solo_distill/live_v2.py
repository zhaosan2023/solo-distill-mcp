"""Explicit A-only engineering V2 path; never dispatched by the V1 server.

Selection consumes syntax and the versioned lifecycle policy, not evaluation
answers. Targets remain data. Failure is persisted and never triggers replay.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import re
from dataclasses import replace
import threading
import time

from .codec_v2 import ENCODING
from .core_v2 import CoreBlocked, form_core
from .distillation import Distiller, INPUT_SCHEMA as V1_SCHEMA, SYSTEM_PROMPT, utf8_json, navigation_pages
from .evidence import EvidenceBlocked
from .evidence_v2 import V2EvidenceReader, attach_roles
from .extractor import InputError, Scanner
from .gateway import Gateway, GatewayError, candidate_json
from .graph_v2 import determined_graph, extract_relations
from .package_v2 import build_package, finalize, validate_package
from .scheduler_v2 import Group, Scheduler, Selection, candidates_from_package
from .snapshot_v2 import snapshot_scope, snapshot_metrics
from .telemetry import BudgetTelemetry, POLICY
from .adaptive_wire import TransportCapabilities
from .syntax_v2 import body_text, identity
from .wire_v2 import Admission, FrozenFrame, blocked_report, encode_report, publish

TOOL_NAME = "solo_distill_knowledge_v2"
INPUT_SCHEMA = copy.deepcopy(V1_SCHEMA)
INPUT_SCHEMA["properties"]["contract_version"]["enum"] = [2]
INPUT_SCHEMA["properties"]["scenario"]["enum"] = ["A"]
INPUT_SCHEMA["properties"]["return_bytes"]["maximum"] = 22016
INPUT_SCHEMA["properties"]["dimensions"]["items"]["properties"]["evidence"]["enum"] = ["sourced_inference"]
INPUT_SCHEMA["properties"]["dimensions"]["items"]["properties"].pop("selector")
INPUT_SCHEMA["properties"]["budget_policy"] = {"enum": ["legacy", POLICY]}
INPUT_SCHEMA["properties"]["transport"] = {
    "type": "object", "properties": {
        "policy": {"enum": [POLICY]}, "authority": {"enum": ["caller_explicit"]},
        "max_frame_bytes": {"type": "integer", "minimum": 1024},
        "max_transfer_bytes": {"type": "integer", "minimum": 1},
        "max_decoded_bytes": {"type": "integer", "minimum": 1},
        "max_chunks": {"type": "integer", "minimum": 1, "maximum": 4096},
        "chunking": {"type": "boolean"}},
    "required": ["policy", "authority", "max_frame_bytes", "max_transfer_bytes",
                 "max_decoded_bytes", "max_chunks", "chunking"], "additionalProperties": False}
INPUT_SCHEMA["properties"]["return_bytes"].pop("maximum")
INPUT_SCHEMA["allOf"] = [{"if": {"properties": {"budget_policy": {"const": POLICY}},
    "required": ["budget_policy"]}, "then": {"required": ["transport", "return_bytes"]},
    "else": {"not": {"required": ["transport"]},
             "properties": {"return_bytes": {"maximum": 22016}}}}]


def query_tokens(text):
    """Versioned lexical navigation, not frozen-answer or runtime inference."""
    words = set(re.findall(r"[a-z][a-z0-9]*", text.lower()))
    families = ({"query", "question", "search"}, {"retrieve", "retrieval", "retriever"},
                {"generate", "generation", "generator", "answer"},
                {"parse", "parser", "parsing"}, {"load", "loading", "loader"},
                {"persist", "persistence", "save", "saving"},
                {"error", "errors", "exception", "exceptions"},
                {"filter", "filters", "filtering"}, {"repo", "repository", "repositories"},
                {"interface", "interfaces"})
    for family in families:
        if words & family:
            words |= family
    return words


def source_priority(package, source, question):
    unit = package["units"][source["unit"]]
    path = package["files"][source["file"]]["path"]
    tokens = query_tokens(question)
    name = query_tokens(unit["qualified_name"] or "")
    filename = query_tokens(path.rsplit("/", 1)[-1])
    return (-len(name & tokens), -len(filename & tokens), path,
            unit["raw_start"], unit["raw_end"], source["id"])


def core_selection(package, question, *, budget_policy="legacy"):
    if budget_policy == POLICY:
        # All structural records, relationship sites, failures and obligations
        # remain in the authenticated core; only representation may be compressed.
        return {"required_sources": [s["id"] for s in package["sources"]],
                "policy_id": POLICY, "wire_encoding": "json"}
    if budget_policy != "legacy":
        raise ValueError("unknown_budget_policy")
    """One focused complete source is explicit; remaining typed groups are optional.

    Required obligations and every support still enter the transitive closure in
    form_core. No generation feedback is used to change this stable source order.
    """
    sources = sorted(package["sources"], key=lambda s: source_priority(package, s, question))
    if not sources:
        raise EvidenceBlocked("zero_v2_syntax_units")
    required = [sources[0]["id"]]
    groups = []
    for rank, source in enumerate(sources):
        references = [("sources", source["id"])]
        references += [("role_proofs", proof["id"]) for proof in package["role_proofs"]
                       if proof["unit"] == source["unit"]]
        groups.append((f"{rank:06d}-{source['id']}", references))
    return {"required_sources": required, "optional_groups": groups, "wire_encoding": ENCODING}


def select_inventory(reader, question="", *, budget_policy="legacy"):
    if budget_policy not in {"legacy", POLICY}:
        raise ValueError("unknown_budget_policy")
    adaptive = budget_policy == POLICY
    telemetry = getattr(reader, "telemetry", None)
    """Read complete leaves, rank from the question, then apply lifecycle quotas."""
    reader.tick()
    units = reader.inventory()
    if not units:
        raise EvidenceBlocked("zero_v2_syntax_units")
    if adaptive:
        # Full structural batch is not a question-body mandatory search problem.
        # Preserve all public sources, conditions and relation sites without the
        # old all-pairs Scheduler closure or a second inventory/package build.
        selected = reader.read([unit["id"] for unit in units])
        selected["relations"] = extract_relations(selected, reader.raw_files)
        validate_package(selected, reader.raw_files)
        reader.navigation_pages = navigation_pages(units, telemetry=telemetry)
        owners = tuple(selected["units"][o["unit"]]["id"] for o in selected["owners"])
        aliases = tuple((u["id"], owners[s["owner"]])
                        for u, s in zip(selected["units"], selected["sources"]))
        role_map = {}
        for proof in selected["role_proofs"]:
            role_map.setdefault(proof["unit"], set()).add(proof["role"])
        counts = [0, 0, 0]
        for owner in selected["owners"]:
            unit = selected["units"][owner["unit"]]
            pipeline = (2 if unit["ast_type"] in {"Try", "TryStar", "Raise"} else
                        0 if role_map.get(owner["unit"], set()) - {"boundary"} else 1)
            counts[pipeline] += 1
        body_bytes = sum(owner["body_bytes"] for owner in selected["owners"])
        if telemetry is not None:
            telemetry.protect([u["id"] for u in selected["units"] if u["kind"] == "definition"], "core_interface")
            telemetry.protect([c["id"] for c in selected["conditions"]], "syntax_invariant")
            telemetry.protect([r["id"] for r in selected["relations"]], "required_call_boundary")
        reader.tick()
        return selected, Selection("selected", None, owners=owners, aliases=aliases,
            required_owners=owners, counts=tuple(counts), body_bytes=body_bytes,
            proof_scope="complete_static_inventory_not_prompt_selection")
    paths = {unit["id"]: path for path, unit in reader.units.values()}
    tokens = query_tokens(question)
    # Avoid selecting a whole class just to obtain methods already represented
    # as independent complete units. Condition origins are added by build_package.
    leaves = [unit for unit in units if unit["ast_type"] != "ClassDef" or not any(
        other["id"] != unit["id"] and paths[other["id"]] == paths[unit["id"]]
        and other["syntax_path"][:len(unit["syntax_path"])] == unit["syntax_path"]
        for other in units)]
    def rank(unit):
        path = paths[unit["id"]]
        name = query_tokens(unit["qualified_name"] or "")
        filename = query_tokens(path.rsplit("/", 1)[-1])
        return (-len(name & tokens), -len(filename & tokens),
                unit["kind"] not in {"definition", "main_guard"},
                path, unit["raw_start"], unit["raw_end"], unit["id"])
    focused = sorted(units if adaptive else leaves, key=rank)
    if not adaptive:
        focused = focused[:48]
    else:
        reader.navigation_pages = navigation_pages(focused, telemetry=telemetry)
    inventory = build_package(reader.report["project_id"], reader.report["snapshot_id"],
                              reader.raw_files, [unit["id"] for unit in focused])
    inventory = attach_roles(inventory, reader.raw_files)
    inventory["relations"] = extract_relations(inventory, reader.raw_files)
    priorities = {unit["id"]: len(focused) - i for i, unit in enumerate(focused)}
    candidates = tuple(replace(item, signal=priorities.get(item.id, 0) * 8 + item.signal)
                       for item in candidates_from_package(inventory, reader.raw_files))
    reader.tick()
    obligations = ({"complete_inventory": (Group("all_registered_syntax",
                    tuple(item.id for item in candidates)),)} if adaptive else {})
    selection = Scheduler(candidates, stopped=lambda: reader.cancelled.is_set()
        or time.monotonic() >= reader.deadline, budget_policy=budget_policy,
        telemetry=telemetry).select(obligations,
        optional=[] if adaptive else [item.id for item in candidates],
        adjacency=determined_graph(inventory))
    if selection.status != "selected":
        raise EvidenceBlocked(selection.reason or "selection_failed")
    selected = reader.read(list(selection.owners))
    if adaptive:
        selected["relations"] = extract_relations(selected, reader.raw_files)
        validate_package(selected, reader.raw_files)
        if telemetry is not None:
            telemetry.protect([u["id"] for u in selected["units"]
                               if u["kind"] == "definition"], "core_interface")
            telemetry.protect([c["id"] for c in selected["conditions"]], "syntax_invariant")
            telemetry.protect([r["id"] for r in selected["relations"]], "required_call_boundary")
    return selected, selection


def add_proposal(package, candidate, dimensions, raw_files, *, allowed_source_ids=None):
    if (type(candidate) is not dict or set(candidate) != {"summary", "conclusions", "unknowns"}
            or type(candidate["summary"]) is not str or type(candidate["conclusions"]) is not list
            or type(candidate["unknowns"]) is not list
            or any(type(item) is not str for item in candidate["unknowns"])):
        raise ValueError("invalid_v2_proposal")
    result = copy.deepcopy(package)
    source_map = {item["id"]: i for i, item in enumerate(result["sources"])}
    dimension_map = {item["id"]: item for item in dimensions}
    supports = {key: [] for key in dimension_map}
    for number, item in enumerate(candidate["conclusions"]):
        if (type(item) is not dict or set(item) != {"dimension_id", "text", "source_ids"}
                or type(item["dimension_id"]) is not str or item["dimension_id"] not in dimension_map
                or type(item["text"]) is not str or not item["text"].strip()
                or len(item["text"].encode("utf-8")) > 4096
                or type(item["source_ids"]) is not list or not item["source_ids"]
                or any(type(key) is not str or key not in source_map for key in item["source_ids"])
                or len(set(item["source_ids"])) != len(item["source_ids"])):
            raise ValueError("invalid_or_unsourced_v2_conclusion")
        if allowed_source_ids is not None and not set(item["source_ids"]) <= set(allowed_source_ids):
            raise ValueError("model_reference_not_in_sent_body_view")
        fact = {"id": identity("live-fact-v2", [number, item]), "text": item["text"],
                "level": "sourced_inference", "sources": [source_map[key] for key in item["source_ids"]],
                "relations": [], "conditions": []}
        supports[item["dimension_id"]].append({"table": "facts", "index": len(result["facts"])})
        result["facts"].append(fact)
    for dimension in dimensions:
        result["obligations"].append({"id": identity("live-obligation-v2", dimension),
            "required": dimension["required"], "evidence": "sourced_inference", "role": None,
            "state": "unknown", "supports": supports[dimension["id"]],
            "reason": "required_semantic_evidence_missing"})
    result = finalize(result)
    validate_package(result, raw_files)
    return result


class LiveV2Distiller(Distiller):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if type(self.gateway) is Gateway:
            self.gateway.capture_stream = True
        self.used = False

    def validate_v2(self, arguments, rpc_id):
        if type(arguments) is not dict or arguments.get("contract_version") != 2 or type(arguments.get("contract_version")) is not int:
            raise InputError("显式 V2 契约必填")
        if arguments.get("scenario") != "A" or arguments.get("seeds", []):
            raise InputError("本入口仅允许无定向种子的单次 A")
        dimensions = arguments.get("dimensions")
        if type(dimensions) is not list or any(type(item) is not dict or item.get("evidence") != "sourced_inference" or "selector" in item for item in dimensions):
            raise InputError("V2 实测只接收无答案的推断维度")
        budget_policy = arguments.get("budget_policy", "legacy")
        if budget_policy not in {"legacy", POLICY}:
            raise InputError("未知预算策略")
        legacy_shape = {key: value for key, value in arguments.items()
                        if key not in {"budget_policy", "transport"}}
        legacy_shape["contract_version"] = 1
        caps = None
        if budget_policy == POLICY:
            try:
                caps = TransportCapabilities.from_dict(arguments.get("transport"))
            except (ValueError, TypeError) as error:
                raise InputError("显式传输能力无效") from error
            budget = arguments.get("return_bytes")
            if type(budget) is not int or not 1024 <= budget <= caps.max_frame_bytes:
                raise InputError("调用帧硬上限必须显式声明且在消费者能力内")
            # Reuse all legacy authorization, input and deadline checks, not its
            # historical return quota. The new transport is a separate capability.
            legacy_shape["return_bytes"] = 1024
        elif "transport" in arguments:
            raise InputError("旧策略不得静默接受新版传输能力")
        project, include, deadline, legacy_budget = self.validate(legacy_shape)
        if caps is None:
            budget = legacy_budget
            if budget > 22016:
                raise InputError("V2 A 调用预算不能超过 22016")
        admission = Admission("2024-11-05", "A", rpc_id, budget, caps)
        return project, include, deadline, admission

    @snapshot_scope
    def distill_frame(self, arguments, cancelled=None, *, rpc_id):
        project, include, deadline, admission = self.validate_v2(arguments, rpc_id)
        if self.used:
            raise InputError("单次 A 已接纳，禁止重放")
        self.used = True
        cancelled = cancelled or threading.Event()
        started = time.monotonic()
        budget_policy = arguments.get("budget_policy", "legacy")
        telemetry = (BudgetTelemetry(run_id=arguments["request_id"], project_id=project.project_id,
                                     contract_version="1") if budget_policy == POLICY else None)
        def stage(name):
            return telemetry.stage(name) if telemetry is not None else nullcontext()
        trace = {"scratch_version": 2, "request_id": arguments["request_id"], "arguments": arguments,
                 "status": "blocked", "failure": None, "structure": None, "model_outputs": [],
                 "gateway_requests": [], "outer_replays": 0, "target_writes": 0,
                 "semantic_acceptance": "requires_independent_review", "core": None}
        frame = FrozenFrame(encode_report(blocked_report(), rpc_id), admission, False)
        reader = None
        try:
            scanner = Scanner(project, include, project.max_seconds)
            scanner.deadline = min(scanner.deadline, deadline - 15)
            with stage("scan"):
                structure = scanner.run()
            if telemetry is not None:
                telemetry.identity["snapshot_id"] = structure["snapshot_id"]
            trace["structure"] = structure
            hard = [item for item in structure["unfinished"] if item["reason"] != "unsupported_language_or_asset"]
            if (hard or not structure["inventory_complete"] or structure["freshness"] != "current_at_verification"
                    or not structure["coverage"]["parsed_files"]):
                raise EvidenceBlocked("snapshot_not_ready")
            scanner.deadline = deadline - 15
            reader = V2EvidenceReader(scanner, structure, deadline - 15, cancelled,
                                      budget_policy=budget_policy, telemetry=telemetry)
            navigation_question = arguments["question"] + " " + " ".join(
                dimension["question"] for dimension in arguments["dimensions"])
            with stage("read"):
                package, selection = select_inventory(reader, navigation_question, budget_policy=budget_policy)
            trace["selection"] = {"owners": list(selection.owners), "counts": list(selection.counts),
                                  "body_bytes": selection.body_bytes, "borrowed": selection.borrowed}
            trace["scope"] = sorted(reader.raw_files)
            trace["source_bodies"] = [{"source_id": source["id"],
                "text": body_text(reader.raw_files[package["files"][source["file"]]["path"]],
                                  package["files"][source["file"]], package["units"][source["unit"]])}
                for source in package["sources"]]
            # Check the complete mandatory structural representation before POST.
            # Generation can add size, never make an oversized minimum feasible.
            slicing = core_selection(package, navigation_question, budget_policy=budget_policy)
            with stage("encode"):
                preliminary = form_core(package, reader.raw_files, telemetry=telemetry, **slicing)
            # Only authenticated, budget-admitted bodies may support generation;
            # all scanned files remain in the package and its input identity.
            package = preliminary.document["package"]
            trace["core_selection"] = {"required_sources": slicing["required_sources"],
                "optional_groups": [group[0] for group in slicing.get("optional_groups", [])],
                "wire_encoding": slicing["wire_encoding"],
                "policy": POLICY if telemetry is not None else "query-leaves-v2-1"}
            trace["source_bodies"] = [{"source_id": source["id"],
                "text": body_text(reader.raw_files[package["files"][source["file"]]["path"]],
                                  package["files"][source["file"]], package["units"][source["unit"]])}
                for source in package["sources"]]
            reader.tick()
            payload = {"operation": "compression", "question": arguments["question"],
                       "dimensions": arguments["dimensions"], "sources": [
                {"source_id": source["id"], "path": package["files"][source["file"]]["path"],
                 "symbol": package["units"][source["unit"]]["qualified_name"],
                 "text": trace["source_bodies"][i]["text"]}
                for i, source in enumerate(package["sources"])], "coverage": structure["coverage"]}
            allowed_source_ids = None
            if telemetry is not None:
                from .scouting_v2 import select_prompt
                messages, post, prompt_receipt = select_prompt(package, reader.raw_files,
                    arguments["question"], arguments["dimensions"], structure["coverage"], tick=reader.tick)
                trace["prompt_selection"] = prompt_receipt
                allowed_source_ids = frozenset(prompt_receipt["allowed_source_ids"])
                trace["http_post_utf8"] = post.decode("utf-8")
                trace["http_post_sha256"] = hashlib.sha256(post).hexdigest()
                trace["http_post_bytes"] = len(post)
            else:
                messages = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": utf8_json(payload).decode("utf-8")}]
            from .gateway import encode_request
            if telemetry is not None and encode_request(messages) != post:
                raise EvidenceBlocked("preflight_actual_request_encoding_conflict")
            attempt = {"stage": "compression"}
            trace["gateway_requests"].append(attempt)
            try:
                with stage("generate"):
                    answer, metrics = self.gateway.generate(messages, deadline - 15, cancelled)
                attempt.update(metrics)
            except GatewayError as error:
                attempt.update(error.observations)
                raise
            candidate = candidate_json(answer)
            trace["model_outputs"].append({"stage": "compression", "answer": answer, "candidate": candidate})
            package = add_proposal(package, candidate, arguments["dimensions"], reader.raw_files,
                                   allowed_source_ids=allowed_source_ids)
            with stage("verify"):
                reader.verify()
            with stage("encode"):
                core = form_core(package, reader.raw_files, telemetry=telemetry,
                    **core_selection(package, navigation_question, budget_policy=budget_policy))
                trace["core"] = core.document
                frame = publish(core, admission, telemetry=telemetry,
                    observations={"gateway_requests": len(trace["gateway_requests"]),
                                  "elapsed_ms": int((time.monotonic() - started) * 1000)})
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise EvidenceBlocked("business_deadline_or_cancelled")
            trace["status"] = package["status"] if frame.knowledge_delivered else "blocked"
            trace["freshness"] = "current_at_final_verification"
        except (EvidenceBlocked, CoreBlocked, GatewayError) as error:
            trace["failure"] = str(error)
            frame = FrozenFrame(encode_report(blocked_report(), rpc_id), admission, False)
        except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
            trace["failure"] = type(error).__name__
            frame = FrozenFrame(encode_report(blocked_report(), rpc_id), admission, False)
        if telemetry is not None:
            if not frame.knowledge_delivered:
                telemetry.coverage = "blocked"
                telemetry.frames = []
                telemetry.frame(frame.data)
                frame = replace(frame, telemetry=telemetry)
            trace["telemetry"] = telemetry.snapshot()
        trace["syntax_snapshot_metrics"] = snapshot_metrics()
        trace["elapsed_seconds"] = round(time.monotonic() - started, 6)
        trace["frozen_frame_bytes"] = len(frame.data)
        trace["frozen_frame_sha256"] = hashlib.sha256(frame.data).hexdigest()
        self.persist_scratch(arguments["request_id"], trace)
        return frame
