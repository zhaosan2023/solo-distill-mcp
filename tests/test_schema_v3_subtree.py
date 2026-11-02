"""新子结构表示独立规则、负控及有界生成；不导入生产模块。

继承既有独立规则完整集合，替换测试目标而不削减断言；
另核对引用候选、确定表顺序、原出现计数及展开资源边界。
不联网、不执行来源、不启动真实冷预检或占用在线接纳。
"""
from __future__ import annotations

import copy
import random
import unittest
from unittest.mock import patch

import test_schema_v3_representation as rules
import schema_v3_representation_oracle as predecessor
from schema_v3_subtree_oracle import ENCODING, canonical, pack, unpack


def size_shape(value):
    """从完整原值独立计算规范字节、值节点和根为零的深度。"""
    def walk(item, depth):
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        counts = [walk(child, depth + 1) for child in children]
        return 1 + sum(row[0] for row in counts), max([depth] + [row[1] for row in counts])
    nodes, depth = walk(value, 0)
    return len(canonical(value)), nodes, depth


def nested_case():
    leaf = [10] + [0] * 30  # 恰好 64 个规范编码字节。
    parent = [leaf, leaf, True]
    original = [parent, parent]
    expected = {"encoding": ENCODING, "strings": [], "schemas": [],
        "subtrees": [leaf, [{"q": 0}, {"q": 0}, True]],
        "value": [{"q": 1}, {"q": 1}]}
    return original, expected


