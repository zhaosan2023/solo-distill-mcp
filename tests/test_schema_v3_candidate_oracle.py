"""候选恢复的独立来源硬门禁正负控；不导入生产恢复器、不访问网关。

整份非法候选仍须拒绝，合法别名和全部结论必须保留；不证明在线或人工语义。
"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from schema_v3_task_oracle import expected_task
from test_schema_v3_task_oracle import fixture
from test_schema_v3_local import SOURCES


class CandidateOracleTests(unittest.TestCase):
    def test_all_legal_conclusions_and_unknowns_are_preserved(self):
        candidate, trusted, *_ = fixture()
        original = copy.deepcopy(candidate)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            task = expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(task["status"], "complete")
        self.assertEqual(len(task["facts"]), len(original["conclusions"]))
        self.assertEqual([item["text"] for item in task["facts"]],
                         [item["text"] for item in original["conclusions"]])
        self.assertEqual(task["unknowns"], original["unknowns"])
        self.assertEqual(candidate, original)

    def test_one_unsent_source_rejects_whole_candidate_without_mutation(self):
        candidate, trusted, *_ = fixture()
        candidate["conclusions"][2]["source_ids"] = ["unsent-source"]
        before = copy.deepcopy(candidate)
        with self.assertRaisesRegex(ValueError, "invalid_or_unsent_conclusion"):
            expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(candidate, before)
        self.assertEqual(len(candidate["conclusions"]), 4)

    def test_known_but_unsent_symbol_is_not_a_body_source(self):
        candidate, trusted, _, _, core = fixture()
        hidden = next(row[0] for row in core["package"]["macro"]["symbols"]
                      if row[4] == "Service.unrelated")
        candidate["conclusions"][0]["source_ids"] = [hidden]
        with self.assertRaisesRegex(ValueError, "invalid_or_unsent_conclusion"):
            expected_task(candidate, SOURCES, **trusted)

    def test_actual_nested_alias_is_legal_without_rewriting(self):
        files = {"guard.py": b"if enabled:\n    def chosen():\n        return target()\n"}
        candidate, trusted, messages, _, _ = fixture(files, {("guard.py", "chosen")})
        import json
        record = json.loads(messages[1]["content"])["sources"][0]
        alias = next(key for key in record["alias_source_ids"] if key != record["source_id"])
        candidate["conclusions"][0]["source_ids"] = [alias]
        task = expected_task(candidate, files, **trusted)
        self.assertEqual(task["facts"][0]["sources"][0]["id"], alias)
        self.assertEqual(len(task["facts"]), len(candidate["conclusions"]))

    def test_empty_duplicate_nonstrings_and_wrong_dimensions_stay_invalid(self):
        candidate, trusted, _, allowed, _ = fixture()
        changes = [{"source_ids": []}, {"source_ids": [allowed[0], allowed[0]]},
                   {"source_ids": [True]}, {"source_ids": [None]},
                   {"source_ids": [""]}, {"dimension_id": "unknown"}, {"text": " "}]
        for change in changes:
            altered = copy.deepcopy(candidate)
            altered["conclusions"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                expected_task(altered, SOURCES, **trusted)

    def test_unsent_source_does_not_hide_another_malformed_conclusion(self):
        candidate, trusted, *_ = fixture()
        candidate["conclusions"][0]["source_ids"] = ["unsent-source"]
        candidate["conclusions"][-1]["dimension_id"] = "unknown"
        before = copy.deepcopy(candidate)
        with self.assertRaises(ValueError):
            expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(candidate, before)

    def test_trusted_post_or_source_conflict_is_not_candidate_acceptance(self):
        candidate, trusted, *_ = fixture()
        candidate["conclusions"][0]["source_ids"] = ["unsent-source"]
        with self.assertRaisesRegex(ValueError, "frozen_post_or_source_conflict"):
            expected_task(candidate, SOURCES, **{**trusted, "post": trusted["post"] + b"\n"})
        changed = {**SOURCES, "unused.py": SOURCES["unused.py"] + b"# changed\n"}
        with self.assertRaises(ValueError):
            expected_task(candidate, changed, **trusted)


if __name__ == "__main__":
    unittest.main()
