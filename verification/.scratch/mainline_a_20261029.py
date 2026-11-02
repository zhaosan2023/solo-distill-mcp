"""主线 A 获准的本地预检；来源仅作数据，不启动或修改网关。

本驱动先实现 health/preflight；预检失败不得推进线上依赖或增加 POST。
独占保存证据，不修改 FastCode 或任何已有资产。草稿位置不是执行沙箱。
"""
from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.distillation import SYSTEM_PROMPT, load_policy
from solo_distill.evidence_v2 import V2EvidenceReader
from solo_distill.extractor import Scanner, load_projects, open_root
from solo_distill.gateway import ENDPOINT, MAX_MESSAGE_BYTES, MODEL
from solo_distill.live_v2 import LiveV2Distiller, select_inventory
from solo_distill.core_v2 import canonical_package
from solo_distill.telemetry import BudgetTelemetry
from solo_distill.syntax_v2 import body_text, extract_units, identity
from live_v2_driver import request
import gateway_readonly_diagnostic

ROOT = Path(__file__).resolve().parents[2]
OUT = "verification/mainline-a-lifecycle-v3-20261029T094933Z"
PLAN = "_bmad-output/mainline-a-v2-scenario-a-20261029.md"
POLICY = "adaptive-backpressure-v1"
CAPS = dict(policy=POLICY, authority="caller_explicit", max_frame_bytes=22016,
            max_transfer_bytes=4194304, max_decoded_bytes=4194304,
            max_chunks=256, chunking=True)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(name, value):
    """独占、无软链接的程序证据保存，fsync 后按原字节复读。"""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("unsafe_evidence_name")
    raw = value if type(value) is bytes else encoded(value)
    descriptors = [open_root(str(ROOT))]
    try:
        for part in OUT.split("/"):
            try:
                os.mkdir(part, 0o700, dir_fd=descriptors[-1])
            except FileExistsError:
                pass
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=descriptors[-1])
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptors[-1])
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptors[-1])
        with os.fdopen(fd, "rb") as stream:
            checked = stream.read(len(raw) + 1)
        if checked != raw:
            raise ValueError("evidence_readback_conflict")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
    return {"path": OUT + "/" + name, "bytes": len(raw), "sha256": sha(raw),
            "readback": "exact_bytes_pass"}


def gateway_identity():
    """只读 Linux 监听 socket 与拥有者 starttime，不输出命令行或凭据。"""
    inodes = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in Path(filename).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1].rsplit(":", 1)[1] == "1F90" and fields[3] == "0A":
                inodes.add(fields[9])
    owners = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            found = set()
            for fd in (process / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    found.add(target[8:-1])
            if found:
                raw = (process / "stat").read_text()
                ticks = raw.rsplit(")", 1)[1].split()[19]
                owners.append({"pid": int(process.name), "start_ticks": int(ticks),
                               "socket_inodes": sorted(found)})
        except (OSError, ValueError, IndexError):
            continue
    return {"listening_inodes": sorted(inodes), "owners": sorted(owners, key=lambda x: x["pid"]),
            "identity_observable": bool(inodes and owners),
            "scope": "point_in_time_linux_listener_identity_not_continuous_isolation"}


def arguments():
    now = int(time.time() * 1000)
    value = request("mainline-a-preflight-" + str(now), now)
    value.update(budget_policy=POLICY, transport=dict(CAPS))
    return value


def blueprint_integrity():
    root = ROOT / "_bmad-output/blueprints/mainline-b-20261028"
    manifest_raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    bad = []
    for name, expected in manifest["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("blueprint_manifest_path")
        raw = (root / name).read_bytes()
        if sha(raw) != expected:
            bad.append(name)
    return {"manifest_sha256": sha(manifest_raw), "listed_files": len(manifest["files"]),
            "mismatches": bad, "status": "pass" if not bad else "blocked",
            "acceptance_scope": manifest.get("acceptance_scope"),
            "historical_runtime_evidence_inherited": False}


def configs():
    projects = load_projects(str(ROOT / "config/projects.json"))
    policy = load_policy(str(ROOT / "config/distillation.json"))
    if "fastcode-sample" not in policy["authorized_project_ids"]:
        raise ValueError("project_not_authorized")
    if (policy["max_business_seconds"] != 180 or policy["max_message_bytes"] != MAX_MESSAGE_BYTES
            or policy["endpoint"] != ENDPOINT or policy["model"] != MODEL):
        raise ValueError("fixed_gateway_policy_conflict")
    return projects, policy


def health():
    start = time.monotonic()
    before = gateway_identity()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = gateway_readonly_diagnostic.run()
    report = json.loads(captured.getvalue())
    after = gateway_identity()
    http_ok = (code == 0 and len(report["results"]) == 2
               and all(item.get("http_status") == 200 for item in report["results"]))
    inode_stable = bool(before["listening_inodes"]) and before["listening_inodes"] == after["listening_inodes"]
    ok = http_ok and inode_stable
    receipt = {"receipt_version": 1, "phase": "health", "status": "pass" if ok else "blocked",
               "diagnostic": report, "listener_before": before, "listener_after": after,
               "gateway_restart_operations": 0, "gateway_writes": 0,
               "gate_contract_version": 3,
               "acceptance_basis": "health_http_200_stats_http_200_nonempty_stable_listener_inodes",
               "pid_observation_waived_by_current_user": True, "privilege_escalation_operations": 0,
               "listener_inode_stable": inode_stable,
               "elapsed_seconds": round(time.monotonic() - start, 6),
               "command": "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_20261029.py health </dev/null",
               "cwd": str(ROOT), "driver_exit_code": 0 if ok else 2}
    location = save("health.receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "evidence": location,
                      "listener_identity_stable": before == after,
                      "health_reports_ok": report["results"][0].get("health_reports_ok")}, ensure_ascii=False), flush=True)
    return 0 if ok else 2