class SubtreeOracleTests(rules.RepresentationOracleTests):
    def setUp(self):
        for name, value in (("pack", pack), ("unpack", unpack), ("ENCODING", ENCODING)):
            replacement = patch.object(rules, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_known_columnar_encoding_retains_duplicate_records_and_negative_integer(self):
        original = [{"a": "x", "b": -2}, {"b": -2, "a": "x"}]
        expected = {"encoding": ENCODING, "strings": ["a", "b", "x"],
            "schemas": [[0, 1]], "subtrees": [],
            "value": {"t": 0, "r": [[-3, {"i": -2}], [-3, {"i": -2}]]}}
        self.assertEqual(pack(original), expected)
        self.assertEqual(canonical(unpack(expected)), canonical(original))
        self.assertEqual(len(unpack(expected)), 2)

    def test_manual_63_64_byte_threshold_and_occurrence_boundary(self):
        below = [0] * 31
        at = [10] + [0] * 30
        self.assertEqual(len(canonical(below)), 63)
        self.assertEqual(len(canonical(at)), 64)
        for value in ([below, below], [at]):
            self.assertEqual(pack(value)["subtrees"], [])
            self.assertEqual(unpack(pack(value)), value)
        expected = {"encoding": ENCODING, "strings": [], "schemas": [],
            "subtrees": [at], "value": [{"q": 0}, {"q": 0}]}
        self.assertEqual(pack([at, at]), expected)
        self.assertEqual(pack([at[:], at[:]]), expected)
        self.assertEqual(unpack(expected), [at, at])

    def test_manual_nested_candidates_use_original_occurrences_and_backward_references(self):
        original, expected = nested_case()
        self.assertEqual(pack(original), expected)
        self.assertEqual(unpack(expected), original)
        # 叶树在折叠根值中不出现，但仍因原值树四次出现而成为候选。
        self.assertEqual(len(expected["subtrees"]), 2)
        self.assertEqual(expected["subtrees"][1][0], {"q": 0})
        self.assertLess(len(canonical(expected)), len(canonical(predecessor.pack(original))))

    def test_full_byte_order_not_numeric_or_insertion_order(self):
        first = [10] + [0] * 30
        second = [20] + [0] * 30
        original = [second, first, first, second]
        self.assertLess(canonical(first), canonical(second))
        expected = {"encoding": ENCODING, "strings": [], "schemas": [],
            "subtrees": [first, second], "value": [{"q": 1}, {"q": 0}, {"q": 0}, {"q": 1}]}
        self.assertEqual(pack(original), expected)
        self.assertEqual(unpack(expected), original)

    def test_dictionary_shapes_and_full_original_values_are_unchanged(self):
        original = [{"body": "中文\r\n\\\"" * 100,
            "signature": "完整签名", "negative": -(10 ** 90),
            "records": list(range(45)), "unknowns": ["不可确定"],
            "failure": {"kind": "blocked", "reason": "保留失败"}}] * 3
        base = predecessor.pack(original)
        document = pack(original)
        self.assertEqual(document["strings"], base["strings"])
        self.assertEqual(document["schemas"], base["schemas"])
        self.assertTrue(document["subtrees"])
        self.assertEqual(canonical(unpack(document)), canonical(original))
        self.assertEqual(predecessor.pack(unpack(document)), base)
        self.assertEqual(predecessor.unpack(base), original)
        with self.assertRaises(ValueError):
            predecessor.unpack(document)
        with self.assertRaises(ValueError):
            unpack(base)

    def test_columnar_metadata_rows_and_row_values_may_be_references(self):
        original = [{"a": list(range(35)), "b": list(range(35))}] * 4
        document = pack(original)
        self.assertTrue(document["subtrees"])
        self.assertEqual(unpack(document), original)
        self.assertEqual(pack(unpack(document)), document)

    def test_large_negative_integer_escape_can_be_referenced(self):
        original = [-(10 ** 100)] * 4
        document = pack(original)
        self.assertEqual(len(document["subtrees"]), 1)
        self.assertEqual(document["subtrees"][0], {"i": original[0]})
        self.assertEqual(document["value"], [{"q": 0}] * 4)
        self.assertEqual(unpack(document), original)

    def test_reference_type_range_forward_cycle_and_root_controls(self):
        _, expected = nested_case()
        mutations = [
            lambda d: d.update(value={"q": True}),
            lambda d: d.update(value={"q": -1}),
            lambda d: d.update(value={"q": len(d["subtrees"])}),
            lambda d: d.update(value={"q": "0"}),
            lambda d: d.update(value={"q": 0, "extra": 1}),
            lambda d: d["subtrees"][0].__setitem__(0, {"q": 0}),
            lambda d: d["subtrees"][0].__setitem__(0, {"q": 1}),
            lambda d: d["subtrees"][1].__setitem__(0, {"q": 1}),
            lambda d: d["subtrees"].__setitem__(1, {"q": 0}),
            lambda d: d["subtrees"].__setitem__(0, 0),
            lambda d: d["subtrees"].__setitem__(0, None),
        ]
        for number, mutate in enumerate(mutations):
            document = copy.deepcopy(expected)
            mutate(document)
            with self.subTest(number=number), self.assertRaises(ValueError):
                unpack(document)

    def test_noncanonical_table_duplicates_unused_entries_sort_and_inline_rejected(self):
        original, expected = nested_case()
        mutations = [
            lambda d: d["subtrees"].append(copy.deepcopy(d["subtrees"][0])),
            lambda d: d["subtrees"].append([0] * 40),
            lambda d: d["subtrees"].__setitem__(0, [0] * 31),
            lambda d: d.update(value=copy.deepcopy(predecessor.pack(original)["value"])),
            lambda d: d["value"].__setitem__(0, copy.deepcopy(d["subtrees"][1])),
            lambda d: d["subtrees"][1].__setitem__(0, copy.deepcopy(d["subtrees"][0])),
            lambda d: d.update(encoding=predecessor.ENCODING),
            lambda d: d.pop("subtrees"),
            lambda d: d.update(extra=True),
        ]
        for number, mutate in enumerate(mutations):
            document = copy.deepcopy(expected)
            mutate(document)
            with self.subTest(number=number), self.assertRaises(ValueError):
                unpack(document)
        # 两个同高表项交换且修正引用后仍可还原，但不是规范字节序。
        left = [10] + [0] * 30
        right = [20] + [0] * 30
        document = pack([left, right, left, right])
        document["subtrees"].reverse()
        document["value"] = [{"q": 1}, {"q": 0}, {"q": 1}, {"q": 0}]
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            unpack(document)

    def test_repeated_expansion_bytes_nodes_and_logical_depth_are_exact(self):
        original, document = nested_case()
        byte_count, node_count, depth = size_shape(original)
        self.assertEqual(unpack(document, max_bytes=byte_count,
            max_nodes=node_count, max_depth=depth), original)
        self.assertEqual(pack(original, max_bytes=byte_count,
            max_nodes=node_count, max_depth=depth), document)
        for budget in ({"max_bytes": byte_count - 1}, {"max_nodes": node_count - 1},
                       {"max_depth": depth - 1}):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    unpack(document, **budget)
                with self.assertRaises(ValueError):
                    pack(original, **budget)
        self.assertLess(size_shape(document)[1], node_count)

    def test_reference_expansion_bomb_is_bounded_before_full_materialization(self):
        document = {"encoding": ENCODING, "strings": [], "schemas": [],
            "subtrees": [[0] * 40], "value": [{"q": 0}] * 1000}
        with self.assertRaisesRegex(ValueError, "nodes"):
            unpack(document, max_nodes=1100)
        with self.assertRaisesRegex(ValueError, "bytes"):
            unpack(document, max_bytes=2100)
        # 後向引用可嵌套倍增，跳转没有消除原对象的逻辑深度。
        document["subtrees"] += [[{"q": index}, {"q": index}] for index in range(12)]
        document["value"] = {"q": 12}
        with self.assertRaisesRegex(ValueError, "depth"):
            unpack(document, max_depth=5)
        with self.assertRaisesRegex(ValueError, "nodes"):
            unpack(document, max_nodes=2000)

    def test_syntax_budget_exact_boundaries_and_reference_hop_guard(self):
        original, document = nested_case()
        byte_count, node_count, depth = size_shape(document)
        budgets = {"max_syntax_bytes": byte_count, "max_syntax_nodes": node_count,
                   "max_syntax_depth": depth, "max_reference_hops": 1}
        self.assertEqual(pack(original, **budgets), document)
        self.assertEqual(unpack(document, **budgets), original)
        for name, value in budgets.items():
            lower = {name: value - 1}
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    pack(original, **lower)
                with self.assertRaises(ValueError):
                    unpack(document, **lower)
        self.assertEqual(unpack(pack(0, max_reference_hops=0), max_reference_hops=0), 0)
        for bad in ({"max_syntax_bytes": True}, {"max_syntax_nodes": -1},
                    {"max_syntax_depth": 193}, {"max_reference_hops": 193},
                    {"max_reference_hops": True}, {"max_syntax_bytes": 8388609},
                    {"max_syntax_nodes": 500001}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    pack(original, **bad)
                with self.assertRaises(ValueError):
                    unpack(document, **bad)

    def test_physical_syntax_depth_192_and_193_rejection(self):
        value = 0
        for _ in range(193):
            value = [value]
        document = {"encoding": ENCODING, "strings": [], "schemas": [],
            "subtrees": [], "value": value}
        with self.assertRaisesRegex(ValueError, "depth"):
            unpack(document)
        # 非规范深度输入即使物理预算放行，原对象的 64 层仍拒绝。
        document["value"] = document["value"][0][0]
        with self.assertRaisesRegex(ValueError, "depth"):
            unpack(document)

    def test_shared_original_objects_and_restored_views_are_independent(self):
        original, _ = nested_case()
        document = pack(original)
        before = canonical(document)
        restored = unpack(document)
        restored[0][0].clear()
        self.assertEqual(restored[0][1], original[0][1])
        self.assertEqual(restored[1], original[1])
        self.assertEqual(canonical(document), before)
        self.assertEqual(unpack(document), original)

    def test_cancellation_across_syntax_reference_and_reencoding_phases(self):
        original, _ = nested_case()
        document = pack(original)
        for operation, argument in ((pack, original), (unpack, document)):
            calls = [0]
            def count():
                calls[0] += 1
            operation(argument, tick=count)
            for stop in (1, 25, max(1, calls[0] - 5)):
                seen = [0]
                def cancelled():
                    seen[0] += 1
                    if seen[0] == stop:
                        raise RuntimeError("cancelled_or_deadline")
                with self.subTest(operation=operation.__name__, stop=stop):
                    with self.assertRaisesRegex(RuntimeError, "cancelled_or_deadline"):
                        operation(argument, tick=cancelled)
                    self.assertEqual(seen[0], stop)

    def test_seeded_repeated_generated_containers_preserve_full_canonical_values(self):
        rng = random.Random(20261101)
        for number in range(180):
            leaf = [rng.choice([None, False, True, -5, 0, 10, "中文\n\\\""])
                    for _ in range(rng.randrange(20, 50))]
            record = {"a": leaf, "b": copy.deepcopy(leaf), "n": number}
            original = [record, copy.deepcopy(record), [leaf, copy.deepcopy(leaf)]]
            before = canonical(original)
            with self.subTest(number=number):
                document = pack(original)
                self.assertEqual(canonical(unpack(document)), before)
                self.assertEqual(pack(unpack(document)), document)
                self.assertEqual(canonical(original), before)
                self.assertEqual(predecessor.pack(unpack(document)), predecessor.pack(original))


if __name__ == "__main__":
    unittest.main()
