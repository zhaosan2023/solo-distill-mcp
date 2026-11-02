"""新增字节表示的独立规则与篡改负控；不导入生产编解码。

只使用有界内存夹具，不联网、不启动服务、不执行来源或真实样本。
手工期望与生成回归互补，不以可逆性代替唯一表示及解析前预算。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import random
import unittest
from unittest.mock import patch

import schema_v3_lz_oracle as codec


def document(raw, tokens=None):
    tokens = codec._compress(raw) if tokens is None else tokens
    return {"encoding": codec.ENCODING, "raw_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data": base64.b64encode(tokens).decode("ascii")}


def literals(raw):
    return b"".join(bytes([len(part) - 1]) + part
                    for start in range(0, len(raw), 128)
                    for part in [raw[start:start + 128]])


def parsed_tokens(tokens):
    cursor = position = 0
    result = []
    while cursor < len(tokens):
        control = tokens[cursor]
        cursor += 1
        if control < 128:
            count = control + 1
            result.append((position, "literal", count, tokens[cursor:cursor + count]))
            cursor += count
        else:
            count = control - 128 + 3
            distance = 1 + tokens[cursor] + 256 * tokens[cursor + 1]
            result.append((position, "copy", count, distance))
            cursor += 2
        position += count
    return result


class LzOracleTests(unittest.TestCase):
    def test_handwritten_scalar_document_and_literal_bytes(self):
        expected = {"encoding": "lz-json-v3-1", "raw_bytes": 4,
                    "sha256": hashlib.sha256(b"null").hexdigest(),
                    "data": "A251bGw="}
        self.assertEqual(codec.pack(None), expected)
        self.assertIsNone(codec.unpack(expected))
        self.assertEqual(codec._compress(b"abc"), b"\x02abc")

    def test_handwritten_overlap_and_full_match_length(self):
        self.assertEqual(codec._compress(b"a" * 131), b"\x00a\xff\x00\x00")
        self.assertEqual(codec._inflate(b"\x00a\xff\x00\x00", 131), b"a" * 131)
        self.assertEqual(codec._compress(b"abcabc"), b"\x02abc\x80\x02\x00")
        self.assertEqual(codec._inflate(b"\x02abc\x80\x02\x00", 6), b"abcabc")

    def test_literal_128_and_end_flush(self):
        raw = bytes(range(129))
        self.assertEqual(codec._compress(raw), b"\x7f" + raw[:128] + b"\x00" + raw[128:])
        self.assertEqual(codec._inflate(codec._compress(raw), len(raw)), raw)

    def test_copy_length_3_130_and_short_remainder(self):
        for count in (3, 4, 129, 130, 131, 132, 260, 261):
            raw = b"a" * (count + 1)
            with self.subTest(count=count):
                tokens = codec._compress(raw)
                self.assertEqual(codec._inflate(tokens, len(raw)), raw)
                self.assertTrue(all(3 <= item[2] <= 130 for item in parsed_tokens(tokens)
                                    if item[1] == "copy"))
        self.assertEqual(codec._compress(b"aaaa"), b"\x00a\x80\x00\x00")
        self.assertEqual(codec._compress(b"aaa"), b"\x02aaa")

    def test_nearest_candidate_not_older_longer_candidate(self):
        raw = b"abcXabcYabcXabcY"
        tokens = codec._compress(raw)
        self.assertEqual(tokens, b"\x03abcX\x80\x03\x00\x00Y\x80\x03\x00\x82\x07\x00")
        self.assertIn((8, "copy", 3, 4), parsed_tokens(tokens))
        self.assertEqual(codec._inflate(tokens, len(raw)), raw)

    def test_consumed_copy_positions_register_and_longest_match(self):
        raw = b"abcabcabcXbcabcabcY"
        tokens = codec._compress(raw)
        self.assertEqual(codec._inflate(tokens, len(raw)), raw)
        # 位置 10 的 bcabc 使用先前复制内部的位置 4；第六字节 X/a 不同。
        self.assertIn((10, "copy", 5, 6), parsed_tokens(tokens))

    def test_window_65536_equal_and_expired(self):
        for distance in (65536, 65537):
            raw = b"abc" + b"\x00" * (distance - 3) + b"abc"
            with self.subTest(distance=distance):
                tokens = codec._compress(raw)
                self.assertEqual(codec._inflate(tokens, len(raw)), raw)
                matches = [item for item in parsed_tokens(tokens)
                           if item[0] == distance and item[1] == "copy"]
                if distance == 65536:
                    self.assertEqual(matches, [(distance, "copy", 3, distance)])
                    self.assertEqual(tokens[-3:], b"\x80\xff\xff")
                else:
                    self.assertEqual(matches, [])
                    self.assertTrue(tokens.endswith(b"\x02abc"))

    def test_all_scalar_types_unicode_and_complete_fields(self):
        value = {"signature": "f(x: str) -> 中文", "documentation": "完整\n说明🙂",
                 "body": "\ufeffa\r\nb\t\"\\", "failures": ["未完成"],
                 "unknowns": ["不确定"], "values": [None, True, False, 0, 1, -1, 10**80],
                 "": "", "e\u0301": "é", "nested": [{}, []]}
        self.assertEqual(codec.unpack(codec.pack(value)), value)
        self.assertEqual(codec.canonical(codec.unpack(codec.pack(value))), codec.canonical(value))
        for value in (None, False, True, 0, -1, [], {}, "", "🙂"):
            with self.subTest(value=value):
                restored = codec.unpack(codec.pack(value))
                self.assertIs(type(restored), type(value))
                self.assertEqual(restored, value)

    def test_determinism_key_order_and_repeated_records(self):
        row = {"body": "abcdef" * 60, "id": 3, "unknown": None}
        value = {"z": [row, row], "a": {"x": [True, -3]}}
        reordered = {"a": {"x": [True, -3]}, "z": [dict(reversed(list(row.items()))), row]}
        self.assertEqual(codec.pack(value), codec.pack(reordered))
        result = codec.unpack(codec.pack(value))
        self.assertEqual(result, value)
        self.assertEqual(len(result["z"]), 2)
        self.assertIsNot(result["z"][0], result["z"][1])
        result["z"][0]["body"] = "changed"
        self.assertEqual(result["z"][1]["body"], row["body"])

    def test_300_generated_documents_and_token_reversibility(self):
        rng = random.Random(20261031)
        alphabet = "ab中文🙂\n\t\"\\"

        def make(depth):
            if depth == 0 or rng.randrange(3) == 0:
                return rng.choice([None, True, False, rng.randrange(-10000, 10000),
                    "".join(rng.choice(alphabet) for _ in range(rng.randrange(40)))])
            if rng.randrange(2):
                return [make(depth - 1) for _ in range(rng.randrange(5))]
            return {"key-" + str(index): make(depth - 1) for index in range(rng.randrange(5))}

        for index in range(300):
            item = make(4)
            value = {"sample": index, "records": [item, copy.deepcopy(item), item],
                     "text": "same-content-" * rng.randrange(1, 50)}
            with self.subTest(index=index):
                packed = codec.pack(value)
                self.assertEqual(codec.unpack(packed), value)
                self.assertEqual(codec.pack(copy.deepcopy(value)), packed)
                raw = codec.canonical(value)
                self.assertEqual(codec._inflate(codec._compress(raw), len(raw)), raw)

    def test_invalid_original_types_and_unicode(self):
        for value in (1.5, float("nan"), float("inf"), b"x", (1,), {1: "x"}, "\ud800"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    codec.pack(value)

    def test_expanded_byte_budget_exact_and_one_less(self):
        value = {"k": ["\"\\中文\n"] * 12}
        size = len(codec.canonical(value))
        self.assertEqual(codec.unpack(codec.pack(value, max_bytes=size), max_bytes=size), value)
        for action in (lambda: codec.pack(value, max_bytes=size - 1),
                       lambda: codec.unpack(codec.pack(value), max_bytes=size - 1)):
            with self.assertRaises(ValueError):
                action()

    def test_expanded_depth_and_nodes_exact(self):
        value = {"k": [None, {"v": "abc"}]}
        # Root, array, null, object, string: five values; deepest string depth 3.
        self.assertEqual(codec.unpack(codec.pack(value, max_nodes=5, max_depth=3),
                                      max_nodes=5, max_depth=3), value)
        for kwargs in ({"max_nodes": 4}, {"max_depth": 2}, {"max_nodes": 0}):
            for action in (codec.pack, codec.unpack):
                with self.subTest(action=action.__name__, kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        action(value if action is codec.pack else codec.pack(value), **kwargs)

    def test_preparse_limits_before_json_object_construction(self):
        for raw, kwargs in ((b"[[[0]]]", {"max_depth": 2}),
                            (b"[0,0,0]", {"max_nodes": 3})):
            packed = document(raw)
            with self.subTest(raw=raw), patch.object(codec, "_strict_load",
                    side_effect=AssertionError("JSON parser must not run")):
                with self.assertRaisesRegex(ValueError, "preparse_"):
                    codec.unpack(packed, **kwargs)

    def test_preparse_key_not_node_and_escaped_string_not_structure(self):
        value = {"key": "[{\"key\":0}]\\\"🙂"}
        raw = codec.canonical(value)
        self.assertEqual(codec._preparse(raw, max_depth=1, max_nodes=2)["nodes"], 2)
        self.assertEqual(codec.unpack(codec.pack(value), max_depth=1, max_nodes=2), value)
        with self.assertRaises(ValueError):
            codec._preparse(raw, max_nodes=1)

    def test_preparse_closed_and_invalid_lexical_boundaries(self):
        for raw in (b'"unterminated', b'"bad\\x"', b'"bad\\u12"', b'"line\n"',
                    b"[", b"{", b"[0,]", b'{"a"0}', b"00", b"-", b"[0 0]",
                    b"truefalse", b"[[]", b'{"a":}', b"null "):
            with self.subTest(raw=raw), patch.object(codec, "_strict_load",
                    side_effect=AssertionError("preparse must reject before parser")):
                with self.assertRaises(ValueError):
                    codec.unpack(document(raw))

    def test_strict_json_and_noncanonical_original_bytes(self):
        for raw in (b'{"a":0,"a":1}', b"1.5", b"NaN", b"Infinity", b"-0",
                    b'{"z":0,"a":1}', b'"\\u0061"', b'"\\ud800"', b'"\xff"'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    codec.unpack(document(raw))

    def test_header_closed_types_identity_and_digest(self):
        original = codec.pack({"x": ["abc"] * 10})
        mutations = [lambda d: d.update(extra=0), lambda d: d.pop("data"),
            lambda d: d.update(encoding="dictionary-columnar-v3-2"),
            lambda d: d.update(encoding=3), lambda d: d.update(raw_bytes=True),
            lambda d: d.update(raw_bytes=0), lambda d: d.update(raw_bytes=-1),
            lambda d: d.update(raw_bytes=codec.MAX_BYTES + 1),
            lambda d: d.update(sha256="A" * 64), lambda d: d.update(sha256="0" * 64),
            lambda d: d.update(sha256=False), lambda d: d.update(data=None)]
        for index, mutate in enumerate(mutations):
            altered = copy.deepcopy(original)
            mutate(altered)
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    codec.unpack(altered)

    def test_base64_shape_padding_alphabet_and_nonzero_padding_bits(self):
        original = codec.pack(None)
        for text in ("", original["data"] + "=", original["data"] + "\n",
                     "🙂===", "!!!!", "A251bGx=", "====", "AA=A"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    codec.unpack({**original, "data": text})

    def test_inflate_truncation_distance_extra_and_length(self):
        cases = [(b"\x02ab", 3), (b"\x80", 3), (b"\x80\x00", 3),
                 (b"\x80\x00\x00", 3), (b"\x00a\x80\x01\x00", 4),
                 (b"\x00a\x00b", 1), (b"\x00a", 2),
                 (b"\x00a\xff\x00\x00", 130), (b"\x01ab", 1)]
        for tokens, size in cases:
            with self.subTest(tokens=tokens, size=size):
                with self.assertRaises(ValueError):
                    codec._inflate(tokens, size)

    def test_expansion_stops_before_declared_overflow(self):
        tokens = b"\x00a" + b"\xff\x00\x00" * 100
        with self.assertRaisesRegex(ValueError, "expanded_bytes"):
            codec._inflate(tokens, 1000, max_bytes=1000)
        with self.assertRaisesRegex(ValueError, "raw_bytes"):
            codec._inflate(tokens, 13001, max_bytes=1000)

    def test_noncanonical_literal_cut_and_missed_copy_reject(self):
        raw = b'"' + b"a" * 20 + b'"'
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            codec.unpack(document(raw, literals(raw)))
        raw = b"null"
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            codec.unpack(document(raw, b"\x00n\x02ull"))

    def test_nonlongest_copy_rejects_even_equal_original(self):
        raw = b'"' + b"a" * 10 + b'"'
        tokens = b'\x01"a\x80\x00\x00\x83\x00\x00\x00"'
        self.assertEqual(codec._inflate(tokens, len(raw)), raw)
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            codec.unpack(document(raw, tokens))

    def test_token_input_budget_before_base64_decode(self):
        packed = codec.pack(None)
        token_size = len(base64.b64decode(packed["data"]))
        self.assertEqual(codec.unpack(packed, max_token_bytes=token_size), None)
        with patch.object(codec.base64, "b64decode", side_effect=AssertionError("must not allocate")):
            with self.assertRaisesRegex(ValueError, "token_bytes"):
                codec.unpack(packed, max_token_bytes=token_size - 1)
        with self.assertRaises(ValueError):
            codec._compress(b"null", max_token_bytes=token_size - 1)

    def test_physical_syntax_budget_exact_and_one_less(self):
        packed = codec.pack(None)
        size = len(codec.canonical(packed))
        # Four scalar fields and the root object; logical depth one.
        self.assertEqual(codec.pack(None, max_syntax_bytes=size, max_syntax_depth=1,
                                    max_syntax_nodes=5), packed)
        self.assertIsNone(codec.unpack(packed, max_syntax_bytes=size,
                                       max_syntax_depth=1, max_syntax_nodes=5))
        for kwargs in ({"max_syntax_bytes": size - 1}, {"max_syntax_depth": 0},
                       {"max_syntax_nodes": 4}):
            for action, input_value in ((codec.pack, None), (codec.unpack, packed)):
                with self.subTest(kwargs=kwargs, action=action.__name__):
                    with self.assertRaises(ValueError):
                        action(input_value, **kwargs)

    def test_budget_types_and_ceiling_not_increased(self):
        ceilings = {"max_bytes": codec.MAX_BYTES, "max_depth": codec.MAX_DEPTH,
            "max_nodes": codec.MAX_NODES, "max_syntax_bytes": codec.MAX_BYTES,
            "max_syntax_depth": codec.MAX_SYNTAX_DEPTH, "max_syntax_nodes": codec.MAX_NODES,
            "max_token_bytes": codec.MAX_BYTES}
        for name, ceiling in ceilings.items():
            for bad in (True, -1, 1.0, ceiling + 1):
                for action, input_value in ((codec.pack, None), (codec.unpack, codec.pack(None))):
                    with self.subTest(name=name, bad=bad, action=action.__name__):
                        with self.assertRaises(ValueError):
                            action(input_value, **{name: bad})

    def test_cancellation_during_all_long_operations(self):
        def fail_after(count):
            def check():
                nonlocal count
                count -= 1
                if count <= 0:
                    raise TimeoutError("injected_deadline")
            return check

        raw = codec.canonical({"body": "abcdef" * 500})
        packed = codec.pack({"body": "abcdef" * 500})
        actions = [lambda: codec.pack(["x"] * 100, tick=fail_after(20)),
            lambda: codec._compress(raw, tick=fail_after(20)),
            lambda: codec._inflate(codec._compress(raw), len(raw), tick=fail_after(20)),
            lambda: codec._preparse(raw, tick=fail_after(4)),
            lambda: codec.unpack(packed, tick=fail_after(20))]
        for index, action in enumerate(actions):
            with self.subTest(index=index):
                with self.assertRaisesRegex(TimeoutError, "injected_deadline"):
                    action()
        for action, input_value in ((codec.pack, None), (codec.unpack, packed)):
            with self.assertRaises(ValueError):
                action(input_value, tick=3)

    def test_independent_module_does_not_import_production(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(codec))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names)
        self.assertFalse(any(name and (name.startswith("solo_distill")
                                      or name.startswith("schema_v3_")) for name in imports))


if __name__ == "__main__":
    unittest.main()
