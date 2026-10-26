"""Opt-in engineering stdio V2 service. The V1 server is not modified."""
from __future__ import annotations

import argparse
import hashlib
import os
import selectors
import sys
import threading
import time

from .distillation import load_policy
from .extractor import InputError, load_projects
from .live_v2 import INPUT_SCHEMA, TOOL_NAME, LiveV2Distiller
from .server import Server, decode, encode, error
from .wire_v2 import Admission, FrameWriter, FrozenFrame


class V2Server(Server):
    def dispatch(self, message, cancelled=None):
        if isinstance(message, dict) and message.get("jsonrpc") == "2.0" and message.get("method") == "tools/call":
            if not self.ready:
                return error(message.get("id"), -32002, "必须先初始化并通知就绪")
            if (set(message) != {"jsonrpc", "id", "method", "params"}
                    or type(message["params"]) is not dict
                    or set(message["params"]) != {"name", "arguments"}
                    or message["params"]["name"] != TOOL_NAME):
                return error(message.get("id"), -32602, "本工程入口只接受显式 V2 A")
            try:
                return self.distiller.distill_frame(message["params"]["arguments"], cancelled,
                                                   rpc_id=message["id"])
            except InputError as exception:
                return error(message.get("id"), -32602, str(exception))
        response = super().dispatch(message, cancelled)
        if (isinstance(message, dict) and message.get("method") == "tools/list"
                and response is not None and "result" in response):
            response["result"]["tools"] = [{"name": TOOL_NAME,
                "description": "显式获准的单次 V2 A；不是 Roo 验收，不外层重试。",
                "inputSchema": INPUT_SCHEMA}]
        return response


def serve_v2(server, input_fd, output_fd):
    """One owned worker; bounded input and foreground cancellation/EOF handling."""
    os.set_blocking(input_fd, False)
    os.set_blocking(output_fd, False)
    cancelled = threading.Event()
    failed = threading.Event()
    output_lock = threading.Lock()
    phase_lock = threading.Lock()
    publishing = threading.Event()
    worker = None
    active_id = None
    buffer = bytearray()

    def dispatch(message):
        try:
            response = server.dispatch(message, cancelled)
            if response is None:
                return
            if isinstance(response, FrozenFrame):
                frame = response
            else:
                identity = response.get("id")
                # Invalid/missing IDs are protocol errors, not admitted business.
                if type(identity) not in (str, int):
                    identity = 0
                    response = {**response, "id": identity}
                frame = FrozenFrame(encode(response) + b"\n",
                                    Admission("2024-11-05", "A", identity, 22016), False)
            # Input EOF ends request intake, not an already started bounded
            # response publication. Explicit cancellation still reaches the writer.
            with phase_lock:
                publishing.set()
            with output_lock:
                receipt = FrameWriter(output_fd).send(frame, deadline=time.monotonic() + 10,
                                                     cancelled=cancelled)
            if isinstance(response, FrozenFrame):
                request_id = message["params"]["arguments"]["request_id"]
                server.distiller.persist_scratch(request_id + ".delivery", {
                    "request_id": request_id, "rpc_id": message["id"], "written": receipt.written,
                    "frame_sha256": hashlib.sha256(frame.data).hexdigest(),
                    "knowledge_delivered": frame.knowledge_delivered, "status": receipt.status})
        except Exception as exception:
            failed.set()
            cancelled.set()
            server.distiller.cancel()
            # Bounded diagnostics contain only a type and a writer's fixed reason,
            # never source bodies, credentials or arbitrary exception messages.
            reason = getattr(exception, "reason", "publication_or_persistence_failure")
            print(f"V2 发布或证据保存失败；门禁阻塞：{type(exception).__name__}:{reason}",
                  file=sys.stderr, flush=True)

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(input_fd, selectors.EVENT_READ)
            while not failed.is_set():
                if worker is not None and not worker.is_alive():
                    worker.join()
                    worker = None
                if not selector.select(0.01):
                    continue
                raw = os.read(input_fd, 65537)
                if not raw:
                    with phase_lock:
                        if not publishing.is_set():
                            cancelled.set()
                            server.distiller.cancel()
                    break
                buffer.extend(raw)
                while b"\n" in buffer:
                    line, _, tail = buffer.partition(b"\n")
                    buffer = bytearray(tail)
                    if len(line) + 1 > 65536:
                        failed.set()
                        break
                    try:
                        message = decode(line)
                    except (ValueError, UnicodeError, RecursionError):
                        failed.set()
                        break
                    if isinstance(message, dict) and message.get("method") == "notifications/cancelled":
                        if ("id" not in message and type(message.get("params")) is dict
                                and type(message["params"].get("requestId")) is type(active_id)
                                and message["params"].get("requestId") == active_id):
                            cancelled.set()
                            server.distiller.cancel()
                        continue
                    if worker is not None:
                        # No second request, ping or replay while a business is active.
                        failed.set()
                        break
                    if isinstance(message, dict) and message.get("method") == "tools/call":
                        active_id = message.get("id")
                        publishing.clear()
                        worker = threading.Thread(target=dispatch, args=(message,), daemon=False)
                        worker.start()
                    else:
                        dispatch(message)
                if len(buffer) >= 65536:
                    failed.set()
    finally:
        if worker is not None:
            with phase_lock:
                if failed.is_set() or not publishing.is_set():
                    cancelled.set()
                    server.distiller.cancel()
            worker.join(timeout=12)
            if worker.is_alive():
                failed.set()
                print("V2 业务线程清理未确认", file=sys.stderr, flush=True)
    return 2 if failed.is_set() or buffer else 0


def main():
    parser = argparse.ArgumentParser(description="显式 V2 单次 A 工程入口；不启动网关")
    parser.add_argument("--config", required=True)
    parser.add_argument("--distillation-config", required=True)
    arguments = parser.parse_args()
    try:
        projects = load_projects(arguments.config)
        distiller = LiveV2Distiller(projects, load_policy(arguments.distillation_config))
    except (ValueError, TypeError, OSError):
        return 2
    return serve_v2(V2Server(projects, distiller), sys.stdin.fileno(), sys.stdout.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
