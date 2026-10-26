"""Bounded MCP stdio adapter; one owned business worker, not a complete SDK."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from typing import Any, BinaryIO

from .distillation import (Distiller, INPUT_SCHEMA as DISTILL_SCHEMA, TOOL_NAME as DISTILL_TOOL,
                           load_policy, pin_report, tool_response)

from . import __version__
from .extractor import InputError, Project, extract, load_projects

PROTOCOL_VERSION = "2024-11-05"
MAX_INPUT_BYTES = 65536
MAX_OUTPUT_BYTES = 24576
TOOL_NAME = "solo_extract_structure_v1"
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_version": {"type": "integer", "enum": [1]},
        "project_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "include": {"type": "array", "minItems": 1, "maxItems": 32,
                    "items": {"type": "string", "minLength": 1}},
        "max_seconds": {"type": "integer", "minimum": 1, "maximum": 10},
        "return_bytes": {"type": "integer", "minimum": 1024, "maximum": 24576},
    },
    "required": ["contract_version", "project_id"],
    "additionalProperties": False,
}


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("重复 JSON 字段")
        value[key] = item
    return value


def reject_constant(value: str) -> None:
    raise ValueError("不允许非有限 JSON 数值")


def decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


class Server:
    def __init__(self, projects: dict[str, Project], distiller: Distiller | None = None):
        self.projects = projects
        self.distiller = distiller
        self.initialized = False
        self.ready = False

    def dispatch(self, message: Any, cancelled: threading.Event | None = None) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return error(None, -32600, "仅接受单条 JSON-RPC 消息，不接受批处理")
        request_id = message.get("id")
        has_id = "id" in message
        if (message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or set(message) - {"jsonrpc", "method", "params", "id"}
                or (has_id and (type(request_id) not in (str, int)))
                or (isinstance(request_id, str) and len(request_id) > 128)):
            return error(None, -32600, "无效 JSON-RPC 请求")
        method = message["method"]
        params = message.get("params", {})
        if not isinstance(params, dict):
            return error(request_id, -32602, "参数必须为对象") if has_id else None
        if not has_id:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
            # Runtime cancellation is handled by the foreground transport loop.
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "initialize":
            if self.initialized:
                return error(request_id, -32600, "不可重复初始化")
            if (not isinstance(params.get("protocolVersion"), str)
                    or not isinstance(params.get("capabilities"), dict)
                    or not isinstance(params.get("clientInfo"), dict)
                    or not isinstance(params["clientInfo"].get("name"), str)
                    or not isinstance(params["clientInfo"].get("version"), str)):
                return error(request_id, -32602, "初始化参数无效")
            self.initialized = True
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "solo-distill-mcp", "version": __version__},
                "instructions": "仅读取授权项目。蒸馏需服务侧授权配置；语法证据与推断分层，协议成功不代表业务或宿主验收通过。",
            }}
        if not self.ready:
            return error(request_id, -32002, "必须先初始化并通知就绪")
        if method == "tools/list":
            if params:
                return error(request_id, -32602, "工具清单不支持分页参数")
            tools = [{"name": TOOL_NAME,
                      "description": "只读离线结构扫描；不请求模型、不执行或写入目标。",
                      "inputSchema": INPUT_SCHEMA}]
            if self.distiller is not None:
                tools.append({"name": DISTILL_TOOL,
                              "description": "有界只读正文取证，经固定网关压缩；语法与推断分层，不外层重试。调用窗口与绝对截止必填。",
                              "inputSchema": DISTILL_SCHEMA})
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}
        if method == "tools/call":
            if (set(params) != {"name", "arguments"}
                    or params.get("name") not in {TOOL_NAME, DISTILL_TOOL}
                    or (params.get("name") == DISTILL_TOOL and self.distiller is None)):
                return error(request_id, -32602, "未知工具或无效调用字段")
            try:
                if params["name"] == DISTILL_TOOL:
                    report = self.distiller.distill(params["arguments"], cancelled, rpc_id=request_id)
                else:
                    report = extract(self.projects, params["arguments"])
            except InputError as exception:
                return error(request_id, -32602, str(exception))
            result = {"content": [{"type": "text", "text": encode(report).decode("utf-8")}],
                      "isError": report["status"] in {"blocked", "failed"}}
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            # Budget the actual escaped JSON-RPC wire payload, not only the inner JSON.
            default_budget = (24576 if params["arguments"].get("scenario") == "A" else 12288
                              if params["name"] == DISTILL_TOOL else
                              self.projects[params["arguments"]["project_id"]].return_bytes)
            requested_budget = params["arguments"].get("return_bytes", default_budget)
            wire_budget = min(MAX_OUTPUT_BYTES, requested_budget)
            if params["name"] == DISTILL_TOOL:
                # Reuse the exact envelope cost; never lose a valid proposal at
                # the protocol boundary after successfully writing its Scratch.
                response = tool_response(pin_report(report, wire_budget, request_id), request_id)
            elif len(encode(response)) + 1 > wire_budget:
                response = error(request_id, -32001,
                                 "返回预算不足以保存完整结果和未完成事项；本次工具结果未交付")
            return response
        return error(request_id, -32601, "不支持的方法")


def serve(server: Server, input_stream: BinaryIO, output_stream: BinaryIO) -> int:
    # One managed business worker; the foreground protocol loop handles cancellation.
    # No concurrent business calls and no worker survives EOF or service exit.
    output_lock = threading.Lock()
    worker: threading.Thread | None = None
    active_id: Any = None
    cancelled = threading.Event()
    broken_output = threading.Event()

    def send(response: dict[str, Any] | None) -> None:
        if response is None:
            return
        encoded = encode(response) + b"\n"
        if len(encoded) > MAX_OUTPUT_BYTES:
            encoded = encode(error(response.get("id"), -32001, "协议返回预算不足")) + b"\n"
        try:
            with output_lock:
                output_stream.write(encoded)
                output_stream.flush()
        except (BrokenPipeError, OSError):
            broken_output.set()
            cancelled.set()
            if server.distiller is not None:
                server.distiller.cancel()

    def dispatch(message: Any) -> None:
        try:
            send(server.dispatch(message, cancelled))
        except Exception:
            print("工具内部错误；未登记成功", file=sys.stderr, flush=True)
            send(error(message.get("id") if isinstance(message, dict) else None,
                       -32603, "内部错误，未登记成功"))

    exit_code = 0
    try:
        while not broken_output.is_set():
            raw = input_stream.readline(MAX_INPUT_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_INPUT_BYTES:
                send(error(None, -32600, "输入消息超出上限，连接关闭"))
                exit_code = 2
                break
            try:
                message = decode(raw)
            except (UnicodeError, ValueError, RecursionError):
                send(error(None, -32700, "JSON 解析失败"))
                continue
            active = worker is not None and worker.is_alive()
            if (isinstance(message, dict) and message.get("jsonrpc") == "2.0"
                    and message.get("method") == "notifications/cancelled"
                    and "id" not in message):
                params = message.get("params", {})
                if (active and isinstance(params, dict)
                        and type(params.get("requestId")) is type(active_id)
                        and params.get("requestId") == active_id):
                    cancelled.set()
                    server.distiller.cancel()
                continue
            if active:
                if isinstance(message, dict) and message.get("method") == "ping":
                    dispatch(message)
                elif isinstance(message, dict) and "id" in message:
                    send(error(message["id"], -32003, "已有蒸馏请求运行；禁止并发业务调用"))
                continue
            if worker is not None:
                worker.join()
                worker = None
            if (server.ready and server.distiller is not None
                    and isinstance(message, dict) and "id" in message
                    and message.get("method") == "tools/call"
                    and isinstance(message.get("params"), dict)
                    and message["params"].get("name") == DISTILL_TOOL):
                active_id = message["id"]
                cancelled = threading.Event()
                worker = threading.Thread(target=dispatch, args=(message,),
                                          name="solo-distill-business", daemon=False)
                worker.start()
            else:
                dispatch(message)
    finally:
        if worker is not None:
            cancelled.set()
            if server.distiller is not None:
                server.distiller.cancel()
            worker.join(timeout=12)
            if worker.is_alive():
                # Non-daemon ownership is retained; process-level test deadline is
                # the final guard. This is explicitly NOT a successful cleanup.
                print("业务线程清理未确认；门禁阻塞", file=sys.stderr, flush=True)
                exit_code = 2
        if broken_output.is_set():
            exit_code = 2
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="只读 MCP stdio 服务；不启动网关")
    parser.add_argument("--config", required=True, help="服务端项目授权配置")
    parser.add_argument("--distillation-config", help="显式模型外发授权策略；省略则保持离线")
    arguments = parser.parse_args()
    try:
        projects = load_projects(arguments.config)
        distiller = (Distiller(projects, load_policy(arguments.distillation_config))
                     if arguments.distillation_config else None)
    except (OSError, ValueError, TypeError) as exception:
        print(f"配置加载失败：{type(exception).__name__}", file=sys.stderr)
        return 2
    return serve(Server(projects, distiller), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
