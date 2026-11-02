"""显式 V3 可信来源边界：冻结完整扫描，不构建全库 V2 语法包。

沿用描述符访问、内容摘要及秘密检查；只读取来源，不执行或写入。
本层认证磁盘范围与新鲜度，不代替独立选择、结构或人工语义验收。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time

from .evidence import EvidenceBlocked, suspicious_literal
from .extractor import Scanner, ScanBlocked, open_root, open_relative, signature, within
from .snapshot_v2 import parsed_tree


class V3EvidenceReader:
    """一个调用内的私有扫描上下文；所有对外来源视图均为独立副本。"""

    def __init__(self, scanner, report, deadline, cancelled):
        if (not isinstance(scanner, Scanner) or type(report) is not dict
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not callable(getattr(cancelled, "is_set", None))):
            raise ValueError("v3_evidence_trusted_context")
        self.scanner = scanner
        self.deadline = deadline
        self.cancelled = cancelled
        self._raw_files = {}
        self._loaded = False
        self._report = copy.deepcopy(report)
        self._directories = dict(scanner.directories)
        self._versions = dict(scanner.versions)
        self._entries = copy.deepcopy(scanner.entries)
        self._root = scanner.project.root
        self._max_file_bytes = scanner.project.max_file_bytes
        self._project_id = scanner.project.project_id

        if (report.get("project_id") != self._project_id
                or report.get("contract_version") != 1
                or type(report.get("contract_version")) is not int
                or report.get("kind") != "repository_structure"
                or report.get("include") != list(scanner.include)
                or any(not any(within(path, allowed) for allowed in scanner.project.include)
                       for path in scanner.include)
                or report.get("entries") != self._entries
                or report.get("unfinished") != scanner.obligations
                or report.get("inventory_complete") is not True
                or not scanner.inventory_complete
                or report.get("freshness") != "current_at_verification"
                or report.get("status") not in {"complete", "partial"}
                or "." not in self._directories
                or any(item.get("reason") != "unsupported_language_or_asset"
                       for item in report.get("unfinished", []))):
            raise EvidenceBlocked("v3_scan_context_not_ready")

        paths = [entry["path"] for entry in self._entries]
        if paths != sorted(set(paths)):
            raise EvidenceBlocked("v3_scan_path_identity_conflict")
        parsed = [entry for entry in self._entries if entry.get("status") == "parsed"]
        file_entries = [entry for entry in self._entries
                        if entry["kind"] == "file" and entry["status"] != "excluded"]
        coverage = {"enumerated_entries": len(self._entries),
                    "file_denominator": len(file_entries), "parsed_files": len(parsed),
                    "skipped_files": sum(entry["status"] == "skipped" for entry in file_entries),
                    "failed_files": sum(entry["status"] == "failed" for entry in file_entries),
                    "excluded_entries": sum(entry["status"] == "excluded" for entry in self._entries),
                    "symbols": sum(len(entry["symbols"]) for entry in parsed),
                    "imports": sum(len(entry["imports"]) for entry in parsed),
                    "call_expressions": sum(len(entry["calls"]) for entry in parsed)}
        if (not parsed or report.get("coverage") != coverage
                or any(type(value) is not int for value in report.get("coverage", {}).values())
                or any(entry["path"] not in self._versions for entry in parsed)):
            raise EvidenceBlocked("v3_scan_scope_or_coverage_conflict")
        manifest = [{"path": entry["path"], "status": entry["status"],
                     "sha256": entry.get("content_sha256"),
                     "metadata": self._versions.get(entry["path"])}
                    for entry in self._entries]
        expected_snapshot = hashlib.sha256(json.dumps(
            {"extractor_version": "0.1.0", "include": scanner.include,
             "exclude": scanner.project.exclude, "manifest": manifest},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if report.get("snapshot_id") != expected_snapshot:
            raise EvidenceBlocked("v3_scan_snapshot_identity_conflict")
        self._snapshot_id = expected_snapshot
        self._parsed = parsed

    @property
    def project_id(self):
        return self._project_id

    @property
    def snapshot_id(self):
        return self._snapshot_id

    @property
    def raw_files(self):
        return dict(self._raw_files)

    @property
    def report(self):
        return copy.deepcopy(self._report)

    def tick(self):
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise EvidenceBlocked("cancelled_or_evidence_deadline")

    def _check_metadata(self, root_fd):
        self.tick()
        if signature(os.fstat(root_fd)) != self._directories["."]:
            raise EvidenceBlocked("root_changed")
        for path, version in {**self._directories, **self._versions}.items():
            self.tick()
            descriptor = open_relative(root_fd, path, directory=path in self._directories)
            try:
                if signature(os.fstat(descriptor)) != version:
                    raise EvidenceBlocked("source_changed")
            finally:
                os.close(descriptor)
            self.tick()

    def _read_verified(self, root_fd, entry):
        self.tick()
        raw, version = self.scanner.read_bytes(root_fd, entry["path"], self._max_file_bytes)
        self.tick()
        if (version != self._versions[entry["path"]]
                or hashlib.sha256(raw).hexdigest() != entry["content_sha256"]):
            raise EvidenceBlocked("source_changed")
        return raw

    def _visit(self, *, loading):
        self.tick()
        descriptor = None
        previous_deadline = self.scanner.deadline
        # 扫描与正文认证有各自获准截止；借用读取器不继承扫描段的旧余量。
        self.scanner.deadline = self.deadline
        proposed = {}
        try:
            descriptor = open_root(self._root)
            self._check_metadata(descriptor)
            for entry in self._parsed:
                raw = self._read_verified(descriptor, entry)
                if loading:
                    self.tick()
                    if suspicious_literal(parsed_tree(raw, entry["path"])):
                        raise EvidenceBlocked("possible_literal_secret")
                    self.tick()
                    proposed[entry["path"]] = raw
                elif raw != self._raw_files[entry["path"]]:
                    raise EvidenceBlocked("source_changed_after_selection")
            # 读取期间新建、替换、删除文件及根路径变化同样阻塞。
            self._check_metadata(descriptor)
            current_fd = open_root(self._root)
            try:
                self._check_metadata(current_fd)
            finally:
                os.close(current_fd)
            self.tick()
        except (OSError, ScanBlocked) as error:
            raise EvidenceBlocked("v3_source_access_or_change:" + type(error).__name__) from error
        finally:
            self.scanner.deadline = previous_deadline
            if descriptor is not None:
                os.close(descriptor)
        return proposed

    def inventory(self):
        """只在整批来源认证成功后发布字节视图；重读不绕过新鲜度。"""
        self.tick()
        if self._loaded:
            self.verify()
            return self.raw_files
        proposed = self._visit(loading=True)
        self.tick()
        self._raw_files = proposed
        self._loaded = True
        return self.raw_files

    def verify(self):
        """从私有冻结扫描复核所有目录／文件元数据及全部 Python 原字节。"""
        self.tick()
        if not self._loaded:
            raise EvidenceBlocked("v3_source_inventory_not_read")
        self._visit(loading=False)
        self.tick()
