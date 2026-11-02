"""600 秒新恢复的隔离正负控；不联网，不消耗真实实例标记。"""
from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from test_schema_v3_live_retry import LiveRetryTests, TRANSIENT
from test_distillation import Connection, Response, event
from solo_distill.gateway import Gateway
from solo_distill.preflight_v3 import INSTANCE, PARENT

ORIGINAL = INSTANCE + "-live-20261101"
PREVIOUS = INSTANCE + "-live-recovery-20261101"
DIAGNOSTIC = INSTANCE + "-live-diagnostic-20261101T133705Z"
EXTENDED = INSTANCE + "-live-extended-20261101T233105Z"
MARKER = INSTANCE + ".live-extended-20261101T233105Z"
DIAGNOSTIC_RECEIPT = "phase5-live-diagnostic-20261101T133705Z.receipt.json"
POSTMORTEM = "phase5-diagnostic-postmortem-20261101T135839Z.receipt.json"


class ExtendedRecoveryTests(LiveRetryTests):
    def fixture(self):
        distiller, arguments, prepared = self.live(Gateway(capture_stream=True, absolute_timeout=True),
            request_id=EXTENDED, recover_from_request_id=ORIGINAL)
        arguments["host_timeout_seconds"] = 600
        arguments["deadline_unix_ms"] = arguments["started_at_unix_ms"] + 600000
        distiller.persist_scratch(INSTANCE + ".live-admission", {
            "request_id": ORIGINAL, "automatic_replay": False})
        distiller.persist_scratch(INSTANCE + ".live-recovery-20261101", {
            "request_id": PREVIOUS, "recover_from_request_id": ORIGINAL,
            "automatic_replay": False, "max_retries": 3})
        distiller.persist_scratch(INSTANCE + ".live-diagnostic-20261101T133705Z", {
            "request_id": DIAGNOSTIC, "recover_from_request_id": ORIGINAL,
            "previous_recovery_request_id": PREVIOUS,
            "automatic_replay": False, "max_retries": 3})
        distiller.persist_scratch(ORIGINAL, {
            "request_id": ORIGINAL, "post_utf8": prepared.post.decode("utf-8"),
            "selection": prepared.selection, "report": prepared.report,
            "failures": [{"kind": "blocked", "reason": "upstream_stream_error"}]})
        parent = self.root / PARENT
        first = parent / "phase5-live-recovery-20261101.receipt.json"
        first.write_text(json.dumps({"status": "blocked", "execution_instance": INSTANCE,
            "request_id": PREVIOUS, "service_exit_code": 2,
            "service_cleanup": "confirmed_nonzero_exit", "business_call_attempts": 1}))
        evidence = parent / "phase5-live-diagnostic-20261101T133705Z.gateway.sse"
        evidence.write_bytes(b'data: {"partial":true}\n\n')
        receipt = parent / DIAGNOSTIC_RECEIPT
        receipt.write_text(json.dumps({"status": "blocked", "execution_instance": INSTANCE,
            "request_id": DIAGNOSTIC, "service_exit_code": 0,
            "service_cleanup": "confirmed_natural_exit", "business_call_attempts": 1,
            "binding": {
                "original_admission_sha256": hashlib.sha256((self.root / "verification/.scratch" /
                    (INSTANCE + ".live-admission.full.json")).read_bytes()).hexdigest(),
                "original_trace_sha256": hashlib.sha256((self.root / "verification/.scratch" /
                    (ORIGINAL + ".full.json")).read_bytes()).hexdigest(),
                "previous_marker_sha256": hashlib.sha256((self.root / "verification/.scratch" /
                    (INSTANCE + ".live-recovery-20261101.full.json")).read_bytes()).hexdigest(),
                "previous_receipt_sha256": hashlib.sha256(first.read_bytes()).hexdigest()},
            "assets": {"sse": {"path": str(evidence.relative_to(self.root)),
                "bytes": len(evidence.read_bytes()),
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "exact_readback": True}}}))
        postmortem = parent / POSTMORTEM
        postmortem.write_text(json.dumps({"execution_instance": INSTANCE,
            "online_acceptance": "blocked", "online_result": {
                "actual_exit_code": 2, "service_exit_code": 0,
                "service_cleanup": "confirmed_natural_exit",
                "original_receipt": str(receipt.relative_to(self.root)),
                "original_receipt_bytes": len(receipt.read_bytes()),
                "original_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}}))
        scratch = self.root / "verification/.scratch"
        prior = [scratch / (INSTANCE + ".live-admission.full.json"),
                 scratch / (INSTANCE + ".live-recovery-20261101.full.json"),
                 scratch / (INSTANCE + ".live-diagnostic-20261101T133705Z.full.json"),
                 scratch / (ORIGINAL + ".full.json"), first, receipt, postmortem, evidence]
        return distiller, arguments, prepared, prior

    def test_600_window_and_fixed_recovery_identity_without_network(self):
        distiller, arguments, _, _ = self.fixture()
        with patch("socket.socket", side_effect=AssertionError("offline")):
            distiller.validate_v3(arguments, 3)
        for change in ({"host_timeout_seconds": 601}, {"host_timeout_seconds": True},
                       {"deadline_unix_ms": arguments["started_at_unix_ms"] + 600001},
                       {"request_id": EXTENDED + "-unknown"},
                       {"recover_from_request_id": DIAGNOSTIC}, {"operation": "preflight"}):
            with self.subTest(change=change), self.assertRaises(Exception):
                distiller.validate_v3({**arguments, **change}, 3)

    def test_new_admission_links_all_prior_windows_and_is_exclusive(self):
        distiller, arguments, _, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        distiller._admit_live(arguments)
        marker = json.loads((self.root / "verification/.scratch" / (MARKER + ".full.json")).read_bytes())
        self.assertEqual(marker["request_id"], EXTENDED)
        self.assertEqual(marker["recover_from_request_id"], ORIGINAL)
        self.assertEqual(marker["previous_recovery_request_id"], PREVIOUS)
        self.assertEqual(marker["previous_diagnostic_request_id"], DIAGNOSTIC)
        self.assertEqual(marker["max_retries"], 3)
        self.assertEqual(marker["process_seconds"], 600)
        self.assertFalse(marker["automatic_replay"])
        with self.assertRaises(Exception):
            distiller._admit_live(arguments)
        self.assertEqual([path.read_bytes() for path in prior], before)

    def test_missing_each_prior_asset_rejects_before_new_slot_or_transport(self):
        distiller, arguments, _, prior = self.fixture()
        for path in prior:
            raw = path.read_bytes()
            path.unlink()
            try:
                with self.subTest(path=path.name), patch.object(distiller.gateway, "generate") as generate:
                    with self.assertRaises(Exception):
                        distiller._admit_live(arguments)
                    generate.assert_not_called()
                    self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())
            finally:
                path.write_bytes(raw)

    def test_changed_diagnostic_receipt_and_asset_are_not_overwritten(self):
        distiller, arguments, _, prior = self.fixture()
        receipt, evidence = prior[5], prior[7]
        receipt_raw, evidence_raw = receipt.read_bytes(), evidence.read_bytes()
        for path in (receipt, evidence):
            path.write_bytes(b"changed")
            with self.subTest(path=path.name), self.assertRaises(Exception):
                distiller._admit_live(arguments)
            self.assertEqual(path.read_bytes(), b"changed")
            self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())
            path.write_bytes(receipt_raw if path == receipt else evidence_raw)

    def test_missing_original_trace_does_not_consume_new_slot(self):
        distiller, arguments, _, prior = self.fixture()
        prior[3].unlink()
        with patch.object(distiller.gateway, "generate") as generate:
            with self.assertRaises(Exception):
                distiller.distill_frame(arguments, rpc_id=3)
        generate.assert_not_called()
        self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())

    def test_new_600_window_fourth_success_same_post_deadline_and_old_bytes(self):
        distiller, arguments, prepared, prior = self.fixture()
        before = [path.read_bytes() for path in prior]
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        success = event({"choices": [{"index": 0,
            "delta": {"content": json.dumps(candidate)}, "finish_reason": None}]}) + event({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"
        connections = [Connection(Response([stream])) for stream in [TRANSIENT] * 3 + [success]]
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect:
            distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        trace = self.trace(EXTENDED)
        self.assertEqual(trace["failures"], [])
        self.assertEqual(trace["candidate"], candidate)
        self.assertEqual(len(trace["model_outputs"]), 1)
        self.assertEqual(trace["live_timing"]["host_window_seconds"], 600)
        self.assertEqual(trace["live_timing"]["process_hard_ceiling_seconds"], 600)
        deadlines = {item["absolute_deadline"] for item in trace["gateway_requests"]}
        self.assertEqual(len(deadlines), 1)
        self.assertAlmostEqual(next(iter(deadlines)), distiller.business_deadline - 15)
        self.assertEqual([item["attempt_index"] for item in trace["gateway_requests"]], [1, 2, 3, 4])
        for number, connection in enumerate(connections, 1):
            self.assertEqual(connection.calls[0][1]["body"], prepared.post)
            self.assertTrue((self.root / "verification/.scratch" /
                (EXTENDED + ".gateway-attempt-" + str(number) + ".full.json")).exists())
        self.assertEqual([path.read_bytes() for path in prior], before)


if __name__ == "__main__":
    unittest.main()
