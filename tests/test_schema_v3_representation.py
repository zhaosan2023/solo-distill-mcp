"""V3 字典／列式表示的独立正控、负控和有界生成回归。

先验证独立规则资产，再对生产实现作差分；不联网、不执行来源，
不占用真实冷样本或在线接纳，不证明人工语义或宿主加载。
"""
from __future__ import annotations

import copy
import json
import random
import unittest

from schema_v3_representation_oracle import ENCODING, pack, unpack


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class RepresentationOracleTests(unittest.TestCase):
    def test_known_columnar_encoding_retains_duplicate_records_and_negative_integer(self):
        value = [{"a": "x", "b": -2}, {"b": -2, "a": "x"}]
        expected = {"encoding": ENCODING, "strings": ["a", "b", "x"],
                    "schemas": [[0, 1]],
                    "value": {"t": 0, "r": [[-3, {"i": -2}], [-3, {"i": -2}]]}}
        self.assertEqual(pack(value), expected)
        self.assertEqual(canonical(unpack(expected)), canonical(value))
        self.assertEqual(len(unpack(expected)), 2)

    def test_scalars_empty_containers_unicode_and_all_string_values_are_exact(self):
        values = [None, True, False, 0, 1, -1, -1000000, 10 ** 40,
                  "", "中文\n\"\\\t", "\ufeff正文\r\n", [], {},
                  [True, 1, "1", -1, None, False],
                  {"": "", "原始文档": "完整签名与正文\r\n", "负数": -7},
                  [{"a": 1}, {"b": 1}]]
        for value in values:
            with self.subTest(value=repr(value)):
                before = canonical(value)
                document = pack(value)
                self.assertEqual(canonical(unpack(document)), before)
                self.assertEqual(canonical(value), before)
                self.assertEqual(pack(unpack(document)), document)

    def test_key_order_is_deterministic_without_reordering_arrays(self):
        first = {"z": [{"b": "重复", "a": 1}, {"a": 2, "b": "重复"}], "a": {}}
        second = {"a": {}, "z": [{"a": 1, "b": "重复"}, {"b": "重复", "a": 2}]}
        self.assertEqual(pack(first), pack(second))
        changed = copy.deepcopy(first)
        changed["z"].reverse()
        self.assertNotEqual(pack(first), pack(changed))
        self.assertEqual(unpack(pack(changed)), changed)

    def test_seeded_bounded_generated_json_roundtrips_full_canonical_bytes(self):
        rng = random.Random(20261031)
        scalars = [None, True, False, 0, 1, -1, -900, "", "重复", "正文\n\"\\"]
        keys = ["a", "b", "中文", ""]

        def generate(depth):
            if depth == 0 or rng.randrange(3) == 0:
                return rng.choice(scalars)
            if rng.randrange(2):
                return [generate(depth - 1) for _ in range(rng.randrange(5))]
            chosen = rng.sample(keys, rng.randrange(len(keys) + 1))
            return {key: generate(depth - 1) for key in chosen}

        for number in range(160):
            value = generate(4)
            with self.subTest(number=number):
                document = pack(value)
                self.assertEqual(canonical(unpack(document)), canonical(value))
                self.assertEqual(canonical(pack(unpack(document))), canonical(document))

    def test_expanded_byte_node_and_depth_exact_boundaries(self):
        cases = [(["中文\n\"\\"] * 4, 5, 1),
                 ([{"a": "重复", "b": -2}] * 2, 7, 2),
                 ({"": {}, "数组": []}, 3, 1)]
        for value, nodes, depth in cases:
            size = len(canonical(value))
            with self.subTest(value=value):
                document = pack(value, max_bytes=size, max_nodes=nodes, max_depth=depth)
                self.assertEqual(unpack(document, max_bytes=size,
                                        max_nodes=nodes, max_depth=depth), value)
                for budgets in ({"max_bytes": size - 1},
                                {"max_nodes": nodes - 1},
                                {"max_depth": depth - 1}):
                    with self.subTest(budgets=budgets):
                        with self.assertRaises(ValueError):
                            pack(value, **budgets)
                        with self.assertRaises(ValueError):
                            unpack(document, **budgets)

    def test_depth_64_passes_and_65_is_rejected(self):
        value = 0
        for _ in range(64):
            value = [value]
        self.assertEqual(unpack(pack(value)), value)
        with self.assertRaises(ValueError):
            pack([value])
        document = pack(value)
        document["value"] = [document["value"]]
        with self.assertRaises(ValueError):
            unpack(document)

    def test_repeated_large_string_expansion_is_rejected_by_original_byte_budget(self):
        value = ["正文" * 200] * 40
        document = pack(value)
        self.assertLess(len(canonical(document)), len(canonical(value)))
        with self.assertRaisesRegex(ValueError, "bytes"):
            unpack(document, max_bytes=len(canonical(value)) - 1)
        self.assertEqual(unpack(document, max_bytes=len(canonical(value))), value)

    def test_reference_type_width_tag_and_canonicality_tampering_is_rejected(self):
        original = pack([{"a": "x", "b": -2}] * 2)

        def replace_value(value):
            return lambda document: document.update(value=value)

        mutations = [
            lambda d: d.update(encoding="unknown"),
            lambda d: d.update(extra=True),
            lambda d: d.pop("value"),
            lambda d: d["strings"].append("unused"),
            lambda d: d["strings"].append(d["strings"][-1]),
            lambda d: d["strings"].reverse(),
            lambda d: d["schemas"].append(d["schemas"][0][:]),
            lambda d: d["schemas"][0].reverse(),
            lambda d: d["schemas"][0].__setitem__(0, True),
            lambda d: d["schemas"][0].__setitem__(0, 999),
            lambda d: d["value"].update(t=True),
            lambda d: d["value"].update(t=999),
            lambda d: d["value"].update(r=[]),
            lambda d: d["value"]["r"][0].pop(),
            lambda d: d["value"]["r"][0].__setitem__(0, -999),
            lambda d: d["value"]["r"][0].__setitem__(1, {"i": True}),
            lambda d: d["value"]["r"][0].__setitem__(1, {"i": 0}),
            lambda d: d["value"]["r"][0].__setitem__(1, {"i": -2, "extra": 0}),
            lambda d: d["value"]["r"][0].__setitem__(0, "x"),
            replace_value({"unknown": []}),
            replace_value({"o": 0, "v": []}),
            replace_value({"t": 0, "r": [[-3, {"i": -2}]], "extra": 0}),
            # 同键组对象数组必须列式编码，不能通过其他等价表示放行。
            replace_value([{"o": 0, "v": [-3, {"i": -2}]}] * 2),
        ]
        for number, mutate in enumerate(mutations):
            document = copy.deepcopy(original)
            mutate(document)
            with self.subTest(number=number), self.assertRaises(ValueError):
                unpack(document)

    def test_invalid_original_types_and_budget_types_are_rejected(self):
        for value in (1.0, float("nan"), float("inf"), (1,), b"body", {1: "key"}):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                pack(value)
        for budget in ({"max_bytes": True}, {"max_nodes": -1},
                       {"max_depth": 65}, {"max_bytes": 8388609},
                       {"max_nodes": 500001}):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    pack([], **budget)
                with self.assertRaises(ValueError):
                    unpack(pack([]), **budget)

    def test_cancellation_at_entry_and_during_expansion_and_encoding(self):
        value = [{"a": "正文", "b": index} for index in range(30)]
        document = pack(value)

        def stopped():
            raise RuntimeError("cancelled_or_deadline")

        for operation, argument in ((pack, value), (unpack, document)):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(RuntimeError, "cancelled_or_deadline"):
                    operation(argument, tick=stopped)
                count = [0]

                def stop_later():
                    count[0] += 1
                    if count[0] == 20:
                        stopped()

                with self.assertRaisesRegex(RuntimeError, "cancelled_or_deadline"):
                    operation(argument, tick=stop_later)
                self.assertEqual(count[0], 20)

    def test_unpacked_views_do_not_modify_representation_or_other_views(self):
        value = [{"a": [1, 2]}, {"a": [1, 2]}]
        document = pack(value)
        before = canonical(document)
        first = unpack(document)
        first[0]["a"].clear()
        self.assertEqual(first[1]["a"], [1, 2])
        self.assertEqual(unpack(document), value)
        self.assertEqual(canonical(document), before)


if __name__ == "__main__":
    unittest.main()
