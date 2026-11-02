"""显式 V3 在线冻结选择入站表示；不改变完整选择或来源认证。

只消费生产 LZ 规则，不导入测试 Oracle，不联网、不创建进程或接纳。
取消和绝对期限由调用方检查回调传播；解码成功不证明选择真实。
"""
from __future__ import annotations

import json

from . import lz_v3 as lz

ENCODING = "frozen-selection-lz-v3-1"
MAX_REQUEST_BYTES = 65536
MAX_SELECTION_BYTES = 1048576
MAX_SELECTION_DEPTH = 64
MAX_SELECTION_NODES = 500000
MAX_REPRESENTATION_BYTES = 65536
MAX_TOKEN_BYTES = 49152


def _require(ok, reason):
    if not ok:
        raise ValueError("v3_selection_ingress_" + reason)


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


def _payload(document, tick):
    # 先检查固定浅层形状与标量长度，未知嵌套值不得进入序列化。
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
    count = payload["raw_bytes"]
    _require(type(count) is int and 0 < count <= MAX_SELECTION_BYTES, "raw_bytes")
    digest = payload["sha256"]
    _require(type(digest) is str and len(digest) == 64
             and all(char in "0123456789abcdef" for char in digest), "digest")
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
    _payload(document, tick)
    return document


def unpack_selection(document, *, tick=None):
    payload = _payload(document, tick)
    selection = lz.unpack(payload, tick=tick, **_limits())
    _require(type(selection) is dict, "selection_object")
    _check(tick)
    return selection


def resolve_selection(arguments, *, tick=None):
    """还原完整见证；原准备链仍须逐规范字节核对当前来源复算。"""
    _check(tick)
    _require(type(arguments) is dict, "arguments")
    operation = arguments.get("operation")
    _require(type(operation) is str and operation in {"preflight", "live"}, "operation")
    plain = "frozen_selection" in arguments
    packed_fields = [name for name in (
        "compressed_frozen_selection", "frozen_selection_lz") if name in arguments]
    if operation == "preflight":
        _require(not packed_fields, "compressed_selection_requires_live")
        return arguments.get("frozen_selection")
    _require(int(plain) + len(packed_fields) == 1, "exactly_one_selection_required")
    if packed_fields:
        return unpack_selection(arguments[packed_fields[0]], tick=tick)
    selection = arguments["frozen_selection"]
    _require(type(selection) is dict, "selection_object")
    _check(tick)
    return selection


def live_wire_arguments(arguments, selection, *, rpc_id=3, tick=None):
    """启动在线子进程前显式编码；不修改调用方输入或扩大入站上限。

    调用方须独立认证 selection；本函数仅核对完整等价与实际请求预算。
    """
    _check(tick)
    _require(type(arguments) is dict and arguments.get("operation") == "live"
             and type(arguments.get("contract_version")) is int
             and arguments["contract_version"] == 3, "explicit_live_v3_required")
    _require(type(rpc_id) in (int, str), "rpc_identity")
    # 先有界计量、编码可信输入，再序列化比较，避免先无界遍历选择。
    document = pack_selection(selection, tick=tick)
    expected = resolve_selection(arguments, tick=tick)
    if "frozen_selection" in arguments:
        lz._Budget(tick, MAX_SELECTION_BYTES, MAX_SELECTION_DEPTH,
                   MAX_SELECTION_NODES).inspect(expected)
    _require(lz.canonical(expected) == lz.canonical(selection), "caller_selection_conflict")
    restored = unpack_selection(document, tick=tick)
    _require(lz.canonical(restored) == lz.canonical(selection), "roundtrip_conflict")
    wire = dict(arguments)
    wire.pop("frozen_selection", None)
    wire.pop("compressed_frozen_selection", None)
    wire["frozen_selection_lz"] = document
    message = {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call", "params": {
        "name": "solo_distill_knowledge_v3", "arguments": wire}}
    raw = json.dumps(message, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")).encode("utf-8") + b"\n"
    _require(len(raw) <= MAX_REQUEST_BYTES, "request_wire_budget")
    _check(tick)
    return wire, len(raw)
