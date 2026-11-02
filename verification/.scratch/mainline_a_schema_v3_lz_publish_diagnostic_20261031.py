"""阶段 4 当前 LZ 发布链的必要有界诊断，不作为真实冷预检通过证明。
问题：当前首样本通用服务拒绝且 trace 缺失；单进程完整生产链的
准备、任务、发布认证、LZ 计量与压缩、分片及来源复核分别耗时多少？
范围：读取当前尝试失败请求的导航问题和授权来源；不启动服务或网关，
不联网、不执行或写入来源、不生成业务 trace、不占用三样本或在线接纳。
前提：计划版本 6、执行契约修订 6、ADR 修订 5、显式 lz-json-v3-1；
现有标准库、配置、全部资源硬限与取消认证保持不变，不使用剖析器。
验收：真实退出码、累计至多 8192 字节的阶段和操作计时及完整帧尺寸。
内部 27 秒、外层 30 秒；异常仍阻塞，测量开销不冒充冷链耗时，
单进程结果不能追认原服务拒绝的根因、原服务退出或独立消费通过。
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import mainline_a_schema_v3_phase4_20261031 as support
from solo_distill import lz_v3, wire_v3
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.extractor import load_projects
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.task_v3 import build_task
from solo_distill.wire_v3 import Admission, publish_v3


@snapshot_scope
def probe():
    started = time.monotonic()
    deadline = started + 27
    cancelled = threading.Event()
    stages, operations, measurements = {}, {}, {}
    current_stage = "binding"
    emitted_bytes = 0

    def emit(value):
        nonlocal emitted_bytes
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
        size = len(raw.encode("utf-8")) + 1
        if emitted_bytes + size > 8192:
            raise ValueError("lz_publish_diagnostic_output_budget")
        emitted_bytes += size
        print(raw, flush=True)

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("lz_publish_diagnostic_internal_deadline")

    def stage(name, action):
        nonlocal current_stage
        current_stage = name
        check()
        emit({"stage_start": name})
        begin = time.monotonic()
        try:
            result = action()
            check()
            return result
        finally:
            stages[name] = time.monotonic() - begin
            emit({"stage_end": name, "seconds": stages[name]})

    def measured(name, function):
        def wrapped(*args, **kwargs):
            begin = time.monotonic()
            entry = operations.setdefault(name, {"calls": 0, "seconds": 0.0,
                                                 "completed_calls": 0})
            entry["calls"] += 1
            try:
                result = function(*args, **kwargs)
                entry["completed_calls"] += 1
                if name == "encode" and args[0].get("kind") == "sealed_knowledge":
                    measurements["decoded_response_bytes"] = len(result)
                elif name == "compress":
                    measurements["compression_input_bytes"] = len(args[0])
                    measurements["compression_token_bytes"] = len(result)
                return result
            finally:
                entry["seconds"] += time.monotonic() - begin
        return wrapped

    try:
        bound = stage("binding", support.binding)
        if (bound["plan_contract_version"] != 6
                or bound["execution_contract_revision"] != 6
                or bound["adr_revision"] != 5
                or bound["wire_encoding"] != "lz-json-v3-1"):
            raise ValueError("lz_publish_diagnostic_binding_conflict")
        arguments = support.load(support.PARENT + "/" + support.attempt_name(
            "preflight-1", "request.json"))
        if (arguments["operation"] != "preflight"
                or arguments["contract_version"] != 3
                or arguments["wire_encoding"] != bound["wire_encoding"]
                or arguments["transport"] != support.CAPS):
            raise ValueError("lz_publish_diagnostic_request_conflict")
        arguments["request_id"] = support.INSTANCE + "-lz-publish-diagnostic-20261031T1203Z"
        project = load_projects("config/projects.json")[arguments["project_id"]]
        prepared = stage("prepare", lambda: prepare_snapshot(project=project,
            include=tuple(arguments.get("include", project.include)),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=cancelled, tick=check))
        raw_files, report, selection = prepared.raw_files, prepared.report, prepared.selection
        emit({"frozen_file_count": len(raw_files),
              "preparation_stage_seconds": prepared.metrics["stage_seconds"],
              "syntax_reuse": prepared.metrics["syntax"]})
        trusted = {"project_id": project.project_id, "snapshot_id": report["snapshot_id"],
            "required_core_unit_ids": selection["core_unit_ids"],
            "selection_id": selection["selection_id"], "request_id": arguments["request_id"],
            "question": arguments["question"], "dimensions": arguments["dimensions"],
            "post": prepared.post, "frozen_coverage": report["coverage"], "failures": [],
            "tick": check}
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        task = stage("task", lambda: build_task(candidate, prepared.core, raw_files, **trusted))
        admission = Admission("2024-11-05", "A", 3, arguments["return_bytes"],
            TransportCapabilities.from_dict(support.CAPS), wire_encoding=bound["wire_encoding"])
        with patch.object(wire_v3, "build_publication", measured("publication", wire_v3.build_publication)), \
             patch.object(wire_v3, "_encode", measured("encode", wire_v3._encode)), \
             patch.object(wire_v3, "_chunks", measured("chunks", wire_v3._chunks)), \
             patch.object(lz_v3, "_compress", measured("compress", lz_v3._compress)), \
             patch.object(lz_v3._Budget, "inspect", new=measured("lz_budget_inspect", lz_v3._Budget.inspect)):
            frame = stage("publish", lambda: publish_v3(prepared.core, task, raw_files,
                admission, candidate=candidate, **trusted))
        stage("source_verify", prepared.verify_sources)
        measurements["transfer_bytes"] = len(frame.data)
        measurements["decoded_limit_bytes"] = support.CAPS["max_decoded_bytes"]
        check()
        emit({"status": "diagnostic_completed_not_preflight_acceptance", "binding": bound,
              "stages_seconds": stages, "operations": operations, "measurements": measurements,
              "elapsed_seconds": time.monotonic() - started,
              "gateway_requests": 0, "service_processes_started": 0,
              "preflight_samples_started": 0, "online_admissions": 0,
              "timing_scope": "single_process_including_measurement_overhead_nested_timings_not_additive"})
        return 0
    except Exception as error:
        emit({"status": "blocked", "failure_stage": current_stage,
              "error": type(error).__name__, "reason": str(error)[:256],
              "stages_seconds": stages, "operations": operations, "measurements": measurements,
              "elapsed_seconds": time.monotonic() - started,
              "failure_cause_scope": "diagnostic_not_original_service_exception"})
        return 2


if __name__ == "__main__":
    raise SystemExit(probe())
