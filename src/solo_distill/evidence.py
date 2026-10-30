"""Bounded source selection over an authorized, current structure snapshot.

Candidate identifiers are data, not paths or tool instructions. Target modules
are never imported. Body evidence and syntactic records remain distinct.
"""
from __future__ import annotations

import ast
import hashlib
import io
import os
import re
import time
import tokenize
from typing import Any

from .extractor import ScanBlocked, Scanner, open_root, signature
from .telemetry import BudgetTelemetry, POLICY

MAX_BLOCKS = 32
MAX_BODY_BYTES = 96 * 1024
MAX_SKELETON_BYTES = 240000


class EvidenceBlocked(Exception):
    def __init__(self, reason: str, unfinished: list[dict[str, Any]] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.unfinished = unfinished or [{"reason": reason}]


def suspicious_literal(tree: ast.AST) -> bool:
    """Conservative literal-secret screen, not a comprehensive secret scanner."""
    pattern = re.compile(r"(?:api_?key|access_?token|secret|password|credential)", re.I)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and len(value.value.strip()) >= 8
                    and any(pattern.search(ast.unparse(target)) for target in targets)):
                literal = value.value.casefold()
                if not any(marker in literal for marker in
                           ("example", "placeholder", "your_", "your-", "dummy", "changeme")):
                    return True
    return False


