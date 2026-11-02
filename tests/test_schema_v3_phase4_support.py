"""阶段 4 支撑入口隔离回归；不访问网关、不启动真实来源预检或在线业务。"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_schema_v3_stdio import Fixture, ROOT


def support():
    path = ROOT / "verification/.scratch/mainline_a_schema_v3_phase4_20261031.py"
    spec = importlib.util.spec_from_file_location("schema_v3_phase4_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase4SupportTests(Fixture):
    def setUp(self):
        super().setUp()
        self.module = support()
        self.module.ROOT = self.root
        self.contract = json.loads((ROOT / self.module.PARENT / "schema-v3.execution-contract.json").read_bytes())
        self.plan = (ROOT / self.module.PLAN).read_text()
        self.adr = (ROOT / self.contract["adr_file"]).read_text()
        self.store(self.module.PLAN, self.plan, text=True)
        self.store(self.contract["adr_file"], self.adr, text=True)
        self.store(self.module.PARENT + "/schema-v3.execution-contract.json", self.contract)
        policy = copy.deepcopy(self.policy)
        policy["authorized_project_ids"].append("fastcode-sample")
        self.store("config/distillation.json", policy)

    def store(self, path, value, *, text=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if text else json.dumps(value, ensure_ascii=False))
        return target

    def health_receipt(self):
        receipt = {"status": "pass", "binding": self.module.binding(), "listener_after": ["fixture-inode"]}
        self.store(self.module.PARENT + "/" + self.module.HEALTH_NAME, receipt)
        return receipt

    def audit_fixture(self):
        module = self.module
        arguments = self.arguments(project_id="fastcode-sample", wire_encoding=module.WIRE_ENCODING,
                                   transport=copy.deepcopy(module.CAPS))
        base = module.PARENT + "/preflight-1"
        raw_files = self.files
        report = {"entries": [{"path": path, "status": "parsed",
                  "content_sha256": hashlib.sha256(raw).hexdigest()} for path, raw in sorted(raw_files.items())],
                  "coverage": {"parsed_files": len(raw_files)}, "inventory_complete": True,
                  "freshness": "current_at_verification", "snapshot_id": "fixture-snapshot"}
        selection = {"selection_id": "fixture-selection", "witness": {"unique_body_bytes": 12}}
        frozen = {"project_id": "fastcode-sample", "report": report,
                  "include": ["."], "source_root": str(self.root / "source"),
                  "selection": selection, "raw_files_base64": {
                      path: base64.b64encode(raw).decode("ascii") for path, raw in raw_files.items()}}
        transport = b'{"jsonrpc":"2.0","method":"fixture"}\n{"jsonrpc":"2.0","id":3}\n'
        oracle = {"status": "pass", "production_and_independent_equal": True,
                  "wire_encoding": module.WIRE_ENCODING, "transfer_bytes": len(transport),
                  "physical_frames": 2, "notification_chunks": 1, "decoded_bytes": 200}
        values = {"frozen-source-and-selection.json": frozen,
                  "knowledge-package.json": {}, "sealed-core.json": {},
                  "exact-http-post.json": {}, "independent-oracle.receipt.json": oracle}
        for name, value in values.items():
            self.store(base + "/" + name, value)
        (self.root / base / "transport.jsonl").write_bytes(transport)
        hashes = {name: hashlib.sha256((self.root / base / name).read_bytes()).hexdigest()
                  for name in module.ARTIFACT_NAMES}
        reuse = {name: {"parse_calls": len(raw_files)}
                 for name in ("production_driver", "service_preparation")}
        reuse["independent_oracle"] = {"parse_calls": len(raw_files),
                                       "inventory_builds": len(raw_files), "core_builds": 1}
        result = {"status": "pass", "request_id": arguments["request_id"],
                  "request_wire_bytes": 2000, "wire_encoding": module.WIRE_ENCODING,
                  "service_exit_code": 0, "service_cleanup": "confirmed_natural_exit",
                  "gateway_requests": 0, "internal_elapsed_seconds": 21,
                  "save": {"exact_readback": True, "artifact_sha256": hashes, "timing": {}},
                  "syntax_reuse": reuse, "frozen_selection_bytes": 200,
                  "selection_binding": "fixture", "metrics": {},
                  "independent_selection_seconds": 1, "service_and_production_consume_seconds": 2,
                  "independent_consume_seconds": 3, "save_and_readback_seconds": 1}
        outer = {"actual_exit_code": 0, "external_time_seconds": 22,
                 "execution_instance": module.INSTANCE, "attempt_id": module.ATTEMPT,
                 "sample_id": "preflight-1"}
        self.store(base + "/preflight.receipt.json", result)
        self.store(module.PARENT + "/" + module.attempt_name("preflight-1", "request.json"), arguments)
        self.store(module.PARENT + "/" + module.attempt_name("preflight-1", "process-exit.receipt.json"), outer)
        self.store("config/projects.json", {"projects": {"fastcode-sample": {
            "root": str(self.root / "source"), "include": ["."]}}})
        self.health_receipt()
        return base, result, outer

    def test_current_binding_and_resource_gate_key(self):
        bound = self.module.binding()
        self.assertEqual(bound["execution_contract_revision"], 8)
        self.assertEqual(bound["adr_revision"], 7)
        self.assertEqual(self.module.WIRE_ENCODING, "lz-json-v3-1")
        self.assertEqual(bound["wire_encoding"], self.module.WIRE_ENCODING)
        self.assertIsNone(bound["session_id"])

    def test_changed_contract_revision_capacity_or_encoding_blocks(self):
        for key, value in (("contract_revision", 5), ("adr_revision", 4)):
            contract = copy.deepcopy(self.contract)
            contract[key] = value
            self.store(self.module.PARENT + "/schema-v3.execution-contract.json", contract)
            with self.assertRaisesRegex(ValueError, "execution_contract_conflict"):
                self.module.binding()
        contract = copy.deepcopy(self.contract)
        contract["resource_gates"]["max_decoded_bytes"] += 1
        self.store(self.module.PARENT + "/schema-v3.execution-contract.json", contract)
        with self.assertRaisesRegex(ValueError, "capacity_contract_conflict"):
            self.module.binding()
        contract = copy.deepcopy(self.contract)
        contract["subtree_representation_contract"]["encoding"] = "dictionary-columnar-v3-1"
        self.store(self.module.PARENT + "/schema-v3.execution-contract.json", contract)
        with self.assertRaisesRegex(ValueError, "execution_contract_conflict"):
            self.module.binding()

    def test_old_encodings_cannot_replace_lz_binding(self):
        for encoding in ("json", "dictionary-columnar-v3-1", "dictionary-columnar-v3-2", "unknown"):
            with self.subTest(encoding=encoding):
                contract = copy.deepcopy(self.contract)
                contract["lz_representation_contract"]["encoding"] = encoding
                self.store(self.module.PARENT + "/schema-v3.execution-contract.json", contract)
                with self.assertRaisesRegex(ValueError, "execution_contract_conflict"):
                    self.module.binding()

    def test_changed_adr_or_plan_blocks(self):
        self.store(self.contract["adr_file"], self.adr.replace("adr_revision: 7", "adr_revision: 6"), text=True)
        with self.assertRaisesRegex(ValueError, "adr_binding_conflict"):
            self.module.binding()
        self.store(self.contract["adr_file"], self.adr, text=True)
        self.store(self.module.PLAN, self.plan.replace("status: EXECUTING", "status: SUSPENDED"), text=True)
        with self.assertRaisesRegex(ValueError, "active_contract_conflict"):
            self.module.binding()

    def test_launch_preserves_explicit_encoding_and_does_not_launch_in_test(self):
        self.health_receipt()
        with patch.object(self.module, "listener", return_value=["fixture-inode"]), \
             patch.object(self.module.os, "execv") as execute, \
             patch("builtins.print"):
            self.module.launch("preflight-1")
        execute.assert_called_once()
        path = self.root / self.module.PARENT / self.module.attempt_name("preflight-1", "request.json")
        arguments = json.loads(path.read_bytes())
        self.assertEqual(arguments["wire_encoding"], self.module.WIRE_ENCODING)
        self.assertEqual(arguments["transport"], self.module.CAPS)
        self.assertEqual(arguments["operation"], "preflight")
        self.assertEqual(arguments["host_timeout_seconds"], 60)
        self.assertEqual(arguments["deadline_unix_ms"] - arguments["started_at_unix_ms"], 60000)
        self.assertNotIn("seeds", arguments)
        self.assertFalse((self.root / self.module.PARENT / "preflight-1").exists())

    def test_changed_health_binding_blocks_before_exec(self):
        receipt = self.health_receipt()
        receipt["binding"]["adr_revision"] = 2
        self.store(self.module.PARENT + "/" + self.module.HEALTH_NAME, receipt)
        with patch.object(self.module.os, "execv") as execute:
            with self.assertRaisesRegex(ValueError, "health_binding_missing_or_changed"):
                self.module.launch("preflight-1")
        execute.assert_not_called()

    def test_second_sample_without_accepted_first_cannot_exec(self):
        self.health_receipt()
        with patch.object(self.module, "listener", return_value=["fixture-inode"]), \
             patch.object(self.module.os, "execv") as execute:
            with self.assertRaises(FileNotFoundError):
                self.module.launch("preflight-2")
        execute.assert_not_called()

    def test_audit_dynamic_denominator_and_soft_target(self):
        self.audit_fixture()
        with patch.object(self.module, "listener", return_value=["fixture-inode"]), patch("builtins.print"):
            self.assertEqual(self.module.audit("preflight-1"), 0)
        path = self.root / self.module.PARENT / self.module.attempt_name("preflight-1", "phase4-audit.receipt.json")
        checked = json.loads(path.read_bytes())
        self.assertEqual(checked["frozen_file_count"], len(self.files))
        self.assertEqual(checked["status"], "pass")
        self.assertFalse(checked["performance_target_met"])
        self.assertEqual(checked["wire_encoding"], self.module.WIRE_ENCODING)

    def test_internal_overrun_is_soft_but_external_60_still_blocks(self):
        base, result, outer = self.audit_fixture()
        result["internal_elapsed_seconds"] = 57.001
        outer["external_time_seconds"] = 58.0
        self.store(base + "/preflight.receipt.json", result)
        path = self.module.PARENT + "/" + self.module.attempt_name("preflight-1", "process-exit.receipt.json")
        self.store(path, outer)
        with patch.object(self.module, "listener", return_value=["fixture-inode"]), patch("builtins.print"):
            self.assertEqual(self.module.audit("preflight-1"), 0)
        outer["external_time_seconds"] = 60.0
        self.store(path, outer)
        with self.assertRaisesRegex(ValueError, "process_or_persistence_gate"):
            self.module.audit("preflight-1")

    def test_restored_internal_hard_gate_is_binding_conflict(self):
        contract = copy.deepcopy(self.contract)
        contract["resource_gates"]["preflight_internal_hard_seconds"] = 57
        self.store(self.module.PARENT + "/schema-v3.execution-contract.json", contract)
        with self.assertRaisesRegex(ValueError, "unattended_timing_contract_conflict"):
            self.module.binding()

    def test_audit_nonzero_outer_exit_cannot_save_pass(self):
        _, _, outer = self.audit_fixture()
        outer["actual_exit_code"] = 2
        self.store(self.module.PARENT + "/" + self.module.attempt_name("preflight-1", "process-exit.receipt.json"), outer)
        with self.assertRaisesRegex(ValueError, "process_or_persistence_gate"):
            self.module.audit("preflight-1")
        path = self.root / self.module.PARENT / self.module.attempt_name("preflight-1", "phase4-audit.receipt.json")
        self.assertFalse(path.exists())

    def test_audit_changed_original_artifact_blocks(self):
        base, _, _ = self.audit_fixture()
        (self.root / base / "sealed-core.json").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact_changed"):
            self.module.audit("preflight-1")

    def test_audit_decoded_over_limit_blocks_even_with_saved_matching_hash(self):
        base, result, _ = self.audit_fixture()
        oracle = json.loads((self.root / base / "independent-oracle.receipt.json").read_bytes())
        oracle["decoded_bytes"] = self.module.CAPS["max_decoded_bytes"] + 1
        path = self.store(base + "/independent-oracle.receipt.json", oracle)
        result["save"]["artifact_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.store(base + "/preflight.receipt.json", result)
        with patch.object(self.module, "listener", return_value=["fixture-inode"]):
            with self.assertRaisesRegex(ValueError, "actual_transport_capacity_gate"):
                self.module.audit("preflight-1")


if __name__ == "__main__":
    unittest.main()
