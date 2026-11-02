"""显式 V3 确定性字节表示；独立于测试 Oracle 和既有压缩入口。

只处理完整内存对象，不改变身份或原字节资产，不联网或执行来源。
先控制令牌展开，再在 JSON 容器构造前核验深度和节点。
标准库 JSON、Base64、SHA-256 的共同失效风险不由差分测试消除。
"""
from __future__ import annotations

import base64
import binascii
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


def _require(ok, reason):
    if not ok:
        raise ValueError("v3_lz_" + reason)


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
        raise ValueError("v3_lz_json") from error


class _Budget:
    def __init__(self, tick, byte_limit, depth_limit, node_limit, *, syntax=False):
        _limit(byte_limit, MAX_BYTES)
        _limit(depth_limit, MAX_SYNTAX_DEPTH if syntax else MAX_DEPTH)
        _limit(node_limit, MAX_NODES)
        self.check = _callback(tick)
        self.byte_limit, self.depth_limit, self.node_limit = byte_limit, depth_limit, node_limit

    def inspect(self, value):
        # 显式栈与 Oracle 的递归计量分离；每次出现都独立累计。
        pending = [(value, 0)]
        byte_count = node_count = 0
        string_sizes = {}

        def scalar_size(item):
            if type(item) is str:
                if item not in string_sizes:
                    string_sizes[item] = len(canonical(item))
                return string_sizes[item]
            return len(canonical(item))

        while pending:
            self.check()
            item, depth = pending.pop()
            _require(depth <= self.depth_limit, "depth")
            node_count += 1
            _require(node_count <= self.node_limit, "nodes")
            kind = type(item)
            if item is None or kind in (str, int, bool):
                byte_count += scalar_size(item)
            else:
                _require(kind in (list, dict), "value_type")
                _require(len(item) <= self.node_limit - node_count, "nodes")
                byte_count += 2 + max(0, len(item) - 1)
                if kind is dict:
                    for key, child in item.items():
                        self.check()
                        _require(type(key) is str, "object_key")
                        byte_count += scalar_size(key) + 1
                        _require(byte_count <= self.byte_limit, "bytes")
                        pending.append((child, depth + 1))
                else:
                    for child in item:
                        self.check()
                        pending.append((child, depth + 1))
            _require(byte_count <= self.byte_limit, "bytes")
        self.check()
        return byte_count


def _compress(raw, *, tick=None, max_token_bytes=MAX_BYTES):
    """三字节索引和直接过期游标；最多每 16 个位置核验取消及期限。"""
    check = _callback(tick)
    _limit(max_token_bytes, MAX_BYTES)
    _require(type(raw) is bytes and len(raw) <= MAX_BYTES, "raw_input")
    result = bytearray()
    recent = {}
    cursor = retired = 0
    literal_start = 0
    literal_count = 0
    total = len(raw)

    def key_at(index):
        return raw[index:index + 3]

    def emit(part):
        check()
        _require(len(part) <= max_token_bytes - len(result), "token_bytes")
        result.extend(part)

    def flush():
        nonlocal literal_count
        if literal_count:
            emit(bytes([literal_count - 1]) + raw[literal_start:literal_start + literal_count])
            literal_count = 0

    while cursor < total:
        check()
        # 等距 65536 的候选仍有效；仅删除严格更早的位置。
        while retired < cursor - WINDOW:
            if retired % 16 == 0:
                check()
            if retired + 3 <= total:
                key = key_at(retired)
                if recent.get(key) == retired:
                    del recent[key]
            retired += 1
        length = 0
        source = None
        if cursor + 3 <= total:
            source = recent.get(key_at(cursor))
            if source is not None:
                length = 3
                ceiling = min(MAX_MATCH, total - cursor)
                while length < ceiling:
                    check()
                    stop = min(length + 16, ceiling)
                    # 相等段以完整原字节比较；不等段仍定位首个不同字节。
                    # 每段最多 16 字节，重叠候选同样只读取冻结输入。
                    if raw[cursor + length:cursor + stop] == raw[source + length:source + stop]:
                        length = stop
                    else:
                        while length < stop and raw[cursor + length] == raw[source + length]:
                            length += 1
                        break
        consumed_from = cursor
        if length >= 3:
            flush()
            distance = cursor - source
            _require(1 <= distance <= WINDOW, "encoder_distance")
            emit(bytes([length + 125]) + (distance - 1).to_bytes(2, "little"))
            cursor += length
        else:
            if literal_count == 0:
                literal_start = cursor
            literal_count += 1
            cursor += 1
            if literal_count == MAX_LITERAL:
                flush()
        for index in range(consumed_from, min(cursor, total - 2)):
            if index % 16 == 0:
                check()
            recent[key_at(index)] = index
    flush()
    check()
    return bytes(result)


