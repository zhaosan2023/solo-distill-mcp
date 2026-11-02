"""选择预算编码定向回归；独立完整期望，不运行真实冷样本或在线业务。

问题：候选尝试是否反复双层 JSON 编码全部正文；能否操作内精确计量，
仅在最终选择编码完整 POST，保持边界、别名、守卫、身份和取消不变？
范围：共享选择及 V3 调用方，现有标准库与隔离来源；不共享 Oracle 实现。
工作量断言不证明耗时收益，真实 27／30 秒冷链仍须另行验收。
"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from schema_v3_selection_oracle import expected_selection
from solo_distill import scouting_v2
from solo_distill.evidence import EvidenceBlocked
from solo_distill.scouting_v3 import select_prompt_v3
import test_schema_v3_selection_reuse as materialization
from test_schema_v3_selection_oracle import trusted


class SelectionEncodingTests(unittest.TestCase):
    def fixture(self):
        return materialization.SelectionMaterializationReuseTests().fixture()

    def test_complete_post_encoding_occurs_only_for_empty_budget_and_final_selection(self):
        files, context = self.fixture()
        expected = expected_selection(files, **context)
        with patch.object(scouting_v2, "encode_request", wraps=scouting_v2.encode_request) as encode:
            actual = select_prompt_v3(files, **context)
        self.assertEqual(actual, expected)
        self.assertEqual(encode.call_count, 2, "selection_repeated_complete_post_encoding")

    def test_double_escaping_unicode_guards_aliases_and_budgets_match_independent(self):
        literal = "引号\"反斜杠\\制表\t换行\n值" * 10
        files = {"sample.py": (
            "if enabled:\n"
            " def interfaces():\n"
            "  def interfaces_inner():\n"
            "   return " + repr(literal) + "\n"
            "  return interfaces_inner()\n"
            "else:\n fallback = 0\n"
            "def interfaces_other():\n return '另一正文'\n"
        ).encode("utf-8")}
        context = trusted(files)
        for limits in ({}, {"body_limit": 800}, {"post_limit": 4000}):
            with self.subTest(limits=limits):
                self.assertEqual(select_prompt_v3(files, **context, **limits),
                                 expected_selection(files, **context, **limits))

    def test_exact_post_and_body_boundaries_and_fixed_metadata_block(self):
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        context = trusted(files)
        expected = expected_selection(files, **context)
        limits = {"body_limit": expected[2]["witness"]["unique_body_bytes"],
                  "post_limit": len(expected[1])}
        self.assertEqual(select_prompt_v3(files, **context, **limits),
                         expected_selection(files, **context, **limits))
        for reduced in ({**limits, "body_limit": limits["body_limit"] - 1},
                        {**limits, "post_limit": limits["post_limit"] - 1}):
            with self.subTest(reduced=reduced), self.assertRaisesRegex(
                    EvidenceBlocked, "required_group_search_exhausted"):
                select_prompt_v3(files, **context, **reduced)
        with self.assertRaisesRegex(EvidenceBlocked, "fixed_question_metadata"):
            select_prompt_v3(files, **context, post_limit=100)

    def test_cache_is_operation_local_and_context_source_changes_recompute(self):
        files, context = self.fixture()
        baseline = expected_selection(files, **context)
        actual = select_prompt_v3(files, **context)
        actual[0].clear()
        actual[2]["witness"]["allowed_source_ids"].clear()
        self.assertEqual(select_prompt_v3(files, **copy.deepcopy(context)), baseline)
        changed = {**files, "sample.py": files["sample.py"].replace(b"return 1", b"return 3")}
        altered = {**context, "frozen_coverage": {"parsed_files": len(files), "note": "变化\"\\"}}
        self.assertEqual(select_prompt_v3(changed, **altered),
                         expected_selection(changed, **altered))

    def test_final_encoding_retains_cancellation_check(self):
        files, context = self.fixture()
        original = scouting_v2.encode_request
        cancelled = False

        def encode(messages):
            nonlocal cancelled
            value = original(messages)
            if '"sources":[]' not in messages[-1]["content"]:
                cancelled = True
            return value

        def check():
            if cancelled:
                raise RuntimeError("selection_final_encoding_cancelled")

        with patch.object(scouting_v2, "encode_request", side_effect=encode):
            with self.assertRaisesRegex(RuntimeError, "selection_final_encoding_cancelled"):
                select_prompt_v3(files, **context, tick=check)


if __name__ == "__main__":
    unittest.main()
