"""Read-only structure extraction. Target files are data, never imported or run."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import stat
import time
import tokenize
from dataclasses import dataclass
from typing import Any


class InputError(ValueError):
    """Invalid configuration or caller input; not a business success."""


class ScanBlocked(Exception):
    pass


def relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise InputError("范围必须为项目内相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise InputError("不允许绝对路径、URL 或父目录路径")
    return path.as_posix()


def within(path: str, prefix: str) -> bool:
    return prefix == "." or path == prefix or path.startswith(prefix + "/")


def overlaps(path: str, prefix: str) -> bool:
    return within(path, prefix) or within(prefix, path)


def integer(value: Any, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InputError(f"{label} 超出允许范围")
    return value


@dataclass(frozen=True)
class Project:
    project_id: str
    root: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    max_entries: int
    max_file_bytes: int
    max_total_bytes: int
    max_seconds: int
    return_bytes: int

    @classmethod
    def from_config(cls, project_id: str, value: Any) -> Project:
        keys = {"root", "include", "exclude", "max_entries", "max_file_bytes",
                "max_total_bytes", "max_seconds", "return_bytes"}
        if not isinstance(value, dict) or set(value) != keys:
            raise InputError("项目配置字段不完整或含未知字段")
        root = value["root"]
        if not isinstance(root, str) or not root.startswith("/") or "\x00" in root:
            raise InputError("服务端根目录必须为绝对路径")
        if ".." in PurePosixPath(root).parts or root == "/":
            raise InputError("根目录无效")
        for key in ("include", "exclude"):
            if not isinstance(value[key], list) or not all(isinstance(x, str) for x in value[key]):
                raise InputError("配置范围必须为字符串列表")
        include = tuple(relative_path(x) for x in value["include"])
        exclude = tuple(relative_path(x) for x in value["exclude"])
        if not include or "." in exclude:
            raise InputError("授权包含范围不能为空或全部排除")
        return cls(
            project_id, root, include, exclude,
            integer(value["max_entries"], 1, 20000, "条目预算"),
            integer(value["max_file_bytes"], 1, 2097152, "单文件预算"),
            integer(value["max_total_bytes"], 1, 33554432, "累计读取预算"),
            integer(value["max_seconds"], 1, 10, "扫描时限"),
            integer(value["return_bytes"], 1024, 24576, "返回预算"),
        )


def load_projects(path: str) -> dict[str, Project]:
    with open(path, "rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise InputError("配置过大")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"config_version", "projects"}:
        raise InputError("配置结构无效")
    if type(value["config_version"]) is not int or value["config_version"] != 1:
        raise InputError("不支持的配置版本")
    projects = value["projects"]
    if not isinstance(projects, dict) or not projects or len(projects) > 16:
        raise InputError("项目授权集合无效")
    return {key: Project.from_config(key, item) for key, item in projects.items()
            if isinstance(key, str) and key and len(key) <= 128}


def open_root(root: str) -> int:
    """Linux descriptor traversal rejects symlinks, including parent components."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    try:
        for part in PurePosixPath(root).parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_relative(root_fd: int, path: str, directory: bool = False) -> int:
    descriptor = os.dup(root_fd)
    parts = PurePosixPath(path).parts
    try:
        for position, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if directory or position < len(parts) - 1:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def sensitive(path: str) -> bool:
    for part in PurePosixPath(path).parts:
        name = part.casefold()
        if (name in {".ssh", ".aws", ".gnupg", "credentials", "secrets"}
                or name == ".env" or name.startswith(".env.")
                or name.startswith(("credentials.", "secrets.", "private."))
                or name.endswith((".pem", ".key", ".p12", ".pfx"))):
            return True
    return False


