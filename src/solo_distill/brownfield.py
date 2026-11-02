"""Explicit offline Python -> three-tier review documents; never target writes.

No imports of target modules, gateway calls, installs or default MCP changes.
P0/P1 selection and escaped frame accounting reuse the existing V1 boundary.
Generated documents are review material, not accepted business contracts.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import sys
from urllib.parse import quote

from .extractor import (
    InputError, Scanner, bounded_result, load_projects, open_relative, open_root,
    relative_path, validate_request,
)
from .priority_v1 import expand_entries

VERSION = "python-brownfield-review-v1"
PAGE_RECORDS = 48
MAX_DOCUMENT_BYTES = 24576
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
OUTPUT_PREFIX = "verification/brownfield-review"


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def frame_bytes(report, rpc_id):
    # Match the existing text-result JSON-RPC structure, including final newline.
    text = json.dumps(report, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))
    frame = {"jsonrpc": "2.0", "id": rpc_id, "result": {
        "content": [{"type": "text", "text": text}],
        "isError": report["status"] in {"blocked", "failed"}}}
    return (json.dumps(frame, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")).encode("utf-8") + b"\n")


def display(value):
    # Source-controlled names cannot introduce active Markdown/HTML instructions.
    text = str(value)
    if len(text) > 120:
        text = text[:117] + "…"
    text = json.dumps(text, ensure_ascii=False)[1:-1]
    return (html.escape(text, quote=True).replace("`", "&#96;")
            .replace("[", "&#91;").replace("]", "&#93;"))


def source_link(record, source_root, output_root, document, label):
    origin = Path(source_root) / record["path"]
    start = output_root / PurePosixPath(document).parent
    target = quote(os.path.relpath(origin, start), safe="/._-")
    return f"[{display(label)}]({target}:{record['start_line']})"


@dataclass(frozen=True)
class ReviewBundle:
    documents: dict[str, bytes]
    report: dict
    source_root: str
    output_root: str
    wire_bytes: int
    state: str


def build_review(projects, arguments, output_root, *, rpc_id="brownfield-review"):
    """Pure rendering after authorized descriptor scan; no filesystem writes."""
    if type(rpc_id) not in (str, int) or (isinstance(rpc_id, str) and len(rpc_id) > 128):
        raise InputError("invalid_review_rpc_id")
    from .telemetry import BudgetTelemetry, POLICY
    from .adaptive_wire import TransportCapabilities
    adaptive = type(arguments) is dict and arguments.get("budget_policy") == POLICY
    telemetry = None
    caps = None
    request = arguments
    if adaptive:
        caps = TransportCapabilities.from_dict(arguments.get("transport"))
        budget = arguments.get("return_bytes")
        if type(budget) is not int or not 1024 <= budget <= caps.max_frame_bytes:
            raise InputError("explicit_review_frame_capability_required")
        request = {key: value for key, value in arguments.items()
                   if key not in {"budget_policy", "transport"}}
        request["return_bytes"] = 1024
    project, include, seconds, legacy_budget = validate_request(projects, request)
    budget = budget if adaptive else legacy_budget
    if adaptive:
        telemetry = BudgetTelemetry(run_id=str(rpc_id), project_id=project.project_id)
    output_root = Path(output_root).absolute()
    source_root = Path(project.root).absolute()
    if (output_root == source_root or source_root in output_root.parents
            or output_root in source_root.parents):
        raise InputError("review_output_must_be_separate_from_source_tree")
    scanner = Scanner(project, include, seconds)
    if adaptive:
        with telemetry.stage("scan"):
            raw = scanner.run()
        telemetry.identity["snapshot_id"] = raw["snapshot_id"]
        from .evidence_v2 import V2EvidenceReader
        from .live_v2 import select_inventory, core_selection
        from .core_v2 import form_core
        from .wire_v2 import Admission, FrozenTransfer, publish
        from .consumer_v2 import consume_frame
        import threading
        import time
        reader = V2EvidenceReader(scanner, raw, time.monotonic() + seconds,
                                  threading.Event(), budget_policy=POLICY, telemetry=telemetry)
        with telemetry.stage("read"):
            package, _ = select_inventory(reader, budget_policy=POLICY)
        with telemetry.stage("verify"):
            reader.verify()
        with telemetry.stage("encode"):
            core = form_core(package, reader.raw_files, telemetry=telemetry,
                             **core_selection(package, "", budget_policy=POLICY))
            transfer = publish(core, Admission("2024-11-05", "A", rpc_id, budget, caps),
                               telemetry=telemetry)
        # Local rendering consumes and authenticates the entire V2 transfer, not
        # a legacy wire-limited projection or an unverified original pointer.
        with telemetry.stage("verify"):
            received = consume_frame(transfer.data, reader.raw_files,
                protocol_version="2024-11-05", request_id=rpc_id, scenario="A",
                return_bytes=budget, project_id=project.project_id,
                policy_id=POLICY, transport=caps)
        telemetry.consumer_result = "local_reassembly_and_source_authentication_complete"
        wire_frames = transfer.frames if isinstance(transfer, FrozenTransfer) else (transfer.data,)
        from .priority_v1 import _dependency_graph, _p2
        report = copy.deepcopy(raw)
        counts = {"p0": sum(len(e.get("imports", [])) for e in raw["entries"]),
                  "p1": sum(len(e.get("symbols", [])) for e in raw["entries"]),
                  "p2": sum(_p2(r) for e in raw["entries"] for r in e.get("calls", [])),
                  "p3": sum(not _p2(r) for e in raw["entries"] for r in e.get("calls", []))}
        report["delivery"] = {"version": 1, "state": "FULL",
            "source_status": raw["status"], "core_complete": received.status == "complete",
            "required_counts": {"p0": counts["p0"], "p1": counts["p1"]},
            "delivered_counts": counts, "omitted_counts": {key: 0 for key in counts},
            "p1_scope": "all_definitions_including_methods_and_nested"}
        report["graph"] = _dependency_graph(raw)
    else:
        raw = scanner.run()
        report = bounded_result(raw, budget,
            measure=lambda item: len(frame_bytes(item, rpc_id)),
            hard_limit=project.return_bytes,
            budget_unit="jsonrpc_utf8_bytes_including_newline")
        wire_frames = (frame_bytes(report, rpc_id),)
    delivery = report["delivery"]
    if (raw["status"] == "blocked" or report["status"] == "blocked"
            or delivery["state"] == "CORE_BLOCKED"
            or raw["freshness"] != "current_at_verification"):
        raise InputError("review_requires_current_nonblocked_core")
    entries = expand_entries(report)
    parsed = [entry for entry in entries if entry["status"] == "parsed"]
    if not parsed:
        raise InputError("zero_review_structure_hits")
    required = delivery["required_counts"]
    actual = {"p0": sum(len(e["imports"]) for e in parsed),
              "p1": sum(len(e["symbols"]) for e in parsed)}
    if actual != required:
        raise InputError("review_mandatory_core_incomplete")
    state = "READY_FOR_REVIEW" if delivery["core_complete"] else "PARTIAL_REVIEW"
    documents: dict[str, bytes] = {}
    module_index = []
    record_index = []

    def add(path, text):
        payload = text.encode("utf-8") if isinstance(text, str) else text
        if path in documents:
            raise InputError("duplicate_review_document")
        if path.endswith(".md") and (len(payload) > MAX_DOCUMENT_BYTES
                or len(payload.splitlines()) > 149):
            raise InputError("review_document_budget_exceeded")
        documents[path] = payload

    for entry in parsed:
        path = entry["path"]
        module_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        records = [("P0", item) for item in entry["imports"]]
        records += [("P1", item) for item in entry["symbols"]]
        pages = max(1, (len(records) + PAGE_RECORDS - 1) // PAGE_RECORDS)
        module_pages = []
        for page in range(pages):
            name = f"specs/module-{module_id}-{page + 1:03d}.md"
            module_pages.append(name)
            lines = ["# Python 模块静态契约草案", "", f"状态：{state}；尚未经人工接受。", "",
                f"来源：{source_link({'path': path, 'start_line': 1}, project.root, output_root, name, path)}",
                f"文件摘要：{entry['content_sha256']}",
                f"分页：{page + 1}/{pages}；完整名称、条件和来源记录见[溯源数据](../analysis/provenance.json)。", "",
                "## 不变量与未知", "",
                "- 本页只列语法声明；不证明运行时行为、业务职责或调用目标。",
                "- P0 全部导入与 P1 全部定义（含方法、嵌套定义）保留，不按公开性删减。",
                "- 参数、返回类型、导出闭包、动态绑定及业务不变量：本版本未推断。",
                "- 验证命令未知；未执行来源项目测试，不生成猜测命令。", "",
                "## 静态依赖与接口", ""]
            for tier, item in records[page * PAGE_RECORDS:(page + 1) * PAGE_RECORDS]:
                if tier == "P0":
                    label = f"导入 {item['module'] or '.'} / {item['name'] or '*module*'}"
                else:
                    label = f"{item['kind']} {item['qualified_name']}"
                link = source_link(item, project.root, output_root, name, label)
                lines.append(f"- {tier} {link}；行 {item['start_line']}—{item['end_line']}；来源 {item['source_id']}。")
                record_index.append({"tier": tier, "document": name,
                    "record": copy.deepcopy(item)})
            if not records:
                lines.append("- 该模块没有导入或定义；文件内容身份仍在覆盖集合中。")
            lines += ["", "## 三层阅读与剪枝", "",
                "- 第一层：阅读[项目指引](../AGENTS.md)。",
                "- 第二层：按需阅读本模块分页，不把所有模块一次性注入上下文。",
                "- 第三层：阅读[溯源报告](../analysis/PROVENANCE.md)和完整静态记录。",
                "- 可选调用仅在完整帧预算内交付；未交付数量和所有未完成事项保留在溯源中。", ""]
            add(name, "\n".join(lines))
        module_index.append({"path": path, "content_sha256": entry["content_sha256"],
            "documents": module_pages, "imports": len(entry["imports"]),
            "definitions": len(entry["symbols"])})

    # Paginate routing too; AGENTS never grows with the complete module inventory.
    routing_pages = []
    routes = [(item["path"], name) for item in module_index for name in item["documents"]]
    for offset in range(0, len(routes), PAGE_RECORDS):
        name = f"specs/index-{offset // PAGE_RECORDS + 1:03d}.md"
        routing_pages.append(name)
        lines = ["# 模块导航", "", f"状态：{state}；待人工审核。", ""]
        lines += [f"- [{display(path)}]({PurePosixPath(target).name})" for path, target in routes[offset:offset + PAGE_RECORDS]]
        lines += ["", "完整导航见[溯源数据](../analysis/provenance.json)。", ""]
        add(name, "\n".join(lines))
    first_index = routing_pages[0]
    agents = ["# Python 棕地项目阅读指引（待审核）", "", f"状态：{state}。", "",
        f"项目标识：{display(project.project_id)}；快照：{raw['snapshot_id']}。", "",
        "## 权限与证据", "",
        "- 这是独立输出，不是源项目已加载的规则；不包含源项目写入或命令执行授权。",
        "- 不导入或执行源项目，不联网，不训练模型，不安装编译器或第三方库。",
        "- 仅 Python 语法结构已覆盖；其他语言、资产、解析失败和排除边界保留为未知。",
        "- 语法声明不等于业务事实、测试通过、运行时依赖或语义零丢失。", "",
        "## 三层按需阅读", "",
        "1. 第一层：本项目指引；环境、构建和测试入口保持未知，不执行猜测命令。",
        f"2. 第二层：从[模块导航]({first_index})选择模块静态契约分页。",
        "3. 第三层：按需读取[溯源报告](analysis/PROVENANCE.md)与[溯源数据](analysis/provenance.json)。", "",
        "## 完整性边界", "",
        f"- 已解析文件：{len(parsed)}；P0 导入：{actual['p0']}；P1 全部定义：{actual['p1']}。",
        f"- 结构交付状态：{delivery['state']}；核心完整标识：{delivery['core_complete']}。",
        "- 可选调用允许剪枝；不据此声称全部调用或业务语义完整。",
        "- 源版本新鲜度仅对应本次扫描最后校核时点，不保证后续工作树不变。",
        "- 不生成或覆盖活动计划、事实基线、已接受 ADR、宿主桥接或来源项目文档。", ""]
    add("AGENTS.md", "\n".join(agents))
    provenance = {"review_version": VERSION, "review_state": state,
        "project_id": project.project_id, "snapshot_id": raw["snapshot_id"],
        "freshness": raw["freshness"], "requested_scope": list(include),
        "coverage": raw["coverage"], "delivery": delivery,
        "source_unfinished": raw["unfinished"], "delivery_unfinished": report["unfinished"],
        "inventory": [{key: copy.deepcopy(entry[key]) for key in
            ("path", "kind", "status", "reason", "size_bytes", "content_sha256") if key in entry}
            for entry in raw["entries"]],
        "module_index": module_index, "routing_pages": routing_pages,
        "mandatory_records": record_index,
        "retained_calls": [copy.deepcopy(call) for entry in parsed for call in entry["calls"]],
        "dependency_graph": report["graph"],
        "unknowns": ["runtime_behavior", "business_invariants", "build_test_commands",
            "export_resolution", "whole_program_semantics", "other_language_frontends"],
        "observations": raw["observations"], "source_writes": 0,
        "source_instructions": "untrusted_data_not_authority"}
    if adaptive:
        provenance["review_version"] = "python-brownfield-review-adaptive-v1"
        provenance["policy"] = POLICY
        provenance["relationships"] = received.package["relations"]
        provenance["authenticated_package"] = "analysis/knowledge-package.json"
        provenance["transport_frames"] = [f"analysis/frame-{i + 1:04d}.json"
                                          for i in range(len(wire_frames))]
        add("analysis/knowledge-package.json", received.package_bytes + b"\n")
        add("analysis/telemetry.json", json_bytes(telemetry.snapshot()) + b"\n")
        for i, payload in enumerate(wire_frames):
            add(f"analysis/frame-{i + 1:04d}.json", payload)
    add("analysis/provenance.json", json_bytes(provenance) + b"\n")
    add("analysis/structure.json", json_bytes(report) + b"\n")
    wire = b"".join(wire_frames)
    add("analysis/frame.json", wire)
    add("analysis/PROVENANCE.md", "\n".join([
        "# Python 蒸馏与标准文档溯源报告", "", f"状态：{state}；生成成功不代表业务审核通过。", "",
        "## 可复用能力与语言边界", "",
        "- Python：复用标准库 AST、描述符只读扫描、P0/P1 保留及 P2/P3 可选预算剪枝。",
        "- TypeScript、Go、Java、C#、Rust、C、C++：本次未实现，也未验证。",
        "- 18,869 字节历史样本仅证明其当时核心记录交付，不证明普遍最小值或语义零丢失。", "",
        "## 本次产物", "",
        f"- 完整转义 JSON-RPC 传输：{len(wire)} 字节，单帧硬上限：{budget} 字节；帧数：{len(wire_frames)}。",
        "- [结构结果](structure.json)保留现有版本契约；[完整结构帧](frame.json)包含结尾换行。",
        "- [溯源数据](provenance.json)包含文件分母、内容摘要、P0/P1 全部记录及其模块分页定位。",
        "- 每个核心记录对应模块文档；完整名称和条件在数据中保存，长名称预览允许缩短。",
        "- 核心必保、可选调用优先级及预算由既有程序决定，不由模型改写。", "",
        "## 待审核与未覆盖", "",
        "- 源扫描未完成项与交付剪枝项分别完整保留，不静默删除。",
        "- 不生成业务不变量、运行命令或已接受历史决策；这些需要独立证据和审核。",
        "- 文档预算与结构帧预算分开：文档分页不证明任意项目整体可以塞进 24KB。",
        "- 本目录不自动发布到源项目，不修改源文档，不建立第二份任务进度账本。", ""]))
    if sum(len(payload) for payload in documents.values()) > MAX_BUNDLE_BYTES:
        raise InputError("review_bundle_budget_exceeded")
    return ReviewBundle(documents, copy.deepcopy(report), str(source_root),
                        str(output_root), len(wire), state)


def export_review(bundle, artifact_root, output):
    """Exclusive new-directory publication; no overwrite or atomic-bundle claim.