def _inflate(tokens, raw_bytes, *, tick=None, max_bytes=MAX_BYTES,
             max_token_bytes=MAX_BYTES):
    check = _callback(tick)
    _limit(max_bytes, MAX_BYTES)
    _limit(max_token_bytes, MAX_BYTES)
    _require(type(raw_bytes) is int and 0 < raw_bytes <= max_bytes, "raw_bytes")
    _require(type(tokens) is bytes and len(tokens) <= max_token_bytes, "token_bytes")
    output = bytearray()
    index = 0
    while index < len(tokens):
        check()
        _require(len(output) < raw_bytes, "extra_token")
        tag = tokens[index]
        index += 1
        if tag <= 127:
            width = tag + 1
            _require(index + width <= len(tokens), "literal_truncated")
            _require(width <= raw_bytes - len(output), "expanded_bytes")
            output.extend(tokens[index:index + width])
            index += width
        else:
            width = tag - 125
            _require(index + 2 <= len(tokens), "copy_truncated")
            distance = int.from_bytes(tokens[index:index + 2], "little") + 1
            index += 2
            _require(0 < distance <= len(output), "copy_distance")
            _require(width <= raw_bytes - len(output), "expanded_bytes")
            # 每段最多 16 字节。非重叠用原字节切片；重叠按当前距离
            # 的周期扩展，仍等价于逐字节复制，不提前分配声明总长度。
            for offset in range(0, width, 16):
                check()
                span = min(16, width - offset)
                origin = len(output) - distance
                if span <= distance:
                    output.extend(output[origin:origin + span])
                else:
                    period = output[origin:]
                    output.extend((period * ((span + distance - 1) // distance))[:span])
    _require(len(output) == raw_bytes, "expanded_length")
    check()
    return bytes(output)


def _preparse(raw, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
              max_nodes=MAX_NODES):
    """显式语法状态栈：键不计值节点，根值逻辑深度为零。"""
    check = _callback(tick)
    _limit(max_bytes, MAX_BYTES)
    _limit(max_depth, MAX_DEPTH)
    _limit(max_nodes, MAX_NODES)
    _require(type(raw) is bytes and 0 < len(raw) <= max_bytes, "raw_bytes")
    offset = nodes = 0
    size = len(raw)
    frames = [["root", "value", -1]]

    def scan_string():
        nonlocal offset
        _require(offset < size and raw[offset] == 34, "string_start")
        offset += 1
        while offset < size:
            if offset % 128 == 0:
                check()
            item = raw[offset]
            offset += 1
            if item == 34:
                return
            _require(item >= 32, "string_control")
            if item == 92:
                check()
                _require(offset < size, "string_escape_truncated")
                escape = raw[offset]
                offset += 1
                if escape == 117:
                    _require(offset + 4 <= size, "unicode_escape")
                    for digit in raw[offset:offset + 4]:
                        _require(48 <= digit <= 57 or 65 <= digit <= 70 or 97 <= digit <= 102,
                                 "unicode_escape")
                    offset += 4
                else:
                    _require(escape in (34, 92, 47, 98, 102, 110, 114, 116), "string_escape")
        raise ValueError("v3_lz_string_unclosed")

    while frames:
        check()
        frame = frames[-1]
        kind, stage, parent_depth = frame
        if kind == "root" and stage == "end":
            _require(offset == size, "json_trailing_bytes")
            frames.pop()
            continue
        _require(offset < size, "json_unclosed")
        current = raw[offset]
        if stage == "first":
            closing = 93 if kind == "array" else 125
            if current == closing:
                offset += 1
                frames.pop()
            else:
                frame[1] = "value" if kind == "array" else "key"
            continue
        if stage == "separator":
            closing = 93 if kind == "array" else 125
            offset += 1
            if current == closing:
                frames.pop()
            else:
                _require(current == 44, "value_separator")
                frame[1] = "value" if kind == "array" else "key"
            continue
        if stage == "key":
            scan_string()
            frame[1] = "colon"
            continue
        if stage == "colon":
            _require(current == 58, "key_separator")
            offset += 1
            frame[1] = "value"
            continue
        _require(stage == "value", "preparse_state")
        depth = parent_depth + 1
        _require(depth <= max_depth, "preparse_depth")
        nodes += 1
        _require(nodes <= max_nodes, "preparse_nodes")
        frame[1] = "end" if kind == "root" else "separator"
        if current in (91, 123):
            offset += 1
            frames.append(["array" if current == 91 else "object", "first", depth])
        elif current == 34:
            scan_string()
        elif current in (116, 102, 110):
            word = b"true" if current == 116 else b"false" if current == 102 else b"null"
            _require(raw[offset:offset + len(word)] == word, "literal")
            offset += len(word)
        else:
            if current == 45:
                offset += 1
            _require(offset < size and 48 <= raw[offset] <= 57, "integer")
            if raw[offset] == 48:
                offset += 1
            else:
                while offset < size and 48 <= raw[offset] <= 57:
                    if offset % 128 == 0:
                        check()
                    offset += 1
    check()
    return {"nodes": nodes, "bytes": size}


def _strict_load(raw):
    def object_items(items):
        keys = set()
        for key, _ in items:
            _require(key not in keys, "duplicate_json_key")
            keys.add(key)
        return dict(items)

    def no_float(value):
        raise ValueError("v3_lz_noninteger_json_number")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=object_items,
                          parse_float=no_float, parse_constant=no_float)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("v3_lz_strict_json") from error


def pack(value, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
         max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
         max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
         max_token_bytes=MAX_BYTES):
    budget = _Budget(tick, max_bytes, max_depth, max_nodes)
    syntax = _Budget(tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes, syntax=True)
    _limit(max_token_bytes, MAX_BYTES)
    measured = budget.inspect(value)
    raw = canonical(value)
    budget.check()
    _require(measured == len(raw), "raw_accounting")
    tokens = _compress(raw, tick=budget.check, max_token_bytes=max_token_bytes)
    document = {"encoding": ENCODING, "raw_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(tokens).decode("ascii")}
    syntax.inspect(document)
    budget.check()
    return document


def unpack(document, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
           max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
           max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
           max_token_bytes=MAX_BYTES):
    budget = _Budget(tick, max_bytes, max_depth, max_nodes)
    syntax = _Budget(tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes, syntax=True)
    _limit(max_token_bytes, MAX_BYTES)
    syntax.inspect(document)
    _require(type(document) is dict and set(document) == {
        "encoding", "raw_bytes", "sha256", "data"}, "header")
    _require(type(document["encoding"]) is str and document["encoding"] == ENCODING,
             "encoding")
    count = document["raw_bytes"]
    digest = document["sha256"]
    text = document["data"]
    _require(type(count) is int and 0 < count <= max_bytes, "raw_bytes")
    _require(type(digest) is str and len(digest) == 64
             and all(ch in "0123456789abcdef" for ch in digest), "digest_shape")
    _require(type(text) is str and len(text) > 0 and len(text) % 4 == 0, "base64_shape")
    padding = 2 if text.endswith("==") else 1 if text.endswith("=") else 0
    _require(len(text) // 4 * 3 - padding <= max_token_bytes, "token_bytes")
    budget.check()
    try:
        tokens = base64.b64decode(text.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error, ValueError) as error:
        raise ValueError("v3_lz_base64") from error
    _require(len(tokens) <= max_token_bytes
             and base64.b64encode(tokens).decode("ascii") == text, "base64_canonical")
    budget.check()
    raw = _inflate(tokens, count, tick=budget.check, max_bytes=max_bytes,
                   max_token_bytes=max_token_bytes)
    _require(hashlib.sha256(raw).hexdigest() == digest, "digest")
    _preparse(raw, tick=budget.check, max_bytes=max_bytes, max_depth=max_depth,
              max_nodes=max_nodes)
    budget.check()
    restored = _strict_load(raw)
    measured = budget.inspect(restored)
    _require(measured == len(raw) and canonical(restored) == raw, "raw_canonical")
    budget.check()
    # 原对象、物理表示预算和规范原字节已在本调用完整核验；直接
    # 重压缩这些已验证字节，避免 pack 再遍历同一对象和再次 JSON 编码。
    # 不缓存认证标记，仍比较整个重编码表示的规范字节。
    expected_tokens = _compress(raw, tick=budget.check, max_token_bytes=max_token_bytes)
    expected = {"encoding": ENCODING, "raw_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(expected_tokens).decode("ascii")}
    _require(canonical(expected) == canonical(document), "noncanonical")
    budget.check()
    return restored
