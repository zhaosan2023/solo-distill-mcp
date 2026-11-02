"""真实外层 30 秒负控：隔离冷预检保存段拒不返回，另窗复核退出。

worker 必须由已登记的 timeout -s KILL 30s 前台执行；预期退出非零，
不能将强杀认作成功预检。verify 只读保留现场，不重跑业务或清理资产。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch

from solo_distill.distillation import load_policy
from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT, PreflightGuard

ROOT = Path(__file__).resolve().parents[2]
SOURCE = b"def interfaces():\n return 1\n"
PREFIX = "schema-v3-process-negative-"


def load_driver():
    path = ROOT / "verification/.scratch/mainline_a_schema_v3_cold_chain_20261030.py"
    spec = importlib.util.spec_from_file_location("v3_process_negative_cold_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(source):
    return {"root": str(source), "include": ["."],
            "exclude": [".git", "__pycache__"], "max_entries": 1000,
            "max_file_bytes": 1048576, "max_total_bytes": 16777216,
            "max_seconds": 10, "return_bytes": 24576}


def save_observation(path, value, fsync):
    # 获准测试夹具的独占机械观察；不是任务进度或成功预检回执。
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                separators=(",", ":")).encode("utf-8"))
        stream.flush()
        fsync(stream.fileno())


def worker():
    fixture = Path(tempfile.mkdtemp(prefix=PREFIX, dir=ROOT / "verification/.scratch"))
    source = fixture / "source"
    source.mkdir()
    (source / "sample.py").write_bytes(SOURCE)
    (fixture / "config").mkdir()
    (fixture / "config/projects.json").write_text(json.dumps({
        "config_version": 1, "projects": {"fixture": configuration(source)}}),
        encoding="utf-8")
    policy = load_policy(str(ROOT / "config/distillation.json"))
    policy["authorized_project_ids"] = ["fixture"]
    (fixture / "config/distillation.json").write_text(json.dumps(policy), encoding="utf-8")
    (fixture / "src").symlink_to(ROOT / "src", target_is_directory=True)
    (fixture / PARENT).mkdir(parents=True)
    module = load_driver()
    guard = PreflightGuard()
    now = int(time.time() * 1000)
    arguments = {"contract_version": 3, "protocol_version": "2024-11-05",
        "operation": "preflight", "request_id": "process-deadline-negative",
        "project_id": "fixture", "scenario": "A", "question": "interfaces",
        "dimensions": [{"id": "interfaces", "question": "interfaces",
                        "required": True, "evidence": "sourced_inference"}],
        "started_at_unix_ms": now, "deadline_unix_ms": now + 30000,
        "host_timeout_seconds": 30, "return_bytes": 2048,
        "transport": {"policy": "adaptive-backpressure-v1", "authority": "caller_explicit",
            "max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
            "max_decoded_bytes": 1048576, "max_chunks": 512, "chunking": True}}
    original_fsync = os.fsync
    blocked = []

    def uncooperative_save(fd):
        original_fsync(fd)
        if not blocked:
            blocked.append(True)
            observation = {"control": "outer_30_second_cold_save_negative",
                "status": "awaiting_external_timeout_not_pass",
                "pid": os.getpid(), "fixture": str(fixture.relative_to(ROOT)),
                "guard_started_monotonic": guard.started,
                "entered_save_monotonic": time.monotonic(),
                "injection": "first_preflight_save_fsync_then_noncooperative_wait",
                "real_service_closed_before_injection": True,
                "required_timeout_seconds": 30, "automatic_cleanup": False}
            save_observation(fixture / "process-observation.json", observation, original_fsync)
            print(json.dumps(observation, ensure_ascii=False), flush=True)
            # 不创建来源进程；唯一服务已在驱动的 finally 中确认退出。
            # 故意不调用 guard，让真实外层进程期限承担最终兜底。
            threading.Event().wait(60)
            raise RuntimeError("external_timeout_did_not_terminate_worker")

    with patch("solo_distill.preflight_v3.os.fsync", side_effect=uncooperative_save):
        module.run_preflight(fixture, arguments, "preflight-1", guard=guard)
    raise RuntimeError("negative_control_unexpectedly_returned_success")


def verify(relative):
    path = Path(relative)
    if (path.is_absolute() or ".." in path.parts or len(path.parts) != 3
            or path.parts[:2] != ("verification", ".scratch")
            or not path.name.startswith(PREFIX)):
        raise ValueError("negative_fixture_must_be_workspace_relative")
    fixture = ROOT / path
    if fixture.is_symlink() or not fixture.is_dir():
        raise ValueError("negative_fixture_identity")
    observation = json.loads((fixture / "process-observation.json").read_bytes())
    if (observation["fixture"] != relative or type(observation["pid"]) is not int
            or observation["pid"] <= 1 or observation["required_timeout_seconds"] != 30
            or observation["status"] != "awaiting_external_timeout_not_pass"):
        raise ValueError("negative_observation_identity")
    try:
        os.kill(observation["pid"], 0)
    except ProcessLookupError:
        absent = True
    else:
        raise RuntimeError("negative_worker_exit_not_confirmed")
    target = fixture / PARENT / "preflight-1"
    if not target.is_dir() or (target / "preflight.receipt.json").exists():
        raise RuntimeError("negative_failure_scene_or_success_receipt_conflict")
    if (fixture / "source/sample.py").read_bytes() != SOURCE:
        raise RuntimeError("negative_source_changed")
    delivery = json.loads((fixture / "verification/.scratch/process-deadline-negative.delivery.full.json").read_bytes())
    trace = json.loads((fixture / "verification/.scratch/process-deadline-negative.full.json").read_bytes())
    if trace["gateway_requests"] or trace["model_outputs"] or delivery["written"] <= 0:
        raise RuntimeError("negative_offline_service_trace_conflict")
    print(json.dumps({"control": observation["control"],
        "status": "post_timeout_scene_verified_requires_command_exit_receipt",
        "fixture": relative, "pid": observation["pid"], "worker_pid_absent": absent,
        "source_unchanged": True, "success_receipt_absent": True,
        "failure_scene_retained": True, "gateway_requests": 0,
        "required_artifacts_present": sorted(name for name in ARTIFACT_NAMES
                                             if (target / name).is_file()),
        "proof_boundary": "pair_with_actual_30_second_timeout_command_nonzero_exit"},
        ensure_ascii=False), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="隔离外层期限负控及只读退出复核")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--worker", action="store_true")
    modes.add_argument("--verify", metavar="WORKSPACE_RELATIVE_FIXTURE")
    arguments = parser.parse_args()
    return worker() if arguments.worker else verify(arguments.verify)


if __name__ == "__main__":
    raise SystemExit(main())
