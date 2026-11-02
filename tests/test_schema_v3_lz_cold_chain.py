"""确定性字节表示的隔离冷链门禁；保留全部既有冷链断言。

仅隔离离线来源，不访问网关、不执行来源、不占用真实三样本或在线接纳。
六份原字节、完整发布双消费、失败现场及自然退出分别核验。
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import test_schema_v3_subtree_cold_chain as subtree_rules
import test_schema_v3_representation_cold_chain as representation_rules
from schema_v3_lz_oracle import ENCODING, pack as independent_pack
from schema_v3_subtree_oracle import ENCODING as SUBTREE_ENCODING
from solo_distill.adaptive_wire import TransportCapabilities
from solo_distill.wire_v3 import Admission, reassemble


class LzColdChainTests(subtree_rules.SubtreeColdChainTests):
    def setUp(self):
        super().setUp()
        # 两层继承方法分别读取所属模块全局值；两者均须替换为本版本。
        # 保留父类全部断言，不能仅因父类仍用旧协商而使负控假通过。
        for module in (subtree_rules, representation_rules):
            replacement = patch.object(module, "ENCODING", ENCODING)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_subtree_independent_negotiation_cannot_save_lz_success(self):
        module = representation_rules.driver()
        receive = module.independent_receive
        observed = []

        def wrong_version(*args, **kwargs):
            self.assertEqual(kwargs.get("wire_encoding"), ENCODING)
            observed.append(kwargs["wire_encoding"])
            kwargs["wire_encoding"] = SUBTREE_ENCODING
            return receive(*args, **kwargs)

        with patch.object(module, "independent_receive", side_effect=wrong_version):
            with self.assertRaises(ValueError):
                module.run_preflight(self.root, self.arguments(), "preflight-1")
        self.assertEqual(observed, [ENCODING])
        from solo_distill.preflight_v3 import PARENT
        self.assertFalse((self.root / PARENT / "preflight-1/preflight.receipt.json").exists())
        self.assertEqual(self.trace()["gateway_requests"], [])
        for path, raw in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), raw)

    def test_saved_full_frame_uses_exact_independent_lz_representation(self):
        module = representation_rules.driver()
        arguments = self.arguments()
        result = module.run_preflight(self.root, arguments, "preflight-1")
        from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT
        target = self.root / PARENT / "preflight-1"
        raw = (target / "transport.jsonl").read_bytes()
        trusted = self.trusted(self.trace(), arguments)
        independent = module.independent_receive(raw, self.files, arguments["transport"],
            protocol_version=arguments["protocol_version"], response_id=3,
            return_bytes=arguments["return_bytes"], wire_encoding=ENCODING,
            candidate=self.trace()["candidate"], **trusted)
        admission = Admission(arguments["protocol_version"], "A", 3,
            arguments["return_bytes"], TransportCapabilities.from_dict(arguments["transport"]),
            wire_encoding=ENCODING)
        decoded, manifest = reassemble(raw, admission)
        represented = json.loads(json.loads(decoded)["result"]["content"][0]["text"])
        self.assertEqual(represented, independent_pack(independent["publication"]))
        self.assertEqual(represented["encoding"], ENCODING)
        self.assertIsNotNone(manifest)
        self.assertEqual(result["wire_encoding"], ENCODING)
        self.assertEqual(set(result["save"]["artifact_sha256"]), set(ARTIFACT_NAMES))
        self.assertTrue(result["save"]["exact_readback"])
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["gateway_requests"], 0)
        self.assertEqual(json.loads((target / "sealed-core.json").read_bytes()),
                         independent["publication"]["core"])
        self.assertEqual(json.loads((target / "knowledge-package.json").read_bytes()),
                         independent["publication"]["core"]["package"])
        self.assertEqual(self.trace()["task"], independent["publication"]["task_evidence"])
        for path, original in self.files.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
