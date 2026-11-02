"""Schema 3.0 生产宏观层差分、属性与负控；不执行来源或访问网关。

期望值来自独立测试端 Oracle，生产实现不得导入该 Oracle。
两端共享标准库 AST 的共同失效风险不因差分一致而消失。
"""
from __future__ import annotations

import ast
import copy
import random
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import canonical, expected_macro
from test_schema_v3_macro import SOURCES
from solo_distill.macro_v3 import build_macro, validate_macro


class ProductionMacroTests(unittest.TestCase):
    def compare(self, sources, *, project_id="fixture", snapshot_id="snapshot-1"):
        expected = expected_macro(sources, project_id=project_id, snapshot_id=snapshot_id)
        actual = build_macro(sources, project_id=project_id, snapshot_id=snapshot_id)
        self.assertEqual(canonical(actual), canonical(expected))
        validate_macro(actual, sources, project_id=project_id, snapshot_id=snapshot_id)
        return actual

    def test_complete_manual_fixture_matches_independent_oracle(self):
        actual = self.compare(SOURCES)
        self.assertEqual(actual["mapping"]["definition_count"], 8)
        self.assertEqual(actual["mapping"]["call_site_count"], 11)
        self.assertEqual(actual["mapping"]["import_alias_count"], 3)
        self.assertEqual(actual["mapping"]["inheritance_site_count"], 1)

    def test_generated_binding_and_aggregation_properties(self):
        rng = random.Random(20261030)
        for sample in range(40):
            repetitions = rng.randint(1, 8)
            guarded = bool(rng.randrange(2))
            rebound = bool(rng.randrange(2))
            statements = ["from pkg.base import target as alias", "import pkg.base as module",
                          "class Child(module.Base):", "    pass",
                          "def caller(value=alias()):"]
            if guarded:
                statements.append("    if value:")
            indentation = "        " if guarded else "    "
            statements.extend(indentation + "alias()" for _ in range(repetitions))
            statements.append("    return module.target(), value.dispatch()")
            if rebound:
                statements.append("alias = None")
            raw = {
                "pkg/__init__.py": b"",
                "pkg/base.py": b"class Base:\n    pass\ndef target():\n    return 1\n",
                "sample.py": ("\n".join(statements) + "\n").encode(),
            }
            with self.subTest(sample=sample, guarded=guarded, rebound=rebound):
                actual = self.compare(raw)
                self.assertEqual(actual["mapping"]["call_site_count"], repetitions + 3)
                self.assertEqual(sum(row[7] for row in actual["topology"] if row[0] == "call"),
                                 actual["mapping"]["call_site_count"])
                self.assertEqual(actual["mapping"]["unknown_site_count"],
                                 sum(row[7] for row in actual["topology"] if row[2] is None))

    def test_evaluation_phases_and_lexical_class_boundary(self):
        self.compare({"a.py": b'''def target():
    return int
class Example:
    target = None
    @target()
    def method(self, value=target(), other: target()=1) -> target():
        return target(), self.method()
'''})

    def test_condition_branches_and_pattern_bindings(self):
        self.compare({"a.py": b'''def target():
    return 1
try:
    import json as item
except Exception as failure:
    item = None
else:
    item = 1
finally:
    item = 2

def caller(value):
    match value:
        case {"key": target, **rest}:
            return target(), rest()
    while value:
        target()
    else:
        target()
    with value:
        target()
'''})

    def test_relative_import_and_nonclass_base(self):
        self.compare({
            "pkg/__init__.py": b"",
            "pkg/base.py": b"class Base:\n    pass\ndef helper():\n    return int\n",
            "pkg/child.py": b"from .base import Base as Parent, helper\nclass Child(Parent):\n    pass\nclass Unknown(helper):\n    pass\n",
            "pkg/sibling.py": b"from . import base\nfrom ...missing import thing\nthing()\n",
        })

    def test_empty_bom_crlf_latin1_and_required_metadata_are_preserved(self):
        doc = "文" * 5000
        argument = "parameter_" + "x" * 4200
        raw = {
            "empty.py": b"",
            "bom.py": b"\xef\xbb\xbf" + ('"""' + doc + '"""\r\n').encode(),
            "latin.py": b'# coding: latin-1\r\n"""caf\xe9"""\r\ndef f():\r\n    pass\r\n',
            "long.py": ("def f(" + argument + "):\n    return None\n").encode(),
        }
        actual = self.compare(raw)
        self.assertIn(doc, [row[6] for row in actual["symbols"] if row[3] == "documentation"])
        self.assertTrue(any(argument in (row[5] or "") for row in actual["symbols"]))

    def test_order_and_result_detachment(self):
        actual = self.compare(SOURCES)
        reversed_input = dict(reversed(list(SOURCES.items())))
        self.assertEqual(canonical(actual), canonical(build_macro(
            reversed_input, project_id="fixture", snapshot_id="snapshot-1")))
        actual["symbols"][0][4] = "tampered"
        self.compare(SOURCES)

    def test_project_snapshot_and_content_are_bound(self):
        original = self.compare(SOURCES)
        for project, snapshot in (("other", "snapshot-1"), ("fixture", "snapshot-2")):
            with self.subTest(project=project, snapshot=snapshot):
                other = self.compare(SOURCES, project_id=project, snapshot_id=snapshot)
                self.assertFalse({row[0] for row in original["symbols"]} &
                                 {row[0] for row in other["symbols"]})
                with self.assertRaises(ValueError):
                    validate_macro(original, SOURCES, project_id=project, snapshot_id=snapshot)
        changed = {**SOURCES, "pkg/base.py": SOURCES["pkg/base.py"] + b"\n"}
        with self.assertRaises(ValueError):
            validate_macro(original, changed, project_id="fixture", snapshot_id="snapshot-1")

    def test_every_required_field_and_denominator_is_authenticated(self):
        original = self.compare(SOURCES)
        variants = []
        for table in ("files", "symbols", "topology", "expressions"):
            removed = copy.deepcopy(original)
            removed[table].pop()
            variants.append((table + "_omitted", removed))
            duplicated = copy.deepcopy(original)
            duplicated[table].append(copy.deepcopy(duplicated[table][0]))
            variants.append((table + "_duplicated", duplicated))
        for column, replacement in ((0, "invented-id"), (1, True), (2, 999999),
                                    (3, "invented-kind"), (4, "invented-name"),
                                    (5, "truncated"), (6, "invented-doc"),
                                    (7, [0, 0]), (8, [0, 0]), (9, ["body", 999]),
                                    (10, True)):
            value = copy.deepcopy(original)
            value["symbols"][0][column] = replacement
            variants.append(("symbol_column_" + str(column), value))
        for column, replacement in ((0, "invented-layer"), (1, True), (2, 999999),
                                    (3, 999999), (4, "invented-reason"),
                                    (5, "invented-phase"), (6, "not-bool"), (7, 0)):
            value = copy.deepcopy(original)
            value["topology"][0][column] = replacement
            variants.append(("edge_column_" + str(column), value))
        for key in original["mapping"]:
            value = copy.deepcopy(original)
            value["mapping"][key] = False if type(value["mapping"][key]) is bool else -1
            variants.append(("mapping_" + key, value))
        for key, replacement in (("schema_version", True), ("parser_version", "unknown"),
                                  ("rule_version", "unknown"), ("project_id", "other"),
                                  ("snapshot_id", "other"), ("extra_field", True)):
            value = copy.deepcopy(original)
            value[key] = replacement
            variants.append((key, value))
        for label, value in variants:
            with self.subTest(mutation=label), self.assertRaises(ValueError):
                validate_macro(value, SOURCES, project_id="fixture", snapshot_id="snapshot-1")

    def test_invalid_source_and_identity_inputs_fail_closed(self):
        for raw in ({}, {"../a.py": b"pass\n"}, {"/a.py": b"pass\n"},
                    {"a\\b.py": b"pass\n"}, {"a.txt": b"pass\n"},
                    {"a.py": "pass\n"}, {"a.py": b"def :"}):
            with self.subTest(raw=raw), self.assertRaises((ValueError, SyntaxError)):
                build_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        for project, snapshot in ((True, "snapshot-1"), ("fixture", False),
                                  ("", "snapshot-1"), ("fixture", "")):
            with self.subTest(project=project, snapshot=snapshot), self.assertRaises(ValueError):
                build_macro(SOURCES, project_id=project, snapshot_id=snapshot)

    def test_cancellation_is_propagated_from_build_and_validation(self):
        def cancelled():
            raise RuntimeError("cancelled_or_expired")
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_expired"):
            build_macro(SOURCES, project_id="fixture", snapshot_id="snapshot-1", tick=cancelled)
        actual = self.compare(SOURCES)
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_expired"):
            validate_macro(actual, SOURCES, project_id="fixture", snapshot_id="snapshot-1", tick=cancelled)

    def test_source_is_only_parsed_and_independent_oracle_is_not_called(self):
        raw = {"a.py": b"raise RuntimeError('must not execute')\n"}
        expected = expected_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        with patch("schema_v3_macro_oracle.expected_macro", side_effect=AssertionError("production imported oracle")), \
             patch("socket.socket", side_effect=AssertionError("offline only")):
            actual = build_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
            validate_macro(actual, raw, project_id="fixture", snapshot_id="snapshot-1")
        self.assertEqual(canonical(actual), canonical(expected))
        with open("src/solo_distill/macro_v3.py", encoding="utf-8") as source:
            tree = ast.parse(source.read())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse(any(name.startswith("tests") or "oracle" in name for name in imports))


if __name__ == "__main__":
    unittest.main()
