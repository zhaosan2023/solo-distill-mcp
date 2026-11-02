"""获准版本 5 专用入口；历史驱动和证据保持原样，目标源码始终仅作数据。

控制器覆盖未证明，平台任务记录为进度权威。本程序只生成证据，不启动网关。
一次业务用独占标记封闭，失败也不能重放；语义复读不能由退出码冒充。
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
import sys
import time
import threading

import mainline_a_20261029 as historical
from solo_distill.adaptive_wire import CHUNK_METHOD, TransportCapabilities, reassemble
from solo_distill.client import ClientError, StdioClient
from solo_distill.consumer_v2 import consume_frame
from solo_distill.core_v2 import form_core
from solo_distill.evidence_v2 import V2EvidenceReader
from solo_distill.extractor import Scanner
from solo_distill.gateway import encode_request, strict_json
from solo_distill.live_v2 import LiveV2Distiller, TOOL_NAME, core_selection, select_inventory
from solo_distill.scouting_v2 import select_prompt, POST_LIMIT, BODY_LIMIT
from solo_distill.snapshot_v2 import snapshot_scope, snapshot_metrics, parsed_tree
from solo_distill.syntax_v2 import extract_units
from solo_distill.wire_v2 import publish
from live_v2_driver import request, read_trace, check_trace, source_inputs
from adaptive_receiver import receive as independent_receive
from v2_consumer import evaluate_frozen
import gateway_readonly_diagnostic

ROOT, PLAN, POLICY = historical.ROOT, historical.PLAN, historical.POLICY
CAPS = dict(policy=POLICY, authority="caller_explicit", max_frame_bytes=22016,
            max_transfer_bytes=12582912, max_decoded_bytes=8388608,
            max_chunks=512, chunking=True)
BASE = "verification/mainline-a-scouting-v5-20261030T011416Z-capacity"
OFFLINE_DIR = BASE + "/offline-retry-1"
COMMAND_PREFIX = "PYTHONPATH=src:tests timeout -k 2s "
DRIVER = "python3 -B verification/.scratch/mainline_a_scouting_20261029.py "


def encoded(value):
    return historical.encoded(value)


def sha(raw):
    return historical.sha(raw)


def read(name, directory=BASE):
    return strict_json((ROOT / directory / name).read_bytes().decode("utf-8"))


def save(name, value, directory=BASE):
    historical.OUT = directory
    return historical.save(name, value)


def command(phase):
    if phase == "live":
        return "PYTHONPATH=src:tests timeout -s KILL 180s " + DRIVER + phase + " </dev/null"
    seconds = "15s " if phase == "health" else "30s "
    return COMMAND_PREFIX + seconds + DRIVER + phase + " </dev/null"


def implementation():
    files = sorted((ROOT / "src/solo_distill").glob("*.py"))
    files += sorted((ROOT / "tests").glob("*.py"))
    files += [Path(__file__), ROOT / "verification/.scratch/mainline_a_offline_20261029.py",
              ROOT / "verification/.scratch/mainline_a_20261029.py",
              ROOT / "tests/fastcode_acceptance.json", ROOT / "config/projects.json",
              ROOT / "config/distillation.json"]
    return {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in files}


def binding():
    raw = (ROOT / PLAN).read_bytes()
    text = raw.decode("utf-8")
    for expected in ("plan_id: mainline-a-v2-scenario-a-20261029\n", "contract_version: 5\n",
                     "status: EXECUTING\n", "requires_adr: false\n", "adr_file: null\n",
                     "session_id: null\n", "execution_instance: " + BASE.split("/")[-1] + "\n"):
        if expected not in text:
            raise ValueError("active_plan_boundary_conflict")
    declarations = [line.removeprefix("transport_capabilities: ") for line in text.splitlines()
                    if line.startswith("transport_capabilities: ")]
    if len(declarations) != 1 or strict_json(declarations[0]) != CAPS:
        raise ValueError("plan_and_driver_transport_contract_conflict")
    TransportCapabilities.from_dict(CAPS)
    return {"plan_path": PLAN, "plan_sha256": sha(raw), "contract_version": 5,
            "execution_instance": BASE.split("/")[-1], "transport": dict(CAPS),
            "implementation_sha256": implementation(), "session_id": None}


def check_binding():
    current = binding()
    if current != read("binding.receipt.json"):
        raise ValueError("external_contract_or_implementation_changed_stop_reconcile")
    return current


def arguments(prefix):
    now = int(time.time() * 1000)
    value = request(prefix + "-" + str(now), now)
    value.update(budget_policy=POLICY, transport=dict(CAPS))
    return value


def listener_check(before, after):
    return bool(before["listening_inodes"]) and before["listening_inodes"] == after["listening_inodes"]


def finish(phase, receipt, start, directory=BASE):
    receipt.update(phase=phase, command=command(phase), cwd=str(ROOT),
                   elapsed_seconds=round(time.monotonic() - start, 6),
                   driver_exit_code=0 if receipt["status"] == "pass" else 2,
                   controller_coverage="not_proven_platform_task_record_used")
    location = save(phase + ".receipt.json", receipt, directory)
    summary = {key: receipt.get(key) for key in ("status", "failures", "elapsed_seconds",
               "post_bytes", "coverage", "request_id", "driver_exit_code", "performance_targets")}
    summary["evidence"] = location
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False), flush=True)
    return receipt["driver_exit_code"]


def health():
    start = time.monotonic()
    receipt = {"status": "blocked", "failures": [], "generation_requests": 0,
               "gateway_restart_operations": 0, "target_write_operations": 0}
    try:
        bound = binding()
        offline_gate()
        historical.configs()
        before = historical.gateway_identity()
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = gateway_readonly_diagnostic.run()
        report = strict_json(captured.getvalue())
        after = historical.gateway_identity()
        receipt.update(diagnostic=report, listener_before=before, listener_after=after,
                       listener_scope="point_in_time_not_continuous_isolation",
                       pid_not_observable_not_fabricated=True)
        if (code != 0 or len(report["results"]) != 2
                or any(x.get("http_status") != 200 for x in report["results"])
                or not report["results"][0].get("health_reports_ok")
                or not listener_check(before, after)):
            raise ValueError("health_or_stable_listener_gate")
        receipt["blueprint"] = historical.blueprint_integrity()
        if receipt["blueprint"]["status"] != "pass":
            raise ValueError("historical_blueprint_integrity")
        receipt["binding_evidence"] = save("binding.receipt.json", bound)
        receipt["status"] = "pass"
    except (ValueError, TypeError, KeyError, OSError) as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:180])
    return finish("health", receipt, start)


def offline_gate():
    checked = read("offline-tests.receipt.json", OFFLINE_DIR)
    if (checked.get("status") != "pass" or checked.get("driver_exit_code") != 0
            or checked.get("implementation_sha256") != implementation()
            or checked.get("binding") != binding()):
        raise ValueError("related_offline_gate_missing_or_stale")
    rows = checked.get("commands", [])
    if len(rows) != 3 or any(row.get("actual_exit_code") != 0 or row.get("tests_run", 0) <= 0
                            or row.get("skipped", 0) != 0 for row in rows):
        raise ValueError("offline_tests_exit_count_or_skip_gate")
    return checked


def transport_measurements(raw, decoded, manifest):
    """计量实际字节；重组和双消费者认证仍各自是必需门禁。"""
    lines = raw.splitlines(keepends=True)
    notification_count = len(lines) - 1 if manifest is not None else 0
    measured = {"transfer_bytes": len(raw), "decoded_bytes": len(decoded),
                "notification_count": notification_count, "physical_frame_count": len(lines),
                "max_frame_bytes": max(map(len, lines), default=0),
                "decoded_sha256": sha(decoded), "manifest": manifest, "transport": dict(CAPS)}
    if (not lines or len(raw) > CAPS["max_transfer_bytes"]
            or len(decoded) > CAPS["max_decoded_bytes"]
            or notification_count > CAPS["max_chunks"]
            or len(lines) > CAPS["max_chunks"] + 1
            or measured["max_frame_bytes"] > CAPS["max_frame_bytes"]):
        raise ValueError("measured_transport_hard_limit")
    if manifest is not None:
        value = manifest["manifest"]
        if (value["count"] != notification_count or value["decoded_bytes"] != len(decoded)
                or value["sha256"] != measured["decoded_sha256"]):
            raise ValueError("measured_transport_manifest_conflict")
    measured["hard_limits_pass"] = True
    return measured


@snapshot_scope
def preflight():
    start = time.monotonic()
    attempt = next((i for i in (1, 2, 3) if not (ROOT / BASE / ("preflight-" + str(i))).exists()), None)
    if attempt is None:
        raise ValueError("three_cold_preflights_already_allocated_no_overwrite")
    directory = BASE + "/preflight-" + str(attempt)
    receipt = {"status": "blocked", "failures": [], "generation_requests": 0, "cold_sample": attempt,
               "outer_replays": 0, "target_write_operations": 0, "target_execution_operations": 0,
               "stages_seconds": {}, "transport": CAPS}
    save("allocation.receipt.json", {"cold_sample": attempt, "started_at_unix_ms": int(time.time() * 1000)}, directory)
    stages = receipt["stages_seconds"]
    try:
        check_binding()
        offline_gate()
        h = read("health.receipt.json")
        if h["status"] != "pass":
            raise ValueError("health_dependency_gate")
        before = historical.gateway_identity()
        if not listener_check(h["listener_after"], before):
            raise ValueError("listener_changed_after_health")
        projects, policy = historical.configs()
        args = arguments("mainline-a-cold-" + str(attempt))
        distiller = LiveV2Distiller(projects, policy)
        project, include, _, admission = distiller.validate_v2(args, 3)
        receipt["arguments"] = args
        receipt["negative_deadline_controls"] = historical.deadline_negative_controls(distiller, args)
        checkpoint = time.monotonic()
        scanner = Scanner(project, include, project.max_seconds)
        scanner.deadline = min(scanner.deadline, start + 27)
        structure = scanner.run()
        stages["scan"] = time.monotonic() - checkpoint
        receipt.update(coverage=structure["coverage"], snapshot_id=structure["snapshot_id"],
                       skipped_files=structure["unfinished"])
        hard = [x for x in structure["unfinished"] if x["reason"] != "unsupported_language_or_asset"]
        if (hard or not structure["inventory_complete"] or structure["freshness"] != "current_at_verification"
                or structure["coverage"]["parsed_files"] != 81):
            raise ValueError("full_81_file_snapshot_gate")
        scanner.deadline = start + 27
        reader = V2EvidenceReader(scanner, structure, start + 27, threading.Event(), budget_policy=POLICY)
        checkpoint = time.monotonic()
        question = args["question"] + " " + " ".join(d["question"] for d in args["dimensions"])
        package, selection = select_inventory(reader, question, budget_policy=POLICY)
        stages["inventory_and_static_package"] = time.monotonic() - checkpoint
        checkpoint = time.monotonic()
        core = form_core(package, reader.raw_files, **core_selection(package, question, budget_policy=POLICY))
        package = core.document["package"]
        stages["core_authentication"] = time.monotonic() - checkpoint
        checkpoint = time.monotonic()
        messages, post, selected = select_prompt(package, reader.raw_files, args["question"],
                                                args["dimensions"], structure["coverage"], tick=reader.tick)
        stages["question_view"] = time.monotonic() - checkpoint
        if encode_request(messages) != post or len(post) > POST_LIMIT or selected["unique_body_bytes"] > BODY_LIMIT:
            raise ValueError("exact_post_or_complete_body_gate")
        receipt.update(post_bytes=len(post), post_limit=POST_LIMIT, gateway_hard_limit=393216,
                       prompt_body_bytes=selected["unique_body_bytes"], body_limit=BODY_LIMIT,
                       selected_owner_count=len(selected["owners"]), selection=selected,
                       syntax_snapshot_metrics=snapshot_metrics())
        checkpoint = time.monotonic()
        definitions, calls, unit_ids, conditions = set(), set(), set(), set()
        for path, raw in reader.raw_files.items():
            value = extract_units(path, raw)
            definitions.update(u["qualified_name"] for u in value["units"] if u["kind"] == "definition")
            unit_ids.update(u["id"] for u in value["units"])
            conditions.update(c["id"] for c in value["conditions"])
            calls.update(ast.unparse(n.func) for n in ast.walk(parsed_tree(raw, path)) if isinstance(n, ast.Call))
        if ({u["id"] for u in package["units"]} != unit_ids
                or {c["id"] for c in package["conditions"]} != conditions or not calls or not definitions):
            raise ValueError("complete_unit_condition_or_nonempty_denominator_gate")
        frame = publish(core, admission)
        wire = frame.data
        decoded, manifest = reassemble(wire, TransportCapabilities.from_dict(CAPS),
                                       request_id=3, frame_budget=22016)
        receipt["static_transport"] = transport_measurements(wire, decoded, manifest)
        consumed = consume_frame(wire, reader.raw_files, protocol_version="2024-11-05", request_id=3,
                                 scenario="A", return_bytes=22016, project_id=project.project_id,
                                 policy_id=POLICY, transport=TransportCapabilities.from_dict(CAPS))
        independent = independent_receive(wire, reader.raw_files, CAPS, request_id=3,
                                          project_id=project.project_id, expected_definitions=definitions,
                                          expected_calls=calls)
        if consumed.package != independent or consumed.status != "complete":
            raise ValueError("offline_independent_consumption_conflict")
        stages["independent_denominators_and_transport"] = time.monotonic() - checkpoint
        receipt.update(static_unit_count=len(unit_ids), definition_name_count=len(definitions),
                       condition_count=len(conditions), call_expression_distinct_count=len(calls),
                       relation_site_count=len(package["relations"]), offline_static_consumption="pass",
                       static_wire_bytes=len(wire), static_max_frame_bytes=max(map(len, frame.frames))
                       if hasattr(frame, "frames") else len(frame.data))
        checkpoint = time.monotonic()
        reader.verify()
        stages["source_reverification"] = time.monotonic() - checkpoint
        after = historical.gateway_identity()
        if not listener_check(before, after):
            raise ValueError("listener_changed_during_preflight")
        receipt.update(listener_before=before, listener_after=after, freshness="current_at_final_preflight_verification")
        checkpoint = time.monotonic()
        receipt["evidence"] = [save("structure.json", structure, directory),
            save("sources.json", {p: base64.b64encode(raw).decode("ascii") for p, raw in reader.raw_files.items()}, directory),
            save("static-package.json", package, directory), save("http-post.json", post, directory),
            save("static.stdio.jsonl", wire, directory),
            save("denominators.json", {"definitions": sorted(definitions), "calls": sorted(calls),
                 "unit_ids": sorted(unit_ids), "condition_ids": sorted(conditions)}, directory)]
        stages["evidence_save_and_exact_readback"] = time.monotonic() - checkpoint
        elapsed = time.monotonic() - start
        receipt["performance_targets"] = {"candidate_and_selection_le_3s": sum(
            selected["elapsed_seconds"][k] for k in ("candidate_discovery", "selection_and_encoding")) <= 3,
            "cold_preflight_le_20s": elapsed <= 20, "internal_deadline_le_27s": elapsed < 27}
        if not all(receipt["performance_targets"].values()):
            raise ValueError("approved_preflight_performance_target_not_met")
        receipt["status"] = "pass"
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, RecursionError) as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:200])
    return finish("preflight", receipt, start, directory)


def preflight_gate():
    check_binding()
    offline_gate()
    rows = [read("preflight.receipt.json", BASE + "/preflight-" + str(i)) for i in (1, 2, 3)]
    if (any(r["status"] != "pass" or r["driver_exit_code"] != 0
            or r.get("static_transport", {}).get("hard_limits_pass") is not True
            or r["static_transport"].get("transport") != CAPS for r in rows)
            or len({r["snapshot_id"] for r in rows}) != 1):
        raise ValueError("three_cold_preflight_gate")
    return rows


class ChunkClient(StdioClient):
    """只扩展专用调用的接收，不改变旧生产客户端或其单帧语义。"""
    def __init__(self, *args, **kwargs):
        self.business_wire = bytearray()
        self.business = False
        super().__init__(*args, **kwargs)

    def receive(self, identity, *, deadline=None):
        stop = min(self.deadline, deadline) if deadline is not None else self.deadline
        while True:
            remaining = stop - time.monotonic()
            if remaining <= 0:
                raise ClientError("client_deadline_exhausted")
            events = self.selector.select(remaining)
            if not events:
                raise ClientError("client_deadline_exhausted")
            for selected, _ in events:
                chunk = os.read(selected.fileobj.fileno(), 65536)
                if not chunk:
                    self.selector.unregister(selected.fileobj)
                    if selected.data == "stdout":
                        raise ClientError("stdout_eof_before_response")
                    continue
                if selected.data == "stderr":
                    self.stderr.extend(chunk)
                    if len(self.stderr) > 8192:
                        raise ClientError("diagnostic_budget_exhausted")
                    continue
                self.stdout.extend(chunk)
                while b"\n" in self.stdout:
                    line, _, tail = self.stdout.partition(b"\n")
                    self.stdout = bytearray(tail)
                    raw = bytes(line) + b"\n"
                    if len(raw) > CAPS["max_frame_bytes"]:
                        raise ClientError("physical_frame_limit")
                    value = strict_json(line.decode("utf-8"))
                    if self.business:
                        self.business_wire.extend(raw)
                        if len(self.business_wire) > CAPS["max_transfer_bytes"]:
                            raise ClientError("cumulative_transfer_limit")
                    if type(value) is not dict or value.get("jsonrpc") != "2.0":
                        raise ClientError("stdout_protocol_pollution")
                    if "id" not in value:
                        if not self.business or value.get("method") != CHUNK_METHOD:
                            raise ClientError("unexpected_notification")
                        if self.business_wire.count(b"\n") > CAPS["max_chunks"]:
                            raise ClientError("notification_count_limit")
                        continue
                    if type(value["id"]) is not type(identity) or value["id"] != identity:
                        raise ClientError("unexpected_response_identity")
                    if self.stdout:
                        raise ClientError("trailing_or_duplicate_response")
                    self.last_frame = raw
                    return value
                if len(self.stdout) >= CAPS["max_frame_bytes"]:
                    raise ClientError("unterminated_physical_frame")


def live():
    start = time.monotonic()
    receipt = {"status": "blocked", "failures": [], "outer_replays": 0, "business_admissions": 0,
               "target_write_operations": 0, "gateway_restart_operations": 0,
               "host": "owned_engineering_stdio_client_not_Roo", "Roo_host_acceptance": "not_proven"}
    client = None
    try:
        rows = preflight_gate()
        before = historical.gateway_identity()
        if not listener_check(read("health.receipt.json")["listener_after"], before):
            raise ValueError("listener_changed_before_live")
        args = arguments("mainline-a-v5-live")
        receipt["request_id"] = args["request_id"]
        # 即使失败，该独占标记也封闭本实例；不得重新调用 live。
        receipt["admission_marker"] = save("live.admission.json", {"arguments": args,
            "authorized_max_admissions": 1, "external_retries": 0, "deadline_seconds": 180})
        client = ChunkClient(ROOT, online=True, deadline=start + 180, v2=True)
        discovery = client.initialize()
        if "error" in discovery or TOOL_NAME not in {t["name"] for t in discovery["result"]["tools"]}:
            raise ClientError("v2_tool_not_discovered")
        receipt["discovery"] = discovery
        client.business = True
        client.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": TOOL_NAME, "arguments": args}})
        receipt["business_admissions"] = 1
        response = client.receive(3)
        raw = bytes(client.business_wire)
        receipt["wire_evidence"] = save("live.stdio.jsonl", raw)
        decoded, manifest = reassemble(raw, TransportCapabilities.from_dict(CAPS), request_id=3, frame_budget=22016)
        receipt["transport_measurements"] = transport_measurements(raw, decoded, manifest)
        receipt["decoded_evidence"] = save("live.decoded.jsonl", decoded)
        receipt.update(manifest=manifest, transfer_bytes=len(raw), decoded_bytes=len(decoded),
                       max_frame_bytes=max(map(len, raw.splitlines(keepends=True))),
                       request_to_consumption_seconds=round(time.monotonic() - start, 6))
        if "error" in response:
            raise ClientError("business_response_error")
        trace, location = read_trace(ROOT, args["request_id"])
        receipt["gateway_requests"] = check_trace(trace, raw, args)
        receipt["trace_evidence"] = save("live.trace.json", (ROOT / location["path"]).read_bytes())
        post = trace.get("http_post_utf8", "").encode("utf-8")
        receipt["post_evidence"] = save("live.http-post.json", post)
        if (trace.get("failure") or trace["status"] != "complete" or not post or len(post) > POST_LIMIT
                or sha(post) != trace["http_post_sha256"] or len(receipt["gateway_requests"]) != 1):
            raise ClientError("single_complete_business_and_exact_post_gate")
        attempt = receipt["gateway_requests"][0]
        sse = base64.b64decode(attempt.get("raw_sse_base64", ""), validate=True)
        receipt["sse_evidence"] = save("live.gateway.sse", sse)
        if (not sse or len(sse) != attempt.get("raw_sse_bytes")
                or sha(sse) != attempt.get("raw_sse_sha256")
                or len(sse) != attempt.get("response_bytes")):
            raise ClientError("complete_raw_sse_capture_gate")
        if (attempt.get("http_status") != 200 or attempt.get("finish_reason") != "stop"
                or attempt.get("done_marker") is not True or attempt.get("message_bytes") != len(post)
                or attempt.get("message_sha256") != sha(post)):
            raise ClientError("normal_gateway_completion_or_actual_bytes_gate")
        if trace["structure"]["snapshot_id"] != rows[0]["snapshot_id"]:
            raise ClientError("source_changed_between_preflight_and_live")
        receipt.update(arguments=args, post_bytes=len(post), snapshot_id=trace["structure"]["snapshot_id"],
                       gateway_internal_sends="unknown", semantic_acceptance="pending_independent_review")
        receipt["status"] = "pass"
    except (ClientError, ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:200])
    finally:
        if client is not None:
            client.close()
            receipt.update(child_exit_code=client.exit_code, cleanup=client.cleanup,
                           stderr_bytes=len(client.stderr), stderr_sha256=sha(bytes(client.stderr)))
            if client.cleanup != "confirmed_natural_exit" or client.exit_code != 0 or client.stderr:
                receipt["status"] = "blocked"
                receipt["failures"].append("child_exit_or_cleanup_gate")
        if receipt.get("request_id") and receipt.get("wire_evidence"):
            try:
                delivery, loc = read_trace(ROOT, receipt["request_id"], ".delivery")
                telemetry, tloc = read_trace(ROOT, receipt["request_id"], ".telemetry")
                receipt["delivery_evidence"] = save("live.delivery.json", (ROOT / loc["path"]).read_bytes())
                receipt["telemetry_evidence"] = save("live.telemetry.json", (ROOT / tloc["path"]).read_bytes())
                if (delivery["written"] != receipt["wire_evidence"]["bytes"]
                        or delivery["frame_sha256"] != receipt["wire_evidence"]["sha256"]
                        or delivery["status"] != "frame_written_not_host_consumption"):
                    raise ValueError("delivery_not_actual_consumption")
            except (ClientError, ValueError, TypeError, KeyError, OSError) as error:
                receipt["status"] = "blocked"
                receipt["failures"].append(type(error).__name__ + ":delivery_readback_gate")
        after = historical.gateway_identity()
        if receipt.get("business_admissions") and not listener_check(before, after):
            receipt["status"] = "blocked"
            receipt["failures"].append("listener_changed_during_live")
        receipt["listener_after"] = after
        if time.monotonic() - start >= 180:
            receipt["status"] = "blocked"
            receipt["failures"].append("hard_business_deadline_gate")
    return finish("live", receipt, start)


@snapshot_scope
def consume():
    start = time.monotonic()
    receipt = {"status": "blocked", "failures": [], "generation_requests": 0,
               "semantic_acceptance": "pending_source_readback_review"}
    try:
        preflight_gate()
        live_receipt = read("live.receipt.json")
        if live_receipt["status"] != "pass":
            raise ValueError("live_dependency_gate")
        trace = read("live.trace.json")
        projects, _ = historical.configs()
        inputs = source_inputs(projects["fastcode-sample"], trace["arguments"], trace, start + 27)
        raw = (ROOT / BASE / "live.stdio.jsonl").read_bytes()
        decoded, manifest = reassemble(raw, TransportCapabilities.from_dict(CAPS),
                                       request_id=3, frame_budget=22016)
        receipt["transport_measurements"] = transport_measurements(raw, decoded, manifest)
        if receipt["transport_measurements"] != live_receipt.get("transport_measurements"):
            raise ValueError("saved_live_transport_measurement_conflict")
        denominators = read("denominators.json", BASE + "/preflight-1")
        consumed = consume_frame(raw, inputs, protocol_version="2024-11-05", request_id=3, scenario="A",
            return_bytes=22016, project_id="fastcode-sample", policy_id=POLICY,
            transport=TransportCapabilities.from_dict(CAPS))
        independent = independent_receive(raw, inputs, CAPS, request_id=3, project_id="fastcode-sample",
            expected_definitions=denominators["definitions"], expected_calls=denominators["calls"])
        if consumed.package != independent or consumed.status != "complete" or consumed.core.document != trace["core"]:
            raise ValueError("independent_core_or_complete_status_conflict")
        package = consumed.package
        if ({u["id"] for u in package["units"]} != set(denominators["unit_ids"])
                or {c["id"] for c in package["conditions"]} != set(denominators["condition_ids"])):
            raise ValueError("full_unit_and_condition_identity_denominator")
        # expected 仅在真实接收、独立消费后读取；永不传入生产选择。
        frozen = read("fastcode_acceptance.json", "tests")
        evaluation = evaluate_frozen({"package": package, "core": trace["core"], "version": 2}, frozen)
        if len(evaluation["checks"]) != 13 or not all(c["body_coverage"] for c in evaluation["checks"]):
            raise ValueError("required_13_reference_anchors")
        prompt = strict_json(trace["http_post_utf8"])
        payload = strict_json(prompt["messages"][-1]["content"])
        prompt_sources = payload["sources"]
        source_map = {s["id"]: s for s in package["sources"]}
        review_items = []
        candidate = trace["model_outputs"][0]["candidate"]
        for i, conclusion in enumerate(candidate["conclusions"]):
            evidence = []
            for sid in conclusion["source_ids"]:
                source = source_map[sid]
                unit = package["units"][source["unit"]]
                owners = [s for s in prompt_sources if sid in s["alias_source_ids"]]
                if len(owners) != 1:
                    raise ValueError("conclusion_reference_not_uniquely_in_sent_body")
                evidence.append({"source_id": sid, "path": package["files"][source["file"]]["path"],
                                 "symbol": unit["qualified_name"], "start": unit["start"], "end": unit["end"],
                                 "complete_sent_owner_body": owners[0]["text"]})
            review_items.append({"index": i, **conclusion, "evidence": evidence})
        receipt["four_dimensions"] = {d["id"]: sum(c["dimension_id"] == d["id"] for c in candidate["conclusions"])
                                      for d in trace["arguments"]["dimensions"]}
        if len(receipt["four_dimensions"]) != 4 or not all(receipt["four_dimensions"].values()):
            raise ValueError("required_dimension_conclusion_missing")
        receipt["evidence"] = [save("consumed-package.json", consumed.package_bytes),
            save("consumed-sources.json", {p: base64.b64encode(r).decode("ascii") for p, r in inputs.items()}),
            save("reference-evaluation.json", evaluation), save("semantic-review-items.json", review_items)]
        receipt.update(core_id=trace["core"]["core_id"], snapshot_id=package["snapshot_id"],
                       reference_anchor_count=13, conclusion_count=len(review_items),
                       evidence_scope="independent_identity_and_source_authentication_not_semantic_truth")
        if time.monotonic() - start >= 27:
            raise ValueError("offline_consumption_internal_deadline")
        receipt["status"] = "pass"
    except (ClientError, ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:200])
    return finish("consume", receipt, start)


def reconcile():
    start = time.monotonic()
    receipt = {"status": "blocked", "failures": [], "business_retested": False,
               "Roo_host_consumption": "not_proven", "human_review": "not_claimed",
               "adr_promotions": 0, "fact_baseline_writes": 0, "archive_operations": 0}
    try:
        bound = check_binding()
        rows = preflight_gate()
        live_receipt, consumed, review = read("live.receipt.json"), read("consume.receipt.json"), read("semantic-review.json")
        items = read("semantic-review-items.json")
        if live_receipt["status"] != "pass" or consumed["status"] != "pass":
            raise ValueError("live_or_consume_dependency_gate")
        if (review.get("status") != "pass" or review.get("reviewer_kind") != "human"
                or not review.get("reviewer_identity") or not review.get("reviewed_at")
                or review.get("core_id") != consumed["core_id"]
                or review.get("items_sha256") != sha((ROOT / BASE / "semantic-review-items.json").read_bytes())
                or {r["index"] for r in review.get("conclusions", [])} != set(range(len(items)))
                or any(r.get("assessment") != "supported_static_inference" or not r.get("basis")
                       for r in review["conclusions"])
                or set(review.get("reference_anchors", {})) != {c["id"] for c in read("reference-evaluation.json")["checks"]}
                or any(v.get("status") != "pass" for v in review["reference_anchors"].values())):
            raise ValueError("independent_semantic_source_readback_gate")
        evidence = []
        for path in sorted((ROOT / BASE).rglob("*")):
            if path.is_symlink():
                raise ValueError("evidence_symlink")
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if path.read_bytes() != raw:
                raise ValueError("evidence_changed_at_reconciliation")
            evidence.append({"path": str(path.relative_to(ROOT)), "bytes": len(raw), "sha256": sha(raw)})
        if historical.blueprint_integrity()["status"] != "pass":
            raise ValueError("protected_blueprint_changed")
        receipt.update(binding=bound, evidence=evidence, preflight_samples=3,
                       post_bytes=live_receipt["post_bytes"], coverage=rows[0]["coverage"],
                       transfer_bytes=live_receipt["transfer_bytes"], decoded_bytes=live_receipt["decoded_bytes"],
                       max_frame_bytes=live_receipt["max_frame_bytes"], core_id=consumed["core_id"],
                       four_dimensions=consumed["four_dimensions"], semantic_review=review["reviewer_kind"],
                       human_review="recorded_human_source_and_anchor_review_not_identity_attestation",
                       all_required_engineering_gates="pass", archival_ready=True,
                       actual_archive_authorized=False, active_plan_retained=True)
        receipt["status"] = "pass"
    except (ValueError, TypeError, KeyError, OSError) as error:
        receipt["failures"].append(type(error).__name__ + ":" + str(error)[:200])
    return finish("reconcile", receipt, start)


def main():
    phases = {"health": health, "preflight": preflight, "live": live, "consume": consume, "reconcile": reconcile}
    if len(sys.argv) != 2 or sys.argv[1] not in phases:
        print("只允许 health / preflight / live / consume / reconcile", file=sys.stderr)
        return 2
    return phases[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
