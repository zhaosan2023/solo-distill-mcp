"""授权范围内的离线物理容量反证；不调用模型、不修改来源、不推进依赖。"""
from __future__ import annotations

import base64
import json
import signal
import time

from mainline_a_20261029 import ROOT, OUT, save, sha, configs, arguments, encoded
from solo_distill.distillation import SYSTEM_PROMPT
from solo_distill.gateway import MODEL, MAX_MESSAGE_BYTES
from solo_distill.syntax_v2 import extract_units, body_text, identity
from solo_distill.telemetry import BudgetTelemetry, POLICY

COMMAND = "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_capacity_diagnostic_v3_20261029.py </dev/null"
PREFLIGHT_COMMAND = "PYTHONPATH=src:tests timeout -k 2s 30s python3 -B verification/.scratch/mainline_a_20261029.py preflight </dev/null"


def alarm(_signum, _frame):
    raise TimeoutError("capacity_diagnostic_26_second_budget")


def main():
    started = time.monotonic()
    receipt = {
        "kind": "offline_capacity_diagnostic_not_scheduler_acceptance",
        "contract_version": 3, "status": "blocked", "command": COMMAND,
        "cwd": str(ROOT), "generation_requests": 0, "outer_replays": 0,
        "target_writes": 0, "target_execution": 0, "gateway_restarts": 0,
        "preceding_preflight": {
            "command": PREFLIGHT_COMMAND, "cwd": str(ROOT), "actual_exit_code": 124,
            "provenance": "client_execute_command_result_call_0303dfe2ea45324ae5f21584",
            "stdout": "", "qualified_preflight_receipt": False,
        },
        "scheduler_execution": "preceding_real_select_inventory_timed_out_not_passed",
        "scope": "saved_full_source_snapshot_and_deterministic_mandatory_body_cost_only",
        "live_gate": "blocked_until_required_preflight_passes",
    }
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(26)
    try:
        configs()
        snapshots = {}
        for name in ("preflight.sources.json", "preflight.structure.json", "health.receipt.json"):
            raw = (ROOT / OUT / name).read_bytes()
            snapshots[name] = json.loads(raw)
            receipt.setdefault("source_receipts", []).append({
                "path": OUT + "/" + name, "bytes": len(raw), "sha256": sha(raw),
                "readback": "existing_bytes_read_not_new_execution",
            })
        structure = snapshots["preflight.structure.json"]
        files = {path: base64.b64decode(text, validate=True)
                 for path, text in snapshots["preflight.sources.json"].items()}
        entries = {entry["path"]: entry for entry in structure["entries"]
                   if entry["status"] == "parsed"}
        if set(entries) != set(files) or not files:
            raise ValueError("saved_snapshot_denominator_conflict")
        if any(sha(raw) != entries[path]["content_sha256"] for path, raw in files.items()):
            raise ValueError("saved_snapshot_source_digest_conflict")
        receipt["snapshot_id"] = structure["snapshot_id"]
        receipt["coverage"] = structure["coverage"]
        receipt["python_files"] = len(files)
        receipt["raw_source_bytes"] = sum(map(len, files.values()))
        owners, all_units, conditions = [], [], 0
        for path, raw in sorted(files.items()):
            parsed = extract_units(path, raw)
            units = parsed["units"]
            all_units.extend(unit["id"] for unit in units)
            conditions += len(parsed["conditions"])
            for owner in units:
                if len(owner["syntax_path"]) != 2 or owner["syntax_path"][0] != "body":
                    continue
                aliases = sorted((unit for unit in units
                                  if unit["syntax_path"][:2] == owner["syntax_path"]),
                                 key=lambda unit: unit["id"])
                owners.append((identity("owner-v2", owner["id"]), {
                    "source_id": identity("source-v2", owner["id"]),
                    "alias_source_ids": [identity("source-v2", unit["id"]) for unit in aliases],
                    "path": path, "symbol": owner["qualified_name"],
                    "text": body_text(raw, parsed["file"], owner),
                }))
        owners.sort(key=lambda item: item[0])
        args = arguments()
        payload = {"operation": "compression", "question": args["question"],
                   "dimensions": args["dimensions"], "sources": [item for _, item in owners],
                   "coverage": structure["coverage"]}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":"))}]
        post = json.dumps({"model": MODEL, "messages": messages, "stream": True},
                          ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        body_cost = sum(len(item["text"].encode("utf-8")) for _, item in owners)
        telemetry = BudgetTelemetry(run_id=args["request_id"], project_id=args["project_id"],
                                    snapshot_id=structure["snapshot_id"], contract_version="3")
        telemetry.bodies({key: len(item["text"].encode("utf-8")) for key, item in owners})
        receipt.update(
            syntax_units=len(all_units), conditions=conditions, mandatory_unique_owners=len(owners),
            complete_mandatory_body_utf8_bytes=body_cost, source_body_soft_target_bytes=98304,
            http_post_bytes=len(post), http_post_hard_limit_bytes=MAX_MESSAGE_BYTES,
            mandatory_body_alone_exceeds_http_cap=body_cost > MAX_MESSAGE_BYTES,
            http_post_exceeds_hard_cap=len(post) > MAX_MESSAGE_BYTES,
            telemetry=telemetry.snapshot(),
            cost_derivation="all registered top-level complete syntax owners, nested aliases charged once; owner and alias order matches canonical_package",
            derivation_sources=["src/solo_distill/live_v2.py:132", "src/solo_distill/live_v2.py:145",
                                "src/solo_distill/live_v2.py:311", "src/solo_distill/package_v2.py:41",
                                "src/solo_distill/core_v2.py:52"],
            producer_equivalence="source-inspected_shape_not_completed_scheduler_or_live_runtime_verification",
            post_evidence=save("capacity-diagnostic.http-post.json", post),
        )
        if body_cost > MAX_MESSAGE_BYTES:
            receipt["conclusion"] = "complete_mandatory_body_alone_exceeds_single_post_hard_cap; navigation_paging_or_stdio_chunking_cannot_reduce_it"
        elif len(post) > MAX_MESSAGE_BYTES:
            receipt["conclusion"] = "complete_mandatory_post_shape_exceeds_hard_cap"
        else:
            receipt["conclusion"] = "capacity_shape_fits_but_real_scheduler_preflight_remains_unverified"
        receipt["diagnostic_status"] = "completed_not_business_acceptance"
        receipt["diagnostic_exit_code"] = 0
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        receipt["diagnostic_status"] = "blocked"
        receipt["failure"] = type(error).__name__ + ":" + str(error)[:160]
        receipt["diagnostic_exit_code"] = 2
    finally:
        signal.alarm(0)
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 6)
    location = save("capacity-diagnostic.receipt.json", receipt)
    print(json.dumps({"diagnostic_status": receipt["diagnostic_status"], "task_status": "BLOCKED",
                      "mandatory_body_bytes": receipt.get("complete_mandatory_body_utf8_bytes"),
                      "post_bytes": receipt.get("http_post_bytes"), "hard_cap": MAX_MESSAGE_BYTES,
                      "failure": receipt.get("failure"), "evidence": location}, ensure_ascii=False), flush=True)
    return receipt["diagnostic_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
