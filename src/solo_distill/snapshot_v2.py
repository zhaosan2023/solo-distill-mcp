"""Private, bounded-lifetime syntax reuse. No caller-supplied authentication flags.

Trees are private read-only analysis inputs, never executed. Public registries are
always detached copies; package and seal validation still recompute comparisons.
Content keys are derived here from actual bytes, not supplied source identities.
"""
from __future__ import annotations

import ast
import copy
import hashlib
from contextvars import ContextVar
from functools import wraps

_ACTIVE = ContextVar("solo_distill_syntax_snapshot", default=None)
RULE_VERSION = "snapshot-role-v1"


def snapshot_scope(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _ACTIVE.get() is not None:
            return function(*args, **kwargs)
        token = _ACTIVE.set({"trees": {}, "units": {}, "roles": {}, "parse_calls": 0})
        try:
            return function(*args, **kwargs)
        finally:
            _ACTIVE.reset(token)
    return wrapped


def parsed_tree(raw, path="<source>"):
    from .syntax_v2 import Coordinates, PARSER_VERSION
    cache = _ACTIVE.get()
    key = (hashlib.sha256(raw).digest(), PARSER_VERSION)
    if cache is None:
        return ast.parse(Coordinates(raw).text, filename=path, feature_version=(3, 12))
    if key not in cache["trees"]:
        cache["trees"][key] = ast.parse(Coordinates(raw).text, filename=path,
                                         feature_version=(3, 12))
        cache["parse_calls"] += 1
    return cache["trees"][key]


def cached_units(path, raw, build):
    from .syntax_v2 import PARSER_VERSION
    cache = _ACTIVE.get()
    if cache is None:
        return build(path, raw)
    key = (path, hashlib.sha256(raw).digest(), PARSER_VERSION)
    if key not in cache["units"]:
        cache["units"][key] = build(path, raw)
    return copy.deepcopy(cache["units"][key])


def cached_roles(raw, unit, build):
    from .syntax_v2 import PARSER_VERSION
    cache = _ACTIVE.get()
    if cache is None:
        return build(raw, unit)
    key = (hashlib.sha256(raw).digest(), PARSER_VERSION, RULE_VERSION,
           tuple(unit["syntax_path"]), unit["kind"])
    if key not in cache["roles"]:
        cache["roles"][key] = tuple(build(raw, unit))
    return list(cache["roles"][key])


def snapshot_metrics():
    cache = _ACTIVE.get()
    return {"parser_version": "python-3.12-ast-v1", "rule_version": RULE_VERSION,
            "parse_calls": cache["parse_calls"] if cache else 0,
            "indexed_files": len(cache["units"]) if cache else 0,
            "lifetime": "current_operation_only_no_historical_green_state"}
