"""语法恢复驱动隔离正负控；不联网、不消耗真实窗口或修改历史证据。"""
from __future__ import annotations

import base64
import copy
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from test_schema_v3_candidate_driver import (
    CandidateDriverTests, candidate_support, coverage, diagnostic, load_module,
)
from test_schema_v3_candidate_syntax_admission import CandidateSyntaxAdmissionTests
from test_schema_v3_candidate_syntax_recovery import CandidateSyntaxRecoveryTests, completed
from test_schema_v3_live_retry import TRANSIENT
from test_schema_v3_online_failure_preservation import driver
from test_schema_v3_minimal_recovery import MinimalDriverTests
from test_schema_v3_stdio import Fixture

MODULES = {
    driver.__name__: driver,
    "mainline_a_schema_v3_candidate_20261102": candidate_support,
    "mainline_a_schema_v3_coverage_20261102": coverage,
    "mainline_a_schema_v3_coverage_failure_diagnostic_20261102": diagnostic,
}
with patch.dict(sys.modules, MODULES):
    syntax_support = load_module("syntax_support_fixture", "mainline_a_schema_v3_syntax_20261102.py")
MODULES["mainline_a_schema_v3_syntax_20261102"] = syntax_support


class CandidateSyntaxDriverTests(Fixture):
    fixture = CandidateSyntaxAdmissionTests.fixture
    live = CandidateSyntaxAdmissionTests.live
    candidate = CandidateSyntaxAdmissionTests.candidate
    success = CandidateSyntaxAdmissionTests.success
    run_streams = CandidateSyntaxAdmissionTests.run_streams

    def contract(self):
        result = MinimalDriverTests.contract(self)
        result.update(CandidateDriverTests.contract(self))
        result["syntax_recovery_contract"] = {
            "request_id": syntax_support.REQUEST, "marker_id": syntax_support.MARKER,
            "recover_from_request_id": driver.ORIGINAL_REQUEST,
            "previous_candidate_request_id": candidate_support.REQUEST,
            "previous_marker": syntax_support.PATHS["previous_candidate_marker_sha256"],
            "previous_receipt": syntax_support.PATHS["previous_candidate_receipt_sha256"],
            "previous_diagnostic": syntax_support.PATHS["previous_candidate_diagnostic_sha256"],
            "previous_ready": syntax_support.PATHS["previous_candidate_ready_sha256"],
            "process_seconds": 600, "soft_target_seconds": 180, "return_reserve_seconds": 15,
            "max_total_retries": 3, "max_http_attempts": 4,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "transport_retryable_reasons": ["upstream_stream_error", "upstream_timeout"],
            "candidate_retryable_reasons": syntax_support.REASONS,
        }
        return result

    def previous(self):
        current, arguments, paths = CandidateSyntaxAdmissionTests.case(self)
        contract = self.contract()
        with patch.dict(sys.modules, MODULES), patch.object(driver, "ROOT", self.root):
            lower = driver.verify_minimal_previous(contract)
            lower.update(coverage.verify_previous(driver, contract))
            for key in ("previous_coverage_ready_sha256", "previous_coverage_receipt_sha256"):
                target = self.root / candidate_support.PATHS[key]
                value = json.loads(target.read_bytes())
                value["binding"].update(lower)
                target.write_text(json.dumps(value))
            target = self.root / candidate_support.PATHS["previous_coverage_diagnostic_sha256"]
            value = json.loads(target.read_bytes())
            value["online_receipt_sha256"] = driver.digest((self.root /
                candidate_support.PATHS["previous_coverage_receipt_sha256"]).read_bytes())
            target.write_text(json.dumps(value))
            bound = dict(lower)
            bound.update(candidate_support.verify_previous(driver, contract))
            marker = current._admit_candidate({**arguments, "request_id": candidate_support.REQUEST},
                driver.ORIGINAL_REQUEST, persist=False)
            marker_path = self.root / syntax_support.PATHS["previous_candidate_marker_sha256"]
            marker_raw = json.dumps(marker).encode()
            marker_path.write_bytes(marker_raw)
            ready_path = self.root / syntax_support.PATHS["previous_candidate_ready_sha256"]
            ready = json.loads(ready_path.read_bytes())
            ready["binding"].update(bound)
            ready_path.write_text(json.dumps(ready))
            receipt_path = self.root / syntax_support.PATHS["previous_candidate_receipt_sha256"]
            receipt = json.loads(receipt_path.read_bytes())
            receipt["binding"] = ready["binding"]
            admission = receipt["assets"]["admission"]
            (self.root / admission["path"]).write_bytes(marker_raw)
            admission.update(bytes=len(marker_raw), sha256=driver.digest(marker_raw))
            receipt_raw = json.dumps(receipt).encode()
            receipt_path.write_bytes(receipt_raw)
            target = self.root / syntax_support.PATHS["previous_candidate_diagnostic_sha256"]
            value = json.loads(target.read_bytes())
            value.update(online_receipt_sha256=driver.digest(receipt_raw),
                saved_asset_bytes=sum(item["bytes"] for item in receipt["assets"].values()))
            target.write_text(json.dumps(value))
        return current, arguments, list(dict.fromkeys(paths)), contract

    def test_explicit_namespace_shared_retry_and_old_modes_reset(self):
        with patch.dict(driver.__dict__, dict(driver.__dict__)):
            driver.configure_syntax()
            self.assertTrue(driver.SYNTAX and driver.CANDIDATE and driver.COVERAGE)
            self.assertEqual(driver.REQUEST_ID, syntax_support.REQUEST)
            self.assertEqual((driver.ADR_REVISION, driver.CONTRACT_REVISION), (16, 17))
            self.assertEqual(driver.PROCESS_SECONDS, 600)
            self.assertEqual(driver.RECOVERY_MARKER,
                "verification/.scratch/" + syntax_support.MARKER + ".full.json")
            for reason in syntax_support.REASONS:
                observation = {"attempt_index": 3, "http_status": 200,
                    "finish_reason": "stop", "done_marker": True, "outer_replays": 0,
                    "candidate_error_reason": reason,
                    "candidate_rejection_id": syntax_support.REQUEST + ".candidate-rejection-3"}
                self.assertTrue(driver.retry_allowed(observation))
                for change in ({"attempt_index": 4}, {"attempt_index": True},
                        {"candidate_error_reason": []}, {"candidate_error_reason": "generic"},
                        {"candidate_rejection_id": "wrong"}, {"http_status": True},
                        {"finish_reason": "length"}, {"done_marker": False},
                        {"outer_replays": 1}, {"error_reason": "upstream_timeout"}):
                    with self.subTest(change=change):
                        self.assertFalse(driver.retry_allowed({**observation, **change}))
            observation["candidate_error_reason"] = "invalid_candidate_json_syntax"
            for configure in (driver.configure_candidate, driver.configure_coverage,
                    driver.configure_minimal, driver.configure_extended, driver.configure_diagnostic):
                configure()
                self.assertFalse(driver.SYNTAX)
                self.assertFalse(driver.retry_allowed(observation))

    def test_cli_dispatch_is_explicit_mutually_exclusive_and_not_auto_chained(self):
        with patch.dict(driver.__dict__, dict(driver.__dict__)):
            for phase in ("prepare", "live"):
                with patch.object(sys, "argv", ["fixture", phase, "--syntax"]), patch.object(
                        driver, "prepare", return_value=0) as prepare, patch.object(
                        driver, "live", return_value=0) as live:
                    self.assertEqual(driver.main(), 0)
                    self.assertTrue(driver.SYNTAX)
                    self.assertEqual(prepare.call_count, int(phase == "prepare"))
                    self.assertEqual(live.call_count, int(phase == "live"))
            with patch.object(sys, "argv", ["fixture", "live", "--syntax", "--candidate"]), patch(
                    "sys.stderr"), self.assertRaises(SystemExit):
                driver.main()

    def test_previous_chain_and_new_marker_are_exact_read_only_and_exclusive(self):
        current, arguments, paths, contract = self.previous()
        before = [path.read_bytes() for path in paths]
        with patch.dict(sys.modules, MODULES), patch.object(driver, "ROOT", self.root), patch("socket.socket",
                side_effect=AssertionError("禁止联网")):
            bound = syntax_support.verify_previous(driver, contract)
            syntax_support.verify_preservation(driver, bound)
            current._admit_live(arguments)
            marker = driver.load("verification/.scratch/" + syntax_support.MARKER + ".full.json")
            syntax_support.verify_marker(driver, marker, bound)
            for key in (*syntax_support.PATHS, "max_total_retries", "candidate_retryable_reasons",
                    "soft_target_seconds", "return_reserve_seconds", "generation_controls"):
                altered = dict(marker)
                altered.pop(key)
                with self.subTest(key=key), self.assertRaises(Exception):
                    syntax_support.verify_marker(driver, altered, bound)
            with self.assertRaises(Exception):
                current._admit_live(arguments)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_missing_changed_contract_and_actual_exit_evidence_block(self):
        _, _, paths, contract = self.previous()
        with patch.dict(sys.modules, MODULES), patch.object(driver, "ROOT", self.root), patch("socket.socket",
                side_effect=AssertionError("禁止联网")):
            syntax_support.verify_previous(driver, contract)
            for path in paths:
                raw = path.read_bytes()
                for changed in (False, True):
                    if changed:
                        path.write_bytes(b"changed")
                    else:
                        path.unlink()
                    try:
                        with self.subTest(path=path.name, changed=changed), self.assertRaises(Exception):
                            syntax_support.verify_previous(driver, contract)
                    finally:
                        path.write_bytes(raw)
            for change in ({"max_total_retries": 4}, {"process_seconds": True},
                    {"request_id": candidate_support.REQUEST},
                    {"candidate_retryable_reasons": ["invalid_candidate_json_syntax"]},
                    {"transport_retryable_reasons": ["upstream_timeout"]}):
                altered = copy.deepcopy(contract)
                altered["syntax_recovery_contract"].update(change)
                with self.subTest(change=change), self.assertRaises(Exception):
                    syntax_support.verify_previous(driver, altered)
        self.assertFalse((self.root / "verification/.scratch" / (syntax_support.MARKER + ".full.json")).exists())

    def test_mixed_attempts_original_sse_and_both_whole_rejections(self):
        current, arguments, _, _, _, good, bad = CandidateSyntaxRecoveryTests.case(self)
        malformed = '{\n"summary":"不得修补",\n}'
        self.run_streams(current, arguments,
            [TRANSIENT, completed(malformed), self.success(bad), self.success(good)])
        trace = self.trace(syntax_support.REQUEST)
        receipt = {"assets": {}}
        with patch.dict(driver.__dict__, dict(driver.__dict__)), patch.object(driver, "ROOT", self.root):
            driver.configure_syntax()
            post = trace["post_utf8"].encode()
            for observation in trace["gateway_requests"]:
                captured = base64.b64decode(observation["raw_sse_base64"], validate=True)
                syntax_support.verify_attempt(driver, receipt, observation, post, captured, driver.Guard(30))
            self.assertEqual(set(receipt["assets"]), {"transport_attempt_1", "transport_attempt_2",
                "transport_attempt_3", "transport_attempt_4", "candidate_rejection_2", "candidate_rejection_3"})
            rejection = driver.load(receipt["assets"]["candidate_rejection_2"]["path"])
            self.assertEqual(rejection["output"], malformed)
            self.assertNotIn("candidate", rejection)
            observation = trace["gateway_requests"][1]
            captured = base64.b64decode(observation["raw_sse_base64"], validate=True)
            with patch.object(driver, "save", side_effect=AssertionError("不得重复保存")):
                syntax_support.verify_attempt(driver, receipt, observation, post, captured, driver.Guard(30))
            for raw in (captured + b"x", b"data: [DONE]\n\n"):
                with self.subTest(raw=raw[:32]), self.assertRaises(Exception):
                    syntax_support.verify_attempt(driver, receipt, observation, post, raw, driver.Guard(30))
        self.assertEqual(trace["candidate"], good)
        self.assertEqual(len(trace["model_outputs"]), 1)

    def test_syntax_record_tampering_and_save_failure_are_not_acceptance(self):
        current, arguments, _, _, _, good, _ = CandidateSyntaxRecoveryTests.case(self)
        self.run_streams(current, arguments, [completed('{"a":1,}'), self.success(good)])
        trace = self.trace(syntax_support.REQUEST)
        observation = trace["gateway_requests"][0]
        post = trace["post_utf8"].encode()
        captured = base64.b64decode(observation["raw_sse_base64"], validate=True)
        path = self.root / "verification/.scratch" / (syntax_support.REQUEST + ".candidate-rejection-1.full.json")
        raw = path.read_bytes()
        original = json.loads(raw)
        with patch.dict(driver.__dict__, dict(driver.__dict__)), patch.object(driver, "ROOT", self.root):
            driver.configure_syntax()
            for change in ({"output": '{"a":1}'}, {"parse_error": {}}, {"attempt_index": True},
                    {"absolute_deadline": 1}, {"candidate": {}}, {"post_sha256": "0" * 64}):
                path.write_text(json.dumps({**original, **change}))
                with self.subTest(change=change), self.assertRaises(Exception):
                    syntax_support.verify_attempt(driver, {"assets": {}}, observation,
                        post, captured, driver.Guard(30))
            path.write_bytes(raw)
            with patch.object(driver, "save", side_effect=OSError("隔离保存失败")), self.assertRaises(OSError):
                syntax_support.verify_attempt(driver, {"assets": {}}, observation,
                    post, captured, driver.Guard(30))

    def test_missing_zero_skipped_or_wrong_revision_regressions_cannot_admit(self):
        good = {"actual_exit_code": 0, "tests_run": 1, "failed": 0, "errors": 0, "skipped": 0,
            "adr_revision": 16, "execution_contract_revision": 17, "tool_results": ["isolated-receipt"]}
        syntax_support.require_regression(driver, good)
        for change in ({"tests_run": 0}, {"skipped": 1}, {"failed": 1}, {"errors": True},
                {"actual_exit_code": 2}, {"adr_revision": 15}, {"tool_results": []}):
            with self.subTest(change=change), self.assertRaises(Exception):
                syntax_support.require_regression(driver, {**good, **change})
        with patch.object(driver, "load", return_value=good) as load:
            syntax_support.require_regressions(driver)
        self.assertEqual([call.args[0] for call in load.call_args_list], syntax_support.REGRESSIONS)
        with patch.object(driver, "load", side_effect=FileNotFoundError), self.assertRaises(FileNotFoundError):
            syntax_support.require_regressions(driver)
        with patch.object(candidate_support, "prepare", return_value=0) as prepare:
            self.assertEqual(syntax_support.prepare(driver), 0)
        self.assertIs(prepare.call_args.kwargs["regression_validator"], syntax_support.require_regressions)

    def test_failure_preservation_keeps_raw_syntax_rejection_without_delivery(self):
        current = self.distiller()
        value = {"reason": "invalid_candidate_json_syntax", "output": '{"a":1,}',
            "parse_error": {"error_type": "JSONDecodeError", "position": 7}}
        current.persist_scratch(syntax_support.REQUEST + ".candidate-rejection-1", value)
        receipt = {"request_id": syntax_support.REQUEST, "status": "blocked", "assets": {}, "failures": []}
        client = SimpleNamespace(stderr=bytearray(b"failure"), last_request_bytes=b"request\n")
        with patch.dict(driver.__dict__, dict(driver.__dict__)), patch.object(driver, "ROOT", self.root):
            driver.configure_syntax()
            driver.preserve_failure_evidence(client, receipt, driver.Guard(30))
            self.assertEqual(driver.load(receipt["assets"]["candidate_rejection_1"]["path"]), value)
        self.assertEqual(receipt["status"], "blocked")
        self.assertNotIn("delivery", receipt["assets"])
