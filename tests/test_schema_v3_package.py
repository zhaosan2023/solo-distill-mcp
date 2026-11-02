"""Schema 3.0 结构包接口：独立期望、完整字段负控与旧版本拒绝。

不访问网关、不执行来源、不读取参考答案；不冒充完整离线阶段通过。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from schema_v3_package_oracle import authenticate_package, expected_package
from schema_v3_macro_oracle import canonical
from test_schema_v3_local import SOURCES, core_ids
from solo_distill.package_v3 import build_package, decode_package, validate_package
from solo_distill.package_v2 import decode_package as decode_legacy_package
from solo_distill.snapshot_v2 import snapshot_scope


class PackageV3Tests(unittest.TestCase):
    def trusted(self, files=SOURCES):
        return {"project_id": "fixture", "snapshot_id": "snapshot-1",
                "selection_id": "external-selection-1",
                "required_core_unit_ids": core_ids(files, {
                    ("pkg/base.py", "helper"),
                    ("pkg/service.py", "Service.handle")})}

    @snapshot_scope
    def build(self):
        trusted = self.trusted()
        expected = expected_package(SOURCES, **trusted)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            actual = build_package(SOURCES, **trusted)
            validate_package(actual, SOURCES, **trusted)
        self.assertEqual(canonical(actual), canonical(expected))
        return actual

    def test_complete_macro_and_local_denominators_are_distinct(self):
        value = self.build()
        self.assertEqual(value["contract_version"], 3)
        self.assertEqual(value["status"], "complete")
        self.assertEqual(value["coverage"]["scan_files"], 4)
        self.assertEqual(value["coverage"]["body_units"], 2)
        self.assertEqual(value["coverage"]["core_units"], 2)
        self.assertEqual(value["coverage"]["semantic_supported"], 0)
        local = value["local_closures"]["compatibility_package"]
        self.assertEqual(local["contract_version"], 2)
        self.assertEqual(local["coverage"]["scan_files"], 2)
        receipt = authenticate_package(value, SOURCES, **self.trusted())
        self.assertEqual(receipt["selection_independence"], "not_checked_by_this_asset")
        self.assertEqual(receipt["business_semantics"], "not_checked_by_this_asset")

    def test_exact_json_roundtrip(self):
        value = self.build()
        self.assertEqual(decode_package(canonical(value), SOURCES, **self.trusted()), value)

    def test_macro_scope_symbol_topology_and_counts_tampering_rejected(self):
        original = self.build()
        mutations = []
        for table in ("files", "symbols", "topology"):
            value = copy.deepcopy(original)
            value["macro"][table].pop()
            mutations.append(value)
        value = copy.deepcopy(original)
        value["macro"]["mapping"]["call_site_count"] += 1
        mutations.append(value)
        value = copy.deepcopy(original)
        value["macro"]["symbols"][0][0] = "forged"
        mutations.append(value)
        for value in mutations:
            with self.subTest(macro=value["macro"]["mapping"]), self.assertRaises(ValueError):
                validate_package(value, SOURCES, **self.trusted())
            with self.assertRaises(ValueError):
                authenticate_package(value, SOURCES, **self.trusted())

    def test_local_core_body_alias_and_compatibility_version_rejected(self):
        original = self.build()
        mutations = []
        value = copy.deepcopy(original)
        value["local_closures"]["core_unit_ids"].pop()
        mutations.append(value)
        value = copy.deepcopy(original)
        value["local_closures"]["bodies"][0]["text"] = "截断"
        mutations.append(value)
        value = copy.deepcopy(original)
        value["local_closures"]["compatibility_package"]["sources"][1]["owner"] = 0
        mutations.append(value)
        value = copy.deepcopy(original)
        value["local_closures"]["compatibility_package"]["contract_version"] = 3
        mutations.append(value)
        for number, value in enumerate(mutations):
            with self.subTest(number=number), self.assertRaises(ValueError):
                validate_package(value, SOURCES, **self.trusted())
            with self.assertRaises(ValueError):
                authenticate_package(value, SOURCES, **self.trusted())

    def test_unknown_version_scenario_fields_and_boolean_counts_rejected(self):
        original = self.build()
        for key, replacement in (("contract_version", 2), ("contract_version", True),
                                 ("kind", "sealed_knowledge"), ("scenario", "B"),
                                 ("status", "partial"), ("failures", ["hidden-failure"])):
            value = {**original, key: replacement}
            with self.subTest(key=key, replacement=replacement), self.assertRaises(ValueError):
                validate_package(value, SOURCES, **self.trusted())
        value = copy.deepcopy(original)
        value["coverage"]["semantic_supported"] = False
        with self.assertRaises(ValueError):
            validate_package(value, SOURCES, **self.trusted())
        with self.assertRaises(ValueError):
            validate_package({**original, "authenticated": True}, SOURCES, **self.trusted())
        value = copy.deepcopy(original)
        del value["failures"]
        with self.assertRaises(ValueError):
            validate_package(value, SOURCES, **self.trusted())

    def test_project_snapshot_selection_and_external_core_are_not_self_reported(self):
        value = self.build()
        trusted = self.trusted()
        for key in ("project_id", "snapshot_id", "selection_id"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_package(value, SOURCES, **{**trusted, key: "changed"})
        with self.assertRaises(ValueError):
            validate_package(value, SOURCES, **{**trusted,
                "required_core_unit_ids": trusted["required_core_unit_ids"][:1]})

    def test_changed_source_or_smaller_trusted_scope_rejected(self):
        value = self.build()
        for files in ({k: v for k, v in SOURCES.items() if k != "unused.py"},
                      {**SOURCES, "unused.py": SOURCES["unused.py"] + b"# changed\n"}):
            with self.assertRaises(ValueError):
                validate_package(value, files, **self.trusted())

    def test_duplicate_json_keys_float_and_nonfinite_values_rejected(self):
        raw = canonical(self.build())
        samples = [b'{"contract_version":3,' + raw[1:],
                   raw.replace(b'"semantic_supported":0', b'"semantic_supported":0.0'),
                   raw.replace(b'"semantic_supported":0', b'"semantic_supported":NaN'),
                   raw.replace(b'"semantic_supported":0', b'"semantic_supported":Infinity')]
        for sample in samples:
            with self.subTest(sample=sample[:80]), self.assertRaises(ValueError):
                decode_package(sample, SOURCES, **self.trusted())
        for invalid in (raw.decode(), None, 3):
            with self.assertRaises(ValueError):
                decode_package(invalid, SOURCES, **self.trusted())

    def test_legacy_decoder_does_not_silently_accept_v3(self):
        with self.assertRaisesRegex(ValueError, "unsupported_version"):
            decode_legacy_package(canonical(self.build()), SOURCES)

    def test_empty_duplicate_unknown_external_core_rejected(self):
        trusted = self.trusted()
        ids = trusted["required_core_unit_ids"]
        for invalid in ([], [ids[0], ids[0]], ["unknown"], [True]):
            with self.subTest(core=invalid), self.assertRaises(ValueError):
                build_package(SOURCES, **{**trusted, "required_core_unit_ids": invalid})

    def test_cancel_tick_propagates_without_network(self):
        value = self.build()
        def stopped():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            build_package(SOURCES, **self.trusted(), tick=stopped)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            validate_package(value, SOURCES, **self.trusted(), tick=stopped)

    def test_public_results_are_detached(self):
        first = self.build()
        first["macro"]["symbols"].clear()
        first["local_closures"]["bodies"][0]["text"] = "changed"
        second = self.build()
        self.assertTrue(second["macro"]["symbols"])
        self.assertNotEqual(second["local_closures"]["bodies"][0]["text"], "changed")


if __name__ == "__main__":
    unittest.main()
