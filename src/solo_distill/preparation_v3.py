"""显式 V3 服务及冷预检的共用可信来源准备链。

只读取授权来源，不执行或写入来源，不访问网关或独立 Oracle。
操作内解析复用不代替认证；冻结选择及结构通过不证明独立性、
人工语义、实际发送、证据落盘或外层进程期限。
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import time

from .core_v3 import SealedCore, make_seal
from .distillation import utf8_json
from .evidence import EvidenceBlocked
from .evidence_v3 import V3EvidenceReader
from .extractor import Project, Scanner, relative_path, within
from .macro_v3 import canonical
from .package_v3 import build_package
from .scouting_v3 import COVERAGE_VERSION, VERSION, select_prompt_v3
from .snapshot_v2 import snapshot_metrics, snapshot_scope
from .syntax_v2 import extract_units
from .task_v3 import _question_identity, build_prompt


@dataclass(frozen=True)
class PreparedSnapshot:
    """规范字节及来源私有上下文；公开可变视图均为独立副本。"""

    core: SealedCore
    post: bytes
    _raw_items: tuple
    _report_bytes: bytes
    _selection_bytes: bytes
    _messages_bytes: bytes
    _metrics_bytes: bytes
    _reader: V3EvidenceReader

    @property
    def raw_files(self):
        return dict(self._raw_items)

    @property
    def report(self):
        return json.loads(self._report_bytes)

    @property
    def selection(self):
        return json.loads(self._selection_bytes)

    @property
    def messages(self):
        return json.loads(self._messages_bytes)

    @property
    def package(self):
        return self.core.document["package"]

    @property
    def metrics(self):
        return json.loads(self._metrics_bytes)

    def verify_sources(self):
        """仍使用原操作取消、期限及外部守卫，不继承历史新鲜度。"""
        self._reader.verify()


def _metric_bytes(value):
    # 耗时是观察值，不进入规范知识或选择身份；允许有限浮点。
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


@snapshot_scope
def prepare_snapshot(*, project, include, question, dimensions, deadline,
                     cancelled, frozen_selection=None, selection_version=VERSION,
                     tick=None):
    """扫描、秘密检查、选择、双层构建、认证及精确 POST 编码。

    外部冻结选择若提供，必须与本次完整复算逐规范字节相等。
    index 是完整范围的单元索引预热，selection 包含导航关系构建和
    问题选择；scan 包含扫描器自身解析及新鲜度检查，各阶段不重叠。
    真实独立选择及双层消费由调用方使用独立资产另行执行。
    """
    if (type(project) is not Project or type(include) is not tuple
            or not 1 <= len(include) <= 32
            or any(type(path) is not str or not path
                   or relative_path(path) != path for path in include)
            or len(set(include)) != len(include)
            or any(not any(within(path, allowed) for allowed in project.include)
                   for path in include)):
        raise ValueError("v3_preparation_authorized_scope")
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or not callable(getattr(cancelled, "is_set", None))
            or (tick is not None and not callable(tick))):
        raise ValueError("v3_preparation_guard_context")
    _question_identity(question, dimensions)
    if (type(selection_version) is not str
            or selection_version not in (VERSION, COVERAGE_VERSION)):
        raise ValueError("v3_preparation_unknown_selection_version")
    # 版本来自包外调用方，不从冻结对象自报字段推定授权。
    # 隔离调用方对象，但保留精确 POST 所需的字段顺序；身份另用 canonical。
    dimensions = json.loads(utf8_json(dimensions))
    frozen_bytes = None if frozen_selection is None else canonical(frozen_selection)
    started = time.monotonic()
    timings = {}

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise EvidenceBlocked("v3_preparation_cancelled_or_deadline")
        if tick is not None:
            tick()
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise EvidenceBlocked("v3_preparation_cancelled_or_deadline")

    @contextmanager
    def stage(name):
        check()
        begin = time.monotonic()
        yield
        check()
        timings[name] = time.monotonic() - begin

    check()
    scanner = Scanner(project, include, project.max_seconds)
    scanner.deadline = min(scanner.deadline, deadline)
    scan_tick = scanner.tick

    def checked_scan_tick():
        check()
        scan_tick()

    # 只绑定本操作的实例回调，不修改旧扫描器类或默认行为。
    scanner.tick = checked_scan_tick
    with stage("scan"):
        report = scanner.run()
    reader = V3EvidenceReader(scanner, report, deadline, cancelled)
    reader_tick = reader.tick

    def checked_reader_tick():
        check()
        reader_tick()

    reader.tick = checked_reader_tick
    with stage("read"):
        raw_files = reader.inventory()
    with stage("index"):
        for path, raw in sorted(raw_files.items()):
            check()
            extract_units(path, raw)
            check()
    selection_trusted = {"project_id": reader.project_id,
                         "snapshot_id": reader.snapshot_id,
                         "question": question, "dimensions": dimensions,
                         "frozen_coverage": report["coverage"],
                         "selection_version": selection_version}
    with stage("selection"):
        selected_messages, selected_post, selection = select_prompt_v3(
            raw_files, **selection_trusted, tick=check)
        selection_bytes = canonical(selection)
        if frozen_bytes is not None and frozen_bytes != selection_bytes:
            raise EvidenceBlocked("v3_preparation_frozen_selection_conflict")
    structure_trusted = {"project_id": reader.project_id,
                         "snapshot_id": reader.snapshot_id,
                         "required_core_unit_ids": selection["core_unit_ids"],
                         "selection_id": selection["selection_id"]}
    with stage("build"):
        package = build_package(raw_files, **structure_trusted, tick=check)
    with stage("authenticate"):
        core = make_seal(package, raw_files, **structure_trusted, tick=check)
    with stage("encode"):
        messages, post, allowed = build_prompt(core, raw_files,
            **structure_trusted, question=question, dimensions=dimensions,
            frozen_coverage=report["coverage"], selection_version=selection_version, tick=check)
        if (post != selected_post
                or canonical(messages) != canonical(selected_messages)
                or allowed != selection["witness"]["allowed_source_ids"]):
            raise EvidenceBlocked("v3_preparation_selection_local_prompt_conflict")
    with stage("source_verify"):
        reader.verify()
    check()
    metrics = {"stage_seconds": timings,
               "elapsed_seconds": time.monotonic() - started,
               "syntax": snapshot_metrics(),
               "timing_scope": "preparation_only_excludes_independent_consumption_and_save",
               "selection_scope": "navigation_relations_and_question_selection"}
    return PreparedSnapshot(core=core, post=post,
        _raw_items=tuple(sorted(raw_files.items())),
        _report_bytes=utf8_json(report), _selection_bytes=selection_bytes,
        _messages_bytes=utf8_json(messages), _metrics_bytes=_metric_bytes(metrics),
        _reader=reader)