def deadline_negative_controls(distiller, args):
    results = []
    cases = [("host_window_181", {"host_timeout_seconds": 181}),
             ("deadline_180001_ms", {"deadline_unix_ms": args["started_at_unix_ms"] + 180001}),
             ("implicit_chunking", {"budget_policy": "legacy"})]
    for name, changes in cases:
        try:
            distiller.validate_v2({**args, **changes}, 3)
        except ValueError:
            results.append({"case": name, "rejected_before_business": True})
        else:
            results.append({"case": name, "rejected_before_business": False})
    if not all(x["rejected_before_business"] for x in results):
        raise ValueError("deadline_or_negotiation_negative_control")
    return results


def preflight():
    start = time.monotonic()
    receipt = {"receipt_version": 1, "phase": "preflight", "status": "blocked",
               "failures": [], "generation_requests": 0, "outer_replays": 0,
               "target_write_operations": 0, "target_execution_operations": 0,
               "gateway_restart_operations": 0, "policy": POLICY, "transport": CAPS,
               "controller_coverage": "unverified_platform_task_record_used",
               "command": "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_20261029.py preflight </dev/null",
               "cwd": str(ROOT), "offline_consumption": "not_yet_run", "online_business": "not_started"}
    try:
        h = json.loads((ROOT / OUT / "health.receipt.json").read_bytes())
        if h["status"] != "pass":
            raise ValueError("health_gate_not_passed")
        receipt["listener_before"] = gateway_identity()
        if receipt["listener_before"]["listening_inodes"] != h["listener_after"]["listening_inodes"]:
            raise ValueError("listener_changed_after_health")
        projects, policy = configs()
        args = arguments()
        caps = TransportCapabilities.from_dict(args["transport"])
        distiller = LiveV2Distiller(projects, policy)
        project, include, _, admission = distiller.validate_v2(args, 3)
        receipt["arguments"] = args
        receipt["deadline_controls"] = deadline_negative_controls(distiller, args)
        receipt["business_seconds_limit"] = 180
        receipt["hard_timeout_runtime"] = "pending_related_offline_fixture_and_live_process_receipt"
        receipt["blueprint"] = blueprint_integrity()
        if receipt["blueprint"]["status"] != "pass":
            raise ValueError("blueprint_integrity")
        scanner = Scanner(project, include, project.max_seconds)
        structure = scanner.run()
        receipt["structure_evidence"] = save("preflight.structure.json", structure)
        receipt["coverage"] = structure["coverage"]
        receipt["source_snapshot_id"] = structure["snapshot_id"]
        hard = [x for x in structure["unfinished"] if x["reason"] != "unsupported_language_or_asset"]
        receipt["unfinished"] = structure["unfinished"]
        if hard or not structure["inventory_complete"] or structure["freshness"] != "current_at_verification":
            raise ValueError("snapshot_not_ready")
        scanner.deadline = start + 27
        telemetry = BudgetTelemetry(run_id=args["request_id"], project_id=project.project_id,
                                    snapshot_id=structure["snapshot_id"], contract_version="3")
        reader = V2EvidenceReader(scanner, structure, start + 27, threading.Event(),
                                  budget_policy=POLICY, telemetry=telemetry)
        units = reader.inventory()
        if not units:
            raise ValueError("zero_v2_syntax_units")
        receipt["sources_evidence"] = save("preflight.sources.json", {path: base64.b64encode(raw).decode("ascii")
                                                  for path, raw in reader.raw_files.items()})
        # 使用实际生产选择链及其 Scheduler；不能以旧策略偷偷裁剪必需组。
        navigation_question = args["question"] + " " + " ".join(
            dimension["question"] for dimension in args["dimensions"])
        package, selection = select_inventory(reader, navigation_question, budget_policy=POLICY)
        expected_units = {unit["id"] for unit in units}
        if ({unit["id"] for unit in package["units"]} != expected_units
                or {file["path"] for file in package["files"]} != set(reader.raw_files)):
            raise ValueError("adaptive_structural_denominator_incomplete")
        receipt["selection"] = {"status": selection.status, "owners": list(selection.owners),
            "aliases": [list(item) for item in selection.aliases], "counts": list(selection.counts),
            "body_bytes": selection.body_bytes, "optional_omissions": list(selection.optional_omissions),
            "required_owners": list(selection.required_owners)}
        receipt["navigation_page_count"] = len(reader.navigation_pages)
        # 生产 preliminary core 只规范化全量表序，不删必需证据；按同一表序计 POST。
        package = canonical_package(package)
        sources = [{
            "source_id": next(source["id"] for source in package["sources"]
                              if source["unit"] == owner["unit"]),
            "alias_source_ids": [source["id"] for source in package["sources"]
                                 if source["owner"] == index],
            "path": package["files"][owner["file"]]["path"],
            "symbol": package["units"][owner["unit"]]["qualified_name"],
            "text": body_text(reader.raw_files[package["files"][owner["file"]]["path"]],
                              package["files"][owner["file"]], package["units"][owner["unit"]])}
            for index, owner in enumerate(package["owners"])]
        definition_names, calls = set(), set()
        condition_count = 0
        for path, raw in sorted(reader.raw_files.items()):
            value = extract_units(path, raw)
            condition_count += len(value["conditions"])
            definition_names.update(u["qualified_name"] for u in value["units"] if u["kind"] == "definition")
            calls.update(ast.unparse(n.func) for n in ast.walk(ast.parse(raw)) if isinstance(n, ast.Call))
        receipt["selected_package_evidence"] = save("preflight.selected-package.json", package)
        payload = {"operation": "compression", "question": args["question"],
                   "dimensions": args["dimensions"], "sources": sources,
                   "coverage": structure["coverage"]}
        message = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                   {"role": "user", "content": json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                                           separators=(",", ":"))}], "stream": True}
        post = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        receipt["post_evidence"] = save("preflight.http-post.json", post)
        receipt["post_bytes"] = len(post)
        receipt["post_hard_limit_bytes"] = MAX_MESSAGE_BYTES
        receipt["body_owner_count"] = len(sources)
        receipt["complete_source_body_bytes"] = sum(len(item["text"].encode("utf-8")) for item in sources)
        receipt["source_body_soft_target_bytes"] = 98304
        receipt["source_body_count_basis"] = "scheduler_unique_complete_owners_utf8_without_alias_double_count"
        telemetry.bodies({owner["id"]: owner["body_bytes"] for owner in package["owners"]})
        receipt["telemetry"] = telemetry.snapshot()
        receipt["full_source_unit_count"] = len(units)
        receipt["full_definition_name_count"] = len(definition_names)
        receipt["full_condition_count"] = condition_count
        receipt["full_call_expression_distinct_count"] = len(calls)
        receipt["post_count_basis"] = "actual_adaptive_select_inventory_scheduler_canonical_complete_owners_and_aliases"
        receipt["denominator_evidence"] = save("preflight.denominators.json", {
            "definitions": sorted(definition_names), "calls": sorted(calls),
            "source_unit_ids": sorted(u["id"] for u in units), "conditions": condition_count})
        reader.verify()
        receipt["freshness"] = "current_at_final_preflight_verification"
        receipt["listener_after"] = gateway_identity()
        if (not receipt["listener_after"]["listening_inodes"]
                or receipt["listener_before"]["listening_inodes"] != receipt["listener_after"]["listening_inodes"]):
            raise ValueError("listener_changed_during_preflight")
        if len(post) > MAX_MESSAGE_BYTES:
            receipt["failures"].append("complete_gateway_input_exceeds_fixed_http_message_capability")
        if not definition_names or not calls or condition_count == 0:
            receipt["failures"].append("required_nonempty_denominator_missing")
        if not receipt["failures"]:
            receipt["status"] = "pass"
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:200])
    receipt["elapsed_seconds"] = round(time.monotonic() - start, 6)
    receipt["driver_exit_code"] = 0 if receipt["status"] == "pass" else 2
    receipt["dependencies_not_advanced"] = receipt["status"] != "pass"
    location = save("preflight.receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "failures": receipt["failures"],
                      "coverage": receipt.get("coverage"), "post_bytes": receipt.get("post_bytes"),
                      "post_hard_limit_bytes": MAX_MESSAGE_BYTES,
                      "complete_source_body_bytes": receipt.get("complete_source_body_bytes"),
                      "source_body_soft_target_bytes": receipt.get("source_body_soft_target_bytes"),
                      "telemetry_warnings": receipt.get("telemetry", {}).get("warnings", []),
                      "generation_requests": 0, "evidence": location}, ensure_ascii=False), flush=True)
    return receipt["driver_exit_code"]


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"health", "preflight"}:
        print("本驱动仅允许 health / preflight；线上入口尚未实现，禁止静默执行。", file=sys.stderr)
        return 2
    return health() if sys.argv[1] == "health" else preflight()


if __name__ == "__main__":
    raise SystemExit(main())