Failure may leave a partial directory without manifest.json. It is not reused.
All traversals reject symlinks. The CLI fixes artifact_root to its own project.
"""
    output = relative_path(output)
    if not output.startswith(OUTPUT_PREFIX + "/") or len(PurePosixPath(output).parts) != 3:
        raise InputError("review_output_requires_new_named_review_directory")
    expected = str(Path(artifact_root).absolute() / output)
    if expected != bundle.output_root:
        raise InputError("review_output_binding_changed")
    source_root = Path(bundle.source_root).absolute()
    output_root = Path(expected)
    if (output_root == source_root or source_root in output_root.parents
            or output_root in source_root.parents):
        raise InputError("review_output_must_be_separate_from_source_tree")
    if bundle.state not in {"READY_FOR_REVIEW", "PARTIAL_REVIEW"}:
        raise InputError("invalid_review_state")
    if not bundle.documents or sum(len(item) for item in bundle.documents.values()) > MAX_BUNDLE_BYTES:
        raise InputError("review_bundle_budget_exceeded")
    for name, payload in bundle.documents.items():
        path = relative_path(name)
        parts = PurePosixPath(path).parts
        if (path != name or type(payload) is not bytes
                or (path != "AGENTS.md" and (len(parts) != 2
                    or parts[0] not in {"specs", "analysis"}
                    or not parts[1].endswith((".md", ".json"))))):
            raise InputError("invalid_review_artifact_path")
        if path.endswith(".md") and (len(payload) > MAX_DOCUMENT_BYTES
                or len(payload.splitlines()) > 149):
            raise InputError("review_document_budget_exceeded")
    root_fd = open_root(str(Path(artifact_root).absolute()))
    descriptors = [root_fd]
    try:
        parent = root_fd
        for part in PurePosixPath(OUTPUT_PREFIX).parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
            descriptors.append(parent)
        leaf = PurePosixPath(output).name
        os.mkdir(leaf, mode=0o700, dir_fd=parent)  # Existing directory/file/link fails.
        review_fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
        descriptors.append(review_fd)
        for folder in ("specs", "analysis"):
            os.mkdir(folder, mode=0o700, dir_fd=review_fd)
        hashes = {}
        for name, payload in sorted(bundle.documents.items()):
            path = relative_path(name)
            if (path not in {"AGENTS.md"} and not path.startswith(("specs/", "analysis/"))
                    or len(PurePosixPath(path).parts) > 2):
                raise InputError("invalid_review_artifact_path")
            directory = os.dup(review_fd)
            try:
                parts = PurePosixPath(path).parts
                if len(parts) == 2:
                    os.close(directory)
                    directory = open_relative(review_fd, parts[0], directory=True)
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o600, dir_fd=directory)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(directory)
            fd = open_relative(review_fd, path)
            with os.fdopen(fd, "rb") as stream:
                saved = stream.read(MAX_BUNDLE_BYTES + 1)
            if saved != payload:
                raise InputError("review_saved_content_mismatch")
            hashes[name] = hashlib.sha256(saved).hexdigest()
        manifest = {"review_version": VERSION, "review_state": bundle.state,
            "files": hashes, "structure_frame_bytes": bundle.wire_bytes,
            "snapshot_id": bundle.report["snapshot_id"], "source_writes": 0,
            "status": "SAVED_FOR_REVIEW", "publication": "exclusive_directory_not_atomic"}
        fd = os.open("manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o600, dir_fd=review_fd)
        payload = json_bytes(manifest) + b"\n"
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fd = open_relative(review_fd, "manifest.json")
        with os.fdopen(fd, "rb") as stream:
            if stream.read(len(payload) + 1) != payload:
                raise InputError("review_manifest_readback_failed")
        return manifest
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线 Python 三层待审核文档；不修改来源项目")
    parser.add_argument("--config", default="config/projects.json")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--include", nargs="+")
    parser.add_argument("--return-bytes", type=int, default=24576)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    artifact_root = Path(__file__).resolve().parents[2]
    try:
        output = relative_path(args.output)
        request = {"contract_version": 1, "project_id": args.project_id,
                   "return_bytes": args.return_bytes}
        if args.include:
            request["include"] = args.include
        bundle = build_review(load_projects(args.config), request, artifact_root / output)
        manifest = export_review(bundle, artifact_root, output)
        print(json_bytes({"status": manifest["status"], "review_state": bundle.state,
            "output": output, "file_count": len(manifest["files"]) + 1,
            "structure_frame_bytes": bundle.wire_bytes, "source_writes": 0}).decode("utf-8"))
        return 0 if bundle.state == "READY_FOR_REVIEW" else 2
    except (InputError, OSError, ValueError, RecursionError) as error:
        print(json_bytes({"status": "BLOCKED", "reason": str(error),
            "source_writes": 0, "output_may_be_partial": True}).decode("utf-8"))
        return 2


if __name__ == "__main__":
    sys.exit(main())
