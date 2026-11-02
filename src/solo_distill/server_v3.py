"""显式 V3 stdio 扩展；不修改或复用旧服务的业务分派。

单一受管业务线程，前台非阻塞写入和取消。绝对宿主窗口限制线程
及进程退出；外层仍应采用进程级硬期限，不能靠线程存活判通过。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import traceback
from pathlib import Path
import selectors
import sys
import threading
import time

from .distillation import load_policy
from .extractor import InputError, load_projects
from .gateway import Gateway
from .server import Server, decode, encode, error
from .service_v3 import INPUT_SCHEMA, LIVE_PROCESS_SECONDS, TOOL_NAME, V3Distiller
from .wire_v2 import Admission as ProtocolAdmission, FrameWriter, FrozenFrame, FrozenTransfer


class V3Server(Server):
    def dispatch(self, message, cancelled=None):
        if isinstance(message, dict) and message.get("method") == "tools/call":
            if not self.ready:
                return error(message.get("id"), -32002, "必须先初始化并通知就绪")
            if (set(message) != {"jsonrpc", "id", "method", "params"}
                    or message.get("jsonrpc") != "2.0"
                    or type(message["id"]) not in (str, int)
                    or type(message["params"]) is not dict
                    or set(message["params"]) != {"name", "arguments"}
                    or message["params"]["name"] != TOOL_NAME):
                return error(message.get("id"), -32602, "本入口仅接受显式 V3")
            try:
                return self.distiller.distill_frame(message["params"]["arguments"],
                    cancelled, rpc_id=message["id"])
            except InputError:
                return error(message["id"], -32602, "V3 请求契约、授权或期限无效")
        response = super().dispatch(message, cancelled)
        if (isinstance(message, dict) and message.get("method") == "tools/list"
                and response is not None and "result" in response):
            response["result"]["tools"] = [{"name": TOOL_NAME,
                "description": "显式 Schema 3.0；离线预检不建连，在线须独立授权及冻结选择。",
                "inputSchema": INPUT_SCHEMA}]
        return response


def _host_deadline(arguments, now, *, online):
    if type(arguments) is not dict or type(arguments.get("deadline_unix_ms")) is not int:
        return now + 30
    operation = arguments.get("operation")
    ceiling = LIVE_PROCESS_SECONDS if operation == "live" and online else 60 if operation == "preflight" else 30
    return min(now + ceiling, now + (arguments["deadline_unix_ms"] - time.time() * 1000) / 1000)


def _failure_details(exception):
    """Bounded local diagnostics; omit arbitrary exception prose and source text."""
    reason = str(exception)
    if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason) is None:
        reason = "unclassified_error_text_omitted"
    stack = []
    root = Path(__file__).resolve().parents[2]
    for item in traceback.extract_tb(exception.__traceback__)[-8:]:
        try:
            path = str(Path(item.filename).resolve().relative_to(root))
        except ValueError:
            path = "external_frame_path_omitted"
        stack.append({"path": path[:256], "line": item.lineno})
    return {"status": "failed_not_business_acceptance",
            "error_type": type(exception).__name__[:128], "reason": reason, "stack": stack}


def serve_v3(server, input_fd, output_fd):
    os.set_blocking(input_fd, False)
    os.set_blocking(output_fd, False)
    cancelled = threading.Event()
    failed = threading.Event()
    publishing = threading.Event()
    phase_lock = threading.Lock()
    worker = None
    active_id = None
    hard_deadline = None
    buffer = bytearray()

    def stop():
        cancelled.set()
        server.distiller.cancel()

    def dispatch(message):
        try:
            response = server.dispatch(message, cancelled)
            if response is None:
                return
            business = isinstance(response, (FrozenFrame, FrozenTransfer))
            if business:
                frame = response
                deadline = min(hard_deadline, server.distiller.business_deadline,
                               time.monotonic() + 30)
            else:
                identity = response.get("id")
                if type(identity) not in (str, int):
                    identity = 0
                    response = {**response, "id": identity}
                frame = FrozenFrame(encode(response) + b"\n",
                    ProtocolAdmission("2024-11-05", "A", identity, 22016), False)
                deadline = min(time.monotonic() + 10, hard_deadline or float("inf"))
            with phase_lock:
                publishing.set()
            receipt = FrameWriter(output_fd).send(frame, deadline=deadline, cancelled=cancelled)
            if business:
                request_id = message["params"]["arguments"]["request_id"]
                server.distiller.persist_scratch(request_id + ".delivery", {
                    "request_id": request_id, "rpc_id": message["id"],
                    "written": receipt.written,
                    "frame_sha256": hashlib.sha256(frame.data).hexdigest(),
                    "knowledge_delivered": frame.knowledge_delivered,
                    "status": receipt.status})
        except Exception as exception:
            details = _failure_details(exception)
            try:
                params = message.get("params", {}) if type(message) is dict else {}
                arguments = params.get("arguments", {}) if type(params) is dict else {}
                request_id = arguments.get("request_id") if type(arguments) is dict else None
                if (type(request_id) is str and re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", request_id)):
                    server.distiller.persist_scratch(request_id + ".failure", details)
                    details["failure_receipt_saved"] = True
            except Exception:
                details["failure_receipt_saved"] = False
            failed.set()
            stop()
            print(json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                  file=sys.stderr, flush=True)

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(input_fd, selectors.EVENT_READ)
            while not failed.is_set():
                if hard_deadline is not None and time.monotonic() >= hard_deadline:
                    failed.set()
                    stop()
                    break
                if worker is not None and not worker.is_alive():
                    worker.join()
                    worker = None
                if not selector.select(0.01):
                    continue
                raw = os.read(input_fd, 65537)
                if not raw:
                    with phase_lock:
                        if not publishing.is_set():
                            stop()
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
                    if (type(message) is dict and message.get("jsonrpc") == "2.0"
                            and message.get("method") == "notifications/cancelled"
                            and "id" not in message):
                        params = message.get("params")
                        if (type(params) is dict and type(params.get("requestId")) is type(active_id)
                                and params.get("requestId") == active_id):
                            stop()
                        continue
                    if worker is not None:
                        failed.set()
                        break
                    if type(message) is dict and message.get("method") == "tools/call":
                        if hard_deadline is not None:
                            failed.set()
                            break
                        active_id = message.get("id")
                        args = message.get("params", {})
                        args = args.get("arguments", {}) if type(args) is dict else {}
                        now = time.monotonic()
                        hard_deadline = _host_deadline(args, now, online=server.distiller.online)
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
                    stop()
            # Never add a 12+2 second cleanup grace to the host's window.
            remaining = max(0, (hard_deadline or time.monotonic()) - time.monotonic() - 0.02)
            worker.join(timeout=remaining)
            if worker.is_alive():
                # An uncooperative non-daemon worker must not outlive the process
                # deadline. No target subprocess or source execution is created.
                os._exit(2)
    return 2 if failed.is_set() or buffer else 0


def main():
    parser = argparse.ArgumentParser(description="显式 V3 单次工程服务；不启动网关")
    parser.add_argument("--config", required=True)
    parser.add_argument("--distillation-config", required=True)
    parser.add_argument("--online", action="store_true")
    arguments = parser.parse_args()
    try:
        projects = load_projects(arguments.config)
        distiller = V3Distiller(projects, load_policy(arguments.distillation_config),
            Gateway(capture_stream=True, absolute_timeout=True), artifact_root=Path.cwd(), online=arguments.online)
    except (ValueError, TypeError, OSError):
        return 2
    return serve_v3(V3Server(projects, distiller), sys.stdin.fileno(), sys.stdout.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
