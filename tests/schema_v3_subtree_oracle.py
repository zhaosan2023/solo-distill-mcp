"""重复子结构表示的独立 Oracle；不导入生产模块。

仅借用既有独立编码器实现公开的前版本规则。新候选与引用表、
有界按需解码、原对象计量及规范复算在本模块实现。
不证明真实容量、冷预检、在线业务、人工语义或宿主加载。
"""
from __future__ import annotations

import json

import schema_v3_representation_oracle as predecessor

ENCODING = "dictionary-columnar-v3-2"
MAX_BYTES = 8388608
MAX_DEPTH = 64
MAX_NODES = 500000
MAX_SYNTAX_DEPTH = 192
MAX_REFERENCE_HOPS = 192
MIN_SUBTREE_BYTES = 64


def _require(condition, reason):
    if not condition:
        raise ValueError("independent_v3_subtree_" + reason)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ValueError("independent_v3_subtree_json") from error


class _Meter:
    def __init__(self, tick, max_bytes, max_depth, max_nodes, *, syntax=False):
        depth_ceiling = MAX_SYNTAX_DEPTH if syntax else MAX_DEPTH
        for value, ceiling in ((max_bytes, MAX_BYTES), (max_depth, depth_ceiling),
                               (max_nodes, MAX_NODES)):
            _require(type(value) is int and 0 <= value <= ceiling, "budget")
        _require(tick is None or callable(tick), "tick")
        self.tick = (lambda: None) if tick is None else tick
        self.max_bytes, self.max_depth, self.max_nodes = max_bytes, max_depth, max_nodes
        self.bytes = self.nodes = 0

    def enter(self, depth):
        self.tick()
        _require(depth <= self.max_depth, "depth")
        self.nodes += 1
        _require(self.nodes <= self.max_nodes, "nodes")

    def add(self, count):
        self.bytes += count
        _require(self.bytes <= self.max_bytes, "bytes")

    def container(self, width):
        _require(width <= self.max_nodes - self.nodes, "nodes")
        self.add(2 + max(0, width - 1))


def _syntax(document, tick, max_bytes, max_depth, max_nodes):
    meter = _Meter(tick, max_bytes, max_depth, max_nodes, syntax=True)

    def visit(item, depth):
        meter.enter(depth)
        kind = type(item)
        if item is None or kind in (str, int, bool):
            meter.add(len(canonical(item)))
        elif kind is list:
            meter.container(len(item))
            for child in item:
                visit(child, depth + 1)
        elif kind is dict:
            _require(all(type(key) is str for key in item), "object_key")
            meter.container(len(item))
            for key in sorted(item):
                meter.tick()
                meter.add(len(canonical(key)) + 1)
                visit(item[key], depth + 1)
        else:
            raise ValueError("independent_v3_subtree_value_type")

    visit(document, 0)
    return meter


def _hop_limit(value):
    _require(type(value) is int and 0 <= value <= MAX_REFERENCE_HOPS, "hop_budget")


def pack(value, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
         max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
         max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
         max_reference_hops=MAX_REFERENCE_HOPS):
    """依据原编码值树完整字节计数；不按内存地址去重出现次数。"""
    limits = _Meter(tick, max_bytes, max_depth, max_nodes)
    _Meter(tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes, syntax=True)
    _hop_limit(max_reference_hops)
    limits.tick()
    base = predecessor.pack(value, tick=limits.tick, max_bytes=max_bytes,
                            max_depth=max_depth, max_nodes=max_nodes)
    occurrences = {}
    identities = {}

    def inspect(item):
        limits.tick()
        kind = type(item)
        if kind not in (list, dict):
            return canonical(item), 0
        if kind is list:
            children = [inspect(child) for child in item]
            raw = b"[" + b",".join(part[0] for part in children) + b"]"
        else:
            children = []
            parts = []
            for key in sorted(item):
                limits.tick()
                child = inspect(item[key])
                children.append(child)
                parts.append(canonical(key) + b":" + child[0])
            raw = b"{" + b",".join(parts) + b"}"
        height = 1 + max((child[1] for child in children), default=0)
        identities[id(item)] = raw
        if len(raw) >= MIN_SUBTREE_BYTES:
            if raw in occurrences:
                occurrences[raw][0] += 1
            else:
                occurrences[raw] = [1, height, item]
        return raw, height

    inspect(base["value"])
    selected = []
    for raw, (count, height, item) in occurrences.items():
        limits.tick()
        if count >= 2:
            selected.append((height, raw, item))
    selected.sort(key=lambda entry: (entry[0], entry[1]))
    indices = {raw: number for number, (_, raw, _) in enumerate(selected)}

    def fold(item, *, inline_root=False):
        limits.tick()
        kind = type(item)
        if kind not in (list, dict):
            return item
        raw = identities[id(item)]
        if not inline_root and raw in indices:
            return {"q": indices[raw]}
        if kind is list:
            return [fold(child) for child in item]
        return {key: fold(item[key]) for key in sorted(item)}

    document = {"encoding": ENCODING, "strings": base["strings"],
                "schemas": base["schemas"],
                "subtrees": [fold(item, inline_root=True) for _, _, item in selected],
                "value": fold(base["value"])}
    _syntax(document, limits.tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes)
    # 表项根不为引用；任意一次连续引用解析至多跳转一次。
    _require(not selected or max_reference_hops >= 1, "reference_hops")
    limits.tick()
    return document


