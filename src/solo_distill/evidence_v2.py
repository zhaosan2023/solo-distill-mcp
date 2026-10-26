"""Offline V2 evidence adapter; targets remain source data and V1 stays unchanged."""
from __future__ import annotations

import ast
import copy
import hashlib
import os
import time
from typing import Any

from .evidence import EvidenceBlocked, suspicious_literal
from .extractor import Scanner, open_root, signature
from .package_v2 import build_package, finalize, validate_package
from .syntax_v2 import Coordinates, extract_units, identity


def syntax_roles(raw: bytes, unit: dict) -> list[tuple[str, str]]:
    """Roles certify listed syntax signals, NOT runtime ownership or semantics."""
    tree = ast.parse(Coordinates(raw).text, feature_version=(3, 12))
    node: Any = tree
    for part in unit["syntax_path"]:
        node = node[part] if type(part) is int else getattr(node, part)
    result = []
    if unit["kind"] == "main_guard":
        result.append(("entry", "main-guard-equality-v1"))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
        and item.func.attr in {"route", "command"} for item in node.decorator_list):
        result.append(("entry", "registration-decorator-v1"))
    nodes = list(ast.walk(node))
    if any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
           and item.func.id[:1].isupper() for item in nodes):
        result.append(("assembly", "constructor-expression-v1"))
    if any(isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(
        isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
        and target.value.id == "self" for target in
        (item.targets if isinstance(item, ast.Assign) else [item.target])) for item in nodes):
        result.append(("state", "self-attribute-assignment-v1"))
    if any(isinstance(item, ast.Call) and (
        isinstance(item.func, ast.Name) and item.func.id == "open" or
        isinstance(item.func, ast.Attribute) and item.func.attr in {"write", "write_text", "write_bytes", "commit"})
        for item in nodes):
        result.append(("persistence", "io-expression-v1"))
    if not result:
        result.append(("boundary", "no-lifecycle-signal-v1"))
    return result


def attach_roles(package: dict, raw_files: dict[str, bytes]) -> dict:
    result = copy.deepcopy(package)
    result["role_proofs"] = []
    for index, unit in enumerate(result["units"]):
        file = result["files"][unit["file"]]
        for role, rule in syntax_roles(raw_files[file["path"]], unit):
            result["role_proofs"].append({"id": identity("role-v2", [unit["id"], role, rule]),
                "unit": index, "role": role, "rule": rule, "level": "direct_static", "sources": [index]})
    validate_package(result, raw_files)
    return result


class V2EvidenceReader:
    """One authorized snapshot batch, protected by existing descriptor traversal."""
    def __init__(self, scanner: Scanner, report: dict, deadline: float, cancelled: Any):
        self.scanner, self.report = scanner, report
        self.deadline, self.cancelled = deadline, cancelled
        self.raw_files: dict[str, bytes] = {}
        self.units: dict[str, tuple[str, dict]] = {}
        self.package: dict | None = None

    def tick(self) -> None:
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise EvidenceBlocked("cancelled_or_evidence_deadline")

    def inventory(self) -> list[dict]:
        self.tick()
        descriptor = open_root(self.scanner.project.root)
        raw_files, units = {}, {}
        try:
            if signature(os.fstat(descriptor)) != self.scanner.directories.get("."):
                raise EvidenceBlocked("root_changed")
            for entry in self.report["entries"]:
                if entry["status"] != "parsed":
                    continue
                self.tick()
                path = entry["path"]
                raw, version = self.scanner.read_bytes(descriptor, path, self.scanner.project.max_file_bytes)
                if version != self.scanner.versions[path] or hashlib.sha256(raw).hexdigest() != entry["content_sha256"]:
                    raise EvidenceBlocked("source_changed")
                if suspicious_literal(ast.parse(raw)):
                    raise EvidenceBlocked("possible_literal_secret")
                raw_files[path] = raw
                for unit in extract_units(path, raw)["units"]:
                    units[unit["id"]] = path, unit
        finally:
            os.close(descriptor)
        self.raw_files, self.units = raw_files, units
        return [unit for _, unit in units.values()]

    def read(self, selected: list[str], *, scenario: str = "A") -> dict:
        self.tick()
        if not self.units:
            self.inventory()
        proposed = attach_roles(build_package(self.report["project_id"], self.report["snapshot_id"],
            self.raw_files, selected, scenario=scenario), self.raw_files)
        if len(proposed["owners"]) > 32 or sum(item["body_bytes"] for item in proposed["owners"]) > 98304:
            raise EvidenceBlocked("required_complete_body_budget")
        self.package = proposed
        return copy.deepcopy(proposed)

    def verify(self) -> None:
        self.tick()
        self.scanner.deadline = self.deadline
        descriptor = open_root(self.scanner.project.root)
        try:
            if (signature(os.fstat(descriptor)) != self.scanner.directories.get(".")
                    or not self.scanner.verify_versions(descriptor)):
                raise EvidenceBlocked("source_changed_after_selection")
        finally:
            os.close(descriptor)
        self.tick()

    def add_facts(self, facts: list[dict], obligations: list[dict]) -> dict:
        if self.package is None:
            raise EvidenceBlocked("body_not_read")
        proposed = copy.deepcopy(self.package)
        proposed["facts"], proposed["obligations"] = copy.deepcopy(facts), copy.deepcopy(obligations)
        proposed = finalize(proposed)
        validate_package(proposed, self.raw_files)
        self.package = proposed
        return copy.deepcopy(proposed)
