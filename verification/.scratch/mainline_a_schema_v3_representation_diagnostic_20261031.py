"""阶段 4 必要诊断；不是冷预检或容量验收。
问题：显式新表示真实冷链在最终清单前 EOF，发布是否触及展开、帧或期限门禁？
范围：当前获准来源的单进程准备、空候选任务和生产发布；不启动服务、
不联网、不执行或写入来源、不生成业务 trace、不占用三样本或在线接纳。
前提：计划版本 6、执行契约修订 4、ADR 修订 3 与既有配置和依赖。
验收：有界阶段耗时、实际编码尺寸或明确失败阶段；异常及超时保持阻塞。
不使用性能剖析器，避免把剖析开销误当作真实冷链耗时。
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
    stages = {}
    measurements = {}
    current_stage = "binding"
    cancelled = threading.Event()

    def emit(value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(raw.encode("utf-8")) > 8192:
            raise ValueError("representation_diagnostic_output_budget")
        print(raw, flush=True)

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("representation_diagnostic_internal_deadline")

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

    original_pack = wire_v3.pack
    original_encode = wire_v3._encode

    def measured_pack(value, **kwargs):
        begin = time.monotonic()
        try:
            represented = original_pack(value, **kwargs)
        except Exception as error:
            emit({"operation": "production_representation_pack", "status": "blocked",
                  "error": type(error).__name__, "reason": str(error)[:256],
                  "seconds": time.monotonic() - begin})
            raise
        measurements["representation_pack_seconds"] = time.monotonic() - begin
        measurements["publication_bytes"] = len(canonical(value))
        measurements["represented_json_bytes"] = len(canonical(represented))
        measurements["dictionary_strings"] = len(represented["strings"])
        measurements["dictionary_schemas"] = len(represented["schemas"])
        emit({"operation": "production_representation_pack", "measurements": measurements})
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
        now = int(time.time() * 1000)
        arguments = request(support.INSTANCE + "-representation-diagnostic-20261031T0755Z", now)
        project = load_projects("config/projects.json")[arguments["project_id"]]
        prepared = stage("prepare", lambda: prepare_snapshot(project=project,
            include=tuple(arguments.get("include", project.include)),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=cancelled, tick=check))
        emit({"frozen_file_count": len(prepared.raw_files),
              "preparation_stage_seconds": prepared.metrics["stage_seconds"]})
        trusted = {"project_id": project.project_id, "snapshot_id": prepared.report["snapshot_id"],
            "required_core_unit_ids": prepared.selection["core_unit_ids"],
            "selection_id": prepared.selection["selection_id"],
            "request_id": arguments["request_id"], "question": arguments["question"],
            "dimensions": arguments["dimensions"], "post": prepared.post,
            "frozen_coverage": prepared.report["coverage"], "failures": [], "tick": check}
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        task = stage("task", lambda: build_task(candidate, prepared.core, prepared.raw_files, **trusted))
        admission = Admission("2024-11-05", "A", 3, arguments["return_bytes"],
            TransportCapabilities.from_dict(support.CAPS), wire_encoding=support.WIRE_ENCODING)
        with patch.object(wire_v3, "pack", side_effect=measured_pack), \
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
                             "human_semantics", "host_loading"]})
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
