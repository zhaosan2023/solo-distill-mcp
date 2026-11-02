"""Step 4: closed-input offline handshake; no host changes or target writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "/usr/bin/python3"
EXPECTED_TOOL = "solo_extract_structure_v1"
MESSAGES = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "offline-test", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]

def main():
    if sys.version_info[:3] != (3, 12, 3):
        print(json.dumps({"status": "BLOCKED", "reason": "system_python_version_changed", "actual": sys.version}))
        return 1
    command = ["timeout", "--kill-after=1s", "12s", PYTHON, "-B", "-m",
               "solo_distill.server", "--config", "config/projects.json"]
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                   "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    payload = b"".join(json.dumps(message, separators=(",", ":")).encode() + b"\n"
                       for message in MESSAGES)
    started = time.monotonic()
    process = subprocess.run(command, cwd=ROOT, env=environment, input=payload,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    lines = process.stdout.splitlines()
    reasons = []
    responses = []
    if process.returncode != 0:
        reasons.append("nonzero_or_timeout")
    if process.stderr:
        reasons.append("stderr_not_empty")
    if len(lines) != 2 or not process.stdout.endswith(b"\n") or b"\r" in process.stdout:
        reasons.append("stdout_not_exactly_two_clean_lines")
    try:
        responses = [json.loads(line) for line in lines]
        if len(responses) != 2:
            raise ValueError("response_count")
        for index, response in enumerate(responses, 1):
            if set(response) != {"jsonrpc", "id", "result"} or response["jsonrpc"] != "2.0" or response["id"] != index:
                raise ValueError("invalid_rpc_response")
            if json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() != lines[index - 1]:
                raise ValueError("noncanonical_or_impure_stdout")
        if responses[0]["result"]["protocolVersion"] != "2024-11-05":
            raise ValueError("protocol_mismatch")
        tools = responses[1]["result"]["tools"]
        if [tool["name"] for tool in tools] != [EXPECTED_TOOL]:
            raise ValueError("unexpected_tool_list")
    except (ValueError, KeyError, TypeError) as exception:
        reasons.append(str(exception))
    receipt = {"stage": 1, "status": "PASS" if not reasons else "BLOCKED",
               "cwd": str(ROOT), "command": command, "python": sys.version,
               "exit_code": process.returncode, "elapsed_seconds": round(time.monotonic() - started, 6),
               "stderr_bytes": len(process.stderr), "stderr": process.stderr.decode("utf-8", errors="replace"),
               "stdout_bytes": len(process.stdout), "stdout_line_count": len(lines),
               "stdout": process.stdout.decode("utf-8", errors="replace"), "reasons": reasons,
               "distillation_config_supplied": False, "input_message_count": 3,
               "tool_names": [tool.get("name") for tool in responses[1].get("result", {}).get("tools", [])]
                             if len(responses) == 2 and isinstance(responses[1], dict) else []}
    print(json.dumps(receipt, ensure_ascii=False, allow_nan=False))
    return 0 if not reasons else 1

if __name__ == "__main__":
    raise SystemExit(main())
