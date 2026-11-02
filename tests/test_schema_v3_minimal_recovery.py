"""单网关微创恢复的隔离正负控；模拟 HTTP，不消耗真实在线标记。"""
from __future__ import annotations

import hashlib
import json
import time
from unittest.mock import patch
import unittest

from test_distillation import Connection, Response, event
from test_schema_v3_stdio import Fixture
from test_schema_v3_live_retry import LiveRetryTests, TRANSIENT
from test_schema_v3_extended_recovery import ExtendedRecoveryTests
from test_schema_v3_online_failure_preservation import driver
from solo_distill.gateway import GatewayError, encode_request
from solo_distill.preflight_v3 import INSTANCE, PARENT

ORIGINAL = INSTANCE + "-live-20261101"
EXTENDED = INSTANCE + "-live-extended-20261101T233105Z"
MINIMAL = INSTANCE + "-live-minimal-20261102T020451Z"
MARKER = INSTANCE + ".live-minimal-20261102T020451Z"
TIMEOUT = event({"error": {"type": "upstream_timeout", "message": "deadline exceeded"}})


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class MinimalRecoveryTests(Fixture):
    live = LiveRetryTests.live

    def fixture(self):
        distiller, arguments, prepared, prior = ExtendedRecoveryTests.fixture(self)
        distiller._admit_live(arguments)
        parent = self.root / PARENT
        marker = self.root / "verification/.scratch" / (
            INSTANCE + ".live-extended-20261101T233105Z.full.json")
        diagnostic_raw = prior[5].read_bytes()
        diagnostic = json.loads(diagnostic_raw)
        audit_raw = prior[6].read_bytes()
        evidence = parent / "phase5-live-extended-20261101T233105Z.gateway.sse"
        evidence.write_bytes(TIMEOUT)
        binding = {**diagnostic["binding"],
            "previous_diagnostic_marker_sha256": digest(prior[2].read_bytes()),
            "previous_diagnostic_receipt_sha256": digest(diagnostic_raw),
            "previous_postmortem_sha256": digest(audit_raw),
            "previous_diagnostic_assets": diagnostic["assets"]}
        receipt = parent / "phase5-live-extended-20261101T233105Z.receipt.json"
        receipt.write_text(json.dumps({"status": "blocked", "execution_instance": INSTANCE,
            "request_id": EXTENDED, "service_exit_code": 0,
            "service_cleanup": "confirmed_natural_exit", "business_call_attempts": 1,
            "binding": binding, "assets": {"sse": {
                "path": str(evidence.relative_to(self.root)), "bytes": len(TIMEOUT),
                "sha256": digest(TIMEOUT), "exact_readback": True}}}))
        receipt_raw = receipt.read_bytes()
        audit = parent / "phase5-extended-postmortem-20261101T235855Z.receipt.json"
        audit.write_text(json.dumps({"execution_instance": INSTANCE,
            "online_acceptance": "blocked", "online_result": {
                "actual_exit_code": 2, "service_exit_code": 0,
                "service_cleanup": "confirmed_natural_exit",
                "original_receipt": str(receipt.relative_to(self.root)),
                "original_receipt_bytes": len(receipt_raw),
                "original_receipt_sha256": digest(receipt_raw)}}))
        arguments["request_id"] = MINIMAL
        return distiller, arguments, prepared, prior + [marker, receipt, audit, evidence]

    def candidate(self, prepared):
        source_id = json.loads(prepared.messages[1]["content"])["sources"][0]["source_id"]
        return {"summary": "有来源的隔离测试输出", "conclusions": [{
            "dimension_id": "interfaces", "text": "该函数返回整数。",
            "source_ids": [source_id]}], "unknowns": []}

    def success(self, candidate):
        return event({"choices": [{"index": 0, "delta": {
            "content": json.dumps(candidate, ensure_ascii=False)}, "finish_reason": None}]}) + event({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"

    def test_new_admission_preserves_all_old_assets_and_is_exclusive(self):
        distiller, arguments, _, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        distiller._admit_live(arguments)
        raw = (self.root / "verification/.scratch" / (MARKER + ".full.json")).read_bytes()
        marker = json.loads(raw)
        self.assertEqual(marker["request_id"], MINIMAL)
        self.assertEqual(marker["recover_from_request_id"], ORIGINAL)
        self.assertEqual(marker["previous_extended_request_id"], EXTENDED)
        self.assertEqual(marker["generation_controls"], {"temperature": 0.2, "max_tokens": 3072})
        self.assertEqual(marker["retryable_reasons"], ["upstream_stream_error", "upstream_timeout"])
        self.assertEqual(marker["process_seconds"], 600)
        self.assertEqual(marker["max_retries"], 3)
        self.assertFalse(marker["automatic_replay"])
        with self.assertRaises(Exception):
            distiller._admit_live(arguments)
        self.assertEqual([path.read_bytes() for path in prior], before)
        self.assertEqual((self.root / "verification/.scratch" / (MARKER + ".full.json")).read_bytes(), raw)

    def test_each_missing_or_changed_prior_asset_blocks_without_new_marker(self):
        distiller, arguments, _, prior = self.fixture()
        target = self.root / "verification/.scratch" / (MARKER + ".full.json")
        for path in prior:
            original = path.read_bytes()
            for mode in ("missing", "changed"):
                if mode == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"changed")
                try:
                    with self.subTest(path=path.name, mode=mode), self.assertRaises(Exception):
                        distiller._admit_live(arguments)
                    self.assertFalse(target.exists())
                    if mode == "changed":
                        self.assertEqual(path.read_bytes(), b"changed")
                finally:
                    path.write_bytes(original)

    def test_mixed_transients_fourth_success_same_controlled_post_deadline_and_dual_consumption(self):
        from solo_distill.consumer_v3 import consume_frame
        from solo_distill.wire_v3 import Admission
        from solo_distill.adaptive_wire import TransportCapabilities
        from schema_v3_wire_oracle import receive
        distiller, arguments, prepared, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        candidate = self.candidate(prepared)
        partial = event({"choices": [{"index": 0, "delta": {"content": "discard-me"},
                                      "finish_reason": None}]}) + TIMEOUT
        streams = [TRANSIENT, partial, TIMEOUT, self.success(candidate)]
        connections = [Connection(Response([stream])) for stream in streams]
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect:
            frame = distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        trace = self.trace(MINIMAL)
        controlled = encode_request(prepared.messages, generation_controls=True)
        self.assertEqual(trace["post_utf8"].encode(), controlled)
        self.assertEqual(trace["candidate"], candidate)
        self.assertEqual(trace["failures"], [])
        self.assertEqual(len(trace["model_outputs"]), 1)
        self.assertNotIn("discard-me", trace["model_outputs"][0])
        attempts = trace["gateway_requests"]
        self.assertEqual([item["attempt_index"] for item in attempts], [1, 2, 3, 4])
        self.assertEqual([item["client_auto_retries"] for item in attempts], [0, 1, 2, 3])
        self.assertEqual(len({item["absolute_deadline"] for item in attempts}), 1)
        self.assertAlmostEqual(attempts[0]["absolute_deadline"], distiller.business_deadline - 15)
        for number, connection in enumerate(connections, 1):
            self.assertTrue(connection.closed)
            self.assertEqual(connection.calls[0][1]["body"], controlled)
            self.assertTrue((self.root / "verification/.scratch" / (
                MINIMAL + ".gateway-attempt-" + str(number) + ".full.json")).exists())
        self.assertEqual(attempts[-1]["finish_reason"], "stop")
        self.assertTrue(attempts[-1]["done_marker"])
        trusted = self.trusted(trace, arguments)
        admission = Admission(arguments["protocol_version"], "A", 3, arguments["return_bytes"],
            TransportCapabilities.from_dict(arguments["transport"]))
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            production = consume_frame(frame.data, self.files, admission=admission,
                candidate=candidate, **trusted)
            independent = receive(frame.data, self.files, arguments["transport"],
                protocol_version=arguments["protocol_version"], response_id=3,
                return_bytes=arguments["return_bytes"], candidate=candidate, **trusted)
        self.assertEqual(production.status, "complete")
        self.assertEqual(independent["task_status"], "complete")
        self.assertEqual(production.publication, independent["publication"])
        self.assertEqual([path.read_bytes() for path in prior], before)

    def test_timeout_exhaustion_stops_after_four_and_keeps_failure_evidence(self):
        distiller, arguments, _, _ = self.fixture()
        connections = [Connection(Response([TIMEOUT])) for _ in range(4)]
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect:
            distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        trace = self.trace(MINIMAL)
        self.assertEqual(trace["model_outputs"], [])
        self.assertEqual(trace["failures"], [{"kind": "blocked", "reason": "upstream_timeout"}])
        self.assertEqual(len(trace["gateway_requests"]), 4)
        self.assertTrue(all(connection.closed for connection in connections))

    def test_untyped_mismatched_local_and_auth_errors_are_not_retried(self):
        cases = [("upstream_timeout", {}), ("upstream_timeout", {"upstream_error_type": "upstream_stream_error"}),
            ("upstream_stream_error", {"upstream_error_type": "upstream_timeout"}),
            ("gateway_deadline_exhausted", {"upstream_error_type": "upstream_timeout"}),
            ("upstream_error", {"upstream_error_type": "authentication_required"}),
            ("non_normal_finish", {"finish_reason": "length"})]
        for reason, observation in cases:
            with self.subTest(reason=reason, observation=observation):
                distiller, arguments, _ = self.live(None, request_id=MINIMAL,
                    recover_from_request_id=ORIGINAL)
                prepared = __import__("solo_distill.preparation_v3", fromlist=["prepare_snapshot"]).prepare_snapshot(
                    project=self.project, include=(".",), question=arguments["question"],
                    dimensions=arguments["dimensions"], deadline=time.monotonic() + 5,
                    cancelled=distiller.cancelled)
                original = {"request_id": ORIGINAL, "post_utf8": prepared.post.decode(),
                    "selection": prepared.selection, "report": prepared.report,
                    "failures": [{"kind": "blocked", "reason": "upstream_stream_error"}]}
                with patch.object(distiller, "_admit_live"), patch.object(distiller, "_read_scratch", return_value=original), \
                     patch.object(distiller, "persist_scratch"), patch.object(distiller.gateway, "generate",
                         side_effect=GatewayError(reason, observation)) as generate:
                    distiller.distill_frame(arguments, rpc_id=3)
                self.assertEqual(generate.call_count, 1)

    def test_cancel_and_absolute_or_reserve_deadline_never_start_another_attempt(self):
        for mode in ("cancel", "deadline", "reserve"):
            with self.subTest(mode=mode):
                distiller, arguments, prepared = self.live(None, request_id=MINIMAL,
                    recover_from_request_id=ORIGINAL)
                original = {"request_id": ORIGINAL, "post_utf8": prepared.post.decode(),
                    "selection": prepared.selection, "report": prepared.report,
                    "failures": [{"kind": "blocked", "reason": "upstream_stream_error"}]}
                def fail(*unused, **kwargs):
                    if mode == "cancel":
                        distiller.cancelled.set()
                    elif mode == "deadline":
                        distiller.business_deadline = time.monotonic() - 1
                    raise GatewayError("upstream_timeout", {"upstream_error_type": "upstream_timeout"})
                with patch.object(distiller, "_admit_live"), patch.object(distiller, "_read_scratch", return_value=original), \
                     patch.object(distiller, "persist_scratch"), patch.object(distiller.gateway, "generate", side_effect=fail) as generate:
                    if mode == "reserve":
                        distiller.policy["return_reserve_seconds"] = 300
                    with self.assertRaises(Exception):
                        distiller.distill_frame(arguments, rpc_id=3)
                self.assertEqual(generate.call_count, 0 if mode == "reserve" else 1)

    def test_minimal_identity_and_shorter_window_validation(self):
        distiller, arguments, _, _ = self.fixture()
        distiller.validate_v3(arguments, 3)
        for change in ({"request_id": MINIMAL + "-unknown"}, {"recover_from_request_id": EXTENDED},
                       {"host_timeout_seconds": 601}, {"host_timeout_seconds": True}):
            with self.subTest(change=change), self.assertRaises(Exception):
                distiller.validate_v3({**arguments, **change}, 3)
        now = int(time.time() * 1000)
        _, _, _, deadline = distiller.validate_v3({**arguments, "started_at_unix_ms": now,
            "deadline_unix_ms": now + 30000, "host_timeout_seconds": 30}, 3)
        self.assertLessEqual(deadline, time.monotonic() + 30)


class MinimalDriverTests(MinimalRecoveryTests):
    def contract(self):
        from test_schema_v3_extended_driver import ExtendedDriverTests
        contract = ExtendedDriverTests.contract(self)
        contract["minimal_recovery_contract"] = {
            "request_id": MINIMAL, "marker_id": MARKER, "recover_from_request_id": ORIGINAL,
            "previous_extended_request_id": EXTENDED,
            "previous_extended_receipt": driver.EXTENDED_RECEIPT,
            "previous_extended_postmortem": driver.EXTENDED_POSTMORTEM,
            "process_seconds": 600, "soft_target_seconds": 180,
            "return_reserve_seconds": 15, "max_retries": 3, "max_http_attempts": 4,
            "generation_controls": {"temperature": 0.2, "max_tokens": 3072},
            "retryable_reasons": ["upstream_stream_error", "upstream_timeout"]}
        return contract

    def test_read_only_driver_admission_and_prior_asset_preservation(self):
        _, _, _, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        with patch.object(driver, "ROOT", self.root), patch("socket.socket", side_effect=AssertionError("禁止联网")):
            bound = driver.verify_minimal_previous(self.contract())
            driver.verify_prior_preservation(bound)
        self.assertEqual(bound["previous_extended_marker_sha256"], digest(prior[8].read_bytes()))
        self.assertEqual([path.read_bytes() for path in prior], before)
        self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())

    def test_driver_contract_tampering_blocks(self):
        self.fixture()
        original = self.contract()
        for change in ({"max_retries": 4}, {"process_seconds": True}, {"request_id": EXTENDED},
                       {"generation_controls": {"temperature": True, "max_tokens": 3072}},
                       {"retryable_reasons": ["upstream_timeout"]}):
            contract = {**original, "minimal_recovery_contract": {
                **original["minimal_recovery_contract"], **change}}
            with self.subTest(change=change), patch.object(driver, "ROOT", self.root), self.assertRaises(Exception):
                driver.verify_minimal_previous(contract)

    def test_driver_namespace_exact_post_and_typed_retry_remain_separate(self):
        keys = ("READY", "LIVE", "RECOVERY_MARKER", "REQUEST_ID", "ADR_REVISION",
                "CONTRACT_REVISION", "DIAGNOSTIC", "EXTENDED", "MINIMAL", "PROCESS_SECONDS")
        before = {key: getattr(driver, key) for key in keys}
        with patch.dict(driver.__dict__, before):
            driver.configure_minimal()
            self.assertEqual(driver.REQUEST_ID, MINIMAL)
            self.assertEqual((driver.ADR_REVISION, driver.CONTRACT_REVISION), (13, 14))
            self.assertEqual(driver.PROCESS_SECONDS, 600)
            legacy = b'{"model":"fixture","messages":[],"stream":true}'
            with patch.object(driver, "read", return_value=legacy):
                self.assertEqual(driver.expected_live_post(), legacy[:-1] + b',"temperature":0.2,"max_tokens":3072}')
            self.assertTrue(driver.retry_allowed({"error_reason": "upstream_timeout", "upstream_error_type": "upstream_timeout"}))
            self.assertFalse(driver.retry_allowed({"error_reason": "upstream_timeout"}))
            driver.configure_extended()
            self.assertFalse(driver.MINIMAL)
            self.assertFalse(driver.retry_allowed({"error_reason": "upstream_timeout", "upstream_error_type": "upstream_timeout"}))
            driver.configure_diagnostic()
            self.assertFalse(driver.MINIMAL)
            self.assertEqual(driver.PROCESS_SECONDS, 240)
        self.assertEqual({key: getattr(driver, key) for key in keys}, before)


if __name__ == "__main__":
    unittest.main()
