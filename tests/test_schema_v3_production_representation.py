"""生产无损表示的独立差分与完整负控；不联网、不读写来源。

继承全部规则回归时仅替换被测实现，独立 Oracle 保持单独引用。
本资产不证明完整传输、实际容量、冷预检、在线或人工语义。
"""
from __future__ import annotations

import copy
import random
import unittest
from unittest.mock import patch

import test_schema_v3_representation as rules
from schema_v3_representation_oracle import pack as independent_pack
from schema_v3_representation_oracle import unpack as independent_unpack
from solo_distill.representation_v3 import pack, unpack


class ProductionRepresentationTests(rules.RepresentationOracleTests):
    def setUp(self):
        # 原规则测试的每条正负控实际进入生产实现，不提前让 Oracle 遮蔽失败。
        for name, implementation in (("pack", pack), ("unpack", unpack)):
            replacement = patch.object(rules, name, implementation)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_independent_generated_encoding_and_both_cross_decoders_match(self):
        rng = random.Random(31062026)
        scalars = [None, True, False, 0, -1, 37, -901, "", "重复", "中文\r\n\"\\"]
        keys = ["a", "b", "文档", "", "正文"]

        def generate(depth):
            if depth == 0 or rng.randrange(4) == 0:
                return rng.choice(scalars)
            if rng.randrange(2):
                return [generate(depth - 1) for _ in range(rng.randrange(6))]
            return {key: generate(depth - 1)
                    for key in rng.sample(keys, rng.randrange(len(keys) + 1))}

        for number in range(200):
            value = generate(4)
            before = rules.canonical(value)
            with self.subTest(number=number):
                actual = pack(value)
                expected = independent_pack(value)
                self.assertEqual(rules.canonical(actual), rules.canonical(expected))
                self.assertEqual(rules.canonical(unpack(expected)), before)
                self.assertEqual(rules.canonical(independent_unpack(actual)), before)
                self.assertEqual(rules.canonical(value), before)

    def test_complete_independent_source_publication_keeps_core_and_task_identities(self):
        from test_schema_v3_wire_oracle import case
        _, _, publication = case()
        before = rules.canonical(publication)
        expected = independent_pack(publication)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            actual = pack(publication)
            restored = unpack(expected)
            independent = independent_unpack(actual)
        self.assertEqual(rules.canonical(actual), rules.canonical(expected))
        self.assertEqual(rules.canonical(restored), before)
        self.assertEqual(rules.canonical(independent), before)
        self.assertEqual(restored["core"]["core_id"], publication["core"]["core_id"])
        self.assertEqual(restored["task_evidence"]["evidence_id"],
                         publication["task_evidence"]["evidence_id"])

    def test_production_never_calls_independent_codec_and_vice_versa(self):
        value = [{"正文": "完全保留\n", "n": -7}] * 3
        with (
            patch("schema_v3_representation_oracle.pack", side_effect=AssertionError("禁止 Oracle")),
            patch("schema_v3_representation_oracle.unpack", side_effect=AssertionError("禁止 Oracle")),
            patch("socket.socket", side_effect=AssertionError("禁止联网")),
        ):
            self.assertEqual(unpack(pack(value)), value)
        with (
            patch("solo_distill.representation_v3.pack", side_effect=AssertionError("禁止生产")),
            patch("solo_distill.representation_v3.unpack", side_effect=AssertionError("禁止生产")),
        ):
            self.assertEqual(independent_unpack(independent_pack(value)), value)

    def test_repeated_record_expansion_has_no_shared_mutable_children(self):
        original = [{"a": {"b": [1, -1, "正文"]}}] * 4
        document = independent_pack(original)
        saved = copy.deepcopy(document)
        restored = unpack(document)
        restored[0]["a"]["b"].clear()
        self.assertEqual(restored[1]["a"]["b"], [1, -1, "正文"])
        self.assertEqual(document, saved)


if __name__ == "__main__":
    unittest.main()
