"""生产消费诊断的扫描观察值比较回归；不启动服务、读取来源或联网。"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


def diagnostic():
    path = (Path(__file__).resolve().parents[1] / "verification/.scratch"
            / "mainline_a_schema_v3_consume_diagnostic_20261031T1325Z.py")
    spec = importlib.util.spec_from_file_location("schema_v3_consume_diagnostic_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReportComparisonTests(unittest.TestCase):
    def setUp(self):
        self.compare = diagnostic().reports_match_except_elapsed
        self.saved = {
            "snapshot_id": "fixture-snapshot",
            "coverage": {"parsed_files": 1},
            "entries": [{"path": "sample.py", "content_sha256": "a" * 64}],
            "observations": {"elapsed_seconds": 0.75, "content_bytes_read": 10,
                             "model_requests": 0, "target_writes": 0},
        }
        self.fresh = copy.deepcopy(self.saved)
        self.fresh["observations"]["elapsed_seconds"] = 1.25

    def test_only_elapsed_difference_is_allowed_without_mutating_inputs(self):
        before = copy.deepcopy((self.saved, self.fresh))
        self.assertTrue(self.compare(self.saved, self.saved))
        self.assertTrue(self.compare(self.saved, self.fresh))
        self.assertEqual((self.saved, self.fresh), before)

    def test_source_snapshot_coverage_or_extra_fields_still_reject(self):
        mutations = [
            lambda value: value.update(snapshot_id="changed-snapshot"),
            lambda value: value["coverage"].update(parsed_files=2),
            lambda value: value["entries"][0].update(content_sha256="b" * 64),
            lambda value: value["entries"].append({"path": "extra.py"}),
            lambda value: value.update(extra=True),
            lambda value: value.pop("coverage"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                changed = copy.deepcopy(self.fresh)
                mutate(changed)
                self.assertFalse(self.compare(self.saved, changed))

    def test_other_observation_changes_missing_or_extra_keys_reject(self):
        for key in ("content_bytes_read", "model_requests", "target_writes"):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.fresh)
                changed["observations"][key] += 1
                self.assertFalse(self.compare(self.saved, changed))
                changed["observations"].pop(key)
                self.assertFalse(self.compare(self.saved, changed))
        changed = copy.deepcopy(self.fresh)
        changed["observations"]["extra"] = 0
        self.assertFalse(self.compare(self.saved, changed))

    def test_elapsed_requires_finite_nonnegative_number_on_both_sides(self):
        for invalid in (True, False, None, "0.75", -1, float("inf"),
                        float("-inf"), float("nan")):
            for side in ("saved", "fresh"):
                with self.subTest(invalid=repr(invalid), side=side):
                    saved, fresh = copy.deepcopy((self.saved, self.fresh))
                    target = saved if side == "saved" else fresh
                    target["observations"]["elapsed_seconds"] = invalid
                    self.assertFalse(self.compare(saved, fresh))

    def test_missing_or_invalid_report_observation_containers_reject(self):
        for invalid in (None, [], "report", {"snapshot_id": "fixture-snapshot"}):
            with self.subTest(report=repr(invalid)):
                self.assertFalse(self.compare(invalid, self.fresh))
                self.assertFalse(self.compare(self.saved, invalid))
        for invalid in (None, [], {}, {"model_requests": 0}):
            changed = copy.deepcopy(self.fresh)
            changed["observations"] = invalid
            with self.subTest(observations=repr(invalid)):
                self.assertFalse(self.compare(self.saved, changed))
                self.assertFalse(self.compare(changed, self.saved))


if __name__ == "__main__":
    unittest.main()