class EvidenceReader:
    def __init__(self, scanner: Scanner, report: dict[str, Any],
                 deadline: float, cancelled: Any, *, budget_policy="legacy",
                 telemetry: BudgetTelemetry | None = None):
        if budget_policy not in {"legacy", POLICY}:
            raise ValueError("unknown_budget_policy")
        self.adaptive = budget_policy == POLICY
        self.telemetry = telemetry if telemetry is not None else (BudgetTelemetry(
            project_id=report["project_id"], snapshot_id=report["snapshot_id"])
            if self.adaptive else None)
        self.scanner = scanner
        self.report = report
        self.deadline = deadline
        self.cancelled = cancelled
        self.entries = {item["path"]: item for item in report["entries"]
                        if item["status"] == "parsed"}
        self.candidates: dict[str, dict[str, Any]] = {}
        for entry in self.entries.values():
            for record in entry["symbols"]:
                self.candidates[record["source_id"]] = record
        self.body_bytes = 0
        self.sources: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.unfinished: list[dict[str, Any]] = []

    def tick(self) -> None:
        if self.cancelled.is_set():
            raise EvidenceBlocked("cancelled")
        if time.monotonic() >= self.deadline:
            raise EvidenceBlocked("evidence_deadline_exhausted")

    def skeleton(self) -> list[dict[str, Any]]:
        self.tick()
        return [{"candidate_id": identity, "path": item["path"],
                 "symbol": item["qualified_name"], "kind": item["kind"],
                 "start_line": item["start_line"], "end_line": item["end_line"]}
                for identity, item in sorted(self.candidates.items(),
                                             key=lambda pair: (pair[1]["path"],
                                                               pair[1]["start_line"]))]

    def locate(self, question: str, seeds: list[dict[str, str]]) -> list[str]:
        """Deterministic seed/identifier matching; no implicit model fallback."""
        self.tick()
        selected: list[str] = []
        for seed in seeds:
            matches = [identity for identity, item in self.candidates.items()
                       if item["path"] == seed["path"]
                       and ("symbol" not in seed
                            or item["qualified_name"] == seed["symbol"])]
            if not matches:
                raise EvidenceBlocked("seed_has_no_definition",
                                      [{"reason": "seed_has_no_definition", **seed}])
            selected.extend(matches)
        if seeds:
            return list(dict.fromkeys(selected))
        words = {word.casefold() for word in re.findall(r"[A-Za-z_][A-Za-z_0-9.]*", question)
                 if len(word) >= 3}
        scored = []
        for identity, item in self.candidates.items():
            label = (item["path"] + " " + item["qualified_name"]).casefold()
            score = sum(len(word) for word in words if word in label)
            if score:
                scored.append((-score, item["path"], item["start_line"], identity))
        return [item[-1] for item in sorted(scored)]

    def required_candidates(self, dimensions: list[dict[str, Any]]) -> list[str]:
        """Select complete owners of caller-declared required syntax, not answers."""
        self.tick()
        selected: list[str] = []
        fields = {"definition": "symbols", "import": "imports", "call_expression": "calls"}
        for dimension in dimensions:
            if not dimension["required"] or dimension["evidence"] != "direct_static":
                continue
            selector = dimension["selector"]
            entry = self.entries.get(selector["path"])
            if entry is None:
                continue
            for record in entry[fields[selector["record_kind"]]]:
                self.tick()
                if not all(record.get(key) == value for key, value in selector.items()
                           if key != "record_kind"):
                    continue
                owners = [item for item in entry["symbols"]
                          if item["start_line"] <= record["start_line"]
                          and item["end_line"] >= record["end_line"]]
                if owners:
                    owner = min(owners, key=lambda item: (
                        item["end_line"] - item["start_line"], item["qualified_name"]))
                    selected.append(owner["source_id"])
        identities = list(dict.fromkeys(selected))
        if not self.adaptive and len(identities) > MAX_BLOCKS:
            raise EvidenceBlocked("required_candidate_block_budget")
        return identities

    def validate_candidates(self, identities: Any) -> list[str]:
        if (not isinstance(identities, list) or not identities
                or (not self.adaptive and len(identities) > MAX_BLOCKS)
                or not all(isinstance(item, str) and item in self.candidates
                           for item in identities)
                or len(set(identities)) != len(identities)):
            raise EvidenceBlocked("invalid_navigation_candidates")
        return identities

    def _read_file(self, root_fd: int, path: str) -> tuple[str, ast.AST]:
        self.tick()
        raw, version = self.scanner.read_bytes(root_fd, path,
                                              self.scanner.project.max_file_bytes)
        entry = self.entries[path]
        if (version != self.scanner.versions[path]
                or hashlib.sha256(raw).hexdigest() != entry["content_sha256"]):
            raise EvidenceBlocked("source_changed", [{"path": path, "reason": "source_changed"}])
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        text = raw.decode(encoding)
        tree = ast.parse(text, filename=path)
        if suspicious_literal(tree):
            raise EvidenceBlocked("possible_literal_secret",
                                  [{"path": path, "reason": "possible_literal_secret"}])
        return text, tree

    def read(self, identities: list[str]) -> None:
        if not self.adaptive:
            self._read_batch(identities)
            return
        self.tick()
        if not identities:
            raise EvidenceBlocked("zero_body_candidates")
        if not all(identity in self.candidates for identity in identities):
            raise EvidenceBlocked("candidate_outside_snapshot")
        # Containing owners precede descendants, even across batch boundaries.
        ordered = sorted(set(identities), key=lambda key: (
            self.candidates[key]["path"], self.candidates[key]["start_line"],
            -self.candidates[key]["end_line"]))
        with self.telemetry.stage("read"):
            for offset in range(0, len(ordered), MAX_BLOCKS):
                self._read_batch(ordered[offset:offset + MAX_BLOCKS])
        self.telemetry.bodies({source["source_id"]: len(source["text"].encode("utf-8"))
                               for source in self.sources})
        self.telemetry.protect([source["source_id"] for source in self.sources],
                               "selected_complete_definition")

    def _read_batch(self, identities: list[str]) -> None:
        """One bounded batch. Complete selected definitions, never truncated bodies.

        Overlapping definitions are not reread. Module imports outside the chosen
        block remain structural evidence only and are not promoted to body evidence.
        """
        self.tick()
        if not identities:
            raise EvidenceBlocked("zero_body_candidates")
        if not all(identity in self.candidates for identity in identities):
            raise EvidenceBlocked("candidate_outside_snapshot")
        root_fd = open_root(self.scanner.project.root)
        seen_records: set[str] = {record["source_id"] for record in self.records}
        try:
            if signature(os.fstat(root_fd)) != self.scanner.directories.get("."):
                raise EvidenceBlocked("root_changed")
            loaded: dict[str, tuple[str, ast.AST]] = {}
            for identity in list(dict.fromkeys(identities)):
                self.tick()
                item = self.candidates[identity]
                path = item["path"]
                start, end = item["start_line"], item["end_line"]
                if any(source["path"] == path and source["start_line"] <= start
                       and source["end_line"] >= end for source in self.sources):
                    continue
                if not self.adaptive and len(self.sources) >= MAX_BLOCKS:
                    self.unfinished.append({"candidate_id": identity, "path": path,
                                            "reason": "candidate_block_budget"})
                    continue
                if path not in loaded:
                    loaded[path] = self._read_file(root_fd, path)
                text, tree = loaded[path]
                node = next((node for node in ast.walk(tree)
                             if isinstance(node, (ast.ClassDef, ast.FunctionDef,
                                                  ast.AsyncFunctionDef))
                             and node.lineno == start and node.end_lineno == end), None)
                if node is None:
                    raise EvidenceBlocked("definition_identity_conflict")
                start = min([start, *[decorator.lineno for decorator in node.decorator_list]])
                body = "".join(text.splitlines(keepends=True)[start - 1:end])
                size = len(body.encode("utf-8"))
                if not self.adaptive and self.body_bytes + size > MAX_BODY_BYTES:
                    self.unfinished.append({"candidate_id": identity, "path": path,
                                            "reason": "complete_block_exceeds_body_budget"})
                    continue
                source_id = hashlib.sha256(
                    f"body:{path}:{item['content_sha256']}:{start}:{end}".encode()
                ).hexdigest()[:24]
                source = {"source_id": source_id,
                          "project_id": self.report["project_id"],
                          "snapshot_id": self.report["snapshot_id"],
                          "path": path, "start_line": start, "end_line": end,
                          "content_sha256": item["content_sha256"],
                          "symbol": item["qualified_name"], "text": body,
                          "read_reason": "selected_complete_definition"}
                self.sources.append(source)
                self.body_bytes += size
                entry = self.entries[path]
                for kind, field in (("definition", "symbols"), ("import", "imports"),
                                    ("call_expression", "calls")):
                    for record in entry[field]:
                        if (start <= record["start_line"] <= record["end_line"] <= end
                                and record["source_id"] not in seen_records):
                            seen_records.add(record["source_id"])
                            self.records.append({**record, "record_kind": kind,
                                                 "body_source_id": source_id})
            if not self.sources:
                raise EvidenceBlocked("zero_body_hits", self.unfinished)
        except (OSError, UnicodeError, ValueError, ScanBlocked) as exception:
            if isinstance(exception, EvidenceBlocked):
                raise
            raise EvidenceBlocked("body_read_or_snapshot_failure") from exception
        finally:
            os.close(root_fd)

    def verify(self) -> None:
        """Recheck the original manifest and content after model generation."""
        self.tick()
        # The scan's ten-second stage deadline is over; final verification receives
        # only the remaining shared business window, never a new business budget.
        self.scanner.deadline = self.deadline
        root_fd = open_root(self.scanner.project.root)
        try:
            if (signature(os.fstat(root_fd)) != self.scanner.directories.get(".")
                    or not self.scanner.verify_versions(root_fd)):
                raise EvidenceBlocked("source_changed_after_generation")
        except (OSError, ScanBlocked) as exception:
            raise EvidenceBlocked("final_snapshot_verification_failed") from exception
        finally:
            os.close(root_fd)
        self.tick()

    def public_sources(self) -> list[dict[str, Any]]:
        return [{key: value for key, value in source.items() if key != "text"}
                for source in self.sources]
