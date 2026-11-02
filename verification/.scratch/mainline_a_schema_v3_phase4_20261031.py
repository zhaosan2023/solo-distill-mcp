"""阶段 4 有界支撑：健康绑定、刷新请求后 exec 原冷预检、原字节复核。
不启动网关、不发业务 POST、不执行来源；不修改生产或历史资产。
真实外层退出必须由客户端回执提供，不从驱动的成功字样推断。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

import gateway_readonly_diagnostic
from live_v2_driver import request
from solo_distill.extractor import open_root, open_relative
from solo_distill.preflight_v3 import ARTIFACT_NAMES, INSTANCE, PARENT, _save_exact

ROOT = Path.cwd()
PLAN = "_bmad-output/mainline-a-v2-scenario-a-20261029.md"
DRIVER = "verification/.scratch/mainline_a_schema_v3_cold_chain_20261030.py"
ATTEMPT = "unattended-20261031T1513Z"
HEALTH_NAME = "health." + ATTEMPT + ".receipt.json"
WIRE_ENCODING = "lz-json-v3-1"


def attempt_name(sample, kind):
    return sample + "." + ATTEMPT + "." + kind
CAPS = {"policy": "adaptive-backpressure-v1", "authority": "caller_explicit",
        "max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
        "max_decoded_bytes": 1048576, "max_chunks": 512, "chunking": True}


class Guard:
    def __init__(self, seconds=15):
        self.deadline = time.monotonic() + seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("phase4_support_deadline")


def load(path):
    return json.loads((ROOT / path).read_bytes())


def save(name, value, guard):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    root = open_root(str(ROOT))
    try:
        parent = open_relative(root, PARENT, directory=True)
        try:
            digest = _save_exact(parent, name, raw, guard)
            os.fsync(parent)
            guard.check()
        finally:
            os.close(parent)
    finally:
        os.close(root)
    return {"path": PARENT + "/" + name, "bytes": len(raw),
            "sha256": digest, "exact_readback": True}


def binding():
    text = (ROOT / PLAN).read_text()
    for line in ("plan_id: mainline-a-v2-scenario-a-20261029", "contract_version: 6",
                 "status: EXECUTING", "execution_instance: " + INSTANCE,
                 "adr_revision: 7", "session_id: null"):
        if text.splitlines().count(line) != 1:
            raise ValueError("phase4_active_contract_conflict")
    contract = load(PARENT + "/schema-v3.execution-contract.json")
    if (contract["contract_revision"] != 8 or contract["execution_instance"] != INSTANCE
            or contract["plan_contract_version"] != 6 or contract["adr_revision"] != 7
            or contract["plan_id"] != "mainline-a-v2-scenario-a-20261029"
            or contract["session_id"] is not None
            or contract["adr_file"] != "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md"
            or contract["representation_contract"]["encoding"] != "dictionary-columnar-v3-1"
            or contract["subtree_representation_contract"]["encoding"] != "dictionary-columnar-v3-2"
            or contract["lz_representation_contract"]["encoding"] != WIRE_ENCODING):
        raise ValueError("phase4_execution_contract_conflict")
    adr = (ROOT / contract["adr_file"]).read_text().splitlines()
    for line in ("adr_id: ADR-20261030-harel-fastcode-schema-v3",
                 "plan_id: mainline-a-v2-scenario-a-20261029", "contract_version: 6",
                 "adr_revision: 7", "status: PROPOSED", "session_id: null"):
        if adr.count(line) != 1:
            raise ValueError("phase4_adr_binding_conflict")
    for key in ("max_decoded_bytes", "max_transfer_bytes", "max_frame_bytes", "max_chunks"):
        if (type(contract["resource_gates"][key]) is not int
                or contract["resource_gates"][key] != CAPS[key]):
            raise ValueError("phase4_capacity_contract_conflict")
    gates = contract["resource_gates"]
    if (gates.get("preflight_internal_soft_including_evidence_seconds") != 27
            or "preflight_internal_hard_seconds" not in gates
            or gates["preflight_internal_hard_seconds"] is not None
            or gates.get("preflight_process_seconds") != 60
            or contract["cold_timeout_contract"].get("internal_hard_deadline", False) is not None
            or contract["phase4_support_validation"]["current_attempt_id"] != ATTEMPT):
        raise ValueError("phase4_unattended_timing_contract_conflict")
    policy = load("config/distillation.json")
    if (policy["endpoint"] != "http://127.0.0.1:8080/v1/chat/completions"
            or policy["model"] != "gpt-6-sol"
            or "fastcode-sample" not in policy["authorized_project_ids"]):
        raise ValueError("phase4_gateway_policy_conflict")
    return {"plan_id": contract["plan_id"], "plan_contract_version": 6,
            "execution_contract_revision": 8, "execution_instance": INSTANCE,
            "adr_revision": 7, "wire_encoding": WIRE_ENCODING, "session_id": None}


def listener():
    inodes = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in Path(filename).read_text().splitlines()[1:]:
            fields = line.split()
            address, port = fields[1].rsplit(":", 1)
            reachable = address in {"00000000", "0100007F", "0" * 32,
                                    "00000000000000000000000001000000"}
            if (reachable and port == "1F90" and fields[3] == "0A"
                    and fields[9].isdigit() and int(fields[9]) > 0):
                inodes.add(fields[9])
    return sorted(inodes)


def health():
    guard = Guard()
    started = time.monotonic()
    bound = binding()
    before = listener()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = gateway_readonly_diagnostic.run()
    diagnostic = json.loads(captured.getvalue())
    after = listener()
    items = diagnostic["results"]
    ok = (code == 0 and len(items) == 2 and all(x.get("http_status") == 200 for x in items)
          and items[0].get("health_reports_ok") is True and bool(before) and before == after)
    receipt = {"receipt_version": 1, "phase": "health", "status": "pass" if ok else "blocked",
        "binding": bound, "diagnostic": diagnostic, "listener_before": before,
        "listener_after": after, "listener_inode_stable": bool(before) and before == after,
        "pid_observation_waived": True, "process_owner_identity": "not_proven",
        "listener_scope": "point_in_time_not_continuous_isolation",
        "gateway_restart_operations": 0, "business_post_operations": 0,
        "elapsed_seconds": time.monotonic() - started,
        "controller_coverage": "not_proven_platform_task_record_used",
        "model_availability": "not_proven_by_health_and_stats"}
    evidence = save(HEALTH_NAME, receipt, guard)
    print(json.dumps({"status": receipt["status"], "evidence": evidence,
                      "listener_inodes": after, "diagnostic": diagnostic}), flush=True)
    return 0 if ok else 2


def launch(sample):
    guard = Guard()
    bound = binding()
    health_receipt = load(PARENT + "/" + HEALTH_NAME)
    if (health_receipt["status"] != "pass" or health_receipt["binding"] != bound
            or not health_receipt["listener_after"]
            or listener() != health_receipt["listener_after"]):
        raise ValueError("phase4_health_binding_missing_or_changed")
    index = int(sample[-1])
    for previous in range(1, index):
        checked = load(PARENT + "/" + attempt_name("preflight-" + str(previous), "phase4-audit.receipt.json"))
        if (checked["status"] != "pass" or checked["actual_exit_code"] != 0
                or checked["binding"] != bound or checked["attempt_id"] != ATTEMPT):
            raise ValueError("phase4_previous_sample_not_accepted")
    if os.path.lexists(ROOT / PARENT / sample):
        raise FileExistsError("phase4_sample_already_exists")
    now = int(time.time() * 1000)
    arguments = request(INSTANCE + "-" + sample + "-" + ATTEMPT, now)
    arguments.pop("seeds")
    arguments.update(contract_version=3, protocol_version="2024-11-05", operation="preflight",
                     host_timeout_seconds=60, deadline_unix_ms=now + 60000,
                     transport=CAPS, wire_encoding=WIRE_ENCODING)
    trace = ROOT / "verification/.scratch" / (arguments["request_id"] + ".full.json")
    if os.path.lexists(trace):
        raise FileExistsError("phase4_trace_already_exists")
    evidence = save(attempt_name(sample, "request.json"), arguments, guard)
    print(json.dumps({"request": evidence, "sample_id": sample,
                      "listener_before": health_receipt["listener_after"]}), flush=True)
    os.execv(sys.executable, [sys.executable, "-B", "-S", DRIVER,
                            "--request", evidence["path"], "--sample", sample])


def audit(sample):
    guard = Guard()
    bound = binding()
    base = PARENT + "/" + sample
    result = load(base + "/preflight.receipt.json")
    outer = load(PARENT + "/" + attempt_name(sample, "process-exit.receipt.json"))
    request_evidence = load(PARENT + "/" + attempt_name(sample, "request.json"))
    if (result["request_id"] != request_evidence["request_id"]
            or type(result["request_wire_bytes"]) is not int
            or not 0 < result["request_wire_bytes"] <= 65536
            or result["wire_encoding"] != WIRE_ENCODING
            or request_evidence["wire_encoding"] != WIRE_ENCODING
            or request_evidence["transport"] != CAPS
            or outer["execution_instance"] != INSTANCE or outer["attempt_id"] != ATTEMPT
            or outer["sample_id"] != sample):
        raise ValueError("phase4_retry_request_binding_or_budget")
    if (outer["actual_exit_code"] != 0 or not 0 < outer["external_time_seconds"] < 60
            or result["status"] != "pass" or result["service_exit_code"] != 0
            or result["service_cleanup"] != "confirmed_natural_exit"
            or result["gateway_requests"] != 0
            or result["save"]["exact_readback"] is not True):
        raise ValueError("phase4_process_or_persistence_gate")
    hashes = {}
    for name in ARTIFACT_NAMES:
        guard.check()
        raw = (ROOT / base / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != result["save"]["artifact_sha256"][name]:
            raise ValueError("phase4_artifact_changed")
        hashes[name] = {"bytes": len(raw), "sha256": digest}
    frozen = load(base + "/frozen-source-and-selection.json")
    raw_files = {path: base64.b64decode(raw, validate=True)
                 for path, raw in frozen["raw_files_base64"].items()}
    report = frozen["report"]
    parsed = {entry["path"]: entry for entry in report["entries"] if entry["status"] == "parsed"}
    source_config = load("config/projects.json")["projects"]["fastcode-sample"]
    parsed_entries = [entry for entry in report["entries"] if entry["status"] == "parsed"]
    if (frozen["project_id"] != "fastcode-sample" or not raw_files
            or len(parsed_entries) != len(parsed)
            or type(report["coverage"]["parsed_files"]) is not int
            or len(raw_files) != report["coverage"]["parsed_files"]
            or report["inventory_complete"] is not True
            or report["freshness"] != "current_at_verification"
            or set(raw_files) != set(parsed) or frozen["include"] != source_config["include"]
            or frozen["source_root"] != source_config["root"]):
        raise ValueError("phase4_real_source_scope_drift")
    for path, raw in raw_files.items():
        if hashlib.sha256(raw).hexdigest() != parsed[path]["content_sha256"]:
            raise ValueError("phase4_frozen_source_hash_conflict")
    if int(sample[-1]) > 1:
        previous = load(PARENT + "/preflight-1/frozen-source-and-selection.json")
        if (frozen["report"]["snapshot_id"] != previous["report"]["snapshot_id"]
                or frozen["selection"] != previous["selection"]
                or frozen["raw_files_base64"] != previous["raw_files_base64"]):
            raise ValueError("phase4_inter_sample_source_or_selection_drift")
    health_receipt = load(PARENT + "/" + HEALTH_NAME)
    after = listener()
    if (health_receipt["status"] != "pass" or health_receipt["binding"] != bound
            or not after or after != health_receipt["listener_after"]):
        raise ValueError("phase4_listener_changed")
    reuse = result["syntax_reuse"]
    for name in ("production_driver", "service_preparation"):
        if not 0 < reuse[name]["parse_calls"] <= len(raw_files):
            raise ValueError("phase4_repeated_production_parse")
    if (reuse["independent_oracle"]["parse_calls"] != len(raw_files)
            or reuse["independent_oracle"]["inventory_builds"] != len(raw_files)
            or reuse["independent_oracle"]["core_builds"] != 1):
        raise ValueError("phase4_repeated_independent_parse_or_core")
    oracle = load(base + "/independent-oracle.receipt.json")
    if (oracle["status"] != "pass" or oracle["production_and_independent_equal"] is not True
            or oracle["wire_encoding"] != WIRE_ENCODING):
        raise ValueError("phase4_independent_consumer_gate")
    transport = (ROOT / base / "transport.jsonl").read_bytes()
    frames = transport.splitlines(keepends=True)
    if (not frames or any(not frame.endswith(b"\n") for frame in frames)
            or len(transport) != oracle["transfer_bytes"]
            or len(frames) != oracle["physical_frames"]
            or len(frames) != oracle["notification_chunks"] + 1
            or len(transport) > CAPS["max_transfer_bytes"]
            or max(map(len, frames)) > CAPS["max_frame_bytes"]
            or oracle["notification_chunks"] > CAPS["max_chunks"]
            or type(oracle["decoded_bytes"]) is not int
            or not 0 < oracle["decoded_bytes"] <= CAPS["max_decoded_bytes"]):
        raise ValueError("phase4_actual_transport_capacity_gate")
    post_bytes = hashes["exact-http-post.json"]["bytes"]
    selection = frozen["selection"]
    if post_bytes > 150000 or selection["witness"]["unique_body_bytes"] > 98304:
        raise ValueError("phase4_post_or_body_budget")
    checked = {"receipt_version": 1, "status": "pass", "sample_id": sample,
        "binding": bound, "actual_exit_code": outer["actual_exit_code"],
        "attempt_id": ATTEMPT, "wire_encoding": WIRE_ENCODING,
        "request_id": result["request_id"],
        "actual_max_frame_bytes": max(map(len, frames)),
        "request_wire_bytes": result["request_wire_bytes"],
        "frozen_selection_bytes": result["frozen_selection_bytes"],
        "selection_binding": result["selection_binding"],
        "outer_evidence": PARENT + "/" + attempt_name(sample, "process-exit.receipt.json"),
        "external_time_seconds": outer["external_time_seconds"],
        "external_time_precision_note": "external_time_two_decimal_observation_not_realtime_guarantee",
        "internal_elapsed_seconds": result["internal_elapsed_seconds"],
        "six_artifact_hash_readback": hashes, "frozen_file_count": len(raw_files),
        "source_bytes": sum(map(len, raw_files.values())), "snapshot_id": report["snapshot_id"],
        "selection_id": selection["selection_id"], "body_utf8_bytes": selection["witness"]["unique_body_bytes"],
        "http_post_bytes": post_bytes, "oracle": oracle, "metrics": result["metrics"],
        "syntax_reuse": reuse,
        "performance_target_seconds": 20,
        "performance_target_met": (outer["external_time_seconds"] < 20
                                   and result["internal_elapsed_seconds"] < 20),
        "soft_targets": result["save"]["timing"], "listener_after": after,
        "service_exit_code": result["service_exit_code"], "service_cleanup": result["service_cleanup"],
        "independent_selection_seconds": result["independent_selection_seconds"],
        "service_and_production_consume_seconds": result["service_and_production_consume_seconds"],
        "independent_consume_seconds": result["independent_consume_seconds"],
        "save_and_readback_seconds": result["save_and_readback_seconds"],
        "cold_scope": "new_process_operation_local_index_no_os_cache_flush",
        "not_proven": ["online_generation", "human_semantics", "host_loading", "statistical_p95"]}
    evidence = save(attempt_name(sample, "phase4-audit.receipt.json"), checked, guard)
    print(json.dumps({"evidence": evidence, "audit": checked}, ensure_ascii=False), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["health", "launch", "audit"])
    parser.add_argument("--sample", choices=["preflight-1", "preflight-2", "preflight-3"])
    args = parser.parse_args()
    if args.phase == "health":
        return health()
    if args.sample is None:
        parser.error("launch/audit require --sample")
    return launch(args.sample) if args.phase == "launch" else audit(args.sample)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
                          "reason": str(error)[:256]}), file=sys.stderr, flush=True)
        raise SystemExit(2)
