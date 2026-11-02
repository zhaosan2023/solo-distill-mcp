"""确定性字节表示的独立 Oracle；不导入任何生产模块或前版编解码。

直接消费公开令牌规则，分别执行匹配、有界展开、解析前预检及规范核验。
只处理内存；不证明真实容量、冷预检、在线业务、人工语义或宿主加载。
共享标准库 JSON、Base64、SHA-256 的共同失效风险仍存在。
"""
from __future__ import annotations

import base64
import binascii
from collections import deque
import hashlib
import json

ENCODING = "lz-json-v3-1"
MAX_BYTES = 8388608
MAX_DEPTH = 64
MAX_NODES = 500000
MAX_SYNTAX_DEPTH = 192
WINDOW = 65536
MAX_MATCH = 130
MAX_LITERAL = 128


def _require(condition, reason):
    if not condition:
        raise ValueError("independent_v3_lz_" + reason)


def _callback(tick):
    _require(tick is None or callable(tick), "tick")
    return (lambda: None) if tick is None else tick


def _limit(value, ceiling):
    _require(type(value) is int and 0 <= value <= ceiling, "budget")


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ValueError("independent_v3_lz_json") from error


class _Meter:
    def __init__(self, tick, max_bytes, max_depth, max_nodes, *, syntax=False):
        _limit(max_bytes, MAX_BYTES)
        _limit(max_depth, MAX_SYNTAX_DEPTH if syntax else MAX_DEPTH)
        _limit(max_nodes, MAX_NODES)
        self.tick = _callback(tick)
        self.max_bytes, self.max_depth, self.max_nodes = max_bytes, max_depth, max_nodes
        self.bytes = self.nodes = 0
        self.string_sizes = {}

    def add(self, count):
        _require(count <= self.max_bytes - self.bytes, "bytes")
        self.bytes += count

    def scalar_size(self, value):
        if type(value) is str:
            if value not in self.string_sizes:
                self.string_sizes[value] = len(canonical(value))
            return self.string_sizes[value]
        return len(canonical(value))

    def visit(self, value, depth=0):
        self.tick()
        _require(depth <= self.max_depth, "depth")
        _require(self.nodes < self.max_nodes, "nodes")
        self.nodes += 1
        kind = type(value)
        if value is None or kind in (str, bool, int):
            self.add(self.scalar_size(value))
            return
        _require(kind in (list, dict), "value_type")
        _require(len(value) <= self.max_nodes - self.nodes, "nodes")
        self.add(2 + max(0, len(value) - 1))
        if kind is list:
            for item in value:
                self.visit(item, depth + 1)
        else:
            _require(all(type(key) is str for key in value), "object_key")
            for key, item in value.items():
                self.tick()
                self.add(self.scalar_size(key) + 1)
                self.visit(item, depth + 1)


def _compress(raw, *, tick=None, max_token_bytes=MAX_BYTES):
    """最近三字节候选的唯一贪心编码；索引仅保留窗口内已消费位置。"""
    check = _callback(tick)
    _limit(max_token_bytes, MAX_BYTES)
    _require(type(raw) is bytes and len(raw) <= MAX_BYTES, "raw_input")
    output, literal = bytearray(), bytearray()
    latest, registered = {}, deque()
    size, position = len(raw), 0

    def append(part):
        check()
        _require(len(part) <= max_token_bytes - len(output), "token_bytes")
        output.extend(part)

    def flush():
        if literal:
            append(bytes([len(literal) - 1]) + literal)
            literal.clear()

    while position < size:
        check()
        lower = position - WINDOW
        while registered and registered[0][0] < lower:
            check()
            old, key = registered.popleft()
            if latest.get(key) == old:
                del latest[key]
        start = position
        matched = 0
        previous = None
        if position + 3 <= size:
            previous = latest.get(raw[position:position + 3])
            if previous is not None:
                stop = min(MAX_MATCH, size - position)
                while matched < stop and raw[previous + matched] == raw[position + matched]:
                    if matched % 16 == 0:
                        check()
                    matched += 1
        if matched >= 3:
            flush()
            distance = position - previous
            _require(1 <= distance <= WINDOW, "encoder_distance")
            encoded_distance = distance - 1
            append(bytes([128 + matched - 3, encoded_distance & 255,
                          encoded_distance >> 8]))
            position += matched
        else:
            literal.append(raw[position])
            position += 1
            if len(literal) == MAX_LITERAL:
                flush()
        # 每个已消费位置都登记，即便该位置是复制令牌内部的原字节。
        for index in range(start, min(position, size - 2)):
            check()
            key = raw[index:index + 3]
            latest[key] = index
            registered.append((index, key))
    flush()
    check()
    return bytes(output)


