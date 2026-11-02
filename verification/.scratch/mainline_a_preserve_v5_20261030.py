"""主线 A 获准接管：独占保全版本 5 原字节，不继承历史通过证据。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
PLAN = "_bmad-output/mainline-a-v2-scenario-a-20261029.md"
BASE = "verification/mainline-a-schema-v3-20261030T095713Z"
COMMAND = "timeout -k 2s 15s python3 -B verification/.scratch/mainline_a_preserve_v5_20261030.py </dev/null"


def save(path, raw):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("preservation_symlink")
    with target.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        import os
        os.fsync(handle.fileno())
    if target.read_bytes() != raw:
        raise ValueError("preservation_exact_readback_failed")
    return {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    started = time.monotonic()
    source = ROOT / PLAN
    if source.is_symlink() or not source.is_file():
        raise ValueError("active_plan_not_regular")
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    for expected in ("plan_id: mainline-a-v2-scenario-a-20261029\n",
                     "contract_version: 5\n", "status: EXECUTING\n"):
        if expected not in text:
            raise ValueError("v5_preservation_binding_conflict")
    original = save(BASE + "/plan-v5.original.md", raw)
    if source.read_bytes() != raw:
        raise ValueError("source_changed_during_preservation")
    receipt = {"status": "pass", "purpose": "original_bytes_preservation_not_completed_archive",
               "source_path": PLAN, "source_contract_version": 5,
               "plan_id": "mainline-a-v2-scenario-a-20261029", "session_id": None,
               "source_unchanged": True, "exact_readback": True, "original": original,
               "command": COMMAND, "cwd": str(ROOT), "driver_exit_code": 0,
               "elapsed_seconds": time.monotonic() - started,
               "gateway_requests": 0, "target_execution_operations": 0,
               "archive_operations": 0, "historical_passes_inherited": False}
    encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode("utf-8")
    location = save(BASE + "/plan-v5-preservation.receipt.json", encoded)
    print(json.dumps({"status": "pass", "original": original, "receipt": location,
                      "source_unchanged": True, "driver_exit_code": 0}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
