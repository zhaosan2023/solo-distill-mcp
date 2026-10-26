"""Bounded local gateway status inspection; no generation, retries or changes.

Never print account identities, credentials, raw response bodies or error prose.
Health and pool statistics cannot prove model availability or prior root cause.
"""
from __future__ import annotations

import http.client
import json
import time

from solo_distill.gateway import strict_json

MAX_BYTES = 16384


def run():
    started = time.monotonic()
    deadline = started + 12
    results = []
    for path in ("/health", "/v1/stats"):
        item = {"path": path, "http_status": None, "status": "unknown"}
        results.append(item)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            item["failure"] = "diagnostic_deadline_exhausted"
            break
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=min(4, remaining))
        response = None
        try:
            connection.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
            transport_socket = connection.sock
            response = connection.getresponse()
            item["http_status"] = response.status
            body = bytearray()
            while not response.isclosed():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("diagnostic_deadline")
                # A complete Content-Length response can close its file/socket
                # during read1. Do not reset a closed descriptor on the next turn.
                if transport_socket is not None and transport_socket.fileno() >= 0:
                    transport_socket.settimeout(min(4, remaining))
                chunk = response.read1(min(4096, MAX_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("diagnostic_response_budget")
            item["response_bytes"] = len(body)
            if response.status in (401, 403):
                item["failure"] = "authorization_required_stop"
                break
            if response.status != 200:
                item["failure"] = "diagnostic_http_error"
                continue
            value = strict_json(body.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("diagnostic_schema")
            if path == "/health":
                item["health_reports_ok"] = value.get("status") == "ok"
            else:
                safe_fields = ("ActiveUsedTokens", "ActiveRemaining", "HasStandby", "IsStandbyProvisioning")
                item["pool_counters"] = {key: value[key] for key in safe_fields
                                         if key in value and type(value[key]) in (int, bool)}
                admission = value.get("Admission")
                if isinstance(admission, dict):
                    item["admission_counters"] = {key: counter for key, counter in admission.items()
                                                  if type(counter) in (int, bool)}
                item["account_identity_omitted"] = True
            item["status"] = "read_only_status_observed"
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exception:
            item["failure"] = type(exception).__name__
        finally:
            if response is not None:
                response.close()
            connection.close()
    report = {"kind": "gateway_readonly_diagnostic", "endpoint": "http://127.0.0.1:8080",
              "observed_at_unix_ms": int(time.time() * 1000), "results": results,
              "elapsed_seconds": round(time.monotonic() - started, 6),
              "generation_requests": 0, "outer_replays": 0, "gateway_changes": 0,
              "previous_503_root_cause": "not_established_by_status_snapshot",
              "integration_gate": "remains_blocked"}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
    return 0 if len(results) == 2 and all(item["status"] == "read_only_status_observed"
                                        for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(run())
