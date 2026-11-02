"""冻结选择入站表示的独立 Oracle；不导入任何生产模块。

仅消费公开用途标签、预算和独立 LZ 规则，不联网、不读来源，
不保存在线接纳。解码成功不证明源码选择真实，仍须完整复算对账。
标准库及公开规则的共同失效风险不能由双实现差分消除。
"""
from __future__ import annotations

import json

import schema_v3_lz_oracle as lz

ENCODING = "frozen-selection-lz-v3-1"
MAX_REQUEST_BYTES = 65536
MAX_SELECTION_BYTES = 1048576
MAX_SELECTION_DEPTH = 64
MAX_SELECTION_NODES = 500000
MAX_REPRESENTATION_BYTES = 65536
MAX_TOKEN_BYTES = 49152


def _require(condition, reason):
    if not condition:
        raise ValueError("independent_v3_selection_ingress_" + reason)


def _check(tick):
    _require(tick is None or callable(tick), "tick")
    if tick is not None:
        tick()


def _limits():
    return {"max_bytes": MAX_SELECTION_BYTES,
            "max_depth": MAX_SELECTION_DEPTH,
            "max_nodes": MAX_SELECTION_NODES,
            "max_syntax_bytes": MAX_REPRESENTATION_BYTES,
            "max_syntax_depth": 64,
            "max_syntax_nodes": MAX_SELECTION_NODES,
            "max_token_bytes": MAX_TOKEN_BYTES}


def _inspect(document, tick):
    # 封闭形状及标量上限先于序列化；未知嵌套对象不能触发无界遍历。
    _check(tick)
    _require(type(document) is dict and set(document) == {"encoding", "payload"},
             "wrapper")
    _require(type(document["encoding"]) is str and document["encoding"] == ENCODING,
             "purpose")
    payload = document["payload"]
    _require(type(payload) is dict and set(payload) == {
        "encoding", "raw_bytes", "sha256", "data"}, "payload")
    _require(type(payload["encoding"]) is str and payload["encoding"] == lz.ENCODING,
             "payload_encoding")
    _require(type(payload["raw_bytes"]) is int
             and 0 < payload["raw_bytes"] <= MAX_SELECTION_BYTES, "raw_bytes")
    digest = payload["sha256"]
    _require(type(digest) is str and len(digest) == 64
             and all(character in "0123456789abcdef" for character in digest), "digest")
    text = payload["data"]
    _require(type(text) is str and 0 < len(text) <= MAX_REPRESENTATION_BYTES,
             "data_bytes")
    _require(len(lz.canonical(document)) <= MAX_REPRESENTATION_BYTES,
             "representation_bytes")
    _check(tick)
    return payload


def pack_selection(selection, *, tick=None):
    _check(tick)
    _require(type(selection) is dict, "selection_object")
    payload = lz.pack(selection, tick=tick, **_limits())
    document = {"encoding": ENCODING, "payload": payload}
    _inspect(document, tick)
    return document


def unpack_selection(document, *, tick=None):
    payload = _inspect(document, tick)
    restored = lz.unpack(payload, tick=tick, **_limits())
    _require(type(restored) is dict, "selection_object")
    _check(tick)
    return restored


def resolve_selection(arguments, *, tick=None):
    """只处理选择表示；服务授权、源码认证及绝对窗口由调用边界提供。"""
    _check(tick)
    _require(type(arguments) is dict, "arguments")
    operation = arguments.get("operation")
    _require(type(operation) is str and operation in {"preflight", "live"}, "operation")
    plain = "frozen_selection" in arguments
    packed = "frozen_selection_lz" in arguments
    if operation == "preflight":
        _require(not packed, "compressed_selection_requires_live")
        # 原离线请求由原准备链接管，不在新增表示入口改变旧字段语义。
        return arguments.get("frozen_selection")
    _require(plain != packed, "exactly_one_selection_required")
    if packed:
        return unpack_selection(arguments["frozen_selection_lz"], tick=tick)
    _require(type(arguments["frozen_selection"]) is dict, "selection_object")
    _check(tick)
    return arguments["frozen_selection"]


def live_wire_arguments(arguments, selection, *, rpc_id=3, tick=None):
    """启动子进程前物化显式请求，并精确计量全部 JSON-RPC 字段和 LF。

    完整选择须由调用方独立冻结；此处等价比较不代替源码复算。
    返回新请求及实际入站字节数，不修改调用方对象，也不创建进程。
    """
    _check(tick)
    _require(type(arguments) is dict and arguments.get("operation") == "live"
             and type(arguments.get("contract_version")) is int
             and arguments["contract_version"] == 3, "explicit_live_v3_required")
    _require(type(rpc_id) in (int, str), "rpc_identity")
    _require(type(selection) is dict, "selection_object")
    expected = resolve_selection(arguments, tick=tick)
    _require(lz.canonical(expected) == lz.canonical(selection), "caller_selection_conflict")
    document = pack_selection(selection, tick=tick)
    restored = unpack_selection(document, tick=tick)
    _require(lz.canonical(restored) == lz.canonical(selection), "roundtrip_conflict")
    wire = dict(arguments)
    wire.pop("frozen_selection", None)
    wire["frozen_selection_lz"] = document
    message = {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
               "params": {"name": "solo_distill_knowledge_v3", "arguments": wire}}
    raw = json.dumps(message, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")).encode("utf-8") + b"\n"
    _require(len(raw) <= MAX_REQUEST_BYTES, "request_wire_budget")
    _check(tick)
    return wire, len(raw)
