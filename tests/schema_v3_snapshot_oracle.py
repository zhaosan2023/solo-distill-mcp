"""独立 Oracle 操作内复用；仅标准库，不导入或共享生产期望。

缓存键由实际源码字节、路径和固定解析规则生成；不接受包内认证标记。
树仅作只读分析输入，规范核心返回独立副本；操作结束必定释放。
"""
from __future__ import annotations

import ast
import copy
from contextvars import ContextVar
from functools import wraps
import hashlib
import json

_ACTIVE = ContextVar("independent_schema_v3_snapshot", default=None)
PARSER = "python-3.12-ast-v1"
RULE = "independent-schema-v3-snapshot-1"


def oracle_scope(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _ACTIVE.get() is not None:
            return function(*args, **kwargs)
        token = _ACTIVE.set({"trees": {}, "inventories": {}, "cores": {},
                             "parse_calls": 0, "inventory_builds": 0, "core_builds": 0})
        try:
            return function(*args, **kwargs)
        finally:
            _ACTIVE.reset(token)
    return wrapped


def source_key(path, raw):
    return path, hashlib.sha256(raw).digest(), PARSER, RULE


def parsed_tree(raw, path):
    cache = _ACTIVE.get()
    if cache is None:
        return ast.parse(raw, filename=path, feature_version=(3, 12))
    key = source_key(path, raw)
    if key not in cache["trees"]:
        cache["trees"][key] = ast.parse(raw, filename=path, feature_version=(3, 12))
        cache["parse_calls"] += 1
    return cache["trees"][key]


def cached_inventory(function):
    @wraps(function)
    def wrapped(path, raw, tick):
        tick()
        cache = _ACTIVE.get()
        if cache is None:
            return function(path, raw, tick)
        key = source_key(path, raw)
        if key not in cache["inventories"]:
            cache["inventories"][key] = function(path, raw, tick)
            cache["inventory_builds"] += 1
        file, units, conditions, nodes, tree = cache["inventories"][key]
        # 记录可变视图隔离；AST 不离开独立只读分析链，不共享生产树。
        result = (copy.deepcopy(file), copy.deepcopy(units),
                  copy.deepcopy(conditions), dict(nodes), tree)
        tick()
        return result
    return wrapped


def cached_core(raw_files, trusted_bytes, build, tick):
    tick()
    cache = _ACTIVE.get()
    if cache is None:
        return build()
    key = (tuple(source_key(path, raw) for path, raw in sorted(raw_files.items())),
           trusted_bytes)
    if key not in cache["cores"]:
        value = build()
        cache["cores"][key] = json.dumps(value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        cache["core_builds"] += 1
    value = json.loads(cache["cores"][key])
    tick()
    return value


def oracle_metrics():
    cache = _ACTIVE.get()
    return {"parser_version": PARSER, "rule_version": RULE,
            "parse_calls": cache["parse_calls"] if cache else 0,
            "inventory_builds": cache["inventory_builds"] if cache else 0,
            "core_builds": cache["core_builds"] if cache else 0,
            "lifetime": "current_operation_only_no_cross_process_cache",
            "production_expectations_shared": False}
