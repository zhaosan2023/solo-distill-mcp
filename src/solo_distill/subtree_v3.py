"""显式 V3 第二种无损表示：重复编码子树的确定后向引用。

不改变前版本规则，不导入独立 Oracle；只处理内存封闭 JSON。
引用只节省表示字节，每次原值展开仍计量且不共享可变视图。
"""
from __future__ import annotations

from . import representation_v3 as predecessor

ENCODING = "dictionary-columnar-v3-2"
MAX_BYTES = 8388608
MAX_DEPTH = 64
MAX_NODES = 500000
MAX_SYNTAX_DEPTH = 192
MAX_REFERENCE_HOPS = 192
MIN_SUBTREE_BYTES = 64


def _reject(reason):
    raise ValueError("v3_subtree_" + reason)


def _options(tick, max_bytes, max_depth, max_nodes, max_syntax_bytes,
             max_syntax_depth, max_syntax_nodes, max_reference_hops):
    if tick is not None and not callable(tick):
        _reject("invalid_tick")
    for value, ceiling in ((max_bytes, MAX_BYTES), (max_depth, MAX_DEPTH),
                           (max_nodes, MAX_NODES), (max_syntax_bytes, MAX_BYTES),
                           (max_syntax_depth, MAX_SYNTAX_DEPTH),
                           (max_syntax_nodes, MAX_NODES),
                           (max_reference_hops, MAX_REFERENCE_HOPS)):
        if type(value) is not int or not 0 <= value <= ceiling:
            _reject("invalid_budget")
    return (lambda: None) if tick is None else tick


def _syntax(value, tick, byte_limit, depth_limit, node_limit):
    """迭代检查完整物理表示；先检查宽度，再扩展待检查栈。"""
    pending = [(value, 0)]
    nodes = size = 0
    sizes = {}

    def scalar_bytes(item):
        if type(item) is str:
            if item not in sizes:
                sizes[item] = len(predecessor._json(item))
            return sizes[item]
        return len(predecessor._json(item))

    while pending:
        tick()
        item, depth = pending.pop()
        nodes += 1
        if depth > depth_limit:
            _reject("syntax_depth")
        if nodes > node_limit:
            _reject("syntax_nodes")
        kind = type(item)
        if item is None or kind in (str, int, bool):
            size += scalar_bytes(item)
        elif kind in (list, dict):
            width = len(item)
            if width > node_limit - nodes:
                _reject("syntax_nodes")
            size += 2 + max(0, width - 1)
            if kind is dict:
                if any(type(key) is not str for key in item):
                    _reject("syntax_object_key")
                for key in item:
                    tick()
                    size += scalar_bytes(key) + 1
                    if size > byte_limit:
                        _reject("syntax_bytes")
                pending.extend((child, depth + 1) for child in item.values())
            else:
                pending.extend((child, depth + 1) for child in item)
        else:
            _reject("syntax_value_type")
        if size > byte_limit:
            _reject("syntax_bytes")
    tick()


def pack(value, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
         max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
         max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
         max_reference_hops=MAX_REFERENCE_HOPS):
    """按原编码值树出现计数；完整字节作键而非仅依赖摘要。"""
    tick = _options(tick, max_bytes, max_depth, max_nodes, max_syntax_bytes,
                    max_syntax_depth, max_syntax_nodes, max_reference_hops)
    tick()
    base = predecessor.pack(value, tick=tick, max_bytes=max_bytes,
                            max_depth=max_depth, max_nodes=max_nodes)
    contents = {}
    counts = {}
    representatives = {}
    stack = [(base["value"], False)]

    def part(item):
        if type(item) in (list, dict):
            return contents[id(item)]
        return predecessor._json(item), 0

    while stack:
        tick()
        item, completed = stack.pop()
        kind = type(item)
        if kind not in (list, dict):
            continue
        children = list(item.values()) if kind is dict else item
        if not completed:
            stack.append((item, True))
            stack.extend((child, False) for child in reversed(children))
            continue
        height = 1
        for child in children:
            tick()
            height = max(height, part(child)[1] + 1)
        if kind is list:
            raw = b"[" + b",".join(part(child)[0] for child in item) + b"]"
        else:
            raw_parts = []
            for key in sorted(item):
                tick()
                raw_parts.append(predecessor._json(key) + b":" + part(item[key])[0])
            raw = b"{" + b",".join(raw_parts) + b"}"
        contents[id(item)] = raw, height
        if len(raw) >= MIN_SUBTREE_BYTES:
            counts[raw] = counts.get(raw, 0) + 1
            representatives.setdefault(raw, (item, height))
    candidates = []
    for raw, count in counts.items():
        tick()
        if count >= 2:
            candidates.append(raw)
    candidates.sort(key=lambda raw: (representatives[raw][1], raw))
    tick()
    slots = {}
    for index, raw in enumerate(candidates):
        tick()
        slots[raw] = index
    if candidates and max_reference_hops < 1:
        _reject("reference_hops")

    def fold(item, root=False):
        tick()
        kind = type(item)
        if kind not in (list, dict):
            return item
        raw = contents[id(item)][0]
        if not root and raw in slots:
            return {"q": slots[raw]}
        if kind is list:
            return [fold(child) for child in item]
        return {key: fold(item[key]) for key in sorted(item)}

    table = []
    for raw in candidates:
        tick()
        table.append(fold(representatives[raw][0], root=True))
    document = {"encoding": ENCODING, "strings": base["strings"],
                "schemas": base["schemas"], "subtrees": table,
                "value": fold(base["value"])}
    _syntax(document, tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes)
    tick()
    return document


