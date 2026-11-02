"""V3 有界恢复正负控；模拟 HTTP，不联网、不占用真实接纳。"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch

from test_distillation import Connection, Response, event, normal_stream
from test_schema_v3_stdio import Fixture
from solo_distill.gateway import Gateway, GatewayError, encode_request
from solo_distill.preflight_v3 import INSTANCE

TRANSIENT = event({"error": {"message": "Upstream stream ended before completion",
                             "type": "upstream_stream_error"}})


class GatewayClassificationTests(unittest.TestCase):
    def test_only_typed_error_is_retryable_and_partial_capture_is_exact(self):
        for stream, expected in (
                (TRANSIENT, "upstream_stream_error"),
                (b"event: error\n" + TRANSIENT, "upstream_stream_error"),
                (event({"error": {"type": "authentication_required"}}), "upstream_error"),
                (event({"error": {"message": "unknown"}}), "upstream_error"),
                (b"event: error\n\n", "stream_ended_without_done"),
                (event([1]), "invalid_sse_object")):
            connection = Connection(Response([stream]))
            with self.subTest(stream=stream), patch(
                    "solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
                with self.assertRaises(GatewayError) as caught:
                    Gateway(capture_stream=True).generate([], time.monotonic() + 5, threading.Event())
            self.assertEqual(caught.exception.reason, expected)
            self.assertEqual(base64.b64decode(caught.exception.observations["raw_sse_base64"]), stream)
            self.assertTrue(connection.closed)
            self.assertEqual(len(connection.calls), 1)

    def test_absolute_timeout_opt_in_and_legacy_default_remain_separate(self):
        for absolute, expected in ((False, 10.0), (True, 230.0)):
            connection = Connection(Response([normal_stream()]))
            with patch("solo_distill.gateway.time.monotonic", return_value=100.0), patch(
                    "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as constructor:
                Gateway(absolute_timeout=absolute).generate([], 330.0, threading.Event())
            self.assertEqual(constructor.call_args.kwargs["timeout"], expected)


class LiveRetryTests(Fixture):
    def live(self, gateway, **changes):
        from solo_distill.preparation_v3 import prepare_snapshot
        from solo_distill.service_v3 import V3Distiller
        arguments = self.arguments()
        prepared = prepare_snapshot(project=self.project, include=(".",),
            question=arguments["question"], dimensions=arguments["dimensions"],
            deadline=time.monotonic() + 5, cancelled=threading.Event())
        now = int(time.time() * 1000)
        arguments.update(operation="live", frozen_selection=prepared.selection,
            host_timeout_seconds=240, started_at_unix_ms=now, deadline_unix_ms=now + 240000)
        arguments.update(changes)
        distiller = V3Distiller({"fixture": self.project}, self.policy, gateway,
            artifact_root=self.root, online=True)
        return distiller, arguments, prepared

    def test_three_transient_errors_then_fourth_success_same_post_and_no_partial_merge(self):
        candidate = {"summary": "", "conclusions": [], "unknowns": []}
        partial = event({"choices": [{"index": 0, "delta": {"content": "discard-me"},
                                      "finish_reason": None}]}) + TRANSIENT
        success = event({"choices": [{"index": 0, "delta": {"content": json.dumps(candidate)},
                                      "finish_reason": None}]}) + event({"choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"
        streams = [partial] * 3 + [success]
        connections = [Connection(Response([stream])) for stream in streams]
        distiller, arguments, prepared = self.live(Gateway(capture_stream=True, absolute_timeout=True))
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections):
            distiller.distill_frame(arguments, rpc_id=3)
        trace = self.trace()
        self.assertEqual(trace["failures"], [])
        self.assertEqual(trace["candidate"], candidate)
        self.assertEqual(len(trace["model_outputs"]), 1)
        self.assertNotIn("discard-me", trace["model_outputs"][0])
        attempts = trace["gateway_requests"]
        self.assertEqual(len(attempts), 4)
        self.assertEqual([item["client_auto_retries"] for item in attempts], [0, 1, 2, 3])
        self.assertEqual([item["attempt_index"] for item in attempts], [1, 2, 3, 4])
        self.assertEqual([base64.b64decode(item["raw_sse_base64"]) for item in attempts], streams)
        self.assertEqual(len({item["absolute_deadline"] for item in attempts}), 1)
        for connection, observation in zip(connections, attempts):
            self.assertTrue(connection.closed)
            self.assertEqual(connection.calls[0][1]["body"], prepared.post)
            self.assertEqual(observation["message_sha256"], hashlib.sha256(prepared.post).hexdigest())
        self.assertTrue(attempts[-1]["done_marker"])
        self.assertEqual(attempts[-1]["finish_reason"], "stop")
        self.assertTrue((self.root / "verification/.scratch" / (INSTANCE + ".live-admission.full.json")).exists())

    def test_exhaustion_stops_after_four_without_success(self):
        connections = [Connection(Response([TRANSIENT])) for _ in range(4)]
        distiller, arguments, _ = self.live(Gateway(capture_stream=True))
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as constructor:
            distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(constructor.call_count, 4)
        trace = self.trace()
        self.assertEqual(trace["model_outputs"], [])
        self.assertEqual(trace["failures"], [{"kind": "blocked", "reason": "upstream_stream_error"}])
        self.assertEqual(len(trace["gateway_requests"]), 4)

    def test_nonretryable_errors_do_not_consume_retry_allowance(self):
        for reason in ("authentication_required", "gateway_http_error", "upstream_error",
                       "stream_ended_without_done", "non_normal_finish", "gateway_deadline_exhausted"):
            with self.subTest(reason=reason):
                gateway = Gateway()
                distiller, arguments, _ = self.live(gateway, request_id="nonretry-" + reason)
                # Each subcase uses a different isolated admission namespace via mock,
                # not the actual instance marker.
                with patch.object(distiller, "_admit_live"), patch.object(gateway, "generate",
                        side_effect=GatewayError(reason, {"outer_replays": 0})) as generate:
                    distiller.distill_frame(arguments, rpc_id=3)
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(self.trace(arguments["request_id"])["model_outputs"], [])

    def test_cancel_and_deadline_between_attempts_never_send_again(self):
        for mode in ("cancel", "deadline"):
            with self.subTest(mode=mode):
                gateway = Gateway()
                distiller, arguments, _ = self.live(gateway, request_id="guard-" + mode)
                def fail(*unused):
                    if mode == "cancel":
                        distiller.cancelled.set()
                    else:
                        distiller.business_deadline = time.monotonic() - 1
                    raise GatewayError("upstream_stream_error", {"upstream_error_type": "upstream_stream_error"})
                with patch.object(distiller, "_admit_live"), patch.object(gateway, "generate", side_effect=fail) as generate:
                    with self.assertRaises(Exception):
                        distiller.distill_frame(arguments, rpc_id=3)
                self.assertEqual(generate.call_count, 1)
                self.assertFalse((self.root / "verification/.scratch" / (arguments["request_id"] + ".full.json")).exists())

    def test_reason_string_without_typed_observation_is_not_retried(self):
        gateway = Gateway()
        distiller, arguments, _ = self.live(gateway)
        with patch.object(gateway, "generate", side_effect=GatewayError("upstream_stream_error")) as generate:
            distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(generate.call_count, 1)

    def test_recovery_missing_original_and_preflight_field_are_rejected(self):
        distiller, arguments, _ = self.live(Gateway(), request_id=INSTANCE + "-live-recovery-20261101",
            recover_from_request_id=INSTANCE + "-live-20261101")
        with patch.object(distiller.gateway, "generate") as generate:
            with self.assertRaises(Exception):
                distiller.distill_frame(arguments, rpc_id=3)
        generate.assert_not_called()
        with self.assertRaises(Exception):
            self.distiller().validate_v3(self.arguments(recover_from_request_id=INSTANCE + "-live-20261101"), 3)

    def test_recovery_retains_original_marker_and_is_exclusive(self):
        gateway = Gateway()
        distiller, arguments, _ = self.live(gateway, request_id=INSTANCE + "-live-recovery-20261101",
            recover_from_request_id=INSTANCE + "-live-20261101")
        old = {"request_id": INSTANCE + "-live-20261101", "automatic_replay": False}
        distiller.persist_scratch(INSTANCE + ".live-admission", old)
        raw = (self.root / "verification/.scratch" / (INSTANCE + ".live-admission.full.json")).read_bytes()
        distiller._admit_live(arguments)
        self.assertEqual((self.root / "verification/.scratch" / (INSTANCE + ".live-admission.full.json")).read_bytes(), raw)
        marker = json.loads((self.root / "verification/.scratch" / (INSTANCE + ".live-recovery-20261101.full.json")).read_bytes())
        self.assertEqual(marker["recover_from_request_id"], old["request_id"])
        with self.assertRaises(Exception):
            distiller._admit_live(arguments)


if __name__ == "__main__":
    unittest.main()