def _inflate(tokens, raw_bytes, *, tick=None, max_bytes=MAX_BYTES,
             max_token_bytes=MAX_BYTES):
    """逐令牌先查剩余容量，再写入；重叠复制不先分配声明总长度。"""
    check = _callback(tick)
    _limit(max_bytes, MAX_BYTES)
    _limit(max_token_bytes, MAX_BYTES)
    _require(type(raw_bytes) is int and 0 < raw_bytes <= max_bytes, "raw_bytes")
    _require(type(tokens) is bytes and len(tokens) <= max_token_bytes, "token_bytes")
    result = bytearray()
    cursor = 0
    while cursor < len(tokens):
        check()
        _require(len(result) < raw_bytes, "extra_token")
        control = tokens[cursor]
        cursor += 1
        if control < 128:
            count = control + 1
            _require(count <= len(tokens) - cursor, "literal_truncated")
            _require(count <= raw_bytes - len(result), "expanded_bytes")
            result.extend(tokens[cursor:cursor + count])
            cursor += count
        else:
            count = control - 128 + 3
            _require(2 <= len(tokens) - cursor, "copy_truncated")
            distance = 1 + tokens[cursor] + 256 * tokens[cursor + 1]
            cursor += 2
            _require(distance <= len(result), "copy_distance")
            _require(count <= raw_bytes - len(result), "expanded_bytes")
            for _ in range(count):
                check()
                result.append(result[-distance])
    _require(len(result) == raw_bytes, "expanded_length")
    check()
    return bytes(result)


def _preparse(raw, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
              max_nodes=MAX_NODES):
    """在 JSON 容器构造前检查值节点和逻辑深度；不替代严格 JSON 解析。"""
    check = _callback(tick)
    _limit(max_bytes, MAX_BYTES)
    _limit(max_depth, MAX_DEPTH)
    _limit(max_nodes, MAX_NODES)
    _require(type(raw) is bytes and 0 < len(raw) <= max_bytes, "raw_bytes")
    cursor, nodes = 0, 0
    size = len(raw)

    def string():
        nonlocal cursor
        _require(cursor < size and raw[cursor] == 34, "string_start")
        cursor += 1
        while cursor < size:
            if cursor % 256 == 0:
                check()
            byte = raw[cursor]
            cursor += 1
            if byte == 34:
                return
            _require(byte >= 32, "string_control")
            if byte == 92:
                check()
                _require(cursor < size, "string_escape_truncated")
                escape = raw[cursor]
                cursor += 1
                if escape == 117:
                    _require(cursor + 4 <= size and all(
                        item in b"0123456789abcdefABCDEF" for item in raw[cursor:cursor + 4]),
                        "unicode_escape")
                    cursor += 4
                else:
                    _require(escape in b'"\\/bfnrt', "string_escape")
        raise ValueError("independent_v3_lz_string_unclosed")

    def value(depth):
        nonlocal cursor, nodes
        check()
        _require(depth <= max_depth, "preparse_depth")
        _require(nodes < max_nodes, "preparse_nodes")
        nodes += 1
        _require(cursor < size, "value_missing")
        byte = raw[cursor]
        if byte == 34:
            string()
            return
        if byte in (91, 123):
            object_value = byte == 123
            closing = 125 if object_value else 93
            cursor += 1
            _require(cursor < size, "container_unclosed")
            if raw[cursor] == closing:
                cursor += 1
                return
            while True:
                check()
                if object_value:
                    string()
                    _require(cursor < size and raw[cursor] == 58, "key_separator")
                    cursor += 1
                value(depth + 1)
                _require(cursor < size, "container_unclosed")
                separator = raw[cursor]
                cursor += 1
                if separator == closing:
                    return
                _require(separator == 44, "value_separator")
        elif byte in (116, 102, 110):
            word = {116: b"true", 102: b"false", 110: b"null"}[byte]
            _require(raw[cursor:cursor + len(word)] == word, "literal")
            cursor += len(word)
        else:
            if byte == 45:
                cursor += 1
            _require(cursor < size and 48 <= raw[cursor] <= 57, "integer")
            if raw[cursor] == 48:
                cursor += 1
            else:
                while cursor < size and 48 <= raw[cursor] <= 57:
                    if cursor % 256 == 0:
                        check()
                    cursor += 1

    check()
    value(0)
    _require(cursor == size, "json_trailing_bytes")
    check()
    return {"nodes": nodes, "bytes": size}


