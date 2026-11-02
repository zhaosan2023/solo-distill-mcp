"""Schema 3.0 封印边界的独立差分、伪造负控和旧路径拒绝。

夹具源码只作解析数据，不访问网关，不执行来源，不证明完整阶段通过。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from schema_v3_core_oracle import authenticate_core, expected_core
from schema_v3_macro_oracle import canonical, identity
from test_schema_v3_local import SOURCES, core_ids
from solo_distill.core_v3 import (
    SealedCore, authenticate_seal, input_identity, inspect_seal, make_seal,
)
from solo_distill.core_v2 import (
    SealedCore as LegacyCore, inspect_seal as inspect_legacy_seal,
)
from solo_distill.package_v3 import build_package
from solo_distill.snapshot_v2 import snapshot_scope


class CoreV3Tests(unittest.TestCase):
    def trusted(self):
        return {"project_id": "fixture", "snapshot_id": "snapshot-1",
                "selection_id": "external-selection-1",
                "required_core_unit_ids": core_ids(SOURCES, {
                    ("pkg/base.py", "helper"),
                    ("pkg/service.py", "Service.handle")})}

    @snapshot_scope
    def seal(self):
        trusted = self.trusted()
        package = build_package(SOURCES, **trusted)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            core = make_seal(package, SOURCES, **trusted)
            actual = authenticate_seal(core, SOURCES, **trusted)
        expected = expected_core(SOURCES, **trusted)
        self.assertEqual(core.payload, canonical(expected))
        self.assertEqual(actual, expected)
        self.assertEqual(inspect_seal(core), expected)
        self.assertEqual(authenticate_core(actual, SOURCES, **trusted)["status"], "pass")
        return core

    def reseal(self, value):
        unsigned = {key: item for key, item in value.items() if key != "core_id"}
        value["core_id"] = identity("core-v3", unsigned)
        return SealedCore(canonical(value))

    def test_full_source_and_local_selection_binding_match_independent_oracle(self):
        core = self.seal()
        value = core.document
        self.assertEqual(value["scope"], sorted(SOURCES))
        self.assertEqual(value["input_id"], input_identity(SOURCES))
        self.assertEqual(value["wire_limit"], 22016)
        self.assertEqual(value["package"]["coverage"]["scan_files"], 4)
        self.assertEqual(value["package"]["coverage"]["body_units"], 2)
        self.assertEqual(core.core_id, value["core_id"])

    def test_sealed_payload_and_public_views_are_detached(self):
        core = self.seal()
        original = core.payload
        view = core.document
        view["package"]["macro"]["symbols"].clear()
        self.assertEqual(core.payload, original)
        self.assertTrue(core.document["package"]["macro"]["symbols"])
        with self.assertRaises(AttributeError):
            core.payload = b"changed"

    def test_unsealed_package_tampering_is_not_repaired(self):
        package = self.seal().document["package"]
        package["macro"]["symbols"].pop()
        with self.assertRaises(ValueError):
            make_seal(package, SOURCES, **self.trusted())

    def test_digest_or_payload_mutation_is_rejected(self):
        value = self.seal().document
        value["package"]["local_closures"]["bodies"][0]["text"] = "forged"
        with self.assertRaises(ValueError):
            inspect_seal(SealedCore(canonical(value)))
        value = self.seal().document
        value["core_id"] = "0" * 64
        with self.assertRaises(ValueError):
            inspect_seal(SealedCore(canonical(value)))

    def test_rehashed_structural_forgery_still_fails_source_authentication(self):
        value = self.seal().document
        value["package"]["macro"]["symbols"].pop()
        forged = self.reseal(value)
        with self.assertRaises(ValueError):
            authenticate_seal(forged, SOURCES, **self.trusted())
        with self.assertRaises(ValueError):
            authenticate_core(forged.document, SOURCES, **self.trusted())
        value = self.seal().document
        value["package"]["local_closures"]["bodies"][0]["text"] = "forged"
        with self.assertRaises(ValueError):
            authenticate_seal(self.reseal(value), SOURCES, **self.trusted())

    def test_trusted_scope_changes_are_rejected_including_unselected_file(self):
        core = self.seal()
        for files in ({k: v for k, v in SOURCES.items() if k != "unused.py"},
                      {**SOURCES, "unused.py": SOURCES["unused.py"] + b"# changed\n"}):
            with self.assertRaises(ValueError):
                authenticate_seal(core, files, **self.trusted())

    def test_project_snapshot_selection_and_external_core_changes_are_rejected(self):
        core = self.seal()
        trusted = self.trusted()
        for key in ("project_id", "snapshot_id", "selection_id"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                authenticate_seal(core, SOURCES, **{**trusted, key: "changed"})
        with self.assertRaises(ValueError):
            authenticate_seal(core, SOURCES, **{**trusted,
                "required_core_unit_ids": trusted["required_core_unit_ids"][:1]})

    def test_rehashed_envelope_version_policy_scope_and_cross_layer_conflicts_rejected(self):
        original = self.seal().document
        for key, replacement in (("render_version", "offline-core-v2-1"),
                                 ("policy_id", "lifecycle-v2-1"),
                                 ("wire_limit", True), ("wire_limit", 22017),
                                 ("selection_id", "changed"),
                                 ("scope", list(reversed(original["scope"]))),
                                 ("scope", original["scope"] + [original["scope"][0]]),
                                 ("input_id", "0" * 64)):
            value = copy.deepcopy(original)
            value[key] = replacement
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_seal(self.reseal(value))
        value = copy.deepcopy(original)
        value["package"]["contract_version"] = 2
        with self.assertRaises(ValueError):
            inspect_seal(self.reseal(value))
        value = copy.deepcopy(original)
        value["package"]["local_closures"]["selection_id"] = "changed"
        with self.assertRaises(ValueError):
            inspect_seal(self.reseal(value))
        value = copy.deepcopy(original)
        value["package"]["macro"]["snapshot_id"] = "changed"
        with self.assertRaises(ValueError):
            inspect_seal(self.reseal(value))
        with self.assertRaises(ValueError):
            inspect_seal(self.reseal({**original, "authenticated": True}))

    def test_duplicate_keys_noncanonical_payload_wrong_type_and_encoding_rejected(self):
        core = self.seal()
        invalid = (b'{"core_id":"duplicate",' + core.payload[1:],
                   json.dumps(core.document, ensure_ascii=False, indent=2).encode(),
                   core.payload.decode(), None, b"[]", core.payload + b"\n")
        for raw in invalid:
            with self.subTest(raw=type(raw).__name__), self.assertRaises(ValueError):
                inspect_seal(SealedCore(raw))
        with self.assertRaises(ValueError):
            inspect_seal(SealedCore(core.payload, "zlib-base64-core-v2-1"))
        with self.assertRaises(ValueError):
            make_seal(core.document["package"], SOURCES, **self.trusted(),
                      policy_id="unknown")
        with self.assertRaises(ValueError):
            make_seal(core.document["package"], SOURCES, **self.trusted(),
                      wire_encoding="zlib-base64-core-v2-1")

    def test_legacy_seal_inspection_rejects_v3_and_v3_rejects_legacy_container(self):
        core = self.seal()
        with self.assertRaises(ValueError):
            inspect_legacy_seal(core)
        with self.assertRaises(ValueError):
            inspect_legacy_seal(LegacyCore(core.payload))
        with self.assertRaises(ValueError):
            inspect_seal(LegacyCore(core.payload))

    def test_invalid_trusted_source_scope_is_not_hashed_as_valid_identity(self):
        for files in ({}, [], {"../sample.py": b""}, {"sample.py": "not bytes"},
                      {"pkg\\sample.py": b""}, {"sample.txt": b""}):
            with self.subTest(files=files), self.assertRaises(ValueError):
                input_identity(files)

    def test_cancel_tick_reaches_sealing_and_authentication(self):
        core = self.seal()
        def stopped():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            make_seal(core.document["package"], SOURCES, **self.trusted(), tick=stopped)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            authenticate_seal(core, SOURCES, **self.trusted(), tick=stopped)


if __name__ == "__main__":
    unittest.main()
