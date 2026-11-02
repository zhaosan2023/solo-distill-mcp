"""单网关微创恢复的封闭请求正负控；仅模拟传输，不联网。"""
from __future__ import annotations

import base64
import json
import threading
import time
import unittest
from unittest.mock import patch

from test_distillation import Connection, Response, event, normal_stream
from solo_distill.gateway import Gateway, GatewayError, encode_request
from solo_distill.task_v3 import _check_frozen_post


class MinimalGatewayTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "user", "content": "证据\\正文\n"}]
        self.legacy = json.dumps({"model": "gpt-6-sol", "messages": self.messages,
            "stream": True}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":")).encode("utf-8")
        self.controlled = json.dumps({"model": "gpt-6-sol", "messages": self.messages,
            "stream": True, "temperature": 0.2, "max_tokens": 3072},
            ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")

    def test_default_encoding_preserves_exact_legacy_bytes(self):
        self.assertEqual(encode_request(self.messages), self.legacy)
        self.assertEqual(encode_request(self.messages, generation_controls=False), self.legacy)

    def test_explicit_controls_have_exact_fixed_bytes_and_unchanged_messages(self):
        self.assertEqual(encode_request(self.messages, generation_controls=True), self.controlled)
        self.assertEqual(json.loads(self.controlled)["messages"], self.messages)
        self.assertEqual(len(self.controlled) - len(self.legacy),
                         len(b',"temperature":0.2,"max_tokens":3072'))

    def test_control_switch_rejects_nonboolean_types(self):
        for value in (None, 0, 1, "true", {}, {"temperature": 0.2}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_request(self.messages, generation_controls=value)

    def test_gateway_sends_controlled_request_only_when_explicit(self):
        for enabled, expected in ((False, self.legacy), (True, self.controlled)):
            connection = Connection(Response([normal_stream()]))
            with self.subTest(enabled=enabled), patch(
                    "solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
                output, observation = Gateway(capture_stream=True).generate(
                    self.messages, time.monotonic() + 5, threading.Event(),
                    generation_controls=enabled)
            self.assertTrue(output.strip())
            self.assertEqual(connection.calls[0][1]["body"], expected)
            self.assertEqual(observation["message_bytes"], len(expected))
            self.assertEqual(observation["finish_reason"], "stop")
            self.assertTrue(observation["done_marker"])
            self.assertTrue(connection.closed)

    def test_only_parsed_upstream_timeout_gets_typed_classification(self):
        for payload, reason in (({"error": {"type": "upstream_timeout"}}, "upstream_timeout"),
                ({"error": {"type": "upstream_stream_error"}}, "upstream_stream_error"),
                ({"error": {"message": "upstream_timeout"}}, "upstream_error"),
                ({"error": {"type": "authentication_required"}}, "upstream_error"),
                ({"error": {"type": ["upstream_timeout"]}}, "upstream_error")):
            raw = event(payload)
            connection = Connection(Response([raw]))
            with self.subTest(payload=payload), patch(
                    "solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
                with self.assertRaises(GatewayError) as caught:
                    Gateway(capture_stream=True).generate(
                        self.messages, time.monotonic() + 5, threading.Event())
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(base64.b64decode(caught.exception.observations["raw_sse_base64"]), raw)
            self.assertTrue(connection.closed)

    def test_generation_cap_length_finish_is_not_accepted_as_stop(self):
        raw = event({"choices": [{"index": 0, "delta": {"content": "partial"},
                                  "finish_reason": "length"}]}) + b"data: [DONE]\n\n"
        connection = Connection(Response([raw]))
        with patch("solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
            with self.assertRaises(GatewayError) as caught:
                Gateway(capture_stream=True).generate(self.messages,
                    time.monotonic() + 5, threading.Event(), generation_controls=True)
        self.assertEqual(caught.exception.reason, "non_normal_finish")
        self.assertNotEqual(caught.exception.observations["finish_reason"], "stop")

    def test_frozen_post_authenticates_both_closed_envelopes(self):
        _check_frozen_post(self.legacy, self.legacy)
        _check_frozen_post(self.controlled, self.legacy)

    def test_frozen_post_rejects_parameter_source_type_and_encoding_tampering(self):
        base = json.loads(self.controlled)
        variants = [self.controlled.replace(b'"temperature":0.2', b'"temperature":0.3'),
            self.controlled.replace(b'"temperature":0.2', b'"temperature":true'),
            self.controlled.replace(b'"max_tokens":3072', b'"max_tokens":3073'),
            self.controlled.replace(b'"max_tokens":3072', b'"max_tokens":3072.0'),
            self.controlled.replace(b',"max_tokens":3072', b''),
            self.controlled.replace(b',"temperature":0.2', b''),
            self.controlled.replace(b'"temperature":0.2', b'"temperature":0.2,"temperature":0.2'),
            json.dumps(base, ensure_ascii=False).encode("utf-8"),
            json.dumps({**base, "seed": 1}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            json.dumps({**base, "messages": [{"role": "user", "content": "changed"}]},
                       separators=(",", ":")).encode("utf-8")]
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                _check_frozen_post(raw, self.legacy)


if __name__ == "__main__":
    unittest.main()
