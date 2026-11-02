"""独立候选语法正负控；无网络、无生产解析器、无输出修补。"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from schema_v3_candidate_syntax_oracle import SyntaxRejection, parse_candidate


class CandidateSyntaxOracleTests(unittest.TestCase):
    def test_legal_whole_candidate_preserves_every_field(self):
        candidate = {"summary": "合法完整候选", "conclusions": [
            {"dimension_id": "interfaces", "text": "第一条", "source_ids": ["body", "alias"]},
            {"dimension_id": "query_flow", "text": "第二条", "source_ids": ["other"]}],
            "unknowns": ["未证明的运行时行为"]}
        output = json.dumps(candidate, ensure_ascii=False)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            self.assertEqual(parse_candidate(output), candidate)
            self.assertEqual(parse_candidate(" \n" + output + "\n "), candidate)

    def test_existing_complete_fences_preserve_legal_output(self):
        for header in ("```", "```json"):
            with self.subTest(header=header):
                self.assertEqual(parse_candidate(header + '\n{"a":1}\n```'), {"a": 1})

    def test_trailing_comma_is_rejected_without_repair(self):
        output = '{\n  "summary": "原文",\n}'
        original = output
        with self.assertRaises(SyntaxRejection) as caught:
            parse_candidate(output)
        rejection = caught.exception
        self.assertEqual(rejection.reason, "invalid_candidate_json_syntax")
        self.assertEqual(rejection.output, original)
        self.assertEqual(rejection.details["error_type"], "JSONDecodeError")
        self.assertEqual(rejection.details["position"], output.index("}"))
        self.assertEqual((rejection.details["line"], rejection.details["column"]), (3, 1))
        self.assertEqual(rejection.details["parsed_text_characters"], len(output))
        self.assertEqual(output, original)

    def test_syntax_locations_use_existing_fence_body_not_rewritten_json(self):
        body = '{\n"a":1,\n}'
        output = "  ```json\n" + body + "\n```  "
        with self.assertRaises(SyntaxRejection) as caught:
            parse_candidate(output)
        self.assertEqual(caught.exception.output, output)
        self.assertEqual(caught.exception.details["position"], body.index("}"))
        self.assertEqual(caught.exception.details["parsed_text_characters"], len(body))

    def test_unterminated_or_extra_text_is_syntax_not_salvaged(self):
        for output in ('{"a":', '{a:1}', '{"a":1} trailing', '{"a":1}\n{"b":2}'):
            with self.subTest(output=output), self.assertRaises(SyntaxRejection) as caught:
                parse_candidate(output)
            self.assertEqual(caught.exception.output, output)

    def test_duplicate_keys_nonfinite_and_bad_fences_are_not_syntax_retries(self):
        for output in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}',
                       '{"a":-Infinity}', '```python\n{}\n```', '```json\n{}',
                       '```json', '```json\n{}\n```\nextra'):
            with self.subTest(output=output), self.assertRaises(ValueError) as caught:
                parse_candidate(output)
            self.assertNotIsInstance(caught.exception, SyntaxRejection)

    def test_valid_json_shape_remains_for_separate_hard_gate(self):
        for output, expected in (("[]", []), ("null", None), ("true", True),
                                 ('{"wrong":1}', {"wrong": 1})):
            with self.subTest(output=output):
                self.assertEqual(parse_candidate(output), expected)

    def test_empty_or_nontext_is_not_authorized_completed_output(self):
        for output in ("", " \n", None, 1, True):
            with self.subTest(output=output), self.assertRaises(ValueError) as caught:
                parse_candidate(output)
            self.assertNotIsInstance(caught.exception, SyntaxRejection)


if __name__ == "__main__":
    unittest.main()
