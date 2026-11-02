"""显式诊断恢复接纳正负控；只用隔离来源，不联网或消耗真实标记。"""
from __future__ import annotations

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
MARKER = INSTANCE + ".live-diagnostic-20261101T133705Z"


class DiagnosticRecoveryTests(LiveRetryTests):
    def fixture(self):
        distiller, arguments, prepared = self.live(Gateway(),
            request_id=DIAGNOSTIC, recover_from_request_id=ORIGINAL)
        distiller.persist_scratch(INSTANCE + ".live-admission", {
            "request_id": ORIGINAL, "automatic_replay": False})
        distiller.persist_scratch(INSTANCE + ".live-recovery-20261101", {
            "request_id": PREVIOUS, "recover_from_request_id": ORIGINAL,
            "automatic_replay": False, "max_retries": 3})
        receipt = {"status": "blocked", "execution_instance": INSTANCE,
            "request_id": PREVIOUS, "service_exit_code": 2,
            "service_cleanup": "confirmed_nonzero_exit", "business_call_attempts": 1}
        path = self.root / PARENT / "phase5-live-recovery-20261101.receipt.json"
        path.write_text(json.dumps(receipt))
        return distiller, arguments, prepared, path

    def test_fixed_diagnostic_identity_passes_validation_without_network(self):
        distiller, arguments, _, _ = self.fixture()
        with patch("socket.socket", side_effect=AssertionError("offline")):
            distiller.validate_v3(arguments, 3)
        for change in ({"request_id": DIAGNOSTIC + "-unknown"},
                       {"recover_from_request_id": PREVIOUS},
                       {"operation": "preflight"},
                       {"host_timeout_seconds": 601}):
            with self.subTest(change=change), self.assertRaises(Exception):
                distiller.validate_v3({**arguments, **change}, 3)

    def test_diagnostic_admission_links_both_old_requests_and_is_exclusive(self):
        distiller, arguments, _, path = self.fixture()
        scratch = self.root / "verification/.scratch"
        old_paths = [scratch / (INSTANCE + ".live-admission.full.json"),
                     scratch / (INSTANCE + ".live-recovery-20261101.full.json"), path]
        before = [item.read_bytes() for item in old_paths]
        distiller._admit_live(arguments)
        marker = json.loads((scratch / (MARKER + ".full.json")).read_bytes())
        self.assertEqual(marker["request_id"], DIAGNOSTIC)
        self.assertEqual(marker["recover_from_request_id"], ORIGINAL)
        self.assertEqual(marker["previous_recovery_request_id"], PREVIOUS)
        self.assertEqual(marker["max_retries"], 3)
        self.assertFalse(marker["automatic_replay"])
        with self.assertRaises(Exception):
            distiller._admit_live(arguments)
        self.assertEqual([item.read_bytes() for item in old_paths], before)

    def test_missing_previous_marker_does_not_consume_new_slot(self):
        distiller, arguments, _, _ = self.fixture()
        (self.root / "verification/.scratch" /
         (INSTANCE + ".live-recovery-20261101.full.json")).unlink()
        with self.assertRaises(Exception):
            distiller._admit_live(arguments)
        self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())

    def test_conflicting_previous_receipt_does_not_consume_new_slot(self):
        distiller, arguments, _, path = self.fixture()
        original = json.loads(path.read_bytes())
        for change in ({"request_id": ORIGINAL}, {"status": "pass"},
                       {"service_exit_code": 0}, {"service_exit_code": True},
                       {"service_cleanup": "forced_kill_not_gate_pass"}):
            path.write_text(json.dumps({**original, **change}))
            with self.subTest(change=change), self.assertRaises(Exception):
                distiller._admit_live(arguments)
            self.assertFalse((self.root / "verification/.scratch" / (MARKER + ".full.json")).exists())

    def test_diagnostic_fourth_attempt_success_preserves_prior_evidence(self):
        distiller, arguments, prepared, path = self.fixture()
        distiller.persist_scratch(ORIGINAL, {
            "request_id": ORIGINAL, "post_utf8": prepared.post.decode("utf-8"),
            "selection": prepared.selection, "report": prepared.report,
            "failures": [{"kind": "blocked", "reason": "upstream_stream_error"}]})
        scratch = self.root / "verification/.scratch"
        old_paths = [scratch / (INSTANCE + ".live-admission.full.json"),
                     scratch / (INSTANCE + ".live-recovery-20261101.full.json"),
                     scratch / (ORIGINAL + ".full.json"), path]
        before = [item.read_bytes() for item in old_paths]
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        success = event({"choices": [{"index": 0,
            "delta": {"content": json.dumps(candidate)}, "finish_reason": None}]}) + event({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"
        connections = [Connection(Response([stream])) for stream in [TRANSIENT] * 3 + [success]]
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as connect:
            distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(connect.call_count, 4)
        trace = self.trace(DIAGNOSTIC)
        self.assertEqual(trace["failures"], [])
        self.assertEqual(trace["candidate"], candidate)
        self.assertEqual(len(trace["model_outputs"]), 1)
        self.assertEqual(len({item["absolute_deadline"] for item in trace["gateway_requests"]}), 1)
        self.assertEqual([item["attempt_index"] for item in trace["gateway_requests"]], [1, 2, 3, 4])
        for number, connection in enumerate(connections, 1):
            self.assertEqual(connection.calls[0][1]["body"], prepared.post)
            self.assertTrue((scratch / (DIAGNOSTIC + ".gateway-attempt-" + str(number) + ".full.json")).exists())
        self.assertEqual([item.read_bytes() for item in old_paths], before)

    def test_missing_original_trace_prevents_any_transport(self):
        distiller, arguments, _, _ = self.fixture()
        with patch.object(distiller.gateway, "generate") as generate:
            with self.assertRaises(Exception):
                distiller.distill_frame(arguments, rpc_id=3)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
