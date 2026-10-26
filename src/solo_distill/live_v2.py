"""Explicit A-only engineering V2 path; never dispatched by the V1 server.

Selection consumes syntax and the versioned lifecycle policy, not evaluation
answers. Targets remain data. Failure is persisted and never triggers replay.
"""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import replace
import threading
import time

from .codec_v2 import ENCODING
from .core_v2 import CoreBlocked, form_core
from .distillation import Distiller, INPUT_SCHEMA as V1_SCHEMA, SYSTEM_PROMPT, utf8_json
from .evidence import EvidenceBlocked
from .evidence_v2 import V2EvidenceReader, attach_roles
from .extractor import InputError, Scanner
from .gateway import GatewayError, candidate_json
from .graph_v2 import determined_graph, extract_relations
from .package_v2 import build_package, finalize, validate_package
from .scheduler_v2 import Scheduler, candidates_from_package
from .syntax_v2 import body_text, identity
from .wire_v2 import Admission, FrozenFrame, blocked_report, encode_report, publish

TOOL_NAME = "solo_distill_knowledge_v2"
INPUT_SCHEMA = copy.deepcopy(V1_SCHEMA)
INPUT_SCHEMA["properties"]["contract_version"]["enum"] = [2]
INPUT_SCHEMA["properties"]["scenario"]["enum"] = ["A"]
INPUT_SCHEMA["properties"]["return_bytes"]["maximum"] = 22016
INPUT_SCHEMA["properties"]["dimensions"]["items"]["properties"]["evidence"]["enum"] = ["sourced_inference"]
INPUT_SCHEMA["properties"]["dimensions"]["items"]["properties"].pop("selector")


def query_tokens(text):
    """Versioned lexical navigation, not frozen-answer or runtime inference."""
    words = set(re.findall(r"[a-z][a-z0-9]*", text.lower()))
    families = ({"query", "question", "search"}, {"retrieve", "retrieval", "retriever"},
                {"generate", "generation", "generator", "answer"},
                {"parse", "parser", "parsing"}, {"load", "loading", "loader"},
                {"persist", "persistence", "save", "saving"},
                {"error", "errors", "exception", "exceptions"},
                {"filter", "filters", "filtering"}, {"repo", "repository", "repositories"})
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


def core_selection(package, question):
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


def select_inventory(reader, question=""):
    """Read complete leaves, rank from the question, then apply lifecycle quotas."""
    reader.tick()
    units = reader.inventory()
    if not units:
        raise EvidenceBlocked("zero_v2_syntax_units")
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
    focused = sorted(leaves, key=rank)[:48]
    inventory = build_package(reader.report["project_id"], reader.report["snapshot_id"],
                              reader.raw_files, [unit["id"] for unit in focused])
    inventory = attach_roles(inventory, reader.raw_files)
    inventory["relations"] = extract_relations(inventory, reader.raw_files)
    priorities = {unit["id"]: len(focused) - i for i, unit in enumerate(focused)}
    candidates = tuple(replace(item, signal=priorities.get(item.id, 0) * 8 + item.signal)
                       for item in candidates_from_package(inventory, reader.raw_files))
    reader.tick()
    selection = Scheduler(candidates, stopped=lambda: reader.cancelled.is_set()
        or time.monotonic() >= reader.deadline).select({},
        optional=[item.id for item in candidates], adjacency=determined_graph(inventory))
    if selection.status != "selected":
        raise EvidenceBlocked(selection.reason or "selection_failed")
    return reader.read(list(selection.owners)), selection


