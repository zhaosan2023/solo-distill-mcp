"""显式命名的 V3 发布 API 与真实 22,016 字节物理分片边界。"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import canonical
from schema_v3_wire_oracle import receive
from test_schema_v3_wire_oracle import CAPS, OBSERVATIONS, SOURCES, case, encode
from test_schema_v3_wire import admission
from solo_distill.core_v3 import SealedCore
from solo_distill.task_v3 import build_task
from solo_distill.wire_v2 import FrozenFrame, FrozenTransfer
from solo_distill.wire_v3 import publish, publish_v3, reassemble


class PublishV3ApiTests(unittest.TestCase):
    def test_named_api_preserves_existing_v3_api_and_single_frame(self):
        candidate, trusted, report = case()
        core = SealedCore(canonical(report["core"]))
        task = build_task(candidate, core, SOURCES, **trusted)
        before = copy.deepcopy((candidate, task, trusted))
        first = publish_v3(core, task, SOURCES, admission(), candidate=candidate,
                           observations=OBSERVATIONS, **trusted)
        old_named = publish(core, task, SOURCES, admission(), candidate=candidate,
                            observations=OBSERVATIONS, **trusted)
        self.assertIs(type(first), FrozenFrame)
        self.assertEqual(first.data, old_named.data)
        self.assertEqual(first.data, encode(report))
        self.assertEqual((candidate, task, trusted), before)

    def test_default_22016_profile_fragments_and_independent_receiver_authenticates(self):
        candidate, trusted, report = case(lambda c: c.update(summary="真实分片夹具" * 5000))
        core = SealedCore(canonical(report["core"]))
        task = build_task(candidate, core, SOURCES, **trusted)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            transfer = publish_v3(core, task, SOURCES, admission(), candidate=candidate,
                                  observations=OBSERVATIONS, **trusted)
            received = receive(transfer.data, SOURCES, CAPS, protocol_version="2024-11-05",
                response_id=7, return_bytes=22016, candidate=candidate,
                observations=OBSERVATIONS, **trusted)
        self.assertIs(type(transfer), FrozenTransfer)
        self.assertGreater(len(encode(report)), 22016)
        self.assertLessEqual(max(map(len, transfer.frames)), 22016)
        self.assertLessEqual(len(transfer.data), 1572864)
        self.assertLessEqual(len(transfer.frames) - 1, 512)
        decoded, manifest = reassemble(transfer.data, admission())
        self.assertEqual(decoded, encode(report))
        self.assertEqual(received["publication"], report)
        self.assertEqual(manifest["manifest"]["count"], len(transfer.frames) - 1)
        with self.assertRaises(ValueError):
            publish_v3(core, task, SOURCES, admission({**CAPS, "chunking": False}),
                       candidate=candidate, observations=OBSERVATIONS, **trusted)

    def test_cancellation_during_encoding_not_only_at_entry(self):
        candidate, trusted, report = case(lambda c: c.update(summary="x" * 90000))
        core = SealedCore(canonical(report["core"]))
        task = build_task(candidate, core, SOURCES, **trusted)
        calls = 0
        def tick():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("encoding_cancelled")
        # 精确隔离逐片编码回调；认证已由其他生产及独立负控覆盖。
        with patch("solo_distill.wire_v3.build_publication", return_value=report):
            with self.assertRaisesRegex(RuntimeError, "encoding_cancelled"):
                publish_v3(core, task, SOURCES, admission(), candidate=candidate,
                    observations=OBSERVATIONS, **{**trusted, "tick": tick})
        self.assertEqual(calls, 3)


if __name__ == "__main__":
    unittest.main()
