"""新子结构表示的隔离冷链门禁；保留全部既有冷链断言。

不访问网关、不执行来源、不占用真实来源三样本或唯一在线接纳。
生产驱动已按显式请求传递表示，不为新版本另建冷预检实例。
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import test_schema_v3_representation_cold_chain as rules
from schema_v3_representation_oracle import ENCODING as PREDECESSOR_ENCODING
from schema_v3_subtree_oracle import ENCODING


class SubtreeColdChainTests(rules.RepresentationColdChainTests):
    def setUp(self):
        super().setUp()
        # 父类方法运行时读取该模块全局值；全部继承断言实际验证新版本。
        replacement = patch.object(rules, "ENCODING", ENCODING)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_predecessor_independent_negotiation_cannot_save_new_encoding_success(self):
        module = rules.driver()
        receive = module.independent_receive
        observed = []

        def predecessor_receive(*args, **kwargs):
            self.assertEqual(kwargs.get("wire_encoding"), ENCODING)
            observed.append(kwargs["wire_encoding"])
            kwargs["wire_encoding"] = PREDECESSOR_ENCODING
            return receive(*args, **kwargs)

        with patch.object(module, "independent_receive", side_effect=predecessor_receive):
            with self.assertRaises(ValueError):
                module.run_preflight(self.root, self.arguments(), "preflight-1")
        self.assertEqual(observed, [ENCODING])
        from solo_distill.preflight_v3 import PARENT
        self.assertFalse((self.root / PARENT / "preflight-1/preflight.receipt.json").exists())
        self.assertEqual(self.trace()["gateway_requests"], [])
        for path, raw in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), raw)

    def test_saved_notifications_and_unique_final_manifest_keep_new_encoding(self):
        module = rules.driver()
        result = module.run_preflight(self.root, self.arguments(), "preflight-1")
        from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT
        target = self.root / PARENT / "preflight-1"
        self.assertEqual(set(result["save"]["artifact_sha256"]), set(ARTIFACT_NAMES))
        self.assertTrue(result["save"]["exact_readback"])
        raw = (target / "transport.jsonl").read_bytes()
        frames = [json.loads(line) for line in raw.splitlines()]
        self.assertGreater(len(frames), 1)
        self.assertEqual(sum("id" in frame for frame in frames), 1)
        for frame in frames[:-1]:
            self.assertNotIn("id", frame)
            self.assertEqual(frame["params"]["wire_encoding"], ENCODING)
        final = frames[-1]
        self.assertEqual(final["id"], 3)
        manifest = json.loads(final["result"]["content"][0]["text"])
        self.assertEqual(manifest["kind"], "chunk_manifest")
        self.assertEqual(manifest["manifest"]["wire_encoding"], ENCODING)
        self.assertEqual(manifest["manifest"]["count"], len(frames) - 1)
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertEqual(result["gateway_requests"], 0)
        self.assertEqual(json.loads((target / "preflight.receipt.json").read_bytes()), result)
        for path, original in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
