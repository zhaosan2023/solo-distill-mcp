"""阶段 4 受影响定向验证：单组前台、30 秒进程硬限、日志独占复读。
不访问网关、不安装依赖、不执行来源，不编排真实三样本或在线业务。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path.cwd()
SUPPORT = ROOT / "verification/.scratch/mainline_a_schema_v3_phase4_20261031.py"
GROUPS = {
    "cold-reuse": ["test_schema_v3_cold_reuse", "test_schema_v3_cold_retry",
                   "test_schema_v3_cold_chain.ColdChainTests"],
    "macro": ["test_schema_v3_macro"],
    "local": ["test_schema_v3_local", "test_schema_v3_package", "test_schema_v3_core"],
    "selection": ["test_schema_v3_selection_oracle"],
    "task": ["test_schema_v3_task_oracle"],
    "wire": ["test_schema_v3_wire_oracle"],
    "production-macro": ["test_schema_v3_production_macro"],
    "production-local": ["test_schema_v3_production_local"],
    "production-task": ["test_schema_v3_task"],
    "production-wire": ["test_schema_v3_wire", "test_schema_v3_publish_api"],
    "preparation": ["test_schema_v3_preparation"],
    "stdio": ["test_schema_v3_stdio.ServiceBoundaryTests", "test_schema_v3_stdio.StdioIntegrationTests"],
    "v2-contract": ["test_v2_contract"],
    "v2-consumer": ["test_v2_consumer"],
    "v2-scheduler": ["test_v2_scheduler"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, choices=GROUPS)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("phase4_support", SUPPORT)
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    bound = support.binding()
    argv = ["timeout", "-s", "KILL", "30s", sys.executable, "-B", "-S", "-m",
            "unittest", "-v", *GROUPS[args.group]]
    stem = args.group + ".structure-20261031T0447Z"
    for suffix in ("output.txt", "receipt.json"):
        if os.path.lexists(ROOT / support.PARENT / (stem + "." + suffix)):
            raise FileExistsError("reuse_verification_evidence_exists")
    started = time.monotonic()
    with open(os.devnull, "rb") as stdin:
        done = subprocess.run(argv, cwd=ROOT, stdin=stdin, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": "src:tests"}, check=False)
    elapsed = time.monotonic() - started
    output = done.stdout
    guard = support.Guard()
    workspace = support.open_root(str(ROOT))
    try:
        parent = support.open_relative(workspace, support.PARENT, directory=True)
        try:
            digest = support._save_exact(parent, stem + ".output.txt", output, guard)
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.close(workspace)
    counts = re.findall(rb"^Ran (\d+) tests? in ([0-9.]+)s$", output, re.MULTILINE)
    tests = int(counts[0][0]) if len(counts) == 1 else 0
    skips = len(re.findall(rb"^.*\.\.\. skipped .*$", output, re.MULTILINE))
    passed = (done.returncode == 0 and tests > 0 and skips == 0
              and elapsed < 30 and re.search(rb"^OK$", output, re.MULTILINE) is not None)
    receipt = {"receipt_version": 1, "status": "pass" if passed else "blocked",
        "binding": bound, "group": args.group, "argv": argv,
        "command": "env PYTHONPATH=src:tests " + " ".join(argv) + " </dev/null",
        "cwd": str(ROOT), "actual_exit_code": done.returncode,
        "elapsed_seconds": elapsed, "tests_run": tests, "required_skips": skips,
        "output": {"path": support.PARENT + "/" + stem + ".output.txt",
                   "bytes": len(output), "sha256": digest, "exact_readback": True},
        "gateway_requests": 0, "source_execution_operations": 0,
        "controller_coverage": "not_proven_platform_task_record_used"}
    evidence = support.save(stem + ".receipt.json", receipt, guard)
    print(json.dumps({"receipt": receipt, "evidence": evidence}, ensure_ascii=False), flush=True)
    if not passed:
        print(output[-8192:].decode("utf-8", errors="replace"), file=sys.stderr, flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
