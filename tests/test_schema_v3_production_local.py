"""Schema 3.0 生产局部闭包的独立差分、负控与副作用边界。

生产端只消费可信源码和包外核心清单；期望值由标准库独立 Oracle
计算，不使用生产返回的分母。此门禁不代表选择独立性或业务语义通过。
"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from schema_v3_local_oracle import expected_local_syntax
from schema_v3_macro_oracle import canonical
from test_schema_v3_local import SOURCES, core_ids
from solo_distill.local_v3 import build_local_syntax, validate_local_syntax
from solo_distill.snapshot_v2 import snapshot_scope


class ProductionLocalTests(unittest.TestCase):
    def trusted(self, files=SOURCES, names=None):
        names = names if names is not None else {
            ("pkg/base.py", "helper"), ("pkg/service.py", "Service.handle")}
        return {"project_id": "fixture", "snapshot_id": "snapshot-1",
                "selection_id": "external-selection-1",
                "required_core_unit_ids": core_ids(files, names)}

    @snapshot_scope
    def compare(self, files, trusted):
        expected = expected_local_syntax(files, **trusted)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            actual = build_local_syntax(files, **trusted)
            validate_local_syntax(actual, files, **trusted)
        self.assertEqual(canonical(actual), canonical(expected))
        return actual

    def test_complete_methods_and_dependency_without_unrelated_class(self):
        local = self.compare(SOURCES, self.trusted())
        package = local["compatibility_package"]
        self.assertEqual([f["path"] for f in package["files"]],
                         ["pkg/base.py", "pkg/service.py"])
        self.assertEqual([u["qualified_name"] for u in package["units"]],
                         ["helper", "Service.handle"])
        self.assertEqual(len(package["owners"]), 2)
        self.assertEqual(len(package["conditions"]), 2)
        self.assertEqual(len(package["relations"]), 2)
        self.assertNotIn("unrelated", local["bodies"][1]["text"])
        self.assertTrue(all(f["level"] == "direct_static"
                            for f in package["role_proofs"]))
        self.assertEqual(package["facts"], [])
        self.assertEqual(package["obligations"], [])

    def test_outer_guard_and_internal_branches_remain_complete(self):
        files = {"sample.py": b"if enabled:\n @decorate\n def handle():\n  try:\n   return helper()\n  except ValueError:\n   raise\nelse:\n fallback = 0\n"}
        trusted = self.trusted(files, {("sample.py", "handle")})
        local = self.compare(files, trusted)
        package = local["compatibility_package"]
        self.assertEqual(len(package["owners"]), 1)
        self.assertEqual(local["bodies"][0]["text"], files["sample.py"].decode().rstrip())
        self.assertEqual({c["branch"] for c in package["conditions"]},
                         {"if", "else", "try", "except"})
        self.assertEqual(len(package["sources"]), 2)

    def test_nested_real_aliases_do_not_double_charge(self):
        files = {"sample.py": b"def handle():\n def inner():\n  return 1\n return inner()\n"}
        trusted = self.trusted(files, {("sample.py", "handle"),
                                      ("sample.py", "handle.inner")})
        local = self.compare(files, trusted)
        self.assertEqual(len(local["bodies"]), 1)
        self.assertEqual(len(local["compatibility_package"]["sources"]), 2)
        self.assertEqual(local["compatibility_package"]["owners"][0]["body_bytes"],
                         len(local["bodies"][0]["text"].encode("utf-8")))

    def test_encoding_and_original_byte_coordinates(self):
        variants = [
            b"\xef\xbb\xbf@decorate\r\nasync def handle(value):\r\n return '\xe5\x80\xbc'\r\n",
            b"# coding: latin-1\r\ndef handle():\r\n return '\xe9'\r\n",
            "def handle():\n return '值\u2028仍是字符串'\n".encode("utf-8"),
        ]
        for raw in variants:
            with self.subTest(raw=raw):
                files = {"sample.py": raw}
                local = self.compare(files, self.trusted(files, {("sample.py", "handle")}))
                package = local["compatibility_package"]
                self.assertEqual(package["owners"][0]["body_bytes"],
                                 len(local["bodies"][0]["text"].encode("utf-8")))

    def test_full_scope_stays_external_and_unrelated_source_is_not_executed(self):
        local = self.compare(SOURCES, self.trusted())
        self.assertNotIn("unused.py", [f["path"] for f in local["compatibility_package"]["files"]])
        self.assertEqual(local["compatibility_package"]["coverage"]["scan_files"], 2)
        self.assertEqual(len(SOURCES), 4)

    def test_core_body_condition_role_relation_and_alias_tampering_rejected(self):
        trusted = self.trusted()
        original = self.compare(SOURCES, trusted)
        mutations = []
        value = copy.deepcopy(original)
        value["core_unit_ids"].pop()
        mutations.append(value)
        value = copy.deepcopy(original)
        value["bodies"][0]["text"] = "截断正文"
        mutations.append(value)
        value = copy.deepcopy(original)
        value["bodies"][0]["utf8_sha256"] = "0" * 64
        mutations.append(value)
        value = copy.deepcopy(original)
        value["compatibility_package"]["conditions"].pop()
        mutations.append(value)
        value = copy.deepcopy(original)
        value["compatibility_package"]["role_proofs"].clear()
        mutations.append(value)
        value = copy.deepcopy(original)
        value["compatibility_package"]["relations"].clear()
        mutations.append(value)
        value = copy.deepcopy(original)
        value["compatibility_package"]["sources"][1]["owner"] = 0
        mutations.append(value)
        value = copy.deepcopy(original)
        value["compatibility_package"]["units"][0]["raw_end"] -= 1
        mutations.append(value)
        value = copy.deepcopy(original)
        value["bodies"].append(copy.deepcopy(value["bodies"][0]))
        mutations.append(value)
        for number, value in enumerate(mutations):
            with self.subTest(mutation=number), self.assertRaises(ValueError):
                validate_local_syntax(value, SOURCES, **trusted)

    def test_project_snapshot_selection_and_source_changes_rejected(self):
        trusted = self.trusted()
        original = self.compare(SOURCES, trusted)
        for key in ("project_id", "snapshot_id", "selection_id"):
            changed = {**trusted, key: "changed"}
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_local_syntax(original, SOURCES, **changed)
        changed_files = {**SOURCES, "pkg/base.py": SOURCES["pkg/base.py"] + b"# changed\n"}
        with self.assertRaises(ValueError):
            validate_local_syntax(original, changed_files, **trusted)

    def test_empty_duplicate_unknown_and_wrong_type_core_rejected(self):
        trusted = self.trusted()
        ids = trusted["required_core_unit_ids"]
        for invalid in ([], [ids[0], ids[0]], ["unknown"], [True], "not-a-list"):
            with self.subTest(core=invalid), self.assertRaises(ValueError):
                build_local_syntax(SOURCES, **{**trusted, "required_core_unit_ids": invalid})

    def test_complete_body_limit_is_not_satisfied_by_truncation(self):
        files = {"sample.py": b"def handle():\n return '" + b"x" * 98304 + b"'\n"}
        trusted = self.trusted(files, {("sample.py", "handle")})
        with self.assertRaises(ValueError):
            build_local_syntax(files, **trusted)

    def test_tick_cancellation_propagates_in_build_and_validation(self):
        trusted = self.trusted()
        local = self.compare(SOURCES, trusted)
        def stopped():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            build_local_syntax(SOURCES, **trusted, tick=stopped)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            validate_local_syntax(local, SOURCES, **trusted, tick=stopped)

    def test_public_results_are_detached(self):
        trusted = self.trusted()
        first = self.compare(SOURCES, trusted)
        first["bodies"][0]["text"] = "tampered"
        second = self.compare(SOURCES, trusted)
        self.assertNotEqual(first["bodies"][0]["text"], second["bodies"][0]["text"])


if __name__ == "__main__":
    unittest.main()
