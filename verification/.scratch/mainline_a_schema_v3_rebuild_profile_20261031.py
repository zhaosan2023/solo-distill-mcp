"""阶段 4 必要性能定位，不作为冷预检通过证据。
问题：已有操作内 AST 复用后，源码派生宏观／局部结构是否仍反复构建。
范围：当前配置的授权来源准备、空候选任务及离线发布；不启动子进程、
不建连、不执行或写入来源、不占用三个样本，也不保存业务 trace。
前提：现有配置、依赖及已登记的源码复用实现；27 秒内部守卫、30 秒硬限。
验收：有界阶段计时、调用次数及热点；异常／超时保持阻塞，不降低门禁。
"""
from __future__ import annotations

import cProfile
import faulthandler
import io
import json
from pathlib import Path
import pstats
import threading
import time

from unittest.mock import patch

from live_v2_driver import request
from solo_distill import wire_v3
from solo_distill.macro_v3 import canonical
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.extractor import load_projects
from solo_distill.preflight_v3 import INSTANCE
from solo_distill.preparation_v3 import prepare_snapshot
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.task_v3 import build_task
from solo_distill.wire_v3 import Admission, publish_v3


@snapshot_scope
def probe():
    started = time.monotonic()
    deadline = started + 27
    stages = {}
    profile = cProfile.Profile()
    cancelled = threading.Event()
    now = int(time.time() * 1000)
    arguments = request(INSTANCE + "-rebuild-profile-20261031", now)
    project = load_projects("config/projects.json")[arguments["project_id"]]

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("rebuild_profile_internal_deadline")

    def stage(name, action):
        check()
        print(json.dumps({"stage_start": name}), flush=True)
        begin = time.monotonic()
        value = action()
        stages[name] = time.monotonic() - begin
        print(json.dumps({"stage_end": name, "seconds": stages[name]}), flush=True)
        check()
        return value

    original_encode = wire_v3._encode

    def measured_encode(report, response_id):
        encoded = original_encode(report, response_id)
        if report.get("kind") == "sealed_knowledge":
            package = report["core"]["package"]
            print(json.dumps({"diagnostic_only": True,
                "publication_bytes": len(canonical(report)),
                "decoded_response_bytes": len(encoded),
                "decoded_limit_bytes": 1048576,
                "response_over_limit_bytes": max(0, len(encoded) - 1048576),
                "core_bytes": len(canonical(report["core"])),
                "task_bytes": len(canonical(report["task_evidence"])),
                "package_field_bytes": {key: len(canonical(value))
                    for key, value in package.items()},
                "macro_field_bytes": {key: len(canonical(value))
                    for key, value in package["macro"].items()},
                "local_field_bytes": {key: len(canonical(value))
                    for key, value in package["local_closures"].items()},
                "measurement_scope": "actual_encoder_output_not_capacity_acceptance"}), flush=True)
        return encoded

    faulthandler.dump_traceback_later(8, repeat=True)
    profile.enable()
    try:
        prepared = stage("prepare", lambda: prepare_snapshot(project=project,
            include=tuple(arguments.get("include", project.include)),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=deadline, cancelled=cancelled, tick=check))
        print(json.dumps({"preparation_metrics": prepared.metrics,
                          "frozen_file_count": len(prepared.raw_files)}), flush=True)
        raw_files, report, selection = prepared.raw_files, prepared.report, prepared.selection
        trusted = {"project_id": project.project_id, "snapshot_id": report["snapshot_id"],
            "required_core_unit_ids": selection["core_unit_ids"],
            "selection_id": selection["selection_id"], "request_id": arguments["request_id"],
            "question": arguments["question"], "dimensions": arguments["dimensions"],
            "post": prepared.post, "frozen_coverage": report["coverage"], "failures": [],
            "tick": check}
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        task = stage("task", lambda: build_task(candidate, prepared.core, raw_files, **trusted))
        caps = TransportCapabilities.from_dict({"policy": "adaptive-backpressure-v1",
            "authority": "caller_explicit", "max_frame_bytes": 22016,
            "max_transfer_bytes": 1572864, "max_decoded_bytes": 1048576,
            "max_chunks": 512, "chunking": True})
        admission = Admission("2024-11-05", "A", 3, arguments["return_bytes"], caps)
        with patch.object(wire_v3, "_encode", side_effect=measured_encode):
            frame = stage("publish", lambda: publish_v3(prepared.core, task, raw_files,
                admission, candidate=candidate, **trusted))
        stage("source_verify", prepared.verify_sources)
        print(json.dumps({"diagnostic_only": True, "stages_seconds": stages,
            "elapsed_seconds": time.monotonic() - started, "transfer_bytes": len(frame.data),
            "gateway_requests": 0, "source_execution_operations": 0,
            "preflight_acceptance": "not_run"}), flush=True)
        return 0
    finally:
        profile.disable()
        faulthandler.cancel_dump_traceback_later()
        output = io.StringIO()
        pstats.Stats(profile, stream=output).strip_dirs().sort_stats("cumulative").print_stats(24)
        print(output.getvalue(), flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(probe())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
                          "reason": str(error)[:256]}), flush=True)
        raise SystemExit(2)
