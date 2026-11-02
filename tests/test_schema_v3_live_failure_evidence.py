"""隔离失败证据回归；不联网、不消耗真实恢复接纳，不放宽候选契约。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import unittest
from unittest.mock import patch

from test_schema_v3_live_retry import LiveRetryTests, TRANSIENT
from test_distillation import Connection, Response, event, normal_stream
from test_schema_v3_runtime_gates import RuntimeBoundaryTests
from solo_distill.gateway import Gateway
from solo_distill.extractor import InputError
from solo_distill import server_v3


class TransportJournalTests(LiveRetryTests):
    def journal(self, request_id, number):
        return json.loads((self.root / "verification/.scratch" /
            (request_id + ".gateway-attempt-" + str(number) + ".full.json")).read_bytes())

    def test_normal_stream_invalid_candidate_keeps_exact_transport_without_retry(self):
        connection = Connection(Response([normal_stream()]))
        distiller, arguments, prepared = self.live(Gateway(capture_stream=True))
        with patch("solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as constructor:
            with self.assertRaisesRegex(ValueError, "v3_task_candidate_fields"):
                distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(constructor.call_count, 1)
        saved = self.journal(arguments["request_id"], 1)
        self.assertEqual(saved["status"], "transport_completed_not_business_acceptance")
        self.assertEqual(saved["output"], '{"ok":true}')
        self.assertEqual(saved["post_utf8"].encode(), prepared.post)
        self.assertEqual(saved["post_sha256"], hashlib.sha256(prepared.post).hexdigest())
        self.assertEqual(base64.b64decode(saved["observation"]["raw_sse_base64"]), normal_stream())
        self.assertFalse((self.root / "verification/.scratch" /
            (arguments["request_id"] + ".full.json")).exists())

    def test_transient_journal_survives_next_nonretryable_failure(self):
        connections = [Connection(Response([TRANSIENT])), Connection(Response([event({"error": {"type": "auth"}})]))]
        distiller, arguments, prepared = self.live(Gateway(capture_stream=True))
        with patch("solo_distill.gateway.http.client.HTTPConnection", side_effect=connections) as constructor:
            distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(constructor.call_count, 2)
        for number, reason in ((1, "upstream_stream_error"), (2, "upstream_error")):
            saved = self.journal(arguments["request_id"], number)
            self.assertEqual(saved["status"], "transport_failed_not_business_acceptance")
            self.assertEqual(saved["observation"]["error_reason"], reason)
            self.assertEqual(saved["post_utf8"].encode(), prepared.post)
        self.assertEqual(self.trace()["failures"], [{"kind": "blocked", "reason": "upstream_error"}])

    def test_failed_journal_save_prevents_retry_and_never_claims_success(self):
        connection = Connection(Response([TRANSIENT]))
        distiller, arguments, _ = self.live(Gateway(capture_stream=True))
        original = distiller.persist_scratch
        def fail_journal(request_id, value):
            if ".gateway-attempt-" in request_id:
                raise OSError("isolated_save_failure")
            return original(request_id, value)
        with patch.object(distiller, "persist_scratch", side_effect=fail_journal), patch(
                "solo_distill.gateway.http.client.HTTPConnection", return_value=connection) as constructor:
            with self.assertRaisesRegex(OSError, "isolated_save_failure"):
                distiller.distill_frame(arguments, rpc_id=3)
        self.assertEqual(constructor.call_count, 1)
        self.assertFalse((self.root / "verification/.scratch" /
            (arguments["request_id"] + ".full.json")).exists())


class ServerFailureEvidenceTests(RuntimeBoundaryTests):
    def test_unhandled_local_error_saves_bounded_failure_before_stop(self):
        distiller = self.distiller()
        output = io.StringIO()
        with patch.object(distiller, "distill_frame", side_effect=ValueError("v3_task_candidate_fields")), \
                patch.object(server_v3.sys, "stderr", output):
            with self.transport(distiller) as (send, receive, end, thread, results):
                send(self.call_message(self.arguments()))
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [2])
        path = self.root / "verification/.scratch/stdio-fixture.failure.full.json"
        saved = json.loads(path.read_bytes())
        self.assertEqual(saved["status"], "failed_not_business_acceptance")
        self.assertEqual(saved["error_type"], "ValueError")
        self.assertEqual(saved["reason"], "v3_task_candidate_fields")
        self.assertLessEqual(len(saved["stack"]), 8)
        self.assertLessEqual(len(path.read_bytes()), 4096)
        self.assertIn("v3_task_candidate_fields", output.getvalue())
        self.assertFalse((self.root / "verification/.scratch/stdio-fixture.delivery.full.json").exists())

    def test_failure_description_does_not_expose_arbitrary_error_text(self):
        saved = server_v3._failure_details(ValueError("secret credential=do-not-print"))
        self.assertEqual(saved["reason"], "unclassified_error_text_omitted")
        self.assertNotIn("credential", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
