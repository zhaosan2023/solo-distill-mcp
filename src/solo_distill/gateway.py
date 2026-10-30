"""Fixed loopback model transport. No SDK retries, redirects or target imports."""
from __future__ import annotations

import base64
import http.client
import json
import hashlib
import socket
import threading
import time
from typing import Any

MODEL = "gpt-6-sol"
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
MAX_MESSAGE_BYTES = 384 * 1024
# Delivery size is enforced by the local Pin, not by cutting the model stream.


class GatewayError(Exception):
    def __init__(self, reason: str, observations: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.observations = observations or {}


def strict_json(text: str) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value
    def constant(value):
        raise ValueError("non_finite_json")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def candidate_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0] not in {"```", "```json"} or lines[-1] != "```":
            raise ValueError("incomplete_or_unknown_fence")
        stripped = "\n".join(lines[1:-1])
    return strict_json(stripped)


def encode_request(messages: list[dict[str, str]]) -> bytes:
    """Exact network request encoding, with no connection or other I/O."""
    return json.dumps({"model": MODEL, "messages": messages, "stream": True},
                      ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


class Gateway:
    """One HTTP request per call; upstream recovery belongs to the existing gateway."""
    def __init__(self, *, capture_stream: bool = False):
        self.capture_stream = capture_stream
        self._lock = threading.Lock()
        self._connection: http.client.HTTPConnection | None = None
        self._socket: socket.socket | None = None

    def cancel(self) -> None:
        with self._lock:
            connection = self._connection
            transport_socket = self._socket
        # HTTPConnection may detach its socket into HTTPResponse when the peer
        # sends Connection: close. Retain the actual transport for cancellation.
        if transport_socket is not None:
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if connection is not None:
            connection.close()

    def generate(self, messages: list[dict[str, str]], deadline: float,
                 cancelled: threading.Event) -> tuple[str, dict[str, Any]]:
        started = time.monotonic()
        observation: dict[str, Any] = {
            "endpoint": ENDPOINT, "request_model": MODEL,
            "reported_models": [], "supplier_identity": "unknown",
            "internal_sends": "unknown", "internal_usage": "unknown",
            "cost": "unknown", "effective_gateway_policy": "unknown",
            "outer_replays": 0, "client_auto_retries": 0,
            "http_status": None, "ttft_seconds": None,
            "finish_reason": None, "done_marker": False, "usage": "unknown",
        }
        def remaining() -> float:
            if cancelled.is_set():
                raise GatewayError("cancelled", observation)
            value = deadline - time.monotonic()
            if value <= 0:
                raise GatewayError("gateway_deadline_exhausted", observation)
            return value
        payload = encode_request(messages)
        observation["message_bytes"] = len(payload)
        observation["message_sha256"] = hashlib.sha256(payload).hexdigest()
        if len(payload) > MAX_MESSAGE_BYTES:
            raise GatewayError("message_budget_exhausted", observation)
        connection = http.client.HTTPConnection("127.0.0.1", 8080,
                                               timeout=min(10.0, remaining()))
        with self._lock:
            self._connection = connection
        answer: list[str] = []
        captured = bytearray() if self.capture_stream else None
        try:
            remaining()
            connection.request("POST", "/v1/chat/completions", body=payload,
                               headers={"Content-Type": "application/json",
                                        "Accept": "text/event-stream"})
            transport_socket = connection.sock
            with self._lock:
                self._socket = transport_socket
            remaining()
            if transport_socket is not None:
                transport_socket.settimeout(min(30.0, remaining()))
            response = connection.getresponse()
            observation["http_status"] = response.status
            if response.status != 200:
                reason = "authentication_required" if response.status in {401, 403} else "gateway_http_error"
                raise GatewayError(reason, observation)
            if "text/event-stream" not in response.getheader("Content-Type", "").lower():
                raise GatewayError("expected_event_stream", observation)
            buffer = b""
            event: list[bytes] = []
            total = 0
            terminal = False
            def consume() -> None:
                nonlocal terminal
                if not event:
                    return
                data = b"\n".join(event)
                event.clear()
                if terminal:
                    raise GatewayError("data_after_done", observation)
                if data == b"[DONE]":
                    observation["done_marker"] = True
                    terminal = True
                    return
                value = strict_json(data.decode("utf-8"))
                if not isinstance(value, dict) or value.get("error"):
                    raise GatewayError("upstream_stream_error", observation)
                reported = value.get("model")
                if isinstance(reported, str) and reported not in observation["reported_models"]:
                    observation["reported_models"].append(reported)
                usage = value.get("usage")
                if isinstance(usage, dict):
                    observation["usage"] = {key: item for key, item in usage.items()
                                            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                                            and type(item) is int and item >= 0}
                choices = value.get("choices", [])
                if not isinstance(choices, list) or len(choices) > 1:
                    raise GatewayError("invalid_choices", observation)
                if not choices:
                    return
                choice = choices[0]
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    raise GatewayError("invalid_choice", observation)
                delta = choice.get("delta", {})
                if not isinstance(delta, dict) or delta.get("tool_calls") or delta.get("function_call"):
                    raise GatewayError("unexpected_tool_output", observation)
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise GatewayError("invalid_content", observation)
                    if content:
                        if observation["finish_reason"] is not None:
                            raise GatewayError("content_after_finish", observation)
                        if observation["ttft_seconds"] is None:
                            observation["ttft_seconds"] = round(time.monotonic() - started, 6)
                        answer.append(content)
                finish = choice.get("finish_reason")
                if finish is not None:
                    if finish != "stop":
                        raise GatewayError("non_normal_finish", observation)
                    observation["finish_reason"] = finish
            while not terminal:
                if transport_socket is not None:
                    transport_socket.settimeout(min(60.0, remaining()))
                remaining()
                chunk = response.read1(65536)
                if captured is not None:
                    captured.extend(chunk)
                remaining()
                if not chunk:
                    raise GatewayError("stream_ended_without_done", observation)
                total += len(chunk)
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.rstrip(b"\r")
                    if not line:
                        consume()
                    elif line.startswith(b"data:"):
                        event.append(line[5:].lstrip(b" "))
                    elif line.startswith(b"event:"):
                        if line[6:].strip() == b"error":
                            raise GatewayError("upstream_stream_error", observation)
                    elif not line.startswith((b":", b"id:", b"retry:")):
                        raise GatewayError("invalid_sse_field", observation)
                    if terminal:
                        if buffer.strip():
                            raise GatewayError("data_after_done", observation)
                        break
            if observation["finish_reason"] != "stop" or not "".join(answer).strip():
                raise GatewayError("missing_normal_answer", observation)
            observation["response_bytes"] = total
            remaining()
            return "".join(answer), observation
        except GatewayError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError, ValueError, TypeError) as exception:
            reason = "cancelled" if cancelled.is_set() else "gateway_deadline_exhausted" if time.monotonic() >= deadline else "transport_or_stream_structure_error"
            raise GatewayError(reason, observation) from exception
        finally:
            observation["elapsed_seconds"] = round(time.monotonic() - started, 6)
            if captured is not None:
                observation["raw_sse_base64"] = base64.b64encode(captured).decode("ascii")
                observation["raw_sse_bytes"] = len(captured)
                observation["raw_sse_sha256"] = hashlib.sha256(captured).hexdigest()
            connection.close()
            with self._lock:
                self._connection = None
                self._socket = None
