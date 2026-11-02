"""600 秒恢复驱动的隔离边界；不运行真实在线入口或访问网关。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from test_schema_v3_stdio import Fixture
from test_schema_v3_live_retry import LiveRetryTests
from test_schema_v3_extended_recovery import ExtendedRecoveryTests
from test_schema_v3_online_failure_preservation import driver


class ExtendedDriverTests(Fixture):
    live = LiveRetryTests.live
    fixture = ExtendedRecoveryTests.fixture
    namespace_keys = (
        "READY", "LIVE", "RECOVERY_MARKER", "REQUEST_ID", "ADR_REVISION",
        "CONTRACT_REVISION", "DIAGNOSTIC", "EXTENDED", "PROCESS_SECONDS",
        "PREVIOUS_MARKER", "PREVIOUS_RECEIPT")

    def contract(self):
        return {"extended_recovery_contract": {
            "request_id": driver.INSTANCE + "-live-extended-20261101T233105Z",
            "recover_from_request_id": driver.ORIGINAL_REQUEST,
            "previous_recovery_request_id": driver.INSTANCE + "-live-recovery-20261101",
            "previous_diagnostic_request_id": driver.INSTANCE + "-live-diagnostic-20261101T133705Z",
            "marker_id": driver.INSTANCE + ".live-extended-20261101T233105Z",
            "previous_diagnostic_marker_id": driver.INSTANCE + ".live-diagnostic-20261101T133705Z",
            "previous_diagnostic_receipt": driver.PARENT + "/phase5-live-diagnostic-20261101T133705Z.receipt.json",
            "previous_postmortem": driver.PARENT + "/phase5-diagnostic-postmortem-20261101T135839Z.receipt.json",
            "process_seconds": 600, "soft_target_seconds": 180,
            "return_reserve_seconds": 15, "max_retries": 3, "max_http_attempts": 4}}

    def test_explicit_namespace_does_not_mutate_old_window_identity(self):
        before = {key: getattr(driver, key) for key in self.namespace_keys}
        with patch.dict(driver.__dict__, before):
            driver.configure_extended()
            self.assertTrue(driver.EXTENDED)
            self.assertFalse(driver.DIAGNOSTIC)
            self.assertEqual(driver.PROCESS_SECONDS, 600)
            self.assertEqual((driver.ADR_REVISION, driver.CONTRACT_REVISION), (12, 13))
            self.assertEqual(driver.REQUEST_ID, self.contract()["extended_recovery_contract"]["request_id"])
            self.assertNotEqual(driver.RECOVERY_MARKER, driver.PREVIOUS_MARKER)
            self.assertEqual(driver.PREVIOUS_MARKER, before["PREVIOUS_MARKER"])
            self.assertEqual(driver.PREVIOUS_RECEIPT, before["PREVIOUS_RECEIPT"])
        self.assertEqual({key: getattr(driver, key) for key in self.namespace_keys}, before)

    def test_old_diagnostic_remains_a_240_second_window(self):
        before = {key: getattr(driver, key) for key in self.namespace_keys}
        with patch.dict(driver.__dict__, before):
            driver.configure_diagnostic()
            self.assertEqual(driver.PROCESS_SECONDS, 240)
            self.assertFalse(driver.EXTENDED)
            self.assertEqual((driver.ADR_REVISION, driver.CONTRACT_REVISION), (11, 12))

    def test_new_request_uses_one_origin_and_leaves_ready_object_unchanged(self):
        before = {key: getattr(driver, key) for key in self.namespace_keys}
        with patch.dict(driver.__dict__, before):
            driver.configure_extended()
            wire = {"request_id": driver.REQUEST_ID, "host_timeout_seconds": 600,
                    "started_at_unix_ms": 1, "deadline_unix_ms": 600001}
            result = driver.absolute_live_arguments(wire, 123000)
            self.assertEqual(result["started_at_unix_ms"], 123000)
            self.assertEqual(result["deadline_unix_ms"], 723000)
            self.assertEqual(result["host_timeout_seconds"], 600)
            self.assertEqual(wire["started_at_unix_ms"], 1)
            self.assertEqual(wire["deadline_unix_ms"], 600001)
            for change in ({"host_timeout_seconds": 240}, {"host_timeout_seconds": True},
                           {"request_id": driver.ORIGINAL_REQUEST}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    driver.absolute_live_arguments({**wire, **change}, 123000)

    def test_read_only_prior_verification_preserves_all_assets_and_markers(self):
        _, _, _, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        with patch.object(driver, "ROOT", self.root), patch("socket.socket", side_effect=AssertionError("offline")):
            bound = driver.verify_extended_previous(self.contract())
            driver.verify_prior_preservation(bound)
        for key in ("previous_marker_sha256", "previous_receipt_sha256",
                    "previous_diagnostic_marker_sha256", "previous_diagnostic_receipt_sha256",
                    "previous_postmortem_sha256"):
            self.assertEqual(len(bound[key]), 64)
        self.assertEqual([path.read_bytes() for path in prior], before)
        self.assertFalse((self.root / "verification/.scratch" /
            (driver.INSTANCE + ".live-extended-20261101T233105Z.full.json")).exists())

    def test_each_missing_prior_asset_blocks_read_only_admission(self):
        _, _, _, prior = self.fixture()
        with patch.object(driver, "ROOT", self.root):
            for path in prior:
                raw = path.read_bytes()
                path.unlink()
                try:
                    with self.subTest(path=path.name), self.assertRaises(Exception):
                        driver.verify_extended_previous(self.contract())
                finally:
                    path.write_bytes(raw)

    def test_changed_asset_receipt_or_marker_is_not_overwritten(self):
        _, _, _, prior = self.fixture()
        with patch.object(driver, "ROOT", self.root):
            for path in (prior[0], prior[1], prior[2], prior[4], prior[5], prior[6], prior[7]):
                raw = path.read_bytes()
                path.write_bytes(b"changed")
                try:
                    with self.subTest(path=path.name), self.assertRaises(Exception):
                        driver.verify_extended_previous(self.contract())
                    self.assertEqual(path.read_bytes(), b"changed")
                finally:
                    path.write_bytes(raw)

    def test_contract_identity_budget_or_prior_path_change_is_rejected(self):
        self.fixture()
        original = self.contract()["extended_recovery_contract"]
        with patch.object(driver, "ROOT", self.root):
            for change in ({"request_id": driver.ORIGINAL_REQUEST}, {"process_seconds": 240},
                           {"process_seconds": True}, {"max_retries": 4},
                           {"return_reserve_seconds": 0},
                           {"previous_postmortem": "other.receipt.json"}):
                with self.subTest(change=change), self.assertRaises(Exception):
                    driver.verify_extended_previous({"extended_recovery_contract": {**original, **change}})


if __name__ == "__main__":
    unittest.main()
