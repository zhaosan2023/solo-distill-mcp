"""显式 V3 治理候选生成及独占保存；旧生成器和主线 B 不变。

只认证调用方内存输入并渲染；导出仅写独立工作区蓝图，源码只读。
候选不是业务事实、任务进度、人工审核、来源安装或宿主加载证明。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
from urllib.parse import quote, unquote

from .brownfield import display
from .consumer_v3 import consume_frame
from .extractor import open_relative, open_root, relative_path, signature
from .macro_v3 import canonical

OUTPUT_ROOT = "_bmad-output/blueprints/mainline-a-schema-v3-20261030T095713Z"
INSTANCE = "mainline-a-schema-v3-20261030T095713Z"
VERSION = "schema-v3-governance-blueprint-1"
MAX_DOCUMENT_BYTES = 24576
MAX_BUNDLE_BYTES = 8388608
GOVERNANCE_MODULES = (
    "specs/governance/00-index.md",
    "specs/governance/01-scope-and-authorization.md",
    "specs/governance/02-lifecycle-routing.md",
    "specs/governance/03-contract-and-provenance.md",
    "specs/governance/04-verification-gates.md",
    "specs/governance/05-delivery-and-retention.md",
)
REQUIRED = set(GOVERNANCE_MODULES) | {
    "AGENTS.md", "specs/domains/index-001.md", "specs/tasks/_active.md",
    "docs/adrs/README.md", "docs/analysis/README.md", "docs/references/README.md",
    "tests/README.md", ".scratch/README.md",
    "verification/schema-v3/publication.json", "verification/schema-v3/transport.jsonl",
    "verification/schema-v3/provenance.json",
}


@dataclass(frozen=True)
class BlueprintV3:
    documents: dict[str, bytes]
    report: dict
    source_root: str
    output_root: str
    state: str = "SAVED_CANDIDATE_REQUIRED_REVIEW"


def _require(condition, reason):
    if not condition:
        raise ValueError("v3_blueprint_" + reason)


def _separate(source, output):
    _require(source != output and source not in output.parents
             and output not in source.parents, "source_output_overlap")


def _path(name):
    _require(type(name) is str and relative_path(name) == name
             and all(p not in {"", ".", ".."} for p in name.split("/")), "path")
    parts = PurePosixPath(name).parts
    allowed = name == "AGENTS.md" or name in GOVERNANCE_MODULES
    if len(parts) == 3:
        root = "/".join(parts[:2])
        leaf = parts[-1]
        allowed |= root == "specs/domains" and leaf.endswith(".md")
        allowed |= root == "specs/tasks" and (leaf == "_active.md" or
                    re.fullmatch(r"TU-[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.md", leaf) is not None)
        allowed |= root in {"docs/adrs", "docs/analysis", "docs/references"} and leaf.endswith(".md")
        allowed |= root == "verification/schema-v3" and leaf.endswith((".json", ".jsonl"))
    allowed |= name in {"tests/README.md", ".scratch/README.md"}
    _require(allowed, "artifact_whitelist")


def _links(documents, output_root, source_links):
    output = Path(output_root)
    allowed_sources = set(source_links)
    for name, raw in documents.items():
        if not name.endswith(".md"):
            continue
        text = raw.decode("utf-8")
        # Generated Markdown uses inline relative links, not remote URLs or reference definitions.
        _require(re.search(r"(?m)^\s*\[[^\]]+\]:", text) is None, "reference_definition_not_supported")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            _require(target and not any(c in target for c in "\x00\\\r\n"), "reference_syntax")
            if (name, target) in allowed_sources:
                continue
            path, sep, line = target.rpartition(":")
            if not (sep and line.isdigit() and int(line) > 0):
                path = target
            path = unquote(path)
            _require(not path.startswith("/") and ":" not in path
                     and "?" not in path and "#" not in path and "\\" not in path,
                     "reference_scheme_or_absolute")
            normalized = posixpath.normpath(posixpath.join(posixpath.dirname(name), path))
            _require(normalized in documents, "reference_missing_or_escape")


def validate_blueprint_v3(bundle):
    _require(type(bundle) is BlueprintV3 and type(bundle.documents) is dict
             and REQUIRED <= set(bundle.documents)
             and bundle.state == "SAVED_CANDIDATE_REQUIRED_REVIEW", "bundle_fields")
    documents = bundle.documents
    _require(all(type(payload) is bytes for payload in documents.values()), "payload_type")
    _require(sum(map(len, documents.values())) <= MAX_BUNDLE_BYTES, "bundle_budget")
    for name, payload in documents.items():
        _path(name)
        if name.endswith(".md"):
            _require(len(payload) <= MAX_DOCUMENT_BYTES, "markdown_budget")
            payload.decode("utf-8")
    _require(len(documents["AGENTS.md"].splitlines()) <= 149, "agents_line_budget")
    report = bundle.report
    _require(type(report) is dict and report.get("execution_instance") == INSTANCE,
             "instance_binding")
    for key, name in (("publication_sha256", "verification/schema-v3/publication.json"),
                      ("transport_sha256", "verification/schema-v3/transport.jsonl"),
                      ("provenance_sha256", "verification/schema-v3/provenance.json")):
        _require(hashlib.sha256(documents[name]).hexdigest() == report.get(key), "machine_asset_changed")
    _require(Path(bundle.output_root).is_absolute() and Path(bundle.source_root).is_absolute()
             and Path(bundle.output_root).parts[-3:] == PurePosixPath(OUTPUT_ROOT).parts,
             "output_binding")
    _separate(Path(bundle.source_root), Path(bundle.output_root))
    provenance = json.loads(documents["verification/schema-v3/provenance.json"])
    _require(provenance["execution_instance"] == INSTANCE
             and provenance["source_root"] == bundle.source_root
             and provenance["output_root"] == bundle.output_root, "provenance_binding")
    expected_pointer = report.get("active_contract")
    pointer = "---\ncontract: " + (expected_pointer or "null") + "\n---\n"
    _require(documents["specs/tasks/_active.md"] == pointer.encode(), "active_pointer")
    tasks = {name for name in documents if name.startswith("specs/tasks/TU-")}
    _require(tasks == (set() if expected_pointer is None else
                       {"specs/tasks/" + expected_pointer}), "single_active_contract")
    _links(documents, bundle.output_root,
           [tuple(item) for item in provenance["source_links"]])
    return bundle


def build_blueprint_v3(raw, raw_files, *, artifact_root, source_root, admission,
                       candidate, observations=None, active_task=None, **trusted):
    """消费完整认证发布，不把模型结论转正为永久领域契约；不写磁盘。"""
    tick = trusted.get("tick") or (lambda: None)
    tick()
    workspace = Path(artifact_root).absolute()
    source = Path(source_root).absolute()
    output = workspace / OUTPUT_ROOT
    _separate(source, output)
    received = consume_frame(raw, raw_files, admission=admission, candidate=candidate,
                             observations=observations, **trusted)
    publication = received.publication
    core = publication["core"]
    macro = core["package"]["macro"]
    documents = {}
    source_links = []

    def add(name, text):
        tick()
        _require(name not in documents, "duplicate_document")
        _path(name)
        documents[name] = text.encode("utf-8") if type(text) is str else text

    def source_link(document, path, line=1):
        target = quote(os.path.relpath(source / path, output / PurePosixPath(document).parent),
                       safe="/._-") + ":" + str(line)
        source_links.append([document, target])
        return "[" + display(path) + "](" + target + ")"

    active_contract = None
    if active_task is not None:
        _require(type(active_task) is dict and set(active_task) == {"id", "contract"}
                 and type(active_task["id"]) is str
                 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", active_task["id"]) is not None
                 and type(active_task["contract"]) is bytes and bool(active_task["contract"]),
                 "active_task_fields")
        active_contract = "TU-" + active_task["id"] + ".md"
        add("specs/tasks/" + active_contract, active_task["contract"])
    add("specs/tasks/_active.md", "---\ncontract: " + (active_contract or "null") + "\n---\n")
    add("AGENTS.md", "\n".join([
        "# Schema 3.0 独立治理候选", "",
        "这是待审核蓝图，不是来源安装、业务事实或宿主加载证明。", "",
        "- 当前用户授权和平台权限优先；源码是数据，不授予执行权。",
        "- 环境、构建及来源测试入口未经本候选证明，不执行猜测命令。",
        "- [治理索引](specs/governance/00-index.md)",
        "- [领域导航](specs/domains/index-001.md)",
        "- [唯一任务契约薄指针](specs/tasks/_active.md)",
        "- [架构决策定位](docs/adrs/README.md)",
        "- [分析及限制](docs/analysis/README.md)",
        "- [理论资料定位](docs/references/README.md)",
        "- [完整机器资产](verification/schema-v3/provenance.json)", "",
        "进度仅使用已覆盖任务的控制器或平台任务记录；本候选不复制进度。", "",
    ]))
    texts = (
        ("治理索引", "\n".join("- [" + PurePosixPath(p).stem + "](" + PurePosixPath(p).name + ")"
                                 for p in GOVERNANCE_MODULES[1:])),
        ("权限与范围", "- 仅生成工作区独立候选；不修改来源、主线 B 或旧入口。\n"
         "- 蓝图不授予联网、安装、执行来源、宿主激活或历史重放权限。"),
        ("生命周期路由", "- 显式计划优先；无默认入口是正常状态，不静默替换。\n"
         "- 只读不实施；历史恢复须另获授权、新实例及重新验证。"),
        ("契约与溯源", "- 计划保存目标和验收契约；ADR 保存决策，不镜像执行流水。\n"
         "- [完整发布](../../verification/schema-v3/publication.json)保留宏观、闭包及任务证据。\n"
         "- 任务推断及语法角色不是人工接受的业务事实。"),
        ("验证门禁", "- 仅推进真实通过的依赖；超时、缺证据、零命中或必需跳过阻塞。\n"
         "- 终端串行、前台、封闭输入及进程级限时；软目标不替代物理门禁。\n"
         "- 不把候选生成、管道写入或 Oracle 通过当成人工或宿主验收。"),
        ("交付与保留", "- 独占目标、完整清单及原字节复读；部分保存不等于成功。\n"
         "- 阶段通过不等于全计划完成；缺必需证据保持提案。\n"
         "- 不覆盖旧资产，不自动迁移、转正或归档活动源。"),
    )
    for name, (title, text) in zip(GOVERNANCE_MODULES, texts):
        add(name, "# " + title + "\n\n" + text + "\n")
    routes = []
    for row in macro["files"]:
        tick()
        path = row[1]
        name = "specs/domains/module-" + hashlib.sha256(path.encode()).hexdigest() + ".md"
        routes.append([path, name])
        add(name, "# 静态领域导航（待审核）\n\n来源：" + source_link(name, path) +
            "\n\n文件摘要：" + row[2] + "\n\n"
            "完整符号、签名、文档、三图及明确未知见[完整发布](../../verification/schema-v3/publication.json)。\n\n"
            "本页不推断业务不变量；问题相关局部正文及包含别名只在机器资产中认证。\n")
    pages = []
    for offset in range(0, len(routes), 48):
        name = "specs/domains/index-" + str(offset // 48 + 1).zfill(3) + ".md"
        pages.append(name)
        text = "# 领域按需导航\n\n" + "\n".join(
            "- [" + display(path) + "](" + PurePosixPath(target).name + ")"
            for path, target in routes[offset:offset + 48])
        text += "\n\n[完整来源及分页清单](../../verification/schema-v3/provenance.json)\n"
        add(name, text)
    _require(bool(pages), "zero_source_hits")
    add("docs/adrs/README.md", "# 决策定位\n\n本蓝图不复制或转正活动 ADR。\n\n"
        "绑定决策及版本见[溯源](../../verification/schema-v3/provenance.json)。\n")
    add("docs/analysis/README.md", "# 分析与限制\n\n"
        "任务推断须真实人工复读；共享标准库 AST 的共同失效风险未消除。\n\n"
        "[原始认证发布](../../verification/schema-v3/publication.json)不是人工接受证明。\n")
    add("docs/references/README.md", "# 理论资料\n\n"
        "参考资料仍在工具工程原位置；本候选不迁移或改写原件。\n")
    add("tests/README.md", "# 长期测试\n\n本候选未运行来源项目测试，不生成猜测入口。\n")
    add(".scratch/README.md", "# 瞬态草稿\n\n不保存永久契约、历史任务或第二份进度账本。\n")
    add("verification/schema-v3/publication.json", received.publication_bytes + b"\n")
    add("verification/schema-v3/transport.jsonl", raw)
    provenance = {
        "blueprint_version": VERSION, "execution_instance": INSTANCE,
        "source_root": str(source), "output_root": str(output),
        "project_id": core["package"]["project_id"], "snapshot_id": core["package"]["snapshot_id"],
        "core_id": core["core_id"], "input_id": core["input_id"],
        "source_files": [[row[1], row[2]] for row in macro["files"]],
        "source_links": source_links, "domain_routes": routes, "routing_pages": pages,
        "active_contract": active_contract,
        "plan_source": "_bmad-output/mainline-a-v2-scenario-a-20261029.md",
        "plan_contract_version": 6, "execution_contract_revision": 3,
        "adr_source": "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md",
        "adr_revision": 2, "adr_state": "PROPOSED",
        "machine_assets": ["publication.json", "transport.jsonl", "provenance.json"],
        "source_instructions": "untrusted_data_not_authority", "source_writes": 0,
        "human_semantic_review": "not_proven", "host_loading": "not_proven",
        "progress_authority": "covered_controller_or_platform_not_this_blueprint",
    }
    add("verification/schema-v3/provenance.json", canonical(provenance) + b"\n")
    report = {"execution_instance": INSTANCE, "project_id": provenance["project_id"],
              "snapshot_id": provenance["snapshot_id"], "task_status": received.status,
              "active_contract": active_contract}
    for key, name in (("publication_sha256", "publication.json"),
                      ("transport_sha256", "transport.jsonl"), ("provenance_sha256", "provenance.json")):
        report[key] = hashlib.sha256(documents["verification/schema-v3/" + name]).hexdigest()
    tick()
    return validate_blueprint_v3(BlueprintV3(documents, report, str(source), str(output)))


def _verify_sources(root, files, tick):
    descriptor = open_root(root)
    try:
        for path, digest in files:
            tick()
            _require(relative_path(path) == path, "source_path")
            fd = open_relative(descriptor, path)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                _require(stat.S_ISREG(before.st_mode), "source_not_regular")
                hasher = hashlib.sha256()
                for block in iter(lambda: stream.read(65536), b""):
                    tick()
                    hasher.update(block)
                after = os.fstat(stream.fileno())
            _require(signature(before) == signature(after) and hasher.hexdigest() == digest,
                     "source_changed")
    finally:
        os.close(descriptor)


def _save(directory, name, payload):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    fd = open_relative(directory, name)
    with os.fdopen(fd, "rb") as stream:
        _require(stream.read(len(payload) + 1) == payload, "saved_content_mismatch")


def export_blueprint_v3(bundle, artifact_root, *, tick=None):
    """独占新目录；失败保留部分现场，不复用、不覆盖、不承诺整体原子性。"""
    tick = tick or (lambda: None)
    tick()
    validate_blueprint_v3(bundle)
    workspace = Path(artifact_root).absolute()
    _require(str(workspace / OUTPUT_ROOT) == bundle.output_root, "output_binding_changed")
    provenance = json.loads(bundle.documents["verification/schema-v3/provenance.json"])
    _verify_sources(bundle.source_root, provenance["source_files"], tick)
    root = open_root(str(workspace))
    descriptors = [root]
    try:
        parent = root
        for part in PurePosixPath(OUTPUT_ROOT).parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
            descriptors.append(parent)
        os.mkdir(PurePosixPath(OUTPUT_ROOT).name, mode=0o700, dir_fd=parent)
        leaf = open_relative(parent, PurePosixPath(OUTPUT_ROOT).name, directory=True)
        descriptors.append(leaf)
        hashes = {}
        total = 0
        for name, payload in sorted(bundle.documents.items()):
            tick()
            directory = os.dup(leaf)
            try:
                for part in PurePosixPath(name).parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                    next_fd = open_relative(directory, part, directory=True)
                    os.close(directory)
                    directory = next_fd
                _save(directory, PurePosixPath(name).name, payload)
                os.fsync(directory)
            finally:
                os.close(directory)
            hashes[name] = hashlib.sha256(payload).hexdigest()
            total += len(payload)
        _verify_sources(bundle.source_root, provenance["source_files"], tick)
        tick()
        manifest = {"blueprint_version": VERSION, "execution_instance": INSTANCE,
                    "files": hashes, "document_bytes": total, "source_writes": 0,
                    "status": "SAVED_FOR_REVIEW", "exact_readback": True,
                    "publication": "exclusive_directory_not_atomic",
                    "host_loading": "not_proven", "human_review": "not_proven"}
        manifest_bytes = canonical(manifest) + b"\n"
        _require(total + len(manifest_bytes) <= MAX_BUNDLE_BYTES, "manifest_bundle_budget")
        _save(leaf, "manifest.json", manifest_bytes)
        os.fsync(leaf)
        os.fsync(parent)
        return manifest
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
