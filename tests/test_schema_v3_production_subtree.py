"""新表示生产实现的完整规则回归、独立差分及交叉解码。

不导入来源、不联网、不占用真实冷预检或在线接纳。
继承全部独立规则断言，仅切换被测编解码器；独立引用保留。
"""
from __future__ import annotations

import copy
import random
import unittest
from unittest.mock import patch

import test_schema_v3_representation as base_rules
import test_schema_v3_subtree as subtree_rules
from schema_v3_subtree_oracle import pack as independent_pack
from schema_v3_subtree_oracle import unpack as independent_unpack
from solo_distill.subtree_v3 import pack, unpack


class ProductionSubtreeTests(subtree_rules.SubtreeOracleTests):
    def setUp(self):
        super().setUp()
        # 两层继承规则都实际调用生产实现；独立别名不被替换。
        for module in (base_rules, subtree_rules):
            for name, implementation in (("pack", pack), ("unpack", unpack)):
                replacement = patch.object(module, name, implementation)
                replacement.start()
                self.addCleanup(replacement.stop)

    def test_independent_complete_encodings_and_cross_decoders_for_generated_values(self):
        rng = random.Random(202610310829)
        scalars = [None, True, False, 0, 10, -7, -(10 ** 90),
                   "", "正文\r\n\"\\", "中文签名"]

        def generate(depth):
            if depth == 0 or rng.randrange(4) == 0:
                return rng.choice(scalars)
            if rng.randrange(2):
                return [generate(depth - 1) for _ in range(rng.randrange(5))]
            keys = rng.sample(["a", "b", "正文", "", "签名"], rng.randrange(6))
            return {key: generate(depth - 1) for key in keys}

        for number in range(200):
            leaf = [generate(2) for _ in range(rng.randrange(20, 40))]
            record = {"items": leaf, "failure": {"kind": "blocked", "reason": "保留"},
                      "n": number, "other": generate(3)}
            original = [record, copy.deepcopy(record), [leaf, copy.deepcopy(leaf)]]
            before = subtree_rules.canonical(original)
            with self.subTest(number=number):
                actual = pack(original)
                expected = independent_pack(original)
                self.assertEqual(subtree_rules.canonical(actual),
                                 subtree_rules.canonical(expected))
                self.assertEqual(subtree_rules.canonical(unpack(expected)), before)
                self.assertEqual(subtree_rules.canonical(independent_unpack(actual)), before)
                self.assertEqual(subtree_rules.canonical(original), before)

    def test_production_and_independent_codecs_do_not_call_each_other(self):
        original, _ = subtree_rules.nested_case()
        with (
            patch("schema_v3_subtree_oracle.pack", side_effect=AssertionError("禁止独立编码")),
            patch("schema_v3_subtree_oracle.unpack", side_effect=AssertionError("禁止独立解码")),
            patch("schema_v3_representation_oracle.pack", side_effect=AssertionError("禁止旧独立编码")),
            patch("schema_v3_representation_oracle.unpack", side_effect=AssertionError("禁止旧独立解码")),
            patch("socket.socket", side_effect=AssertionError("禁止联网")),
        ):
            self.assertEqual(unpack(pack(original)), original)
        with (
            patch("solo_distill.subtree_v3.pack", side_effect=AssertionError("禁止生产编码")),
            patch("solo_distill.subtree_v3.unpack", side_effect=AssertionError("禁止生产解码")),
            patch("solo_distill.representation_v3.pack", side_effect=AssertionError("禁止旧生产编码")),
            patch("solo_distill.representation_v3.unpack", side_effect=AssertionError("禁止旧生产解码")),
            patch("socket.socket", side_effect=AssertionError("禁止联网")),
        ):
            self.assertEqual(independent_unpack(independent_pack(original)), original)

    def test_independent_document_and_cross_decoded_mutable_views_stay_isolated(self):
        leaf = list(range(40))
        original = [{"a": {"b": leaf}, "c": copy.deepcopy(leaf)}] * 4
        document = independent_pack(original)
        before = subtree_rules.canonical(document)
        production = unpack(document)
        independent = independent_unpack(pack(original))
        for restored in (production, independent):
            restored[0]["a"]["b"].clear()
            self.assertEqual(restored[0]["c"], leaf)
            self.assertEqual(restored[1]["a"]["b"], leaf)
        self.assertEqual(subtree_rules.canonical(document), before)
        self.assertEqual(unpack(document), original)

    def test_invalid_tick_is_rejected_before_work(self):
        original, document = subtree_rules.nested_case()
        for operation, argument in ((pack, original), (unpack, document)):
            with self.subTest(operation=operation.__name__), self.assertRaises(ValueError):
                operation(argument, tick=1)


if __name__ == "__main__":
    unittest.main()
