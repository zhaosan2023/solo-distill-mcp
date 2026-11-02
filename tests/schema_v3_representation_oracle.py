"""V3 无损表示的独立 Oracle；不导入任何生产模块。

完整源码期望仍由原双层 Oracle 计算。本模块仅证明表示可逆及预算；
不证明真实网络投递、冷预检、业务语义或宿主加载。
"""
from __future__ import annotations

import json

ENCODING = "dictionary-columnar-v3-1"
MAX_BYTES = 8388608
MAX_DEPTH = 64
MAX_NODES = 500000


def _require(ok, reason):
    if not ok:
        raise ValueError("independent_v3_representation_" + reason)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ValueError("independent_v3_representation_json") from error


class _Accounting:
    def __init__(self, tick, max_bytes, max_depth, max_nodes):
        for value, ceiling in ((max_bytes, MAX_BYTES), (max_depth, MAX_DEPTH),
                               (max_nodes, MAX_NODES)):
            _require(type(value) is int and 0 <= value <= ceiling, "budget")
        self.tick = tick if tick is not None else (lambda: None)
        self.max_bytes, self.max_depth, self.max_nodes = max_bytes, max_depth, max_nodes
        self.bytes = self.nodes = 0

    def enter(self, depth):
        self.tick()
        _require(depth <= self.max_depth, "depth")
        self.nodes += 1
        _require(self.nodes <= self.max_nodes, "nodes")

    def add(self, size):
        self.bytes += size
        _require(self.bytes <= self.max_bytes, "bytes")

    def container(self, width):
        # 每个直接子值至少一个节点；分配展开容器前拒绝不可能的宽度。
        _require(width <= self.max_nodes - self.nodes, "nodes")
        self.add(2 + max(0, width - 1))


def pack(value, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
         max_nodes=MAX_NODES):
    """从原对象独立建立全部字符串字典和对象键组，不删除记录。"""
    meter = _Accounting(tick, max_bytes, max_depth, max_nodes)
    strings, shapes = set(), set()

    def collect(item, depth):
        meter.enter(depth)
        kind = type(item)
        if kind is str:
            meter.add(len(_json(item)))
            strings.add(item)
        elif item is None or kind in (int, bool):
            meter.add(len(_json(item)))
        elif kind is list:
            meter.container(len(item))
            for child in item:
                collect(child, depth + 1)
        elif kind is dict:
            _require(all(type(key) is str for key in item), "object_key")
            meter.container(len(item))
            keys = tuple(sorted(item))
            shapes.add(keys)
            for key in keys:
                meter.tick()
                meter.add(len(_json(key)) + 1)
                strings.add(key)
                collect(item[key], depth + 1)
        else:
            raise ValueError("independent_v3_representation_value_type")

    collect(value, 0)
    words, columns = sorted(strings), sorted(shapes)
    word_index = {word: index for index, word in enumerate(words)}
    shape_index = {shape: index for index, shape in enumerate(columns)}

    def encode(item):
        meter.tick()
        if type(item) is str:
            return -(word_index[item] + 1)
        if type(item) is int:
            return {"i": item} if item < 0 else item
        if item is None or type(item) is bool:
            return item
        if type(item) is dict:
            keys = tuple(sorted(item))
            return {"o": shape_index[keys], "v": [encode(item[key]) for key in keys]}
        if item and all(type(child) is dict for child in item):
            keys = tuple(sorted(item[0]))
            if all(tuple(sorted(child)) == keys for child in item):
                return {"t": shape_index[keys],
                        "r": [[encode(child[key]) for key in keys] for child in item]}
        return [encode(child) for child in item]

    result = {"encoding": ENCODING, "strings": words,
              "schemas": [[word_index[key] for key in keys] for keys in columns],
              "value": encode(value)}
    meter.tick()
    return result


def unpack(document, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
           max_nodes=MAX_NODES):
    """展开前及展开中精确核算原规范 JSON，不接受无用字典或非规范表示。"""
    meter = _Accounting(tick, max_bytes, max_depth, max_nodes)
    meter.tick()
    _require(type(document) is dict and set(document) == {
        "encoding", "strings", "schemas", "value"}, "header")
    _require(type(document["encoding"]) is str
             and document["encoding"] == ENCODING, "encoding")
    words, schemas = document["strings"], document["schemas"]
    _require(type(words) is list and type(schemas) is list, "tables")
    _require(len(words) <= MAX_NODES and len(schemas) <= MAX_NODES, "table_width")
    previous = None
    for word in words:
        meter.tick()
        _require(type(word) is str and (previous is None or previous < word), "strings")
        _require(len(_json(word)) <= max_bytes, "bytes")
        previous = word
    columns = []
    previous_keys = None
    for row in schemas:
        meter.tick()
        _require(type(row) is list and len(row) <= MAX_NODES, "schema")
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

    def keys_for(index):
        _require(type(index) is int and 0 <= index < len(columns), "object_reference")
        return columns[index]

    def object_row(keys, row, depth):
        _require(type(row) is list and len(row) == len(keys), "row_width")
        meter.enter(depth)
        meter.container(len(keys))
        result = {}
        for key, encoded in zip(keys, row):
            meter.tick()
            meter.add(len(_json(key)) + 1)
            result[key] = decode(encoded, depth + 1)
        return result

    def decode(item, depth):
        # 对象行自行计入节点，避免把表示标签当成展开节点。
        kind = type(item)
        if kind is dict and set(item) == {"o", "v"}:
            return object_row(keys_for(item["o"]), item["v"], depth)
        meter.enter(depth)
        if item is None or kind is bool or (kind is int and item >= 0):
            meter.add(len(_json(item)))
            return item
        if kind is int:
            index = -item - 1
            _require(0 <= index < len(words), "string_reference")
            meter.add(len(_json(words[index])))
            return words[index]
        if kind is list:
            meter.container(len(item))
            return [decode(child, depth + 1) for child in item]
        _require(kind is dict, "tag_type")
        if set(item) == {"i"}:
            _require(type(item["i"]) is int and item["i"] < 0, "integer_escape")
            meter.add(len(_json(item["i"])))
            return item["i"]
        _require(set(item) == {"t", "r"}, "tag")
        keys = keys_for(item["t"])
        rows = item["r"]
        _require(type(rows) is list and bool(rows), "table_rows")
        meter.container(len(rows))
        return [object_row(keys, row, depth + 1) for row in rows]

    result = decode(document["value"], 0)
    expected = pack(result, tick=meter.tick, max_bytes=max_bytes,
                    max_depth=max_depth, max_nodes=max_nodes)
    _require(_json(expected) == _json(document), "noncanonical")
    meter.tick()
    return result