class StructureVisitor(ast.NodeVisitor):
    def __init__(self, path: str, digest: str, max_records: int = 20000):
        self.path = path
        self.digest = digest
        self.scope: list[str] = []
        self.conditions: list[str] = []
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.max_records = max_records

    def evidence(self, node: ast.AST, kind: str) -> dict[str, Any]:
        if len(self.symbols) + len(self.imports) + len(self.calls) >= self.max_records:
            raise ScanBlocked("structure_record_budget")
        start = node.lineno
        end = node.end_lineno or start
        source_id = hashlib.sha256(
            f"{self.path}:{self.digest}:{start}:{end}:{node.col_offset}:{kind}".encode()
        ).hexdigest()[:24]
        return {"source_id": source_id, "path": self.path, "start_line": start,
                "end_line": end, "content_sha256": self.digest,
                "evidence_level": "direct_static", "conditions": list(self.conditions)}

    def definition(self, node: Any, kind: str) -> None:
        qualified = ".".join([*self.scope, node.name])
        item = {**self.evidence(node, kind), "name": node.name,
                "qualified_name": qualified, "kind": kind}
        if isinstance(node, ast.ClassDef):
            item["bases"] = [ast.unparse(base) for base in node.bases]
        self.symbols.append(item)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definition(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definition(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.definition(node, "async_function")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append({**self.evidence(node, "import:" + alias.name),
                                 "module": alias.name, "name": None,
                                 "alias": alias.asname, "level": 0,
                                 "scope": ".".join(self.scope)})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports.append({**self.evidence(node, "from:" + alias.name),
                                 "module": node.module, "name": alias.name,
                                 "alias": alias.asname, "level": node.level,
                                 "scope": ".".join(self.scope)})

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append({**self.evidence(node, "call"),
                           "caller": ".".join(self.scope) or "<module>",
                           "expression": ast.unparse(node.func),
                           "target_resolution": "unknown",
                           "runtime_execution": "unknown"})
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        for label, statements in (("if", node.body), ("else", node.orelse)):
            self.conditions.append(f"{label}:{node.lineno}:{ast.unparse(node.test)}")
            for statement in statements:
                self.visit(statement)
            self.conditions.pop()

    def visit_Try(self, node: ast.Try) -> None:
        for label, statements in (("try", node.body), ("else", node.orelse),
                                  ("finally", node.finalbody)):
            self.conditions.append(f"{label}:{node.lineno}")
            for statement in statements:
                self.visit(statement)
            self.conditions.pop()
        for handler in node.handlers:
            self.conditions.append(f"except:{handler.lineno}")
            self.generic_visit(handler)
            self.conditions.pop()

    visit_TryStar = visit_Try


def parse_structure(path: str, raw: bytes) -> dict[str, Any]:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    text = raw.decode(encoding)
    digest = hashlib.sha256(raw).hexdigest()
    tree = ast.parse(text, filename=path)
    visitor = StructureVisitor(path, digest)
    visitor.visit(tree)
    return {"content_sha256": digest, "line_count": len(text.splitlines()),
            "symbols": visitor.symbols, "imports": visitor.imports,
            "calls": visitor.calls}


class Scanner:
    def __init__(self, project: Project, include: tuple[str, ...], max_seconds: int):
        self.project = project
        self.include = include
        self.deadline = time.monotonic() + max_seconds
        self.entries: list[dict[str, Any]] = []
        self.obligations: list[dict[str, str]] = []
        self.versions: dict[str, tuple[int, ...]] = {}
        self.directories: dict[str, tuple[int, ...]] = {}
        self.total_bytes = 0
        self.inventory_complete = True

    def tick(self) -> None:
        if time.monotonic() >= self.deadline:
            raise ScanBlocked("deadline_exhausted")

    def excluded(self, path: str) -> bool:
        parts = PurePosixPath(path).parts
        return any(within(path, item) or ("/" not in item and item in parts)
                   for item in self.project.exclude)

    def note(self, path: str, reason: str) -> None:
        self.obligations.append({"path": path, "reason": reason})

    def read_bytes(self, root_fd: int, path: str, limit: int) -> tuple[bytes, tuple[int, ...]]:
        descriptor = open_relative(root_fd, path)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ScanBlocked("not_regular_file")
            if before.st_size > limit:
                raise ScanBlocked("file_or_total_budget")
            chunks = []
            size = 0
            while True:
                self.tick()
                chunk = os.read(descriptor, min(65536, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ScanBlocked("file_or_total_budget")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if signature(before) != signature(after):
                raise ScanBlocked("source_changed")
            return b"".join(chunks), signature(after)
        finally:
            os.close(descriptor)

    def walk(self, root_fd: int, path: str = ".") -> None:
        self.tick()
        descriptor = open_relative(root_fd, path, directory=True)
        try:
            self.directories[path] = signature(os.fstat(descriptor))
            with os.scandir(descriptor) as listing:
                for child in listing:
                    self.tick()
                    if len(self.entries) >= self.project.max_entries:
                        raise ScanBlocked("entry_budget")
                    relative = child.name if path == "." else path + "/" + child.name
                    if not any(overlaps(relative, prefix) for prefix in self.include):
                        continue
                    info = child.stat(follow_symlinks=False)
                    entry: dict[str, Any] = {"path": relative, "kind": "file",
                                             "status": "pending"}
                    self.entries.append(entry)
                    if stat.S_ISLNK(info.st_mode):
                        entry.update(kind="symlink", status="skipped", reason="symlink_not_followed")
                        self.note(relative, "symlink_not_followed")
                    elif self.excluded(relative) or sensitive(relative):
                        entry.update(kind="directory" if stat.S_ISDIR(info.st_mode) else "file",
                                     status="excluded", reason="policy_excluded")
                    elif stat.S_ISDIR(info.st_mode):
                        entry.update(kind="directory", status="listed")
                        self.walk(root_fd, relative)
                    elif not stat.S_ISREG(info.st_mode):
                        entry.update(kind="special", status="skipped", reason="not_regular_file")
                        self.note(relative, "not_regular_file")
                    elif not any(within(relative, prefix) for prefix in self.include):
                        entry.update(status="excluded", reason="outside_requested_scope")
                    else:
                        self.versions[relative] = signature(info)
                        entry["size_bytes"] = info.st_size
                        if not relative.endswith(".py"):
                            entry.update(status="skipped", reason="unsupported_language_or_asset")
                            self.note(relative, "unsupported_language_or_asset")
                            continue
                        limit = min(self.project.max_file_bytes,
                                    self.project.max_total_bytes - self.total_bytes)
                        try:
                            raw, version = self.read_bytes(root_fd, relative, max(0, limit))
                            self.total_bytes += len(raw)
                            if signature(info) != version:
                                raise ScanBlocked("source_changed")
                            entry.update(parse_structure(relative, raw), status="parsed")
                        except (SyntaxError, UnicodeError, LookupError, ValueError,
                                RecursionError, OSError, ScanBlocked) as error:
                            reason = str(error) if isinstance(error, ScanBlocked) else type(error).__name__
                            entry.update(status="failed", reason=reason)
                            self.note(relative, reason)
        finally:
            os.close(descriptor)

    def verify_versions(self, root_fd: int) -> bool:
        for path, version in {**self.directories, **self.versions}.items():
            self.tick()
            descriptor = open_relative(root_fd, path, directory=path in self.directories)
            try:
                if signature(os.fstat(descriptor)) != version:
                    self.note(path, "source_changed")
                    return False
            finally:
                os.close(descriptor)
        for entry in self.entries:
            if entry["status"] != "parsed":
                continue
            raw, _ = self.read_bytes(root_fd, entry["path"], self.project.max_file_bytes)
            if hashlib.sha256(raw).hexdigest() != entry["content_sha256"]:
                self.note(entry["path"], "source_changed")
                return False
        return True

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        root_fd = None
        fresh = False
        blocked = False
        try:
            root_fd = open_root(self.project.root)
            self.walk(root_fd)
            fresh = self.verify_versions(root_fd)
            current_fd = open_root(self.project.root)
            try:
                if signature(os.fstat(current_fd)) != signature(os.fstat(root_fd)):
                    fresh = False
                    self.note(".", "root_changed")
            finally:
                os.close(current_fd)
        except (OSError, ScanBlocked) as error:
            blocked = True
            self.inventory_complete = False
            self.note(".", str(error) if isinstance(error, ScanBlocked) else "filesystem_unavailable")
        finally:
            if root_fd is not None:
                os.close(root_fd)
        self.entries.sort(key=lambda item: item["path"])
        file_entries = [item for item in self.entries if item["kind"] == "file"
                        and item["status"] != "excluded"]
        parsed = [item for item in file_entries if item["status"] == "parsed"]
        identity = [{"path": item["path"], "status": item["status"],
                     "sha256": item.get("content_sha256"),
                     "metadata": self.versions.get(item["path"])} for item in self.entries]
        snapshot = hashlib.sha256(json.dumps(
            {"extractor_version": "0.1.0", "include": self.include,
             "exclude": self.project.exclude, "manifest": identity},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if not parsed:
            blocked = True
            self.note(".", "zero_python_structure_hits")
        status = "blocked" if blocked or not fresh else "partial" if self.obligations else "complete"
        return {
            "contract_version": 1, "kind": "repository_structure", "status": status,
            "project_id": self.project.project_id, "snapshot_id": snapshot,
            "freshness": "current_at_verification" if fresh else "unknown_or_changed",
            "snapshot_scope": "Python content hashes; unsupported files metadata only; excluded content unread",
            "supported_languages": ["python"], "include": list(self.include),
            "inventory_complete": self.inventory_complete,
            "coverage": {"enumerated_entries": len(self.entries),
                         "file_denominator": len(file_entries), "parsed_files": len(parsed),
                         "skipped_files": sum(x["status"] == "skipped" for x in file_entries),
                         "failed_files": sum(x["status"] == "failed" for x in file_entries),
                         "excluded_entries": sum(x["status"] == "excluded" for x in self.entries),
                         "symbols": sum(len(x["symbols"]) for x in parsed),
                         "imports": sum(len(x["imports"]) for x in parsed),
                         "call_expressions": sum(len(x["calls"]) for x in parsed)},
            "entries": self.entries, "unfinished": self.obligations,
            "observations": {"elapsed_seconds": round(time.monotonic() - started, 6),
                             "content_bytes_read": self.total_bytes,
                             "model_requests": 0, "target_writes": 0},
            "limits": {"max_entries": self.project.max_entries,
                       "max_file_bytes": self.project.max_file_bytes,
                       "max_total_bytes": self.project.max_total_bytes},
            "non_goals": ["knowledge_distillation", "runtime_call_graph", "semantic_quality_acceptance",
                          "model_gateway", "Roo_host_activation", "proposal_completion"],
        }


def validate_request(projects: dict[str, Project], arguments: Any) -> tuple[Project, tuple[str, ...], int, int]:
    if not isinstance(arguments, dict):
        raise InputError("参数必须为对象")
    allowed = {"contract_version", "project_id", "include", "max_seconds", "return_bytes"}
    if set(arguments) - allowed or not {"contract_version", "project_id"} <= set(arguments):
        raise InputError("参数字段不完整或含未知字段")
    if type(arguments["contract_version"]) is not int or arguments["contract_version"] != 1:
        raise InputError("不支持的业务版本")
    key = arguments["project_id"]
    if not isinstance(key, str) or key not in projects:
        raise InputError("项目不在服务端授权集合")
    project = projects[key]
    requested = arguments.get("include", list(project.include))
    if not isinstance(requested, list) or not requested or len(requested) > 32:
        raise InputError("请求范围无效")
    include = tuple(relative_path(x) for x in requested)
    if any(not any(within(path, prefix) for prefix in project.include) for path in include):
        raise InputError("请求只能收窄服务端授权范围")
    seconds = integer(arguments.get("max_seconds", project.max_seconds), 1, project.max_seconds, "扫描时限")
    budget = integer(arguments.get("return_bytes", project.return_bytes), 1024, project.return_bytes, "返回预算")
    return project, include, seconds, budget


def serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def bounded_result(report: dict[str, Any], budget: int) -> dict[str, Any]:
    if serialized_size(report) <= budget:
        return report
    compact = {key: value for key, value in report.items() if key != "entries"}
    compact["entries"] = []
    omissions = []
    for entry in report["entries"]:
        reduced = {key: value for key, value in entry.items() if key not in {"symbols", "imports", "calls"}}
        counts = {key: len(entry.get(key, [])) for key in ("symbols", "imports", "calls")}
        if any(counts.values()):
            reduced["omitted_records"] = counts
            omissions.append({"path": entry["path"], "reason": "return_budget_structure_omitted"})
        compact["entries"].append(reduced)
    compact["unfinished"] = [*report["unfinished"], *omissions]
    if compact["status"] == "complete":
        compact["status"] = "partial"
    if serialized_size(compact) <= budget:
        return compact
    minimum = {
        "contract_version": 1, "kind": "repository_structure", "status": "blocked",
        "project_id": report["project_id"], "snapshot_id": report["snapshot_id"],
        "coverage": report["coverage"], "freshness": report["freshness"],
        "inventory_complete": report["inventory_complete"],
        "unfinished": [{"path": ".", "reason": "return_budget_cannot_preserve_manifest_and_obligations"}],
        "unreturned_entry_count": len(report["entries"]),
        "unreturned_obligation_count": len(compact["unfinished"]),
        "return_bytes": budget,
        "observations": report["observations"],
    }
    if serialized_size(minimum) > budget:
        raise InputError("返回预算不足以保存阻塞结果")
    return minimum


def extract(projects: dict[str, Project], arguments: Any) -> dict[str, Any]:
    project, include, seconds, budget = validate_request(projects, arguments)
    report = Scanner(project, include, seconds).run()
    return bounded_result(report, budget)
