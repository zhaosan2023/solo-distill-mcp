"""Foreground stdio integration driver. One owned subprocess, bounded output.

This is an engineering test client, not an alternative business entry point and
not evidence of Roo tool visibility. It never starts or restarts the gateway.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from typing import Any

from .gateway import strict_json

MAX_WIRE_BYTES = 24576
MAX_DIAGNOSTIC_BYTES = 8192


class ClientError(Exception):
    pass


class StdioClient:
    def __init__(self, root: Path, *, online: bool, deadline: float, v2: bool = False,
                 v3: bool = False):
        if v2 and v3:
            raise ClientError("exclusive_service_version_required")
        self.v3 = v3
        if v2 and not online:
            raise ClientError("v2_requires_explicit_online_authorization")
        self.raw_responses: dict[str, bytes] = {}
        self.last_frame: bytes | None = None
        self.negotiated_protocol: str | None = None
        self.started = time.monotonic()
        self.deadline = deadline
        self.stderr = bytearray()
        self.stdout = bytearray()
        self.responses: dict[str, dict[str, Any]] = {}
        self.process: subprocess.Popen | None = None
        self.exit_code: int | None = None
        self.cleanup = "not_started"
        self.selector = selectors.DefaultSelector()
        command = [sys.executable, "-B", "-S", "-m",
                   "solo_distill.server_v3" if v3 else
                   "solo_distill.server_v2" if v2 else "solo_distill.server",
                   "--config", str(root / "config" / "projects.json")]
        if online or v3:
            command.extend(["--distillation-config", str(root / "config" / "distillation.json")])
        if online and v3:
            command.append("--online")
        environment = {"PATH": os.defpath, "PYTHONPATH": str(root / "src"),
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        try:
            self.process = subprocess.Popen(command, cwd=root, env=environment,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0)
            self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
            self.cleanup = "child_owned"
        except BaseException:
            self.close()
            raise

    def send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.poll() is not None:
            raise ClientError("server_not_running")
        raw = json.dumps(message, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8") + b"\n"
        if len(raw) > 65536:
            raise ClientError("request_wire_budget")
        if time.monotonic() >= self.deadline:
            raise ClientError("client_deadline_exhausted")
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
        except (OSError, ValueError) as exception:
            raise ClientError("request_transport_failure") from exception

    @staticmethod
    def key(identity: Any) -> str:
        if type(identity) not in (str, int):
            raise ClientError("invalid_response_identity")
        return json.dumps(identity)

    def receive(self, identity: Any, *, deadline: float | None = None) -> dict[str, Any]:
        key = self.key(identity)
        stop = min(self.deadline, deadline) if deadline is not None else self.deadline
        while key not in self.responses:
            remaining = stop - time.monotonic()
            if remaining <= 0:
                raise ClientError("client_deadline_exhausted")
            events = self.selector.select(remaining)
            if not events:
                raise ClientError("client_deadline_exhausted")
            for selected, _ in events:
                chunk = os.read(selected.fileobj.fileno(), MAX_WIRE_BYTES + 1)
                if not chunk:
                    self.selector.unregister(selected.fileobj)
                    if selected.data == "stdout":
                        raise ClientError("stdout_eof_before_response")
                    continue
                if selected.data == "stderr":
                    self.stderr.extend(chunk)
                    if len(self.stderr) > MAX_DIAGNOSTIC_BYTES:
                        raise ClientError("diagnostic_budget_exhausted")
                    continue
                self.stdout.extend(chunk)
                while b"\n" in self.stdout:
                    line, _, tail = self.stdout.partition(b"\n")
                    self.stdout = bytearray(tail)
                    if len(line) + 1 > MAX_WIRE_BYTES:
                        raise ClientError("response_wire_budget_exhausted")
                    value = strict_json(line.decode("utf-8"))
                    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                        raise ClientError("stdout_protocol_pollution")
                    response_key = self.key(value.get("id"))
                    if response_key in self.responses:
                        raise ClientError("duplicate_response_identity")
                    self.responses[response_key] = value
                    self.raw_responses[response_key] = bytes(line) + b"\n"
                if len(self.stdout) >= MAX_WIRE_BYTES:
                    raise ClientError("unterminated_response_wire_budget")
        self.last_frame = self.raw_responses.pop(key)
        return self.responses.pop(key)

    def initialize(self) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "solo-real-integration-driver", "version": "1"}}})
        initialized = self.receive(1)
        if "error" in initialized:
            raise ClientError("initialization_rejected")
        self.negotiated_protocol = initialized.get("result", {}).get("protocolVersion")
        if self.negotiated_protocol != "2024-11-05":
            raise ClientError("protocol_version_not_negotiated")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return self.receive(2)

    def receive_v3(self, arguments, *, rpc_id=3, tick=None):
        """发送显式 V3 请求并保存完整传输；接收不代表知识或语义认证。"""
        from .adaptive_wire import TransportCapabilities
        from .wire_v3 import Admission, METHOD
        from .selection_ingress_v3 import live_wire_arguments, resolve_selection
        if not self.v3 or self.negotiated_protocol != "2024-11-05":
            raise ClientError("explicit_v3_service_and_protocol_required")
        admission = Admission(self.negotiated_protocol, arguments["scenario"], rpc_id,
            arguments["return_bytes"], TransportCapabilities.from_dict(arguments["transport"]),
            wire_encoding=arguments.get("wire_encoding", "json"))
        caps = admission.transport
        upstream_tick = tick

        def check():
            if time.monotonic() >= self.deadline:
                raise ClientError("client_deadline_exhausted")
            if upstream_tick is not None:
                upstream_tick()

        check()
        wire_arguments = arguments
        if arguments.get("operation") == "live":
            selection = resolve_selection(arguments, tick=check)
            wire_arguments, _ = live_wire_arguments(
                arguments, selection, rpc_id=rpc_id, tick=check)
            # 保留已验证包装规则；生产入站字段使用本次明确指定的名称。
            wire_arguments["compressed_frozen_selection"] = wire_arguments.pop("frozen_selection_lz")
        message = {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call", "params": {
            "name": "solo_distill_knowledge_v3", "arguments": wire_arguments}}
        request_raw = json.dumps(message, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8") + b"\n"
        if len(request_raw) > 65536:
            raise ClientError("request_wire_budget")
        self.last_request_bytes = request_raw
        check()
        self.send(message)
        transfer = bytearray()
        count = 0
        final = False
        while not final:
            check()
            events = self.selector.select(max(0, self.deadline - time.monotonic()))
            if not events:
                raise ClientError("client_deadline_exhausted")
            for selected, _ in events:
                check()
                chunk = os.read(selected.fileobj.fileno(), min(admission.budget + 1, 22017))
                if not chunk:
                    self.selector.unregister(selected.fileobj)
                    if selected.data == "stdout":
                        raise ClientError("stdout_eof_before_v3_manifest")
                    continue
                if selected.data == "stderr":
                    self.stderr.extend(chunk)
                    if len(self.stderr) > MAX_DIAGNOSTIC_BYTES:
                        raise ClientError("diagnostic_budget_exhausted")
                    continue
                self.stdout.extend(chunk)
                while b"\n" in self.stdout:
                    check()
                    line, _, tail = self.stdout.partition(b"\n")
                    self.stdout = bytearray(tail)
                    raw = bytes(line) + b"\n"
                    if final or len(raw) > admission.budget:
                        raise ClientError("extra_or_oversized_v3_frame")
                    transfer.extend(raw)
                    count += 1
                    if len(transfer) > caps.max_transfer_bytes or count > caps.max_chunks + 1:
                        raise ClientError("v3_transfer_or_chunk_budget")
                    value = strict_json(line.decode("utf-8"))
                    if type(value) is not dict or value.get("jsonrpc") != "2.0":
                        raise ClientError("stdout_protocol_pollution")
                    if "id" not in value:
                        if value.get("method") != METHOD or not caps.chunking:
                            raise ClientError("unexpected_v3_notification")
                    else:
                        if type(value["id"]) is not type(rpc_id) or value["id"] != rpc_id:
                            raise ClientError("unexpected_v3_response_identity")
                        if "error" in value:
                            raise ClientError("v3_service_call_rejected")
                        final = True
                if len(self.stdout) >= admission.budget or (final and self.stdout):
                    raise ClientError("unterminated_or_extra_v3_frame")
        self.last_frame = bytes(transfer)
        return self.last_frame

    def call_v3(self, arguments, *, raw_files, candidate, observations=None, rpc_id=3,
                **trusted):
        """完整接收后认证；所有期望仍由调用方的包外可信输入提供。"""
        from .adaptive_wire import TransportCapabilities
        from .consumer_v3 import consume_frame
        from .wire_v3 import Admission
        upstream_tick = trusted.pop("tick", None)

        def check():
            if time.monotonic() >= self.deadline:
                raise ClientError("client_deadline_exhausted")
            if upstream_tick is not None:
                upstream_tick()

        raw = self.receive_v3(arguments, rpc_id=rpc_id, tick=check)
        admission = Admission(self.negotiated_protocol, arguments["scenario"], rpc_id,
            arguments["return_bytes"], TransportCapabilities.from_dict(arguments["transport"]),
            wire_encoding=arguments.get("wire_encoding", "json"))
        return consume_frame(raw, raw_files, admission=admission,
            candidate=candidate, observations=observations, tick=check, **trusted)

    def close(self) -> None:
        process = self.process
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                self.exit_code = process.wait(timeout=max(0, self.deadline - time.monotonic())
                                              if self.v3 else 12)
                self.cleanup = "confirmed_natural_exit" if self.exit_code == 0 else "confirmed_nonzero_exit"
            except subprocess.TimeoutExpired:
                if self.v3:
                    process.kill()
                    self.exit_code = process.wait(timeout=2)
                    self.cleanup = "forced_kill_not_gate_pass"
                else:
                    process.terminate()
                    try:
                        self.exit_code = process.wait(timeout=2)
                        self.cleanup = "forced_termination_not_gate_pass"
                    except subprocess.TimeoutExpired:
                        process.kill()
                        self.exit_code = process.wait(timeout=2)
                        self.cleanup = "forced_kill_not_gate_pass"
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        # Only after confirmed exit; no process can keep writing.
                        raw = stream.read(MAX_DIAGNOSTIC_BYTES + 1)
                        if stream is process.stderr and raw:
                            self.stderr.extend(raw)
                        if stream is process.stdout and raw and raw.strip():
                            self.cleanup = "unconsumed_stdout_not_gate_pass"
                    finally:
                        stream.close()
            if len(self.stderr) > MAX_DIAGNOSTIC_BYTES:
                self.cleanup = "diagnostic_overflow_not_gate_pass"
        self.selector.close()

    def __enter__(self) -> StdioClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
