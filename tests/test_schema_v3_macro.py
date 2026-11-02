"""Schema 3.0 宏观契约的手工期望与篡改负控，不证明在线或局部验收。"""
from __future__ import annotations

import copy
import unittest

from schema_v3_macro_oracle import authenticate_macro, canonical, expected_macro


SOURCES = {
    "pkg/__init__.py": b"",
    "pkg/base.py": b'''"""module documentation"""
class Base:
    """base documentation"""
    pass

def helper(x=1) -> int:
    return x
''',
    "app.py": b'''import pkg.base as b, json as j
from pkg.base import helper as h

@h()
class Derived(b.Base):
    """derived documentation"""
    value = h()

    @h()
    def work(self, x=h()) -> int:
        if x:
            return h(x)
        return self.work(x)

def duplicate():
    return h()

def duplicate():
    return h()

if FLAG:
    def guarded():
        return h()

def caller():
    return duplicate(), guarded()
''',
}


class MacroOracleTests(unittest.TestCase):
    def setUp(self):
        self.value = expected_macro(SOURCES, project_id="fixture", snapshot_id="snapshot-1")

    def symbols_named(self, name):
        return [(i, row) for i, row in enumerate(self.value["symbols"]) if row[4] == name]

    def edges(self, expression, *, phase=None):
        expressions = self.value["expressions"]
        return [row for row in self.value["topology"]
                if expressions[row[3]] == expression and (phase is None or row[5] == phase)]

    def test_manually_counted_complete_denominators(self):
        mapping = self.value["mapping"]
        for key, expected in {
            "definition_count": 8,
            "documentation_count": 3,
            "module_count": 3,
            "condition_site_count": 2,
            "call_site_count": 11,
            "import_statement_count": 2,
            "import_alias_count": 3,
            "inheritance_site_count": 1,
        }.items():
            with self.subTest(key=key):
                self.assertEqual(mapping[key], expected)
        self.assertEqual(len(self.value["symbols"]), 14)
        self.assertEqual([row[1] for row in self.value["files"]], sorted(SOURCES))
        self.assertEqual(len(self.symbols_named("pkg")), 1)
        self.assertEqual(mapping["topology_edge_count"], len(self.value["topology"]))
        self.assertEqual(mapping["unknown_site_count"], sum(
            row[7] for row in self.value["topology"] if row[2] is None))
        self.assertIs(mapping["omitted_noncore_micro_details"], True)

    def test_complete_signatures_documentation_and_hierarchy(self):
        derived_index, derived = self.symbols_named("Derived")[0]
        work_index, work = self.symbols_named("Derived.work")[0]
        module_index, _ = self.symbols_named("app")[0]
        self.assertEqual(derived[5], "(b.Base)")
        self.assertEqual(work[5], "(self, x=h()) -> int")
        self.assertEqual(work[2], derived_index)
        self.assertEqual(derived[2], module_index)
        self.assertEqual(work[9], ["body", 2, "body", 2])
        self.assertEqual(work[7], [10, 4])
        self.assertEqual(work[8], [13, 27])
        self.assertIsNone(work[6])
        docs = [row for row in self.value["symbols"] if row[3] == "documentation"]
        self.assertEqual({row[6] for row in docs}, {
            "module documentation", "base documentation", "derived documentation"})
        derived_doc = next(row for row in docs if row[6] == "derived documentation")
        self.assertEqual(derived_doc[2], derived_index)
        self.assertEqual(derived_doc[9], ["body", 2, "body", 0])
        self.assertLess(derived_index, work_index)

    def test_redefinitions_are_distinct_and_not_known_targets(self):
        duplicates = self.symbols_named("duplicate")
        self.assertEqual(len(duplicates), 2)
        self.assertNotEqual(duplicates[0][1][0], duplicates[1][1][0])
        self.assertNotEqual(duplicates[0][1][9], duplicates[1][1][9])
        edge, = self.edges("duplicate")
        self.assertIsNone(edge[2])
        self.assertEqual(edge[4], "rebound_or_ambiguous_binding")
        guarded, = self.edges("guarded")
        self.assertIsNone(guarded[2])
        self.assertEqual(guarded[4], "conditional_or_unsafe_binding")
        self.assertIs(self.symbols_named("guarded")[0][1][10], True)

    def test_import_alias_denominator_and_inheritance_resolution(self):
        imports = [row for row in self.value["topology"] if row[0] == "module_dependency"]
        self.assertEqual(sum(row[7] for row in imports), 3)
        self.assertEqual({self.value["expressions"][row[3]] for row in imports}, {
            "import pkg.base as b", "import json as j", "from pkg.base import helper as h"})
        module_index, _ = self.symbols_named("app")[0]
        self.assertTrue(all(row[1] == module_index for row in imports))
        external, = self.edges("import json as j")
        self.assertIsNone(external[2])
        self.assertEqual(external[4], "external_or_ambiguous_module")
        inherited, = self.edges("b.Base")
        self.assertEqual(inherited[0], "inheritance")
        self.assertEqual(inherited[1], self.symbols_named("Derived")[0][0])
        self.assertEqual(inherited[2], self.symbols_named("Base")[0][0])
        self.assertEqual(inherited[5], "class_base")
        self.assertIsNone(inherited[4])

    def test_definition_evaluation_not_misassigned_to_function_body(self):
        module_index, _ = self.symbols_named("app")[0]
        class_index, _ = self.symbols_named("Derived")[0]
        work_index, _ = self.symbols_named("Derived.work")[0]
        helper_index, _ = self.symbols_named("helper")[0]
        decorators = self.edges("h", phase="decorator")
        self.assertEqual({row[1] for row in decorators}, {module_index, class_index})
        default, = self.edges("h", phase="default")
        self.assertEqual(default[1], class_index)
        self.assertEqual(default[2], helper_index)
        method_call = next(row for row in self.edges("h", phase="function_body")
                           if row[1] == work_index)
        self.assertIs(method_call[6], True)
        self.assertEqual(method_call[2], helper_index)
        dynamic, = self.edges("self.work")
        self.assertEqual(dynamic[1], work_index)
        self.assertIsNone(dynamic[2])
        self.assertEqual(dynamic[4], "local_or_parameter_binding")

    def test_method_body_does_not_inherit_class_lexical_bindings(self):
        raw = {"a.py": b'''def target():
    pass
class Example:
    target = 1
    def method(self):
        return target()
'''}
        value = expected_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        edge, = value["topology"]
        target_index = next(i for i, row in enumerate(value["symbols"]) if row[4] == "target")
        self.assertEqual(edge[2], target_index)
        self.assertEqual(edge[5], "function_body")

    def test_duplicate_sites_are_aggregated_not_lost(self):
        raw = {"a.py": b'''def target():
    pass

def caller():
    target()
    target()
'''}
        value = expected_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        edge, = value["topology"]
        self.assertEqual(edge[7], 2)
        self.assertEqual(value["mapping"]["call_site_count"], 2)
        self.assertEqual(value["mapping"]["topology_edge_count"], 1)

    def test_relative_imports_annotations_and_nonclass_bases(self):
        raw = {
            "pkg/__init__.py": b"",
            "pkg/base.py": b"class Base:\n    pass\ndef helper():\n    return int\n",
            "pkg/child.py": b'''from .base import Base as Parent, helper
class Child(Parent):
    pass
class NotKnownClass(helper):
    pass
def annotated(value: helper()) -> helper():
    return value
''',
        }
        value = expected_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        bases = [row for row in value["topology"] if row[0] == "inheritance"]
        parent = next(row for row in bases if value["expressions"][row[3]] == "Parent")
        self.assertIsNotNone(parent[2])
        invalid = next(row for row in bases if value["expressions"][row[3]] == "helper")
        self.assertIsNone(invalid[2])
        self.assertEqual(invalid[4], "base_not_unique_class")
        annotations = [row for row in value["topology"] if row[0] == "call"]
        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0][5], "annotation")
        self.assertEqual(annotations[0][7], 2)

    def test_encoding_empty_file_and_long_required_metadata(self):
        doc = "文" * 5000
        raw = {
            "empty.py": b"",
            "bom.py": b"\xef\xbb\xbf" + ('"""' + doc + '"""\r\n').encode(),
            "latin.py": b'# coding: latin-1\r\n"""caf\xe9"""\r\ndef f():\r\n    pass\r\n',
        }
        value = expected_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        docs = [row[6] for row in value["symbols"] if row[3] == "documentation"]
        self.assertIn(doc, docs)
        self.assertIn("café", docs)
        empty = next(row for row in value["symbols"] if row[4] == "empty")
        self.assertEqual(empty[7:9], [[1, 0], [1, 0]])
        self.assertEqual(value["mapping"]["module_count"], 3)

    def test_identity_bound_to_project_snapshot_and_raw_bytes(self):
        other = expected_macro(SOURCES, project_id="other", snapshot_id="snapshot-1")
        newer = expected_macro(SOURCES, project_id="fixture", snapshot_id="snapshot-2")
        changed_sources = dict(SOURCES)
        changed_sources["pkg/base.py"] += b"\n"
        changed = expected_macro(changed_sources, project_id="fixture", snapshot_id="snapshot-1")
        ids = {row[0] for row in self.value["symbols"]}
        self.assertFalse(ids & {row[0] for row in other["symbols"]})
        self.assertFalse(ids & {row[0] for row in newer["symbols"]})
        base_file = next(i for i, row in enumerate(self.value["files"]) if row[1] == "pkg/base.py")
        base_ids = {row[0] for row in self.value["symbols"] if row[1] == base_file}
        self.assertFalse(base_ids & {row[0] for row in changed["symbols"] if row[1] == base_file})

    def test_independent_full_field_tamper_controls(self):
        def mutation(kind):
            value = copy.deepcopy(self.value)
            if kind == "missing_file":
                value["files"].pop()
            elif kind == "duplicate_file":
                value["files"].append(copy.deepcopy(value["files"][0]))
            elif kind == "missing_definition":
                value["symbols"].pop(self.symbols_named("duplicate")[0][0])
            elif kind == "duplicate_symbol":
                value["symbols"].append(copy.deepcopy(value["symbols"][0]))
            elif kind == "signature":
                value["symbols"][self.symbols_named("Derived.work")[0][0]][5] = "()"
            elif kind == "documentation":
                next(row for row in value["symbols"] if row[3] == "documentation")[6] = "forged"
            elif kind == "coordinate":
                value["symbols"][self.symbols_named("Derived.work")[0][0]][7][0] += 1
            elif kind == "missing_edge":
                value["topology"].pop()
            elif kind == "duplicate_edge":
                value["topology"].append(copy.deepcopy(value["topology"][0]))
            elif kind == "unknown_promoted":
                row = next(row for row in value["topology"] if row[2] is None)
                row[2], row[4] = 0, None
            elif kind == "phase":
                next(row for row in value["topology"] if row[5] == "decorator")[5] = "function_body"
            elif kind == "site_count":
                value["topology"][0][7] += 1
            elif kind == "bool_as_integer":
                value["schema_version"] = True
            elif kind == "false_mapping":
                value["mapping"]["call_site_count"] -= 1
            elif kind == "extra_field":
                value["authenticated"] = True
            return value

        for kind in ("missing_file", "duplicate_file", "missing_definition", "duplicate_symbol",
                     "signature", "documentation", "coordinate", "missing_edge", "duplicate_edge",
                     "unknown_promoted", "phase", "site_count", "bool_as_integer", "false_mapping",
                     "extra_field"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                authenticate_macro(mutation(kind), SOURCES,
                                   project_id="fixture", snapshot_id="snapshot-1")
        result = authenticate_macro(self.value, SOURCES,
                                    project_id="fixture", snapshot_id="snapshot-1")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["tier"], 1)

    def test_trusted_input_zero_match_and_invalid_source_fail_closed(self):
        for raw in ({}, {"../a.py": b"pass\n"}, {"a.py": "pass\n"}, {"a.py": b"def :"}):
            with self.subTest(raw=raw), self.assertRaises((ValueError, SyntaxError)):
                expected_macro(raw, project_id="fixture", snapshot_id="snapshot-1")
        with self.assertRaises(ValueError):
            canonical({"float": 1.0})
        with self.assertRaises(ValueError):
            canonical({"nan": float("nan")})

    def test_tick_failure_is_not_converted_to_success(self):
        def cancelled():
            raise ValueError("cancelled_or_expired")
        with self.assertRaisesRegex(ValueError, "cancelled_or_expired"):
            expected_macro(SOURCES, project_id="fixture", snapshot_id="snapshot-1", tick=cancelled)


if __name__ == "__main__":
    unittest.main()