def unpack(document, *, tick=None, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH,
           max_nodes=MAX_NODES, max_syntax_bytes=MAX_BYTES,
           max_syntax_depth=MAX_SYNTAX_DEPTH, max_syntax_nodes=MAX_NODES,
           max_reference_hops=MAX_REFERENCE_HOPS):
    """有界按需解引用，包括行容器；不预展开编码树或复用可变原值。"""
    tick = _options(tick, max_bytes, max_depth, max_nodes, max_syntax_bytes,
                    max_syntax_depth, max_syntax_nodes, max_reference_hops)
    _syntax(document, tick, max_syntax_bytes, max_syntax_depth, max_syntax_nodes)
    if (type(document) is not dict or set(document) != {
            "encoding", "strings", "schemas", "subtrees", "value"}
            or type(document["encoding"]) is not str or document["encoding"] != ENCODING):
        _reject("header_or_encoding")
    words, schemas, table = document["strings"], document["schemas"], document["subtrees"]
    if any(type(item) is not list for item in (words, schemas, table)):
        _reject("tables")
    budget = predecessor._Budget(tick, max_bytes, max_depth, max_nodes)
    for index, word in enumerate(words):
        tick()
        if type(word) is not str or (index and words[index - 1] >= word):
            _reject("string_order")
        if budget.scalar_size(word) > max_bytes:
            _reject("expanded_bytes")
    layouts = []
    for schema in schemas:
        tick()
        if type(schema) is not list:
            _reject("schema_shape")
        keys = []
        for index in schema:
            tick()
            if type(index) is not int or not 0 <= index < len(words):
                _reject("schema_reference")
            key = words[index]
            if keys and keys[-1] >= key:
                _reject("schema_key_order")
            keys.append(key)
        keys = tuple(keys)
        if layouts and layouts[-1] >= keys:
            _reject("schema_order")
        layouts.append(keys)

    def check_references(root, upper):
        pending = [root]
        while pending:
            tick()
            item = pending.pop()
            if type(item) is dict:
                if "q" in item:
                    if (set(item) != {"q"} or type(item["q"]) is not int
                            or not 0 <= item["q"] < upper):
                        _reject("reference_tag_or_range")
                else:
                    pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)

    for index, entry in enumerate(table):
        tick()
        if type(entry) not in (list, dict) or (type(entry) is dict and "q" in entry):
            _reject("table_root")
        check_references(entry, index)
    check_references(document["value"], len(table))

    def resolve(item):
        tick()
        hops = 0
        while type(item) is dict and set(item) == {"q"}:
            tick()
            hops += 1
            if hops > max_reference_hops:
                _reject("reference_hops")
            item = table[item["q"]]
        return item

    def layout(index):
        tick()
        if type(index) is not int or not 0 <= index < len(layouts):
            _reject("layout_reference")
        return layouts[index]

    def restore_row(keys, encoded, depth):
        budget.enter(depth)
        budget.width(len(keys))
        encoded = resolve(encoded)
        if type(encoded) is not list or len(encoded) != len(keys):
            _reject("row_width")
        result = {}
        for index, key in enumerate(keys):
            tick()
            budget.add(budget.scalar_size(key) + 1)
            result[key] = restore(encoded[index], depth + 1)
        return result

    def restore(encoded, depth):
        encoded = resolve(encoded)
        kind = type(encoded)
        if kind is dict:
            tags = set(encoded)
            if tags == {"o", "v"}:
                return restore_row(layout(encoded["o"]), encoded["v"], depth)
            budget.enter(depth)
            if tags == {"i"}:
                value = encoded["i"]
                if type(value) is not int or value >= 0:
                    _reject("integer_escape")
                budget.add(budget.scalar_size(value))
                return value
            if tags != {"t", "r"}:
                _reject("unknown_tag")
            keys = layout(encoded["t"])
            rows = resolve(encoded["r"])
            if type(rows) is not list or not rows:
                _reject("table_rows")
            budget.width(len(rows))
            return [restore_row(keys, row, depth + 1) for row in rows]
        budget.enter(depth)
        if kind is list:
            budget.width(len(encoded))
            return [restore(child, depth + 1) for child in encoded]
        if kind is int and encoded < 0:
            index = -encoded - 1
            if not 0 <= index < len(words):
                _reject("string_reference")
            value = words[index]
        elif encoded is None or kind in (bool, int):
            value = encoded
        else:
            _reject("encoded_value_type")
        budget.add(budget.scalar_size(value))
        return value

    result = restore(document["value"], 0)
    tick()
    expected = pack(result, tick=tick, max_bytes=max_bytes, max_depth=max_depth,
                    max_nodes=max_nodes, max_syntax_bytes=max_syntax_bytes,
                    max_syntax_depth=max_syntax_depth, max_syntax_nodes=max_syntax_nodes,
                    max_reference_hops=max_reference_hops)
    if predecessor._json(expected) != predecessor._json(document):
        _reject("noncanonical")
    tick()
    return result
