"""显式无损表示的隔离冷链门禁；不占用真实来源三样本或在线接纳。

继承完整冷链保存、取消与失败现场负控，仅显式替换请求表示。
独立消费仍使用独立编解码器；不证明真实容量、人工语义或宿主加载。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from schema_v3_representation_oracle import ENCODING
from test_schema_v3_cold_chain import ColdChainTests, driver


class RepresentationColdChainTests(ColdChainTests):
    def arguments(self, **changes):
        return super().arguments(**{"wire_encoding": ENCODING, **changes})

    def test_wire_request_keeps_explicit_encoding_and_complete_external_witness(self):
        module = driver()
        witness = {"selection_id": "external", "witness": {"full": "x" * 70000}}
        arguments = self.arguments(frozen_selection=witness)
        before = copy.deepcopy(arguments)
        wire, count = module.preflight_wire_arguments(arguments, witness)
        self.assertEqual(wire["wire_encoding"], ENCODING)
        self.assertNotIn("frozen_selection", wire)
        self.assertLessEqual(count, 65536)
        self.assertEqual(arguments, before)
        self.assertEqual(arguments["frozen_selection"], witness)

    def test_explicit_independent_consumer_and_saved_original_objects_agree(self):
        module = driver()
        arguments = self.arguments(request_id="representation-cold-fixture")
        before = copy.deepcopy(arguments)
        receive = module.independent_receive
        observed = []

        def checked_receive(*args, **kwargs):
            self.assertEqual(kwargs.get("wire_encoding"), ENCODING)
            result = receive(*args, **kwargs)
            observed.append(result)
            return result

        with patch.object(module, "independent_receive", side_effect=checked_receive):
            result = module.run_preflight(self.root, arguments, "preflight-1")
        self.assertEqual(len(observed), 1)
        self.assertEqual(arguments, before)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertEqual(result["gateway_requests"], 0)
        self.assertTrue(result["save"]["exact_readback"])
        from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT
        target = self.root / PARENT / "preflight-1"
        self.assertEqual(set(result["save"]["artifact_sha256"]), set(ARTIFACT_NAMES))
        core = json.loads((target / "sealed-core.json").read_bytes())
        package = json.loads((target / "knowledge-package.json").read_bytes())
        oracle = json.loads((target / "independent-oracle.receipt.json").read_bytes())
        trace = self.trace("representation-cold-fixture")
        self.assertEqual(core, observed[0]["publication"]["core"])
        self.assertEqual(package, core["package"])
        self.assertEqual(trace["core"], core)
        self.assertEqual(trace["task"], observed[0]["publication"]["task_evidence"])
        self.assertEqual(trace["post_utf8"].encode("utf-8"),
                         (target / "exact-http-post.json").read_bytes())
        self.assertEqual(oracle["wire_encoding"], ENCODING)
        self.assertEqual(result["wire_encoding"], ENCODING)
        self.assertEqual(oracle["decoded_bytes"], observed[0]["decoded_bytes"])
        transport = (target / "transport.jsonl").read_bytes()
        self.assertEqual(len(transport), observed[0]["transfer_bytes"])
        delivery = json.loads((self.root / "verification/.scratch/representation-cold-fixture.delivery.full.json").read_bytes())
        self.assertEqual(delivery["written"], len(transport))
        for frame in transport.splitlines():
            message = json.loads(frame)
            if "id" not in message:
                self.assertEqual(message["params"]["wire_encoding"], ENCODING)
        for path, raw in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), raw)

    def test_independent_wrong_encoding_cannot_save_success(self):
        module = driver()
        receive = module.independent_receive

        def wrong_encoding(*args, **kwargs):
            kwargs["wire_encoding"] = "json"
            return receive(*args, **kwargs)

        with patch.object(module, "independent_receive", side_effect=wrong_encoding):
            with self.assertRaises(ValueError):
                module.run_preflight(self.root, self.arguments(), "preflight-1")
        from solo_distill.preflight_v3 import PARENT
        self.assertFalse((self.root / PARENT / "preflight-1/preflight.receipt.json").exists())
        self.assertEqual(self.trace()["gateway_requests"], [])
        for path, raw in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