def _strict_load(raw):
    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result, "duplicate_json_key")
            result[key] = item
        return result

    def reject(_):
        raise ValueError("independent_v3_lz_noninteger_json_number")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_float=reject, parse_constant=reject)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("independent_v3_lz_strict_json") from error


def pack(value, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
         max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
         max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
         max_token_bytes=MAX_BYTES):
    meter = _Meter(tick, max_bytes, max_depth, max_nodes)
    syntax = _Meter(tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes, syntax=True)
    _limit(max_token_bytes, MAX_BYTES)
    meter.visit(value)
    meter.tick()
    raw = canonical(value)
    _require(len(raw) == meter.bytes, "raw_accounting")
    tokens = _compress(raw, tick=meter.tick, max_token_bytes=max_token_bytes)
    document = {"encoding": ENCODING, "raw_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(tokens).decode("ascii")}
    syntax.visit(document)
    meter.tick()
    return document


def unpack(document, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
           max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
           max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
           max_token_bytes=MAX_BYTES):
    meter = _Meter(tick, max_bytes, max_depth, max_nodes)
    syntax = _Meter(tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes, syntax=True)
    _limit(max_token_bytes, MAX_BYTES)
    syntax.visit(document)
    _require(type(document) is dict and set(document) == {
        "encoding", "raw_bytes", "sha256", "data"}, "header")
    _require(type(document["encoding"]) is str and document["encoding"] == ENCODING,
             "encoding")
    count, digest, text = (document[key] for key in ("raw_bytes", "sha256", "data"))
    _require(type(count) is int and 0 < count <= max_bytes, "raw_bytes")
    _require(type(digest) is str and len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest), "digest_shape")
    _require(type(text) is str and bool(text) and len(text) % 4 == 0, "base64_shape")
    padding = len(text) - len(text.rstrip("="))
    _require(padding <= 2 and len(text) // 4 * 3 - padding <= max_token_bytes,
             "token_bytes")
    meter.tick()
    try:
        tokens = base64.b64decode(text.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error, ValueError) as error:
        raise ValueError("independent_v3_lz_base64") from error
    _require(len(tokens) <= max_token_bytes
             and base64.b64encode(tokens).decode("ascii") == text, "base64_canonical")
    raw = _inflate(tokens, count, tick=meter.tick, max_bytes=max_bytes,
                   max_token_bytes=max_token_bytes)
    _require(hashlib.sha256(raw).hexdigest() == digest, "digest")
    _preparse(raw, tick=meter.tick, max_bytes=max_bytes, max_depth=max_depth,
              max_nodes=max_nodes)
    meter.tick()
    result = _strict_load(raw)
    meter.visit(result)
    meter.tick()
    _require(canonical(result) == raw and meter.bytes == len(raw), "raw_canonical")
    expected = pack(result, tick=meter.tick, max_bytes=max_bytes, max_depth=max_depth,
                    max_nodes=max_nodes, max_syntax_bytes=max_syntax_bytes,
                    max_syntax_depth=max_syntax_depth, max_syntax_nodes=max_syntax_nodes,
                    max_token_bytes=max_token_bytes)
    _require(canonical(expected) == canonical(document), "noncanonical")
    meter.tick()
    return result
