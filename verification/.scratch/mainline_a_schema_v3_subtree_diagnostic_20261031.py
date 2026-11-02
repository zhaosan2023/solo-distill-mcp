"""阶段四新子结构表示的必要单进程诊断，不是冷预检或原服务异常复现。
问题：首个 dictionary-columnar-v3-2 冷链收到通用服务拒绝；当前完整发布
是否在准备、任务、子结构编码、实际帧容量或期限阶段失败？
范围：获准来源的单进程准备、空候选任务、完整生产发布及来源复核。
不启动服务或网关、不联网、不执行或写入来源、不保存业务 trace，
不占用真实三样本或唯一在线接纳；不运行性能剖析器。
前提：活动计划版本 6、执行契约修订 5、ADR 修订 4、既有配置与依赖。
验收：真实退出码及累计不超过 8192 字节的阶段、尺寸与异常观测。
内部 27 秒、外层命令 30 秒；测量开销属于本诊断，不冒充冷链耗时。
历史旧表示诊断及失败保持独立，不把本诊断当成原服务根因证明。
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import mainline_a_schema_v3_phase4_20261031 as support
from live_v2_driver import request
from solo_distill import wire_v3
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.extractor import load_projects
from solo_distill.macro_v3 import canonical
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.task_v3 import build_task
from solo_distill.wire_v3 import Admission, publish_v3


@snapshot_scope
def probe():
    started = time.monotonic()
    deadline = started + 27
    cancelled = threading.Event()
    stages = {}
    measurements = {}
    current_stage = "binding"
    emitted_bytes = 0

    def emit(value):
        nonlocal emitted_bytes
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
        size = len(raw.encode("utf-8")) + 1
        if emitted_bytes + size > 8192:
            raise ValueError("subtree_diagnostic_output_budget")
        emitted_bytes += size
        print(raw, flush=True)

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("subtree_diagnostic_internal_deadline")

    def stage(name, action):
        nonlocal current_stage
        current_stage = name
        check()
        emit({"stage_start": name})
        begin = time.monotonic()
        try:
            return action()
        finally:
            stages[name] = time.monotonic() - begin
            emit({"stage_end": name, "seconds": stages[name]})

    original_pack = wire_v3.subtree_pack
    original_encode = wire_v3._encode

    def measured_pack(value, **kwargs):
        begin = time.monotonic()
        try:
            represented = original_pack(value, **kwargs)
        except Exception as error:
            emit({"operation": "production_subtree_pack", "status": "blocked",
                  "error": type(error).__name__, "reason": str(error)[:256],
                  "seconds": time.monotonic() - begin})
            raise
        measurements["subtree_pack_seconds"] = time.monotonic() - begin
        check()
        measurements["publication_bytes"] = len(canonical(value))
        measurements["represented_json_bytes"] = len(canonical(represented))
        measurements["dictionary_strings"] = len(represented["strings"])
        measurements["dictionary_schemas"] = len(represented["schemas"])
        measurements["subtree_entries"] = len(represented["subtrees"])
        emit({"operation": "production_subtree_pack", "measurements": measurements})
        check()
        return represented

    def measured_encode(report, response_id, **kwargs):
        encoded = original_encode(report, response_id, **kwargs)
        if report.get("kind") == "sealed_knowledge":
            measurements["decoded_response_bytes"] = len(encoded)
            measurements["decoded_limit_bytes"] = support.CAPS["max_decoded_bytes"]
            measurements["decoded_over_limit_bytes"] = max(
                0, len(encoded) - support.CAPS["max_decoded_bytes"])
            emit({"operation": "actual_response_encoding", "measurements": measurements})
        check()
        return encoded

    try:
        bound = stage("binding", support.binding)
        if (bound["plan_contract_version"] != 6
                or bound["execution_contract_revision"] != 5
                or bound["adr_revision"] != 4
                or bound["wire_encoding"] != "dictionary-columnar-v3-2"):
            raise ValueError("subtree_diagnostic_binding_conflict")
        now = int(time.time() * 1000)
        arguments = request(support.INSTANCE + "-subtree-diagnostic-20261031T0900Z", now)
        project = load_projects("config/projects.json")[arguments["project_id"]]
        prepared = stage("prepare", lambda: prepare_snapshot(project=project,
            include=tuple(arguments.get("include", project.include)),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=cancelled, tick=check))
        emit({"frozen_file_count": len(prepared.raw_files),
              "preparation_stage_seconds": prepared.metrics["stage_seconds"]})
        trusted = {"project_id": project.project_id,
            "snapshot_id": prepared.report["snapshot_id"],
            "required_core_unit_ids": prepared.selection["core_unit_ids"],
            "selection_id": prepared.selection["selection_id"],
            "request_id": arguments["request_id"], "question": arguments["question"],
            "dimensions": arguments["dimensions"], "post": prepared.post,
            "frozen_coverage": prepared.report["coverage"], "failures": [], "tick": check}
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        task = stage("task", lambda: build_task(candidate, prepared.core,
                                               prepared.raw_files, **trusted))
        admission = Admission("2024-11-05", "A", 3, arguments["return_bytes"],
            TransportCapabilities.from_dict(support.CAPS),
            wire_encoding="dictionary-columnar-v3-2")
        with patch.object(wire_v3, "subtree_pack", side_effect=measured_pack), \
                patch.object(wire_v3, "_encode", side_effect=measured_encode):
            frame = stage("publish", lambda: publish_v3(prepared.core, task,
                prepared.raw_files, admission, candidate=candidate, **trusted))
        stage("source_verify", prepared.verify_sources)
        check()
        emit({"status": "diagnostic_completed_not_preflight_acceptance", "binding": bound,
              "stages_seconds": stages, "measurements": measurements,
              "transfer_bytes": len(frame.data), "elapsed_seconds": time.monotonic() - started,
              "gateway_requests": 0, "service_processes_started": 0,
              "preflight_samples_started": 0, "online_admissions": 0,
              "not_proven": ["cold_chain", "independent_consumption", "service_failure_cause",
                             "human_semantics", "host_loading"],
              "timing_scope": "single_process_diagnostic_including_measurement_overhead"})
        return 0
    except Exception as error:
        emit({"status": "blocked", "failure_stage": current_stage,
              "error": type(error).__name__, "reason": str(error)[:256],
              "stages_seconds": stages, "measurements": measurements,
              "elapsed_seconds": time.monotonic() - started,
              "failure_cause_scope": "single_process_diagnostic_not_original_service_exception",
              "gateway_requests": 0, "service_processes_started": 0,
              "preflight_samples_started": 0, "online_admissions": 0})
        return 2


if __name__ == "__main__":
    raise SystemExit(probe())
