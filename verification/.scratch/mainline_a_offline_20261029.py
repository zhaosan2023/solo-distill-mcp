"""串行执行获准离线门禁，保存真实退出、完整输出及实现绑定；不启动网关。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import mainline_a_scouting_20261029 as driver

SUITES = [
    ["test_mainline_a_scouting", "test_live_v2", "test_adaptive_backpressure"],
    ["test_v2_contract", "test_v2_scheduler", "test_v2_lifecycle"],
    ["test_v2_consumer", "test_v2_wire", "test_stdio_runtime"],
]


def main():
    import re
    started = time.monotonic()
    initial_binding = driver.binding()
    initial = initial_binding["implementation_sha256"]
    rows = []
    for index, suites in enumerate(SUITES, 1):
        argv = ["timeout", "-k", "2s", "30s", "python3", "-B", "-m", "unittest", "-v", *suites]
        command = "PYTHONPATH=src:tests " + " ".join(argv) + " </dev/null"
        checkpoint = time.monotonic()
        with open(os.devnull, "rb") as stdin:
            completed = subprocess.run(argv, cwd=driver.ROOT, stdin=stdin,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": "src:tests"}, check=False)
        output = completed.stdout
        location = driver.save("offline-suite-" + str(index) + ".output.txt", output,
                               directory=driver.OFFLINE_DIR)
        counts = re.findall(rb"^Ran (\d+) tests? in ([0-9.]+)s$", output, re.MULTILINE)
        skipped = re.findall(rb"^.*\.\.\. skipped .*$", output, re.MULTILINE)
        tests_run = int(counts[-1][0]) if len(counts) == 1 else 0
        passed = (completed.returncode == 0 and tests_run > 0 and not skipped
                  and re.search(rb"^OK$", output, re.MULTILINE) is not None)
        row = {"command": command, "cwd": str(driver.ROOT),
               "actual_exit_code": completed.returncode, "tests_run": tests_run,
               "skipped": len(skipped), "elapsed_seconds": round(time.monotonic() - checkpoint, 6),
               "status": "pass" if passed else "blocked", "output": location}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not passed:
            break
    stable = driver.binding() == initial_binding
    passed = len(rows) == 3 and stable and all(r["status"] == "pass" for r in rows)
    receipt = {"status": "pass" if passed else "blocked", "commands": rows,
               "binding": initial_binding,
               "command": "PYTHONPATH=src:tests timeout -k 2s 105s python3 -B verification/.scratch/mainline_a_offline_20261029.py </dev/null",
               "cwd": str(driver.ROOT), "driver_exit_code": 0 if passed else 2,
               "implementation_sha256": initial, "implementation_stable": stable,
               "target_execution_operations": 0, "gateway_requests": 0,
               "elapsed_seconds": round(time.monotonic() - started, 6),
               "controller_coverage": "not_proven_platform_task_record_used"}
    location = driver.save("offline-tests.receipt.json", receipt, directory=driver.OFFLINE_DIR)
    print(json.dumps({"status": receipt["status"], "evidence": location}, ensure_ascii=False), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
