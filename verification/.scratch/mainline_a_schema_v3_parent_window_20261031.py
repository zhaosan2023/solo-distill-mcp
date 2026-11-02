"""阶段 4 父进程窗口诊断，不运行真实样本或在线业务。

问题：父进程准备和独立选择分别消耗多少窗口，启动服务前还剩多少时间？
范围：当前 selection-reuse-20261031T1234Z 的失败请求、授权来源、生产准备
及独立选择完整差分；不启动子进程、不联网、不执行或写入来源、不保存业务 trace。
前提：活动计划版本 6、执行契约修订 6、ADR 修订 5、既有依赖与配置；
生产及独立操作内索引保持分离，不缓存跨操作通过状态。
验收：实际退出状态、分段耗时、来源数量、两侧解析计量、完整选择及 POST 相等；
内部 27 秒、外层 30 秒、累计输出不超过 8192 字节。诊断不证明真实冷预检，
不追认原服务异常原因或退出，不占用三样本或唯一在线接纳。
"""
from __future__ import annotations

import json
import threading
import time

import mainline_a_schema_v3_phase4_20261031 as support
from schema_v3_selection_oracle import expected_selection
from schema_v3_snapshot_oracle import oracle_metrics, oracle_scope
from solo_distill.extractor import load_projects
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_metrics, snapshot_scope

REQUEST = ("verification/mainline-a-schema-v3-20261030T095713Z/"
           "preflight-1.selection-reuse-20261031T1234Z.request.json")


@snapshot_scope
@oracle_scope
def probe():
    started = time.monotonic()
    deadline = started + 27
    cancelled = threading.Event()
    stages = {}
    current = "binding"
    output_bytes = 0

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("parent_window_internal_deadline")

    def emit(value):
        nonlocal output_bytes
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
        output_bytes += len(raw.encode("utf-8")) + 1
        if output_bytes > 8192:
            raise ValueError("parent_window_output_budget")
        print(raw, flush=True)

    def stage(name, action):
        nonlocal current
        current = name
        check()
        emit({"stage_start": name, "remaining_seconds": deadline - time.monotonic()})
        begin = time.monotonic()
        try:
            value = action()
            check()
            return value
        finally:
            stages[name] = time.monotonic() - begin
            emit({"stage_end": name, "seconds": stages[name]})

    try:
        bound = stage("binding", support.binding)
        if (bound["plan_contract_version"] != 6
                or bound["execution_contract_revision"] != 6
                or bound["adr_revision"] != 5
                or bound["wire_encoding"] != "lz-json-v3-1"):
            raise ValueError("parent_window_binding_conflict")
        arguments = support.load(REQUEST)
        if (arguments["contract_version"] != 3
                or arguments["operation"] != "preflight"
                or arguments["request_id"] != support.INSTANCE +
                   "-preflight-1-selection-reuse-20261031T1234Z"
                or arguments["wire_encoding"] != bound["wire_encoding"]
                or arguments["transport"] != support.CAPS):
            raise ValueError("parent_window_request_conflict")
        project = load_projects("config/projects.json")[arguments["project_id"]]
        prepared = stage("parent_prepare", lambda: prepare_snapshot(
            project=project, include=tuple(arguments.get("include", project.include)),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=cancelled, tick=check))
        raw_files, report = prepared.raw_files, prepared.report
        emit({"frozen_file_count": len(raw_files),
              "source_bytes": sum(map(len, raw_files.values())),
              "preparation_stage_seconds": prepared.metrics["stage_seconds"],
              "production_syntax": snapshot_metrics()})
        messages, post, selection = stage("independent_selection", lambda: expected_selection(
            raw_files, project_id=project.project_id, snapshot_id=report["snapshot_id"],
            question=arguments["question"], dimensions=arguments["dimensions"],
            frozen_coverage=report["coverage"], tick=check))
        if (messages != prepared.messages or post != prepared.post
                or selection != prepared.selection):
            raise ValueError("parent_window_complete_selection_conflict")
        remaining = deadline - time.monotonic()
        emit({"stage_observation": "before_service_launch",
              "elapsed_seconds": time.monotonic() - started,
              "remaining_internal_seconds": remaining,
              "independent_syntax": oracle_metrics(),
              "complete_selection_and_post_equal": True})
        stage("source_verify", prepared.verify_sources)
        emit({"status": "diagnostic_completed_not_preflight_acceptance",
              "binding": bound, "request_source": REQUEST,
              "stages_seconds": stages, "elapsed_seconds": time.monotonic() - started,
              "remaining_before_service_seconds": remaining,
              "production_syntax": snapshot_metrics(), "independent_syntax": oracle_metrics(),
              "service_processes_started": 0, "gateway_requests": 0,
              "preflight_samples_started": 0, "online_admissions": 0,
              "timing_scope": "single_process_measurement_overhead_included_not_original_failure"})
        check()
        return 0
    except Exception as error:
        emit({"status": "blocked", "failure_stage": current,
              "error": type(error).__name__, "reason": str(error)[:256],
              "stages_seconds": stages, "elapsed_seconds": time.monotonic() - started,
              "failure_scope": "diagnostic_not_original_service_exception"})
        return 2


if __name__ == "__main__":
    raise SystemExit(probe())
