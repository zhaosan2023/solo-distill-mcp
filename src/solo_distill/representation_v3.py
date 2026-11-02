"""显式 V3 字典／列式无损表示；不改变原对象、身份或旧入口。

仅操作内存中的封闭 JSON 数据；不导入测试 Oracle，不联网或读写文件。
展开预算按还原对象的完整规范字节、值节点和逻辑深度计量。
"""
from __future__ import annotations

import json

ENCODING = "dictionary-columnar-v3-1"
MAX_BYTES = 8388608
MAX_DEPTH = 64
MAX_NODES = 500000


def _reject(reason):
    raise ValueError("v3_representation_" + reason)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ValueError("v3_representation_invalid_json") from error


class _Budget:
    def __init__(self, tick, max_bytes, max_depth, max_nodes):
        for limit, ceiling in ((max_bytes, MAX_BYTES), (max_depth, MAX_DEPTH),
                               (max_nodes, MAX_NODES)):
            if type(limit) is not int or not 0 <= limit <= ceiling:
                _reject("invalid_budget")
        self.tick = (lambda: None) if tick is None else tick
        self.byte_limit, self.depth_limit, self.node_limit = max_bytes, max_depth, max_nodes
        self.bytes = self.nodes = 0
        self.string_sizes = {}

    def add(self, size):
        if size > self.byte_limit - self.bytes:
            _reject("expanded_bytes")
        self.bytes += size

    def scalar_size(self, value):
        if type(value) is str:
            if value not in self.string_sizes:
                self.string_sizes[value] = len(_json(value))
            return self.string_sizes[value]
        return len(_json(value))

    def enter(self, depth):
        self.tick()
        if depth > self.depth_limit:
            _reject("expanded_depth")
        if self.nodes >= self.node_limit:
            _reject("expanded_nodes")
        self.nodes += 1

    def width(self, count):
        if count > self.node_limit - self.nodes:
            _reject("expanded_nodes")
        self.add(2 + max(0, count - 1))


def pack(value, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
         max_nodes=MAX_NODES):
    """显式生成规范表示；全部字符串、字段和重复记录均保留。"""
    budget = _Budget(tick, max_bytes, max_depth, max_nodes)
    strings, layouts = set(), set()
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        budget.enter(depth)
        kind = type(item)
        if kind is str:
            budget.add(budget.scalar_size(item))
            strings.add(item)
        elif item is None or kind in (bool, int):
            budget.add(budget.scalar_size(item))
        elif kind is list:
            budget.width(len(item))
            pending.extend((child, depth + 1) for child in reversed(item))
        elif kind is dict:
            budget.width(len(item))
            if any(type(key) is not str for key in item):
                _reject("object_key_type")
            keys = tuple(sorted(item))
            layouts.add(keys)
            for key in keys:
                budget.tick()
                budget.add(budget.scalar_size(key) + 1)
                strings.add(key)
            pending.extend((item[key], depth + 1) for key in reversed(keys))
        else:
            _reject("value_type")
    budget.tick()
    words = sorted(strings)
    shapes = sorted(layouts)
    references = {word: index for index, word in enumerate(words)}
    shape_ids = {shape: index for index, shape in enumerate(shapes)}

    def row(item, keys):
        return [encode(item[key]) for key in keys]

    def encode(item):
        budget.tick()
        kind = type(item)
        if kind is str:
            return -references[item] - 1
        if kind is int:
            return item if item >= 0 else {"i": item}
        if item is None or kind is bool:
            return item
        if kind is dict:
            keys = tuple(sorted(item))
            return {"o": shape_ids[keys], "v": row(item, keys)}
        if item and type(item[0]) is dict:
            keys = tuple(sorted(item[0]))
            uniform = True
            for child in item:
                budget.tick()
                if type(child) is not dict or tuple(sorted(child)) != keys:
                    uniform = False
                    break
            if uniform:
                return {"t": shape_ids[keys], "r": [row(child, keys) for child in item]}
        return [encode(child) for child in item]

    encoded = encode(value)
    budget.tick()
    schemas = []
    for shape in shapes:
        budget.tick()
        schemas.append([references[key] for key in shape])
    return {"encoding": ENCODING, "strings": words, "schemas": schemas, "value": encoded}


def unpack(document, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
           max_nodes=MAX_NODES):
    """逐值限额还原，再重编码核对唯一表示；不先无界展开后检查。"""
    budget = _Budget(tick, max_bytes, max_depth, max_nodes)
    budget.tick()
    if (type(document) is not dict or set(document) != {"encoding", "strings", "schemas", "value"}
            or type(document["encoding"]) is not str or document["encoding"] != ENCODING):
        _reject("header_or_encoding")
    words, schemas = document["strings"], document["schemas"]
    if (type(words) is not list or type(schemas) is not list
            or len(words) > MAX_NODES or len(schemas) > MAX_NODES):
        _reject("table_shape_or_width")
    for index, word in enumerate(words):
        budget.tick()
        if (type(word) is not str or (index and words[index - 1] >= word)
                or budget.scalar_size(word) > max_bytes):
            _reject("string_order_or_budget")
    layouts = []
    for schema in schemas:
        budget.tick()
        if type(schema) is not list or len(schema) > MAX_NODES:
            _reject("schema_shape")
        keys = []
        for reference in schema:
            budget.tick()
            if type(reference) is not int or not 0 <= reference < len(words):
                _reject("schema_reference")
            word = words[reference]
            if keys and keys[-1] >= word:
                _reject("schema_key_order")
            keys.append(word)
        keys = tuple(keys)
        if layouts and layouts[-1] >= keys:
            _reject("schema_order")
        layouts.append(keys)

    def layout(reference):
        if type(reference) is not int or not 0 <= reference < len(layouts):
            _reject("layout_reference")
        return layouts[reference]

    def object_value(keys, values, depth):
        if type(values) is not list or len(values) != len(keys):
            _reject("row_width")
        budget.enter(depth)
        budget.width(len(keys))
        restored = {}
        for index, key in enumerate(keys):
            budget.tick()
            budget.add(budget.scalar_size(key) + 1)
            restored[key] = decode(values[index], depth + 1)
        return restored

    def decode(item, depth):
        kind = type(item)
        if kind is dict:
            fields = set(item)
            if fields == {"o", "v"}:
                return object_value(layout(item["o"]), item["v"], depth)
            budget.enter(depth)
            if fields == {"i"}:
                integer = item["i"]
                if type(integer) is not int or integer >= 0:
                    _reject("integer_escape")
                budget.add(budget.scalar_size(integer))
                return integer
            if fields != {"t", "r"}:
                _reject("unknown_tag")
            keys = layout(item["t"])
            rows = item["r"]
            if type(rows) is not list or not rows:
                _reject("table_rows")
            budget.width(len(rows))
            return [object_value(keys, values, depth + 1) for values in rows]
        budget.enter(depth)
        if kind is list:
            budget.width(len(item))
            return [decode(child, depth + 1) for child in item]
        if kind is int and item < 0:
            reference = -item - 1
            if not 0 <= reference < len(words):
                _reject("string_reference")
            value = words[reference]
        elif item is None or kind in (bool, int):
            value = item
        else:
            _reject("encoded_value_type")
        budget.add(budget.scalar_size(value))
        return value

    restored = decode(document["value"], 0)
    budget.tick()
    expected = pack(restored, tick=budget.tick, max_bytes=max_bytes,
                    max_depth=max_depth, max_nodes=max_nodes)
    if _json(expected) != _json(document):
        _reject("noncanonical")
    budget.tick()
    return restored