def add_proposal(package, candidate, dimensions, raw_files):
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
        self.used = False

    def validate_v2(self, arguments, rpc_id):
        if type(arguments) is not dict or arguments.get("contract_version") != 2 or type(arguments.get("contract_version")) is not int:
            raise InputError("显式 V2 契约必填")
        if arguments.get("scenario") != "A" or arguments.get("seeds", []):
            raise InputError("本入口仅允许无定向种子的单次 A")
        dimensions = arguments.get("dimensions")
        if type(dimensions) is not list or any(type(item) is not dict or item.get("evidence") != "sourced_inference" or "selector" in item for item in dimensions):
            raise InputError("V2 实测只接收无答案的推断维度")
        legacy_shape = {**arguments, "contract_version": 1}
        project, include, deadline, budget = self.validate(legacy_shape)
        admission = Admission("2024-11-05", "A", rpc_id, min(budget, 22016))
        if budget > 22016:
            raise InputError("V2 A 调用预算不能超过 22016")
        return project, include, deadline, admission

    def distill_frame(self, arguments, cancelled=None, *, rpc_id):
        project, include, deadline, admission = self.validate_v2(arguments, rpc_id)
        if self.used:
            raise InputError("单次 A 已接纳，禁止重放")
        self.used = True
        cancelled = cancelled or threading.Event()
        started = time.monotonic()
        trace = {"scratch_version": 2, "request_id": arguments["request_id"], "arguments": arguments,
                 "status": "blocked", "failure": None, "structure": None, "model_outputs": [],
                 "gateway_requests": [], "outer_replays": 0, "target_writes": 0,
                 "semantic_acceptance": "requires_independent_review", "core": None}
        frame = FrozenFrame(encode_report(blocked_report(), rpc_id), admission, False)
        reader = None
        try:
            scanner = Scanner(project, include, project.max_seconds)
            scanner.deadline = min(scanner.deadline, deadline - 15)
            structure = scanner.run()
            trace["structure"] = structure
            hard = [item for item in structure["unfinished"] if item["reason"] != "unsupported_language_or_asset"]
            if (hard or not structure["inventory_complete"] or structure["freshness"] != "current_at_verification"
                    or not structure["coverage"]["parsed_files"]):
                raise EvidenceBlocked("snapshot_not_ready")
            scanner.deadline = deadline - 15
            reader = V2EvidenceReader(scanner, structure, deadline - 15, cancelled)
            navigation_question = arguments["question"] + " " + " ".join(
                dimension["question"] for dimension in arguments["dimensions"])
            package, selection = select_inventory(reader, navigation_question)
            trace["selection"] = {"owners": list(selection.owners), "counts": list(selection.counts),
                                  "body_bytes": selection.body_bytes, "borrowed": selection.borrowed}
            trace["scope"] = sorted(reader.raw_files)
            trace["source_bodies"] = [{"source_id": source["id"],
                "text": body_text(reader.raw_files[package["files"][source["file"]]["path"]],
                                  package["files"][source["file"]], package["units"][source["unit"]])}
                for source in package["sources"]]
            # Check the complete mandatory structural representation before POST.
            # Generation can add size, never make an oversized minimum feasible.
            slicing = core_selection(package, navigation_question)
            preliminary = form_core(package, reader.raw_files, **slicing)
            # Only authenticated, budget-admitted bodies may support generation;
            # all scanned files remain in the package and its input identity.
            package = preliminary.document["package"]
            trace["core_selection"] = {"required_sources": slicing["required_sources"],
                "optional_groups": [group[0] for group in slicing["optional_groups"]],
                "wire_encoding": ENCODING, "policy": "query-leaves-v2-1"}
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
            attempt = {"stage": "compression"}
            trace["gateway_requests"].append(attempt)
            try:
                answer, metrics = self.gateway.generate([
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": utf8_json(payload).decode("utf-8")}], deadline - 15, cancelled)
                attempt.update(metrics)
            except GatewayError as error:
                attempt.update(error.observations)
                raise
            candidate = candidate_json(answer)
            trace["model_outputs"].append({"stage": "compression", "answer": answer, "candidate": candidate})
            package = add_proposal(package, candidate, arguments["dimensions"], reader.raw_files)
            reader.verify()
            core = form_core(package, reader.raw_files, **core_selection(package, navigation_question))
            trace["core"] = core.document
            frame = publish(core, admission, observations={"gateway_requests": len(trace["gateway_requests"]),
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
        trace["elapsed_seconds"] = round(time.monotonic() - started, 6)
        trace["frozen_frame_bytes"] = len(frame.data)
        trace["frozen_frame_sha256"] = hashlib.sha256(frame.data).hexdigest()
        self.persist_scratch(arguments["request_id"], trace)
        return frame
