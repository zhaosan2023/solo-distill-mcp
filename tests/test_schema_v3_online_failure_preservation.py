"""隔离验证在线驱动失败现场保存；不运行在线入口，不联网。"""
from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from test_offline import ROOT
from test_schema_v3_stdio import Fixture


spec = importlib.util.spec_from_file_location(
    "phase5_online_failure_fixture",
    ROOT / "verification/.scratch/mainline_a_schema_v3_online_20261101.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class FailurePreservationTests(Fixture):
    def receipt(self):
        return {"request_id": "stdio-fixture", "status": "blocked",
                "assets": {}, "failures": ["stdout_eof_before_v3_manifest"]}

    def test_missing_delivery_does_not_hide_available_evidence(self):
        distiller = self.distiller()
        distiller.persist_scratch("stdio-fixture.failure", {"reason": "fixture_error"})
        distiller.persist_scratch("stdio-fixture.gateway-attempt-1", {"output": "unaccepted"})
        marker = "verification/.scratch/fixture-recovery.full.json"
        distiller.persist_scratch("fixture-recovery", {"request_id": "stdio-fixture"})
        client = SimpleNamespace(stderr=bytearray(b"bounded diagnostic\n"),
                                 last_request_bytes=b"request\n")
        receipt = self.receipt()
        with patch.object(driver, "ROOT", self.root), patch.object(driver, "RECOVERY_MARKER", marker):
            driver.preserve_failure_evidence(client, receipt, driver.Guard(5))
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(set(receipt["assets"]),
            {"stderr", "request_wire", "failure", "admission", "transport_attempt_1"})
        for key, expected in (("stderr", b"bounded diagnostic\n"), ("request_wire", b"request\n")):
            asset = receipt["assets"][key]
            self.assertEqual((self.root / asset["path"]).read_bytes(), expected)
            self.assertTrue(asset["exact_readback"])
        self.assertEqual(json.loads((self.root / receipt["assets"]["transport_attempt_1"]["path"]).read_bytes()),
                         {"output": "unaccepted"})
        self.assertFalse((self.root / "verification/.scratch/stdio-fixture.delivery.full.json").exists())

    def test_save_failure_is_recorded_and_later_assets_are_still_attempted(self):
        receipt = self.receipt()
        client = SimpleNamespace(stderr=bytearray(b"diagnostic"), last_request_bytes=b"request\n")
        with patch.object(driver, "ROOT", self.root), patch.object(driver, "save",
                side_effect=[OSError("fixture_save_failure"), {"path": "fixture", "exact_readback": True}]) as save:
            driver.preserve_failure_evidence(client, receipt, driver.Guard(5))
        self.assertEqual(save.call_count, 2)
        self.assertEqual(receipt["status"], "blocked")
        self.assertIn("failure_evidence_save:OSError", receipt["failures"])
        self.assertNotIn("stderr", receipt["assets"])
        self.assertIn("request_wire", receipt["assets"])

    def test_diagnostic_namespace_keeps_previous_marker_and_saves_new_admission(self):
        keys = ("READY", "LIVE", "RECOVERY_MARKER", "REQUEST_ID", "ADR_REVISION",
                "CONTRACT_REVISION", "DIAGNOSTIC", "PREVIOUS_MARKER", "PREVIOUS_RECEIPT")
        before = {key: getattr(driver, key) for key in keys}
        with patch.dict(driver.__dict__, before):
            driver.configure_diagnostic()
            self.assertTrue(driver.DIAGNOSTIC)
            self.assertEqual(driver.ADR_REVISION, 11)
            self.assertEqual(driver.CONTRACT_REVISION, 12)
            self.assertEqual(driver.PREVIOUS_MARKER, before["RECOVERY_MARKER"])
            self.assertNotEqual(driver.RECOVERY_MARKER, driver.PREVIOUS_MARKER)
            self.assertEqual(driver.REQUEST_ID, driver.INSTANCE + "-live-diagnostic-20261101T133705Z")
            distiller = self.distiller()
            distiller.persist_scratch(driver.INSTANCE + ".live-diagnostic-20261101T133705Z", {
                "request_id": driver.REQUEST_ID,
                "previous_recovery_request_id": driver.INSTANCE + "-live-recovery-20261101"})
            receipt = self.receipt()
            receipt["request_id"] = driver.REQUEST_ID
            client = SimpleNamespace(stderr=bytearray(b"diagnostic"))
            with patch.object(driver, "ROOT", self.root):
                driver.preserve_failure_evidence(client, receipt, driver.Guard(5))
            self.assertIn("admission", receipt["assets"])
            self.assertIn("phase5-live-diagnostic-20261101T133705Z", receipt["assets"]["admission"]["path"])
        self.assertEqual({key: getattr(driver, key) for key in keys}, before)

    def test_failed_partial_stream_reports_real_gateway_reason(self):
        raw = b'data: {"partial":true}\n\n'
        observation = {"raw_sse_bytes": len(raw), "raw_sse_sha256": driver.digest(raw),
                       "error_reason": "gateway_deadline_exhausted"}
        with self.assertRaisesRegex(ValueError, "phase5_gateway_failed:gateway_deadline_exhausted"):
            driver.require_gateway_completion(observation, raw)
        observation["error_reason"] = "upstream_stream_error"
        with self.assertRaisesRegex(ValueError, "phase5_gateway_failed:upstream_stream_error"):
            driver.require_gateway_completion(observation, raw)

    def test_success_still_requires_exact_capture_and_normal_terminal(self):
        raw = b"data: [DONE]\n\n"
        observation = {"raw_sse_bytes": len(raw), "raw_sse_sha256": driver.digest(raw),
            "response_bytes": len(raw), "http_status": 200, "finish_reason": "stop",
            "done_marker": True, "outer_replays": 0}
        driver.require_gateway_completion(observation, raw)
        for change in ({"raw_sse_sha256": "0" * 64}, {"response_bytes": len(raw) + 1},
                       {"http_status": 500}, {"finish_reason": None},
                       {"done_marker": False}, {"outer_replays": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                driver.require_gateway_completion({**observation, **change}, raw)

    def test_bad_partial_capture_is_not_hidden_by_gateway_error(self):
        observation = {"raw_sse_bytes": 1, "raw_sse_sha256": driver.digest(b"x"),
                       "error_reason": "gateway_deadline_exhausted"}
        with self.assertRaisesRegex(ValueError, "phase5_complete_sse_capture"):
            driver.require_gateway_completion(observation, b"y")

    def test_admission_saved_in_failure_path_is_reused_without_overwrite(self):
        receipt = self.receipt()
        name = "fixture-preserved-admission.json"
        raw = b'{"request_id":"stdio-fixture"}'
        guard = driver.Guard(5)
        with patch.object(driver, "ROOT", self.root):
            original = driver.save(name, raw, guard)
            receipt["assets"]["admission"] = original
            with patch.object(driver, "save", side_effect=AssertionError("duplicate write")):
                reused = driver.save_or_verify_asset(receipt, "admission", name, raw, guard)
        self.assertEqual(reused, original)
        self.assertEqual((self.root / original["path"]).read_bytes(), raw)

    def test_changed_existing_admission_blocks_and_is_not_overwritten(self):
        receipt = self.receipt()
        name = "fixture-changed-admission.json"
        raw = b'{"request_id":"stdio-fixture"}'
        guard = driver.Guard(5)
        with patch.object(driver, "ROOT", self.root):
            original = driver.save(name, raw, guard)
            receipt["assets"]["admission"] = original
            (self.root / original["path"]).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "phase5_existing_asset_binding"):
                driver.save_or_verify_asset(receipt, "admission", name, raw, guard)
        self.assertEqual((self.root / original["path"]).read_bytes(), b"changed")

    def test_unsaved_admission_uses_exclusive_save(self):
        receipt = self.receipt()
        with patch.object(driver, "ROOT", self.root):
            asset = driver.save_or_verify_asset(receipt, "admission", "fixture-new-admission.json",
                                               b"new", driver.Guard(5))
        self.assertEqual((self.root / asset["path"]).read_bytes(), b"new")
        self.assertTrue(asset["exact_readback"])

    def test_existing_assets_are_not_overwritten(self):
        receipt = self.receipt()
        receipt["assets"] = {"stderr": {"path": "original"}, "request_wire": {"path": "original-request"}}
        client = SimpleNamespace(stderr=bytearray(b"later"), last_request_bytes=b"later request")
        with patch.object(driver, "ROOT", self.root), patch.object(driver, "save") as save:
            driver.preserve_failure_evidence(client, receipt, driver.Guard(5))
        save.assert_not_called()
        self.assertEqual(receipt["assets"]["stderr"]["path"], "original")
        self.assertEqual(receipt["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
