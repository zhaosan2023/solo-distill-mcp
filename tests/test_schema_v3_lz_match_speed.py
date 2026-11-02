"""LZ 匹配分段修剪的完整字节差分与取消回归；仅有界内存夹具。

独立 Oracle 保持原实现，不联网、不启动服务、不执行业务来源。
工作量与夹具通过不证明真实冷预检耗时或在线准入。
"""
from __future__ import annotations

import unittest

import schema_v3_lz_oracle as independent
from solo_distill import lz_v3 as production


class LzMatchSpeedTests(unittest.TestCase):
    def assert_tokens(self, raw):
        expected = independent._compress(raw)
        actual = production._compress(raw)
        self.assertEqual(actual, expected)
        self.assertEqual(production._inflate(actual, len(raw)), raw)
        self.assertEqual(independent._inflate(actual, len(raw)), raw)

    def test_every_mismatch_position_inside_full_match(self):
        seed = bytes(range(130))
        for mismatch in range(3, 130):
            raw = seed + b"\xff" + seed[:mismatch] + b"\xfe" + seed[mismatch + 1:]
            with self.subTest(mismatch=mismatch):
                self.assert_tokens(raw)

    def test_every_remaining_match_length_and_short_tail(self):
        seed = bytes(range(130))
        for remaining in range(1, 131):
            with self.subTest(remaining=remaining):
                self.assert_tokens(seed + b"\xff" + seed[:remaining])
        self.assertEqual(production._compress(b"a" * 131), b"\x00a\xff\x00\x00")
        self.assertEqual(production._compress(b"abcabc"), b"\x02abc\x80\x02\x00")

    def test_overlap_periods_and_multiple_full_matches(self):
        for distance in (1, 2, 3, 7, 15, 16, 17, 31, 63, 127, 128, 130):
            raw = bytes(range(distance)) * 12 + b"\xfftail"
            with self.subTest(distance=distance):
                self.assert_tokens(raw)

    def test_window_expiry_and_recent_candidate_rules_remain_exact(self):
        for distance in (65535, 65536, 65537, 65666):
            raw = b"abc" + b"\x00" * (distance - 3) + b"abc"
            with self.subTest(distance=distance):
                self.assert_tokens(raw)
        for raw in (b"abcXabcYabcXabcY", b"abcabcabcXbcabcabcY"):
            with self.subTest(raw=raw):
                self.assert_tokens(raw)

    def test_each_callback_can_cancel_without_poisoning_next_operation(self):
        raw = b"a" * 261
        calls = 0

        def observe():
            nonlocal calls
            calls += 1

        expected = independent._compress(raw)
        self.assertEqual(production._compress(raw, tick=observe), expected)
        self.assertGreater(calls, 0)
        for stop in range(1, calls + 1):
            seen = 0

            def cancel():
                nonlocal seen
                seen += 1
                if seen == stop:
                    raise TimeoutError("injected_match_deadline")

            with self.subTest(stop=stop):
                with self.assertRaisesRegex(TimeoutError, "injected_match_deadline"):
                    production._compress(raw, tick=cancel)
                self.assertEqual(seen, stop)
                self.assertEqual(production._compress(raw), expected)


if __name__ == "__main__":
    unittest.main()
