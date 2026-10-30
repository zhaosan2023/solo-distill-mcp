"""Python 3.12 syntactic units. Source bytes are data, never imported or run."""
from __future__ import annotations

import ast
import codecs
import hashlib
import io
import tokenize
from typing import Any

PARSER_VERSION = "python-3.12-ast-v1"
DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def identity(namespace: str, value: Any) -> str:
    import json
    return hashlib.sha256((namespace + ":" + json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False)).encode("utf-8")).hexdigest()


def is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return ((isinstance(left, ast.Name) and left.id == "__name__"
             and isinstance(right, ast.Constant) and right.value == "__main__")
            or (isinstance(right, ast.Name) and right.id == "__name__"
                and isinstance(left, ast.Constant) and left.value == "__main__"))


class Coordinates:
    """Convert AST UTF-8 columns to character columns and ORIGINAL byte offsets."""
    def __init__(self, raw: bytes):
        self.encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
        self.text = raw.decode(self.encoding)
        self.codec = codecs.lookup(self.encoding).name
        self.bom = 3 if raw.startswith(codecs.BOM_UTF8) else 0
        if self.codec == "utf-8-sig":
            self.codec = "utf-8"
        if self.text.encode(self.codec) != raw[self.bom:]:
            raise ValueError("non_lossless_source_encoding")
        # Python physical lines end only at CR, LF or CRLF. Other Unicode
        # separators and form feed may occur inside literals or whitespace.
        self.lines = io.StringIO(self.text, newline="").readlines()
        self.offsets = []
        offset = self.bom
        for line in self.lines:
            self.offsets.append(offset)
            offset += len(line.encode(self.codec))

    def position(self, line: int, utf8_column: int) -> tuple[int, int]:
        prefix = self.lines[line - 1].encode("utf-8")[:utf8_column].decode("utf-8")
        column = len(prefix)
        return column, self.offsets[line - 1] + len(prefix.encode(self.codec))

    def span(self, node: ast.AST) -> dict[str, Any]:
        start_line, start_col = node.lineno, node.col_offset
        if isinstance(node, DEFINITIONS) and node.decorator_list:
            first = min(node.decorator_list, key=lambda item: (item.lineno, item.col_offset))
            start_line, start_col = first.lineno, first.col_offset - 1
        end_line, end_col = node.end_lineno, node.end_col_offset
        char_start, raw_start = self.position(start_line, start_col)
        char_end, raw_end = self.position(end_line, end_col)
        return {"start": [start_line, start_col], "end": [end_line, end_col],
                "char_start": [start_line, char_start], "char_end": [end_line, char_end],
                "raw_start": raw_start, "raw_end": raw_end}


def extract_units(path: str, raw: bytes) -> dict[str, Any]:
    from .snapshot_v2 import cached_units
    return cached_units(path, raw, _extract_units)


def _extract_units(path: str, raw: bytes) -> dict[str, Any]:
    from .extractor import relative_path
    path = relative_path(path)
    if path == ".":
        raise ValueError("source_path_not_file")
    coordinates = Coordinates(raw)
    from .snapshot_v2 import parsed_tree
    tree = parsed_tree(raw, path)
    digest = hashlib.sha256(raw).hexdigest()
    file_id = identity("file-v2", [path, digest, PARSER_VERSION])
    units: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []

    def walk(node: ast.AST, route: list[Any], scope: list[str],
             active: list[int], owner: int | None, top: bool = False) -> None:
        definition = isinstance(node, DEFINITIONS)
        if definition or (top and isinstance(node, ast.stmt)):
            kind = "definition" if definition else "main_guard" if is_main_guard(node) else "statement"
            qualified = ".".join(scope + [node.name]) if definition else None
            span = coordinates.span(node)
            unit = {"id": identity("unit-v2", [file_id, route, kind, qualified, span]),
                    "kind": kind, "ast_type": type(node).__name__, "syntax_path": route,
                    "qualified_name": qualified, **span, "conditions": list(active)}
            owner = len(units)
            units.append(unit)
        child_scope = scope + [node.name] if definition else scope
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            branch = None
            if isinstance(node, ast.If) and field in {"body", "orelse"} and children:
                branch = "if" if field == "body" else "else"
            elif isinstance(node, (ast.Try, ast.TryStar)) and field in {"body", "orelse", "finalbody"} and children:
                branch = {"body": "try", "orelse": "else", "finalbody": "finally"}[field]
            elif isinstance(node, ast.ExceptHandler) and field == "body":
                branch = "except"
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)) and field in {"body", "orelse"} and children:
                branch = "loop" if field == "body" else "loop_else"
            elif isinstance(node, (ast.With, ast.AsyncWith)) and field == "body":
                branch = "with"
            elif isinstance(node, ast.match_case) and field == "body":
                branch = "case"
            nested = active
            if branch is not None:
                if owner is None:
                    raise ValueError("condition_without_unit")
                condition = {"id": identity("condition-v2", [file_id, route, field, branch]),
                             "syntax_path": route, "field": field, "branch": branch,
                             "scope": ".".join(child_scope), "unit": owner}
                nested = active + [len(conditions)]
                conditions.append(condition)
            for index, child in enumerate(children):
                if isinstance(child, ast.AST):
                    child_route = route + [field] + ([index] if isinstance(value, list) else [])
                    walk(child, child_route, child_scope, nested, owner,
                         isinstance(node, ast.Module) and field == "body")

    walk(tree, [], [], [], None)
    return {"file": {"id": file_id, "path": path, "sha256": digest,
                     "encoding": coordinates.encoding, "parse_status": "parsed"},
            "units": units, "conditions": conditions}


def body_text(raw: bytes, file: dict[str, Any], unit: dict[str, Any]) -> str:
    encoding = "utf-8" if file["encoding"] == "utf-8-sig" else file["encoding"]
    return raw[unit["raw_start"]:unit["raw_end"]].decode(encoding)
