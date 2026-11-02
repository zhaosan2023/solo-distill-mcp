"""非法来源候选类型边界；独立期望不容错，不访问网关。"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from schema_v3_task_oracle import expected_task
from solo_distill.core_v3 import SealedCore
from solo_distill.task_v3 import InvalidSourceCandidate, build_task
from test_schema_v3_local import SOURCES
from test_schema_v3_task_oracle import fixture
from schema_v3_macro_oracle import canonical


class CandidateClassificationTests(unittest.TestCase):
    def case(self, files=SOURCES, names=None):
        candidate, trusted, messages, allowed, document = fixture(files, names)
        return candidate, trusted, messages, allowed, SealedCore(canonical(document))

    def test_legal_candidate_matches_independent_without_losing_text(self):
        candidate, trusted, _, _, core = self.case()
        before = copy.deepcopy(candidate)
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            actual = build_task(candidate, core, SOURCES, **trusted)
            expected = expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(actual, expected)
        self.assertEqual(candidate, before)
        self.assertEqual(len(actual["facts"]), len(candidate["conclusions"]))
        self.assertEqual(actual["unknowns"], candidate["unknowns"])

    def test_unsent_reference_is_typed_and_whole_candidate_is_unchanged(self):
        candidate, trusted, _, _, core = self.case()
        candidate["conclusions"][2]["source_ids"] = ["unsent-source"]
        before = copy.deepcopy(candidate)
        with self.assertRaises(InvalidSourceCandidate) as caught:
            build_task(candidate, core, SOURCES, **trusted)
        self.assertEqual(caught.exception.reason, "invalid_or_unsent_source_id")
        self.assertEqual(caught.exception.invalid_references, ((2, "unsent-source"),))
        self.assertEqual(candidate, before)
        with self.assertRaises(ValueError):
            expected_task(candidate, SOURCES, **trusted)

    def test_known_unsent_macro_symbol_is_still_rejected(self):
        candidate, trusted, _, _, core = self.case()
        hidden = next(row[0] for row in core.document["package"]["macro"]["symbols"]
                      if row[4] == "Service.unrelated")
        candidate["conclusions"][0]["source_ids"] = [hidden]
        with self.assertRaises(InvalidSourceCandidate):
            build_task(candidate, core, SOURCES, **trusted)

    def test_real_nested_alias_is_accepted_without_rewriting(self):
        files = {"guard.py": b"if enabled:\n    def chosen():\n        return target()\n"}
        candidate, trusted, messages, _, core = self.case(files, {("guard.py", "chosen")})
        record = json.loads(messages[1]["content"])["sources"][0]
        alias = next(key for key in record["alias_source_ids"] if key != record["source_id"])
        candidate["conclusions"][0]["source_ids"] = [alias]
        actual = build_task(candidate, core, files, **trusted)
        self.assertEqual(actual, expected_task(candidate, files, **trusted))
        self.assertEqual(actual["facts"][0]["sources"][0]["id"], alias)

    def test_shape_errors_are_not_typed_even_after_an_unknown_reference(self):
        candidate, trusted, _, allowed, core = self.case()
        changes = [{"source_ids": []}, {"source_ids": [allowed[0], allowed[0]]},
                   {"source_ids": [True]}, {"source_ids": [None]},
                   {"source_ids": [""]}, {"source_ids": [" "]},
                   {"dimension_id": "unknown"}, {"text": " "}, {"extra": True}]
        for change in changes:
            altered = copy.deepcopy(candidate)
            altered["conclusions"][0]["source_ids"] = ["unsent-source"]
            altered["conclusions"][-1].update(change)
            before = copy.deepcopy(altered)
            with self.subTest(change=change), self.assertRaises(ValueError) as caught:
                build_task(altered, core, SOURCES, **trusted)
            self.assertNotIsInstance(caught.exception, InvalidSourceCandidate)
            self.assertEqual(altered, before)

    def test_trusted_post_or_source_conflict_is_not_candidate_recovery(self):
        candidate, trusted, _, _, core = self.case()
        candidate["conclusions"][0]["source_ids"] = ["unsent-source"]
        for files, inputs in ((SOURCES, {**trusted, "post": trusted["post"] + b"\n"}),
                              ({**SOURCES, "unused.py": SOURCES["unused.py"] + b"# changed\n"}, trusted)):
            with self.subTest(post=inputs["post"]), self.assertRaises(ValueError) as caught:
                build_task(candidate, core, files, **inputs)
            self.assertNotIsInstance(caught.exception, InvalidSourceCandidate)


if __name__ == "__main__":
    unittest.main()
