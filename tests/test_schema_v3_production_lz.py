"""新增字节表示生产规则、完整差分及交叉解码；不启动服务或联网。

继承全部已通过独立规则，仅替换被测模块；独立模块别名保持独立。
生成样本不替代手工令牌期望、篡改负控或真实来源门禁。
"""
from __future__ import annotations

import ast
import base64
import copy
import inspect
import random
import unittest
from unittest.mock import patch

import schema_v3_lz_oracle as independent
import test_schema_v3_lz as rules
from solo_distill import lz_v3 as production


class ProductionLzTests(rules.LzOracleTests):
    def setUp(self):
        replacement = patch.object(rules, "codec", production)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_complete_bytes_cross_decoding_240_generated_documents(self):
        rng = random.Random(202610311018)
        scalars = [None, True, False, 0, 1, -1, -(10**90), "", "中文🙂\r\n\"\\"]

        def make(depth):
            if depth == 0 or rng.randrange(4) == 0:
                return rng.choice(scalars)
            if rng.randrange(2):
                return [make(depth - 1) for _ in range(rng.randrange(6))]
            return {key: make(depth - 1) for key in rng.sample(
                ["", "正文", "signature", "documentation", "id"], rng.randrange(6))}

        for number in range(240):
            leaf = make(4)
            value = {"records": [leaf, copy.deepcopy(leaf), leaf], "number": number,
                     "body": ("same-content-中文\n" * rng.randrange(1, 100)),
                     "failures": [{"status": "blocked", "reason": "保留全部未知"}]}
            before = independent.canonical(value)
            with self.subTest(number=number):
                actual = production.pack(value)
                expected = independent.pack(value)
                self.assertEqual(production.canonical(actual), independent.canonical(expected))
                self.assertEqual(production.canonical(production.unpack(expected)), before)
                self.assertEqual(independent.canonical(independent.unpack(actual)), before)
                self.assertEqual(independent.canonical(value), before)

    def test_raw_token_difference_at_window_and_binary_boundaries(self):
        rng = random.Random(13065536)
        inputs = [bytes(range(256)) * 6, b"abcXabcYabcXabcY", b"a" * 10000,
                  b"abc" + b"\x00" * 65533 + b"abc",
                  b"abc" + b"\x00" * 65534 + b"abc"]
        inputs.extend(bytes(rng.randrange(256) for _ in range(size))
                      for size in (1, 2, 3, 127, 128, 129, 130, 131, 2000, 66000))
        for number, raw in enumerate(inputs):
            with self.subTest(number=number):
                actual = production._compress(raw)
                expected = independent._compress(raw)
                self.assertEqual(actual, expected)
                self.assertEqual(production._inflate(expected, len(raw)), raw)
                self.assertEqual(independent._inflate(actual, len(raw)), raw)

    def test_modules_do_not_call_each_other_or_old_codecs(self):
        value = {"records": [{"body": "abc中文" * 20}] * 5}
        with (
            patch.object(independent, "pack", side_effect=AssertionError("禁止独立编码")),
            patch.object(independent, "unpack", side_effect=AssertionError("禁止独立解码")),
            patch.object(independent, "_compress", side_effect=AssertionError("禁止独立令牌")),
            patch.object(independent, "_preparse", side_effect=AssertionError("禁止独立预检")),
            patch("socket.socket", side_effect=AssertionError("禁止联网")),
        ):
            self.assertEqual(production.unpack(production.pack(value)), value)
        with (
            patch.object(production, "pack", side_effect=AssertionError("禁止生产编码")),
            patch.object(production, "unpack", side_effect=AssertionError("禁止生产解码")),
            patch.object(production, "_compress", side_effect=AssertionError("禁止生产令牌")),
            patch.object(production, "_preparse", side_effect=AssertionError("禁止生产预检")),
            patch("socket.socket", side_effect=AssertionError("禁止联网")),
        ):
            self.assertEqual(independent.unpack(independent.pack(value)), value)
        for module in (production, independent):
            tree = ast.parse(inspect.getsource(module))
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                           for alias in node.names)
            self.assertFalse(any(name and (name.startswith("schema_v3_")
                                           or name.startswith("solo_distill")) for name in imports))

    def test_cross_decoded_views_and_input_document_remain_independent(self):
        leaf = {"body": "abcdef中文" * 30, "unknowns": ["未验证"]}
        value = [leaf, leaf, copy.deepcopy(leaf)]
        for encoder, decoder in ((production.pack, independent.unpack),
                                 (independent.pack, production.unpack)):
            document = encoder(value)
            before = independent.canonical(document)
            restored = decoder(document)
            restored[0]["unknowns"].clear()
            self.assertEqual(restored[1]["unknowns"], ["未验证"])
            self.assertEqual(restored[2]["unknowns"], ["未验证"])
            self.assertEqual(independent.canonical(document), before)
            self.assertEqual(decoder(document), value)

    def test_reencoded_alternative_older_candidate_rejected_by_both(self):
        raw = b'"abcXabcYabcXabcY"'
        # 位置 9 的 abc 规范上应使用最近位置 5，而不是更旧的位置 1。
        tokens = bytearray(independent._compress(raw))
        cursor = position = 0
        changed = False
        while cursor < len(tokens):
            control = tokens[cursor]
            cursor += 1
            if control < 128:
                width = control + 1
                cursor += width
            else:
                width = control - 125
                if position == 9:
                    self.assertEqual(width, 3)
                    tokens[cursor:cursor + 2] = (7).to_bytes(2, "little")
                    changed = True
                cursor += 2
            position += width
        self.assertTrue(changed)
        altered = rules.document(raw, bytes(tokens))
        self.assertEqual(production._inflate(bytes(tokens), len(raw)), raw)
        for decoder in (production.unpack, independent.unpack):
            with self.assertRaisesRegex(ValueError, "noncanonical"):
                decoder(altered)


class ProductionLzSpeedBoundaryTests(unittest.TestCase):
    def test_segmented_copy_all_widths_overlap_and_nonoverlap(self):
        for distance in (1, 2, 3, 7, 15, 16, 17, 63, 127, 128):
            seed = bytes(range(distance))
            for width in range(3, 131):
                tokens = (bytes([distance - 1]) + seed + bytes([width + 125])
                          + (distance - 1).to_bytes(2, "little"))
                expected = (seed * ((distance + width + distance - 1) // distance))[:distance + width]
                with self.subTest(distance=distance, width=width):
                    self.assertEqual(production._inflate(tokens, len(expected)), expected)
                    self.assertEqual(independent._inflate(tokens, len(expected)), expected)

    def test_reencoding_reuses_verified_raw_not_pack_or_cached_acceptance(self):
        value = {"body": "abcdef中文" * 100, "unknowns": ["未验证"]}
        encoded = production.pack(value)
        with patch.object(production, "pack", side_effect=AssertionError("重复对象计量")):
            self.assertEqual(production.unpack(encoded), value)
            altered = copy.deepcopy(encoded)
            altered["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "digest"):
                production.unpack(altered)
            self.assertEqual(production.unpack(encoded), value)

    def test_bounded_segment_callbacks_still_cancel_before_completion(self):
        seed = b"a"
        tokens = b"\x00a\xff\x00\x00"
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise TimeoutError("cancel_during_segment")

        with self.assertRaisesRegex(TimeoutError, "cancel_during_segment"):
            production._inflate(tokens, 131, tick=cancel)
        self.assertEqual(calls, 4)
        self.assertEqual(production._inflate(tokens, 131), seed * 131)


if __name__ == "__main__":
    unittest.main()
