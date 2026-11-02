"""新选择恢复驱动的隔离正负控；不消耗真实样本或在线标记。"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from unittest.mock import patch

from test_schema_v3_coverage_recovery import CoverageRecoveryTests, VERSION, COVERAGE
from test_schema_v3_stdio import Fixture
from test_schema_v3_online_failure_preservation import driver

spec = importlib.util.spec_from_file_location(
    "coverage_driver_fixture",
    Path(__file__).resolve().parents[1] / "verification/.scratch/mainline_a_schema_v3_coverage_20261102.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class CoverageDriverTests(Fixture):
    live = CoverageRecoveryTests.live
    candidate = CoverageRecoveryTests.candidate
    success = CoverageRecoveryTests.success
    fixture = CoverageRecoveryTests.fixture

    def contract(self):
        return {"selection_coverage_recovery_contract": {
            "request_id": COVERAGE,
            "marker_id": driver.INSTANCE + ".live-coverage-20261102T050022Z",
            "recover_from_request_id": driver.ORIGINAL_REQUEST,
            "previous_minimal_request_id": driver.INSTANCE + "-live-minimal-20261102T020451Z",
            "previous_minimal_receipt": coverage.MINIMAL_RECEIPT,
            "previous_consumption_receipt": coverage.CONSUME_RECEIPT,
            "previous_reconciliation_receipt": coverage.RECONCILE_RECEIPT,
            "process_seconds": 600, "soft_target_seconds": 180,
            "return_reserve_seconds": 15, "max_retries": 3, "max_http_attempts": 4,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "retryable_reasons": ["upstream_stream_error", "upstream_timeout"],
            "selection_contract": {"explicit_version": VERSION}}}

    def test_explicit_namespace_post_and_old_modes_remain_separate(self):
        keys = ("READY", "LIVE", "RECOVERY_MARKER", "REQUEST_ID", "ADR_REVISION",
                "CONTRACT_REVISION", "DIAGNOSTIC", "EXTENDED", "MINIMAL", "COVERAGE",
                "PROCESS_SECONDS", "FROZEN_SAMPLE")
        before = {key: getattr(driver, key) for key in keys}
        with patch.dict(driver.__dict__, before):
            driver.configure_coverage()
            self.assertTrue(driver.COVERAGE)
            self.assertFalse(driver.MINIMAL)
            self.assertEqual(driver.REQUEST_ID, COVERAGE)
            self.assertEqual((driver.ADR_REVISION, driver.CONTRACT_REVISION), (14, 15))
            self.assertEqual(driver.FROZEN_SAMPLE, coverage.SAMPLES[0])
            with patch.object(driver, "read", return_value=b'{"messages":[]}') as read:
                self.assertEqual(driver.expected_live_post(),
                    b'{"messages":[],"temperature":0.2,"max_tokens":3072}')
                self.assertIn(coverage.SAMPLES[0], read.call_args.args[0])
            self.assertTrue(driver.retry_allowed({"error_reason": "upstream_timeout",
                                                  "upstream_error_type": "upstream_timeout"}))
            for configure in (driver.configure_minimal, driver.configure_extended,
                              driver.configure_diagnostic):
                configure()
                self.assertFalse(driver.COVERAGE)
                self.assertEqual(driver.FROZEN_SAMPLE, "preflight-1")

    def test_read_only_previous_verification_preserves_all_assets(self):
        _, _, _, _, paths = self.fixture()
        before = [path.read_bytes() for path in paths]
        with patch.object(driver, "ROOT", self.root), patch("socket.socket", side_effect=AssertionError("offline")):
            bound = coverage.verify_previous(driver, self.contract())
            coverage.verify_preservation(driver, bound)
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertEqual(bound["previous_minimal_receipt_sha256"], driver.digest(paths[-5].read_bytes()))
        self.assertFalse((self.root / "verification/.scratch" / (
            driver.INSTANCE + ".live-coverage-20261102T050022Z.full.json")).exists())

    def test_missing_or_changed_immediate_assets_block_without_writes(self):
        _, _, _, _, paths = self.fixture()
        with patch.object(driver, "ROOT", self.root):
            for path in paths[12:]:
                raw = path.read_bytes()
                for changed in (False, True):
                    if changed:
                        path.write_bytes(b"changed")
                    else:
                        path.unlink()
                    try:
                        with self.subTest(path=path.name, changed=changed), self.assertRaises(Exception):
                            coverage.verify_previous(driver, self.contract())
                        if changed:
                            self.assertEqual(path.read_bytes(), b"changed")
                    finally:
                        path.write_bytes(raw)

    def test_contract_identity_types_and_generation_controls_are_closed(self):
        self.fixture()
        original = self.contract()
        for change in ({"request_id": driver.ORIGINAL_REQUEST}, {"process_seconds": True},
                       {"max_retries": 4}, {"previous_minimal_receipt": "wrong.json"},
                       {"generation_controls": {"temperature": True, "max_tokens": 3072}},
                       {"retryable_reasons": ["upstream_timeout"]},
                       {"selection_contract": {"explicit_version": "question-scout-v1"}}):
            contract = copy.deepcopy(original)
            contract["selection_coverage_recovery_contract"].update(change)
            with patch.object(driver, "ROOT", self.root), self.subTest(change=change), self.assertRaises(Exception):
                coverage.verify_previous(driver, contract)

    def test_marker_requires_current_selection_and_all_immediate_links(self):
        distiller, arguments, _, _, _ = self.fixture()
        distiller._admit_live(arguments)
        with patch.object(driver, "ROOT", self.root):
            bound = coverage.verify_previous(driver, self.contract())
            marker = driver.load("verification/.scratch/" + driver.INSTANCE +
                                 ".live-coverage-20261102T050022Z.full.json")
            coverage.verify_marker(driver, marker, bound)
            for key in ("selection_version", "previous_minimal_receipt_sha256",
                        "previous_consumption_receipt_sha256", "previous_reconciliation_receipt_sha256",
                        "process_seconds", "generation_controls"):
                altered = dict(marker)
                altered.pop(key)
                with self.subTest(key=key), self.assertRaises(Exception):
                    coverage.verify_marker(driver, altered, bound)

    def test_zero_missing_or_skipped_regression_evidence_is_not_pass(self):
        good = {"actual_exit_code": 0, "tests_run": 5, "failed": 0, "errors": 0,
                "skipped": 0, "adr_revision": 14, "execution_contract_revision": 15}
        coverage.require_regression(driver, good)
        for change in ({"tests_run": 0}, {"actual_exit_code": 2}, {"skipped": 1},
                       {"adr_revision": 13}, {"errors": True}):
            with self.subTest(change=change), self.assertRaises(Exception):
                coverage.require_regression(driver, {**good, **change})
