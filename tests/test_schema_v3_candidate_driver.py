"""候选恢复驱动正负控；仅隔离夹具，不使用真实网关、冷样本或在线标记。"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from test_schema_v3_candidate_admission import CandidateAdmissionTests
from test_schema_v3_candidate_recovery import CandidateRecoveryTests
from test_schema_v3_coverage_driver import CoverageDriverTests, coverage
from test_schema_v3_coverage_recovery import CoverageRecoveryTests
from test_schema_v3_live_retry import TRANSIENT
from test_schema_v3_minimal_recovery import TIMEOUT
from test_schema_v3_online_failure_preservation import driver
from test_schema_v3_stdio import Fixture

SCRATCH = Path(__file__).resolve().parents[1] / "verification/.scratch"


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRATCH / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostic = load_module("candidate_diagnostic_fixture",
    "mainline_a_schema_v3_coverage_failure_diagnostic_20261102.py")
with patch.dict(sys.modules, {
        "mainline_a_schema_v3_coverage_20261102": coverage,
        "mainline_a_schema_v3_coverage_failure_diagnostic_20261102": diagnostic}):
    candidate_support = load_module("candidate_support_fixture",
        "mainline_a_schema_v3_candidate_20261102.py")


class CandidateDriverTests(Fixture):
    fixture = CoverageRecoveryTests.fixture
    live = CoverageRecoveryTests.live
    candidate = CoverageRecoveryTests.candidate
    success = CoverageRecoveryTests.success
    case = CandidateRecoveryTests.case
    run_streams = CandidateRecoveryTests.run_streams

    def contract(self):
        contract = CoverageDriverTests.contract(self)
        contract["candidate_recovery_contract"] = {
            "request_id": candidate_support.REQUEST, "marker_id": candidate_support.MARKER,
            "recover_from_request_id": driver.ORIGINAL_REQUEST,
            "previous_coverage_request_id": coverage.REQUEST,
            "previous_marker": candidate_support.PATHS["previous_coverage_marker_sha256"],
            "previous_receipt": candidate_support.PATHS["previous_coverage_receipt_sha256"],
            "previous_diagnostic": candidate_support.PATHS["previous_coverage_diagnostic_sha256"],
            "previous_ready": candidate_support.PATHS["previous_coverage_ready_sha256"],
            "process_seconds": 600, "soft_target_seconds": 180,
            "return_reserve_seconds": 15, "max_total_retries": 3, "max_http_attempts": 4,
            "candidate_retryable_reason": candidate_support.REASON,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "transport_retryable_reasons": ["upstream_stream_error", "upstream_timeout"]}
        return contract

    def previous(self):
        current, arguments, paths = CandidateAdmissionTests.case(self)
        contract = self.contract()
        with patch.object(driver, "ROOT", self.root):
            bound = coverage.verify_previous(driver, contract)
        ready_path = self.root / candidate_support.PATHS["previous_coverage_ready_sha256"]
        receipt_path = self.root / candidate_support.PATHS["previous_coverage_receipt_sha256"]
        diagnostic_path = self.root / candidate_support.PATHS["previous_coverage_diagnostic_sha256"]
        for path in (ready_path, receipt_path):
            value = json.loads(path.read_bytes())
            value["binding"].update(bound)
            path.write_text(json.dumps(value))
        value = json.loads(diagnostic_path.read_bytes())
        value["online_receipt_sha256"] = driver.digest(receipt_path.read_bytes())
        diagnostic_path.write_text(json.dumps(value))
        return current, arguments, paths, contract

    def test_namespace_is_explicit_and_all_old_modes_reset_candidate_flag(self):
        with patch.dict(driver.__dict__, dict(driver.__dict__)):
            driver.configure_candidate()
            self.assertTrue(driver.CANDIDATE)
            self.assertTrue(driver.COVERAGE)
            self.assertFalse(driver.MINIMAL)
            self.assertEqual(driver.REQUEST_ID, candidate_support.REQUEST)
            self.assertEqual(driver.RECOVERY_MARKER,
                "verification/.scratch/" + candidate_support.MARKER + ".full.json")
            self.assertEqual((driver.ADR_REVISION, driver.CONTRACT_REVISION), (15, 16))
            self.assertEqual(driver.PROCESS_SECONDS, 600)
            self.assertEqual(driver.FROZEN_SAMPLE, coverage.SAMPLES[0])
            with patch.object(driver, "read", return_value=b'{"messages":[]}'):
                self.assertEqual(driver.expected_live_post(),
                    b'{"messages":[],"temperature":0.2,"max_tokens":3072}')
            observation = {"attempt_index": 2, "http_status": 200,
                "finish_reason": "stop", "done_marker": True, "outer_replays": 0,
                "candidate_error_reason": candidate_support.REASON,
                "candidate_rejection_id": candidate_support.REQUEST + ".candidate-rejection-2"}
            self.assertTrue(driver.retry_allowed(observation))
            for change in ({"candidate_rejection_id": "wrong"}, {"finish_reason": "length"},
                           {"done_marker": False}, {"candidate_error_reason": "generic_error"},
                           {"error_reason": "upstream_timeout"}, {"attempt_index": True}):
                with self.subTest(change=change):
                    self.assertFalse(driver.retry_allowed({**observation, **change}))
            for configure in (driver.configure_coverage, driver.configure_minimal,
                              driver.configure_extended, driver.configure_diagnostic):
                configure()
                self.assertFalse(driver.CANDIDATE)
                self.assertFalse(driver.retry_allowed(observation))

    def test_previous_evidence_and_new_marker_are_read_only_and_exact(self):
        current, arguments, paths, contract = self.previous()
        before = [path.read_bytes() for path in paths]
        with patch.object(driver, "ROOT", self.root), patch("socket.socket",
                side_effect=AssertionError("不得联网")):
            bound = candidate_support.verify_previous(driver, contract)
            candidate_support.verify_preservation(driver, bound)
            current._admit_live(arguments)
            marker = driver.load("verification/.scratch/" + candidate_support.MARKER + ".full.json")
            candidate_support.verify_marker(driver, marker, bound)
            for key in ("previous_coverage_ready_sha256", "previous_coverage_receipt_sha256",
                        "candidate_retryable_reason", "max_total_retries", "generation_controls"):
                altered = dict(marker)
                altered.pop(key)
                with self.subTest(key=key), self.assertRaises(Exception):
                    candidate_support.verify_marker(driver, altered, bound)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_missing_changed_exit_or_contract_evidence_cannot_be_inferred(self):
        _, _, _, contract = self.previous()
        with patch.object(driver, "ROOT", self.root):
            for key, path in candidate_support.PATHS.items():
                target = self.root / path
                raw = target.read_bytes()
                for changed in (False, True):
                    if changed:
                        target.write_bytes(b"changed")
                    else:
                        target.unlink()
                    try:
                        with self.subTest(key=key, changed=changed), self.assertRaises(Exception):
                            candidate_support.verify_previous(driver, contract)
                    finally:
                        target.write_bytes(raw)
            for change in ({"process_seconds": True}, {"max_total_retries": 4},
                           {"request_id": driver.ORIGINAL_REQUEST},
                           {"generation_controls": {"temperature": True, "max_tokens": 3072}},
                           {"transport_retryable_reasons": ["upstream_timeout"]}):
                altered = copy.deepcopy(contract)
                altered["candidate_recovery_contract"].update(change)
                with self.subTest(change=change), self.assertRaises(Exception):
                    candidate_support.verify_previous(driver, altered)
        self.assertFalse((self.root / "verification/.scratch" /
            (candidate_support.MARKER + ".full.json")).exists())

    def test_mixed_attempts_verify_original_stream_and_whole_rejection(self):
        distiller, arguments, _, _, _, good, bad = self.case()
        distiller.gateway.capture_stream = True
        self.run_streams(distiller, arguments,
            [TRANSIENT, self.success(bad), TIMEOUT, self.success(good)])
        trace = self.trace(candidate_support.REQUEST)
        receipt = {"assets": {}}
        with patch.dict(driver.__dict__, dict(driver.__dict__)), patch.object(driver, "ROOT", self.root):
            driver.configure_candidate()
            for observation in trace["gateway_requests"]:
                captured = base64.b64decode(observation["raw_sse_base64"], validate=True)
                candidate_support.verify_attempt(driver, receipt, observation,
                    trace["post_utf8"].encode(), captured, driver.Guard(30))
            self.assertEqual(set(receipt["assets"]), {"transport_attempt_1", "transport_attempt_2",
                "transport_attempt_3", "transport_attempt_4", "candidate_rejection_2"})
            rejection_asset = receipt["assets"]["candidate_rejection_2"]
            self.assertEqual(json.loads((self.root / rejection_asset["path"]).read_bytes())["candidate"], bad)
            observation = trace["gateway_requests"][1]
            captured = base64.b64decode(observation["raw_sse_base64"], validate=True)
            with patch.object(driver, "save", side_effect=AssertionError("不得重复写入")):
                candidate_support.verify_attempt(driver, receipt, observation,
                    trace["post_utf8"].encode(), captured, driver.Guard(30))
            with self.assertRaises(ValueError):
                candidate_support.verify_attempt(driver, receipt, observation,
                    trace["post_utf8"].encode(), captured + b"x", driver.Guard(30))
            self.assertEqual(trace["candidate"], good)
            self.assertEqual(len(trace["model_outputs"]), 1)

    def test_failure_preservation_keeps_all_available_rejections_without_delivery(self):
        distiller = self.distiller()
        for number in (1, 2):
            distiller.persist_scratch(candidate_support.REQUEST + ".candidate-rejection-" + str(number),
                {"reason": candidate_support.REASON, "candidate": {"summary": "拒收原文"}})
        receipt = {"request_id": candidate_support.REQUEST, "status": "blocked", "assets": {}, "failures": []}
        client = SimpleNamespace(stderr=bytearray(b"failure"), last_request_bytes=b"request\n")
        with patch.dict(driver.__dict__, dict(driver.__dict__)), patch.object(driver, "ROOT", self.root):
            driver.configure_candidate()
            driver.preserve_failure_evidence(client, receipt, driver.Guard(30))
        self.assertEqual(set(receipt["assets"]),
            {"stderr", "request_wire", "candidate_rejection_1", "candidate_rejection_2"})
        self.assertEqual(receipt["status"], "blocked")
        self.assertNotIn("delivery", receipt["assets"])

    def test_zero_skipped_missing_or_wrong_revision_regression_is_not_pass(self):
        good = {"actual_exit_code": 0, "tests_run": 6, "failed": 0, "errors": 0, "skipped": 0,
            "adr_revision": 15, "execution_contract_revision": 16, "tool_results": ["isolated-real-receipt"]}
        candidate_support.require_regression(driver, good)
        for change in ({"tests_run": 0}, {"skipped": 1}, {"errors": True},
                       {"actual_exit_code": 2}, {"adr_revision": 14}, {"tool_results": []}):
            with self.subTest(change=change), self.assertRaises(Exception):
                candidate_support.require_regression(driver, {**good, **change})
