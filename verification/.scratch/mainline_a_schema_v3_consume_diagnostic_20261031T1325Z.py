"""阶段 4 必要诊断：已写完完整帧后，生产消费的哪个步骤耗尽窗口？

范围：读取 selection-encoding-20261031T1313Z 的既有请求、trace 和投递
凭证；重新读取获准来源，核对完整选择、结构及 POST，再在内存重建
同一发布并计时生产消费。独立 Oracle 不修改，也不由本诊断替代。
不启动子进程或网关、不联网、不执行或写入来源、不保存业务资产，
不占用真实冷样本或在线接纳。既有失败现场及历史诊断保持不变。
前提：计划版本 6、执行契约修订 6、ADR 修订 5 和既有标准库环境。
验收：真实退出状态、累计不超过 8192 字节的阶段／操作计时，以及
重建传输与既有投递摘要及长度的比较。内部 27 秒、进程外层 30 秒。
退出 0 仅表示诊断完成；不证明原客户端消费、原服务自然退出、六份
保存、三次冷预检、独立 Oracle、在线业务或人工语义验收通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import math
from pathlib import Path
import threading
import time
from unittest.mock import patch

from solo_distill import consumer_v3, lz_v3
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.extractor import load_projects, open_root, open_relative
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.wire_v3 import Admission, publish_v3

ATTEMPT = "selection-encoding-20261031T1313Z"
INPUT_LIMIT = 16 * 1024 * 1024


def reports_match_except_elapsed(saved, fresh):
    """仅诊断比较排除独立扫描的耗时观察值；其余完整字段仍须相等。"""
    if type(saved) is not dict or type(fresh) is not dict:
        return False
    observations = []
    for report in (saved, fresh):
        value = report.get("observations")
        if type(value) is not dict or "elapsed_seconds" not in value:
            return False
        elapsed = value["elapsed_seconds"]
        if (type(elapsed) not in (int, float)
                or not math.isfinite(elapsed) or elapsed < 0):
            return False
        observations.append({key: item for key, item in value.items()
                             if key != "elapsed_seconds"})
    return ({**saved, "observations": observations[0]}
            == {**fresh, "observations": observations[1]})


@snapshot_scope
def probe():
    started = time.monotonic()
    deadline = started + 27
    stages, operations, measurements = {}, {}, {}
    current = "binding"
    emitted_bytes = 0

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("consume_diagnostic_internal_deadline")

    def emit(value):
        nonlocal emitted_bytes
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
        emitted_bytes += len(raw.encode("utf-8")) + 1
        if emitted_bytes > 8192:
            raise ValueError("consume_diagnostic_output_budget")
        print(raw, flush=True)

    def stage(name, action):
        nonlocal current
        current = name
        check()
        emit({"stage_start": name})
        begin = time.monotonic()
        try:
            value = action()
            check()
            return value
        finally:
            stages[name] = time.monotonic() - begin
            emit({"stage_end": name, "seconds": stages[name]})

    def measured(name, function):
        def wrapped(*args, **kwargs):
            begin = time.monotonic()
            entry = operations.setdefault(name, {"calls": 0, "completed_calls": 0,
                                                  "seconds": 0.0})
            entry["calls"] += 1
            try:
                result = function(*args, **kwargs)
                entry["completed_calls"] += 1
                return result
            finally:
                entry["seconds"] += time.monotonic() - begin
        return wrapped

    def read_json(path):
        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("consume_diagnostic_duplicate_json_key")
                result[key] = value
            return result

        def reject_constant(value):
            raise ValueError("consume_diagnostic_nonfinite_json")

        root = open_root(str(Path.cwd()))
        try:
            descriptor = open_relative(root, path)
            try:
                if os.fstat(descriptor).st_size > INPUT_LIMIT:
                    raise ValueError("consume_diagnostic_input_budget")
                raw = bytearray()
                while True:
                    check()
                    chunk = os.read(descriptor, min(65536, INPUT_LIMIT + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > INPUT_LIMIT:
                        raise ValueError("consume_diagnostic_input_budget")
            finally:
                os.close(descriptor)
        finally:
            os.close(root)
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        check()
        return value

    try:
        # 支撑入口仅在实际诊断时加载；纯内存比较测试不依赖草稿目录导入路径。
        import mainline_a_schema_v3_phase4_20261031 as support

        bound = stage("binding", support.binding)
        if bound["wire_encoding"] != "lz-json-v3-1":
            raise ValueError("consume_diagnostic_encoding_conflict")
        request_id = support.INSTANCE + "-preflight-1-" + ATTEMPT
        arguments = read_json(support.PARENT + "/preflight-1." + ATTEMPT + ".request.json")
        trace = read_json("verification/.scratch/" + request_id + ".full.json")
        delivery = read_json("verification/.scratch/" + request_id + ".delivery.full.json")
        if (arguments["request_id"] != request_id or trace["request_id"] != request_id
                or delivery["request_id"] != request_id or delivery["rpc_id"] != 3
                or arguments["operation"] != "preflight" or trace["operation"] != "preflight"
                or arguments["contract_version"] != 3 or trace["contract_version"] != 3
                or arguments["wire_encoding"] != "lz-json-v3-1"
                or arguments["transport"] != support.CAPS
                or trace["gateway_requests"] != [] or trace["model_outputs"] != []
                or trace["failures"] != []
                or trace["candidate"] != {"summary": "", "conclusions": [], "unknowns": []}
                or delivery["knowledge_delivered"] is not True
                or delivery["status"] != "frame_written_not_host_consumption"
                or type(delivery["written"]) is not int or delivery["written"] <= 0):
            raise ValueError("consume_diagnostic_saved_evidence_binding")
        emit({"historical_preparation_metrics": trace["metrics"],
              "historical_written_bytes": delivery["written"],
              "historical_delivery_scope": "writer_receipt_not_client_consumption_or_exit"})
        project = load_projects("config/projects.json")[arguments["project_id"]]
        prepared = stage("fresh_prepare", lambda: prepare_snapshot(project=project,
            include=tuple(arguments.get("include", project.include)),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=threading.Event(), tick=check))
        raw_files, report, selection = prepared.raw_files, prepared.report, prepared.selection
        comparisons = {
            "report_equal": trace["report"] == report,
            "report_contract_equal": reports_match_except_elapsed(trace["report"], report),
            "selection_equal": trace["selection"] == selection,
            "core_equal": trace["core"] == prepared.core.document,
            "post_equal": trace["post_utf8"].encode("utf-8") == prepared.post,
            "post_sha256_equal": trace["post_sha256"] == hashlib.sha256(prepared.post).hexdigest(),
        }
        saved_report = trace["report"]
        if type(saved_report) is dict:
            comparisons["report_differing_top_level_keys"] = sorted(
                key for key in set(saved_report) | set(report)
                if key not in saved_report or key not in report or saved_report[key] != report[key])[:16]
            saved_observations = saved_report.get("observations")
            fresh_observations = report.get("observations")
            if type(saved_observations) is dict and type(fresh_observations) is dict:
                comparisons["observation_differing_keys"] = sorted(
                    key for key in set(saved_observations) | set(fresh_observations)
                    if key not in saved_observations or key not in fresh_observations
                    or saved_observations[key] != fresh_observations[key])[:16]
        emit({"fresh_binding_comparisons": comparisons})
        if not all(comparisons[key] for key in (
                "report_contract_equal", "selection_equal", "core_equal", "post_equal", "post_sha256_equal")):
            raise ValueError("consume_diagnostic_fresh_source_or_selection_drift")
        trusted = {"project_id": project.project_id, "snapshot_id": report["snapshot_id"],
            "required_core_unit_ids": selection["core_unit_ids"],
            "selection_id": selection["selection_id"], "request_id": request_id,
            "question": arguments["question"], "dimensions": arguments["dimensions"],
            "post": prepared.post, "frozen_coverage": report["coverage"],
            "failures": [], "tick": check}
        admission = Admission("2024-11-05", "A", 3, arguments["return_bytes"],
            TransportCapabilities.from_dict(support.CAPS), wire_encoding="lz-json-v3-1")
        frame = stage("reconstruct_publication", lambda: publish_v3(prepared.core,
            trace["task"], raw_files, admission, candidate=trace["candidate"], **trusted))
        wire = frame.data
        if (len(wire) != delivery["written"]
                or hashlib.sha256(wire).hexdigest() != delivery["frame_sha256"]):
            raise ValueError("consume_diagnostic_reconstructed_delivery_conflict")
        measurements.update(reconstructed_transfer_bytes=len(wire),
            reconstructed_delivery_hash_equal=True, frozen_file_count=len(raw_files))
        with patch.object(consumer_v3, "_reassemble_report", measured(
                "reassemble_and_decode", consumer_v3._reassemble_report)), \
             patch.object(consumer_v3, "build_publication", measured(
                "trusted_publication", consumer_v3.build_publication)), \
             patch.object(lz_v3, "_inflate", measured("inflate", lz_v3._inflate)), \
             patch.object(lz_v3, "_preparse", measured("preparse", lz_v3._preparse)), \
             patch.object(lz_v3, "_compress", measured("canonical_recompress", lz_v3._compress)), \
             patch.object(lz_v3._Budget, "inspect", new=measured(
                "lz_budget_inspect", lz_v3._Budget.inspect)):
            consumed = stage("production_consume", lambda: consumer_v3.consume_frame(
                wire, raw_files, admission=admission, candidate=trace["candidate"], **trusted))
        measurements.update(decoded_bytes=consumed.decoded_bytes,
            physical_frames=consumed.physical_frames,
            notification_chunks=consumed.notification_chunks,
            task_status=consumed.status)
        stage("fresh_source_verify", prepared.verify_sources)
        emit({"status": "diagnostic_completed_not_preflight_acceptance", "binding": bound,
              "attempt_id": ATTEMPT, "stages_seconds": stages, "operations": operations,
              "measurements": measurements, "elapsed_seconds": time.monotonic() - started,
              "service_processes_started": 0, "gateway_requests": 0,
              "preflight_samples_started": 0, "online_admissions": 0,
              "timing_scope": "single_process_with_measurement_overhead_nested_timings_not_additive",
              "not_proven": ["original_client_consumption", "original_service_exit",
                             "independent_oracle", "six_artifact_save", "three_cold_samples",
                             "online_business", "human_semantics"]})
        return 0
    except Exception as error:
        emit({"status": "blocked", "failure_stage": current,
              "error": type(error).__name__, "reason": str(error)[:256],
              "stages_seconds": stages, "operations": operations,
              "measurements": measurements, "elapsed_seconds": time.monotonic() - started,
              "failure_scope": "diagnostic_not_original_client_stack"})
        return 2


if __name__ == "__main__":
    raise SystemExit(probe())
