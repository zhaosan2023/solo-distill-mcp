"""Schema 3.0 局部独立 Oracle 的手工期望、差分及篡改负控。

仅验证包外给定核心的局部语法，不将其冒充选择独立性或语义验收。
V2 生产 API 仅用于显式局部兼容格式差分，不证明 V3 调用链已实现。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import random
import unittest
from unittest.mock import patch

from schema_v3_local_oracle import (
    _inventory, authenticate_local_syntax, expected_local_syntax,
)
from schema_v3_macro_oracle import canonical
from solo_distill.evidence_v2 import attach_roles
from solo_distill.graph_v2 import extract_relations
from solo_distill.package_v2 import build_package, validate_package
from solo_distill.snapshot_v2 import snapshot_scope

SOURCES = {
    "pkg/__init__.py": b"",
    "pkg/base.py": b"def helper():\n    return 1\n",
    "pkg/service.py": b'''from pkg.base import helper
class Service:
    async def handle(self, value):
        self.value = value
        try:
            return helper(), self.dispatch()
        except ValueError:
            self.value = None
            raise
    def unrelated(self):
        return "not selected"
''',
    "unused.py": b"raise RuntimeError('source must not execute')\n",
}


def core_ids(files, names):
    """测试夹具的手工限定名选择；从独立源码解析取得真实身份。"""
    result = []
    found = set()
    for path, raw in sorted(files.items()):
        for unit in _inventory(path, raw, lambda: None)[1]:
            key = (path, unit["qualified_name"])
            if key in names:
                result.append(unit["id"])
                found.add(key)
    if found != set(names):
        raise AssertionError("manual_fixture_core_not_found")
    return result


class LocalOracleTests(unittest.TestCase):
    def trusted(self, files=SOURCES, names=None):
        names = names if names is not None else {
            ("pkg/base.py", "helper"), ("pkg/service.py", "Service.handle")}
        return {"project_id": "fixture", "snapshot_id": "snapshot-1",
                "selection_id": "externally-reviewed-selection-1",
                "required_core_unit_ids": core_ids(files, names)}

    def expected(self, files=SOURCES, names=None):
        trusted = self.trusted(files, names)
        return expected_local_syntax(files, **trusted), trusted

    @snapshot_scope
    def compare_production_compatibility(self, files, local, trusted):
        paths = {file["path"] for file in local["compatibility_package"]["files"]}
        local_files = {path: raw for path, raw in files.items() if path in paths}
        package = attach_roles(build_package(trusted["project_id"], trusted["snapshot_id"],
            local_files, trusted["required_core_unit_ids"]), local_files)
        package["relations"] = extract_relations(package, local_files)
        validate_package(package, local_files)
        self.assertEqual(canonical(package), canonical(local["compatibility_package"]))
        return package

    def test_manual_complete_method_and_dependency_without_unrelated_class(self):
        local, trusted = self.expected()
        package = self.compare_production_compatibility(SOURCES, local, trusted)
        self.assertEqual([f["path"] for f in package["files"]],
                         ["pkg/base.py", "pkg/service.py"])
        self.assertEqual([u["qualified_name"] for u in package["units"]],
                         ["helper", "Service.handle"])
        self.assertEqual(len(package["owners"]), 2)
        self.assertEqual(len(package["conditions"]), 2)
        self.assertEqual(len(package["relations"]), 2)
        by_expression = {r["site"]["expression"]: r for r in package["relations"]}
        self.assertEqual(by_expression["helper"]["to_unit"], 0)
        self.assertIsNone(by_expression["self.dispatch"]["to_unit"])
        self.assertEqual(by_expression["self.dispatch"]["reason"], "local_or_parameter_binding")
        self.assertTrue(all(len(r["conditions"]) == 1 for r in package["relations"]))
        self.assertEqual({p["role"] for p in package["role_proofs"]
                          if p["unit"] == 1}, {"state"})
        self.assertTrue(local["bodies"][1]["text"].startswith("async def handle"))
        self.assertNotIn("unrelated", local["bodies"][1]["text"])
        receipt = authenticate_local_syntax(local, SOURCES, **trusted)
        self.assertEqual(receipt["status"], "pass")
        self.assertEqual(receipt["selection_independence"], "not_checked_by_this_asset")
        self.assertEqual(receipt["business_semantics"], "not_checked_by_this_asset")

    def test_external_guard_origin_body_and_nested_alias_are_complete(self):
        files = {"guard.py": b'''if enabled:
    @decorate
    def chosen(value=factory()):
        if value:
            return target()
        else:
            raise ValueError()
else:
    fallback = 1
'''}
        local, trusted = self.expected(files, {("guard.py", "chosen")})
        package = self.compare_production_compatibility(files, local, trusted)
        self.assertEqual(len(package["owners"]), 1)
        self.assertEqual(len(package["sources"]), 2)
        self.assertEqual(len(package["conditions"]), 4)
        # 裸装饰器是名称表达式，不是调用位点；三个显式调用必须全部保留。
        self.assertEqual(len(package["relations"]), 3)
        self.assertEqual({r["site"]["expression"] for r in package["relations"]},
                         {"factory", "target", "ValueError"})
        self.assertEqual(local["bodies"][0]["text"], files["guard.py"].decode().rstrip())
        self.assertIn("@decorate\n", local["bodies"][0]["text"])
        self.assertEqual(package["sources"][0]["owner"], package["sources"][1]["owner"])
        self.assertEqual([c["branch"] for c in package["conditions"]], ["if", "if", "else", "else"])

    def test_bom_crlf_latin1_decorator_and_unicode_coordinates(self):
        files = {
            "bom.py": b"\xef\xbb\xbf@decorate\r\nasync def chosen(value):\r\n    return '\xe6\x96\x87'\r\n",
            "latin.py": b"# coding: latin-1\r\ndef chosen():\r\n    return '\xe9'\r\n",
        }
        local, trusted = self.expected(files, {("bom.py", "chosen"), ("latin.py", "chosen")})
        self.compare_production_compatibility(files, local, trusted)
        self.assertTrue(local["bodies"][0]["text"].startswith("@decorate\r\nasync def"))
        self.assertIn("é", local["bodies"][1]["text"])
        for body in local["bodies"]:
            self.assertEqual(body["utf8_sha256"], hashlib.sha256(body["text"].encode()).hexdigest())
        self.assertEqual(local["compatibility_package"]["units"][0]["raw_start"], 3)

    def test_generated_local_control_and_binding_properties(self):
        rng = random.Random(20261030)
        for sample in range(24):
            count = rng.randint(1, 7)
            raw = ("def helper():\n    return 1\ndef chosen(value):\n    try:\n" +
                   "        helper()\n" * count +
                   "    except Exception as failure:\n        raise\n").encode()
            files = {"sample.py": raw, "unused.py": b"pass\n"}
            local, trusted = self.expected(files, {("sample.py", "chosen"), ("sample.py", "helper")})
            with self.subTest(sample=sample):
                self.compare_production_compatibility(files, local, trusted)
                self.assertEqual(len(local["compatibility_package"]["relations"]), count)
                self.assertEqual(len(local["compatibility_package"]["conditions"]), 2)
                self.assertEqual(authenticate_local_syntax(local, files, **trusted)["status"], "pass")

    def test_every_local_table_body_binding_and_required_core_omission_rejected(self):
        local, trusted = self.expected()
        variants = []
        for table in ("files", "units", "owners", "sources", "conditions", "role_proofs", "relations"):
            for kind in ("remove", "duplicate"):
                value = copy.deepcopy(local)
                records = value["compatibility_package"][table]
                records.pop() if kind == "remove" else records.append(copy.deepcopy(records[0]))
                variants.append((table + ":" + kind, value))
        for key, replacement in (("selection_id", "wrong-selection"), ("core_unit_ids", []),
                                 ("bodies", []), ("extra_field", True)):
            value = copy.deepcopy(local)
            value[key] = replacement
            variants.append((key, value))
        for key, replacement in (("text", "truncated"), ("owner_id", "invented-owner"),
                                 ("utf8_sha256", "0" * 64)):
            value = copy.deepcopy(local)
            value["bodies"][0][key] = replacement
            variants.append(("body:" + key, value))
        for key, replacement in (("project_id", "other"), ("snapshot_id", "other"),
                                 ("contract_version", 3), ("status", "partial")):
            value = copy.deepcopy(local)
            value["compatibility_package"][key] = replacement
            variants.append(("package:" + key, value))
        for label, value in variants:
            with self.subTest(mutation=label), self.assertRaises(ValueError):
                authenticate_local_syntax(value, SOURCES, **trusted)
        with self.assertRaises(ValueError):
            authenticate_local_syntax(local, SOURCES,
                **{**trusted, "required_core_unit_ids": trusted["required_core_unit_ids"][:1]})

    def test_forged_coordinate_alias_condition_role_and_relation_rejected(self):
        local, trusted = self.expected()
        changes = (("units", "raw_start", 1), ("sources", "owner", 999),
                   ("conditions", "branch", "invented"), ("role_proofs", "role", "entry"),
                   ("relations", "to_unit", 999))
        for table, field, value in changes:
            mutated = copy.deepcopy(local)
            self.assertNotEqual(local["compatibility_package"][table][0][field], value,
                                "篡改负控必须实际改变原字段")
            mutated["compatibility_package"][table][0][field] = value
            with self.subTest(table=table), self.assertRaises(ValueError):
                authenticate_local_syntax(mutated, SOURCES, **trusted)

    def test_cross_snapshot_source_and_complete_body_budget_fail_closed(self):
        local, trusted = self.expected()
        changed = {**SOURCES, "pkg/base.py": SOURCES["pkg/base.py"] + b"# changed\n"}
        with self.assertRaises(ValueError):
            authenticate_local_syntax(local, changed, **trusted)
        files = {"huge.py": b"def chosen():\n    return '" + b"x" * 100000 + b"'\n"}
        with self.assertRaisesRegex(ValueError, "complete_body_budget"):
            self.expected(files, {("huge.py", "chosen")})
        for ids in ([], ["missing"], [True], trusted["required_core_unit_ids"] * 2):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                expected_local_syntax(SOURCES, **{**trusted, "required_core_unit_ids": ids})

    def test_parse_once_per_trusted_file_without_source_execution_or_network(self):
        trusted = self.trusted()
        with patch("ast.parse", wraps=ast.parse) as parser, \
             patch("socket.socket", side_effect=AssertionError("offline only")):
            local = expected_local_syntax(SOURCES, **trusted)
        self.assertEqual(parser.call_count, len(SOURCES))
        self.assertEqual(local["compatibility_package"]["coverage"]["scan_files"], 2)
        self.assertNotIn("unused.py", [f["path"] for f in local["compatibility_package"]["files"]])

    def test_cancellation_propagates(self):
        trusted = self.trusted()
        def stopped():
            raise RuntimeError("cancelled_or_expired")
        with self.assertRaisesRegex(RuntimeError, "cancelled_or_expired"):
            expected_local_syntax(SOURCES, **trusted, tick=stopped)


if __name__ == "__main__":
    unittest.main()
