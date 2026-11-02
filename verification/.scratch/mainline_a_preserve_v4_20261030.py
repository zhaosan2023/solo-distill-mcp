"""获准 Phase 1 原字节保全；仅保存本程序证据，不修订计划、不访问网关。"""
from __future__ import annotations

import json
import time

import mainline_a_20261029 as historical

BASE = "verification/mainline-a-scouting-v5-20261030T011416Z-capacity"


def main():
    started = time.monotonic()
    source = historical.ROOT / historical.PLAN
    if source.is_symlink() or not source.is_file():
        raise ValueError("active_plan_not_regular")
    original = source.read_bytes()
    text = original.decode("utf-8")
    for expected in ("plan_id: mainline-a-v2-scenario-a-20261029\n",
                     "contract_version: 4\n", "status: BLOCKED\n"):
        if expected not in text:
            raise ValueError("v4_preservation_binding_conflict")
    historical.OUT = BASE
    saved = historical.save("plan-v4.original.md", original)
    backup = historical.ROOT / saved["path"]
    if source.read_bytes() != original or backup.read_bytes() != original:
        raise ValueError("plan_changed_or_preservation_not_exact")
    receipt = {
        "status": "pass", "purpose": "phase_1_original_bytes_preservation_not_archive",
        "source_path": historical.PLAN, "source_contract_version": 4,
        "plan_id": "mainline-a-v2-scenario-a-20261029", "session_id": None,
        "source_bytes": len(original), "source_sha256": historical.sha(original),
        "evidence": saved, "source_unchanged": True, "exact_readback": True,
        "gateway_requests": 0, "source_write_operations": 0, "archive_operations": 0,
        "command": "PYTHONPATH=src:tests timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_preserve_v4_20261030.py </dev/null",
        "cwd": str(historical.ROOT), "driver_exit_code": 0,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    location = historical.save("plan-v4-preservation.receipt.json", receipt)
    print(json.dumps({"status": "pass", "original": saved, "receipt": location,
                      "source_unchanged": True, "archive_operations": 0}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
