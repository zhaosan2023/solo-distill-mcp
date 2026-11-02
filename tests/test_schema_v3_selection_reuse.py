"""选择物化的定向复用回归；隔离源码与独立完整期望，不证明真实冷链。

问题：每个候选尝试是否仍扫描未选单元来计算选中祖先？
范围：只检查选择物化、完整正文与别名、规范 POST 和冻结身份。
不联网、不执行来源、不启动服务或占用真实样本及在线接纳。
"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from schema_v3_selection_oracle import expected_selection
from solo_distill import scouting_v2
from solo_distill.scouting_v3 import select_prompt_v3
from test_schema_v3_selection_oracle import trusted


class SelectionMaterializationReuseTests(unittest.TestCase):
    def fixture(self):
        files = {
            "sample.py": (
                "def interfaces():\n return helper()\n"
                "def helper():\n return 1\n"
                "class Owner:\n"
                " def interfaces_method(self):\n  return helper()\n"
                " def unrelated(self):\n  return 0\n"
                "if enabled:\n"
                " def interfaces_guarded():\n  return helper()\n"
                "else:\n fallback = 0\n"
                "def interfaces_parent():\n"
                " def interfaces_inner():\n  return 2\n"
                " return interfaces_inner()\n"
            ).encode(),
            "unused.py": "".join(
                f"def unused_{index}():\n return {index}\n" for index in range(80)
            ).encode(),
        }
        return files, trusted(files)

    def test_owner_mapping_does_not_visit_unselected_units(self):
        files, context = self.fixture()
        expected = expected_selection(files, **context)
        original = scouting_v2.containing_selected
        calls = []

        def observed(units, selected):
            selected = list(selected)
            self.assertEqual(
                set(selected), set(range(len(units))),
                "selection_materialization_unselected_owner_scan",
            )
            calls.append(len(units))
            return original(units, selected)

        with patch.object(scouting_v2, "containing_selected", side_effect=observed):
            actual = select_prompt_v3(files, **context)
        self.assertTrue(calls)
        self.assertEqual(actual, expected)

    def test_complete_post_aliases_guards_and_identity_match_independent_oracle(self):
        files, context = self.fixture()
        for changes in ({}, {"body_limit": 500}, {"post_limit": 3500}):
            with self.subTest(changes=changes):
                self.assertEqual(
                    select_prompt_v3(files, **context, **changes),
                    expected_selection(files, **context, **changes),
                )

    def test_four_dimension_state_failure_and_caller_duties_remain_complete(self):
        files = {"sample.py": (
            "def interfaces():\n return helper()\n"
            "def helper():\n return 1\n"
            "class Owner:\n"
            " def state(self):\n  self.value = helper()\n"
            " def failure(self):\n  try:\n   return helper()\n"
            "  except ValueError:\n   raise\n"
            "def caller():\n return interfaces()\n"
        ).encode()}
        dimensions = [
            {"id": name, "question": question, "required": True,
             "evidence": "sourced_inference"}
            for name, question in (
                ("interfaces", "Describe interfaces"),
                ("state", "Explain state ownership"),
                ("failure", "Explain failure exceptions"),
                ("caller", "Explain caller callee"),
            )
        ]
        context = trusted(files, dimensions=dimensions)
        self.assertEqual(select_prompt_v3(files, **context), expected_selection(files, **context))

    def test_operation_results_are_detached_and_changed_sources_are_recomputed(self):
        files, context = self.fixture()
        baseline = expected_selection(files, **context)
        first = select_prompt_v3(files, **context)
        first[0].clear()
        first[2]["witness"]["allowed_source_ids"].clear()
        self.assertEqual(select_prompt_v3(dict(files), **copy.deepcopy(context)), baseline)
        changed = {**files, "sample.py": files["sample.py"].replace(b"return 1", b"return 3")}
        actual = select_prompt_v3(changed, **context)
        self.assertEqual(actual, expected_selection(changed, **context))
        self.assertNotEqual(actual[2]["input_id"], baseline[2]["input_id"])

    def test_materialization_keeps_cancellation_checks(self):
        files, context = self.fixture()
        original = scouting_v2.messages_for
        cancelled = False

        def materialized(*args, **kwargs):
            nonlocal cancelled
            cancelled = True
            return original(*args, **kwargs)

        def check():
            if cancelled:
                raise RuntimeError("selection_materialization_cancelled")

        with patch.object(scouting_v2, "messages_for", side_effect=materialized):
            with self.assertRaisesRegex(RuntimeError, "selection_materialization_cancelled"):
                select_prompt_v3(files, **context, tick=check)


if __name__ == "__main__":
    unittest.main()