def unpack(document, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
           max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
           max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
           max_reference_hops=MAX_REFERENCE_HOPS):
    """引用包括编码元数据数组；逐原值展开，不物化重复编码树。"""
    meter = _Meter(tick, max_bytes, max_depth, max_nodes)
    _hop_limit(max_reference_hops)
    _syntax(document, meter.tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes)
    _require(type(document) is dict and set(document) == {
        "encoding", "strings", "schemas", "subtrees", "value"}, "header")
    _require(type(document["encoding"]) is str and document["encoding"] == ENCODING,
             "encoding")
    words, schemas, table = (document[key] for key in ("strings", "schemas", "subtrees"))
    _require(type(words) is list and type(schemas) is list and type(table) is list, "tables")
    previous = None
    for word in words:
        meter.tick()
        _require(type(word) is str and (previous is None or previous < word), "strings")
        _require(len(canonical(word)) <= max_bytes, "bytes")
        previous = word
    columns = []
    previous_keys = None
    for row in schemas:
        meter.tick()
        _require(type(row) is list, "schema")
        keys = []
        for index in row:
            meter.tick()
            _require(type(index) is int and 0 <= index < len(words), "schema_reference")
            key = words[index]
            _require(not keys or keys[-1] < key, "schema_order")
            keys.append(key)
        keys = tuple(keys)
        _require(previous_keys is None or previous_keys < keys, "schemas_order")
        columns.append(keys)
        previous_keys = keys

    def references(item, upper):
        meter.tick()
        if type(item) is dict:
            if "q" in item:
                _require(set(item) == {"q"}, "reference_tag")
                index = item["q"]
                _require(type(index) is int and 0 <= index < upper, "reference")
                return
            for child in item.values():
                references(child, upper)
        elif type(item) is list:
            for child in item:
                references(child, upper)

    for number, entry in enumerate(table):
        meter.tick()
        _require(type(entry) in (list, dict), "table_entry")
        _require(type(entry) is not dict or "q" not in entry, "table_root_reference")
        references(entry, number)
    references(document["value"], len(table))

    def resolve(item):
        hops = 0
        meter.tick()
        while type(item) is dict and set(item) == {"q"}:
            hops += 1
            _require(hops <= max_reference_hops, "reference_hops")
            meter.tick()
            item = table[item["q"]]
        return item

    def shape(index):
        meter.tick()
        _require(type(index) is int and 0 <= index < len(columns), "object_reference")
        return columns[index]

    def object_row(keys, row, depth):
        meter.enter(depth)
        meter.container(len(keys))
        row = resolve(row)
        _require(type(row) is list and len(row) == len(keys), "row_width")
        result = {}
        for key, encoded in zip(keys, row):
            meter.tick()
            meter.add(len(canonical(key)) + 1)
            result[key] = decode(encoded, depth + 1)
        return result

    def decode(item, depth):
        item = resolve(item)
        kind = type(item)
        if kind is dict and set(item) == {"o", "v"}:
            return object_row(shape(item["o"]), item["v"], depth)
        meter.enter(depth)
        if item is None or kind is bool or (kind is int and item >= 0):
            meter.add(len(canonical(item)))
            return item
        if kind is int:
            index = -item - 1
            _require(0 <= index < len(words), "string_reference")
            meter.add(len(canonical(words[index])))
            return words[index]
        if kind is list:
            meter.container(len(item))
            return [decode(child, depth + 1) for child in item]
        _require(kind is dict, "tag_type")
        if set(item) == {"i"}:
            _require(type(item["i"]) is int and item["i"] < 0, "integer_escape")
            meter.add(len(canonical(item["i"])))
            return item["i"]
        _require(set(item) == {"t", "r"}, "tag")
        keys = shape(item["t"])
        rows = resolve(item["r"])
        _require(type(rows) is list and bool(rows), "table_rows")
        meter.container(len(rows))
        return [object_row(keys, row, depth + 1) for row in rows]

    result = decode(document["value"], 0)
    expected = pack(result, tick=meter.tick, max_bytes=max_bytes,
                    max_depth=max_depth, max_nodes=max_nodes,
                    max_syntax_bytes=max_syntax_bytes, max_syntax_depth=max_syntax_depth,
                    max_syntax_nodes=max_syntax_nodes, max_reference_hops=max_reference_hops)
    _require(canonical(expected) == canonical(document), "noncanonical")
    meter.tick()
    return result
