"""本次验收支撑的隔离负控；不读取真实来源、不联网、不保存业务证据。"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

import mainline_a_schema_v3_minimal_acceptance_20261102 as acceptance


class ReportComparisonTests(unittest.TestCase):
    def report(self):
        return {"snapshot_id": "fixture", "coverage": {"parsed_files": 1},
                "observations": {"elapsed_seconds": 1.0, "source_writes": 0}}

    def test_only_elapsed_may_change_and_inputs_are_preserved(self):
        left = self.report()
        right = copy.deepcopy(left)
        right["observations"]["elapsed_seconds"] = 2.0
        before = copy.deepcopy((left, right))
        self.assertTrue(acceptance.reports_equal_except_scan_elapsed(left, right))
        self.assertEqual((left, right), before)

    def test_source_and_coverage_drift_rejected(self):
        for field, replacement in (("snapshot_id", "changed"),
                                   ("coverage", {"parsed_files": 2})):
            with self.subTest(field=field):
                left = self.report()
                right = copy.deepcopy(left)
                right[field] = replacement
                self.assertFalse(acceptance.reports_equal_except_scan_elapsed(left, right))

    def test_other_observation_and_field_set_changes_rejected(self):
        for mode in ("change", "add", "remove"):
            with self.subTest(mode=mode):
                left = self.report()
                right = copy.deepcopy(left)
                if mode == "change":
                    right["observations"]["source_writes"] = 1
                elif mode == "add":
                    right["observations"]["new"] = 0
                else:
                    del right["observations"]["source_writes"]
                self.assertFalse(acceptance.reports_equal_except_scan_elapsed(left, right))

    def test_invalid_elapsed_on_either_side_rejected(self):
        for value in (None, True, "1", -1, float("nan"), float("inf")):
            for side in (0, 1):
                with self.subTest(value=value, side=side):
                    pair = [self.report(), self.report()]
                    pair[side]["observations"]["elapsed_seconds"] = value
                    self.assertFalse(acceptance.reports_equal_except_scan_elapsed(*pair))

    def test_missing_elapsed_and_nonobject_inputs_rejected(self):
        left = self.report()
        del left["observations"]["elapsed_seconds"]
        self.assertFalse(acceptance.reports_equal_except_scan_elapsed(left, self.report()))
        self.assertFalse(acceptance.reports_equal_except_scan_elapsed([], self.report()))
        right = self.report()
        right["observations"] = []
        self.assertFalse(acceptance.reports_equal_except_scan_elapsed(self.report(), right))


class AnchorLocationTests(unittest.TestCase):
    def fixture(self):
        syntax = {"files": [{"path": "fixture.py"}],
                  "units": [{"qualified_name": "Fixture.run", "start": list((1, 0)),
                             "end": list((20, 0))}],
                  "sources": [{"id": "source-1", "file": 0, "unit": 0}]}
        frozen = {"version": 1, "anchors": [
            {"id": "anchor-" + str(index), "path": "fixture.py",
             "symbol": "Fixture.run", "lines": list((5, 10)),
             "expected": "仅用于验收的隔离期望", "record_kind": "definition"}
            for index in range(13)]}
        return syntax, frozen

    def test_all_thirteen_locations_remain_semantically_pending(self):
        syntax, frozen = self.fixture()
        before = copy.deepcopy((syntax, frozen))
        sources, checks = acceptance.locate_anchors(syntax, frozen)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(checks), 13)
        self.assertTrue(all(check["body_coverage"] for check in checks))
        self.assertTrue(all(check["semantic_support"] == "requires_real_human_review"
                            for check in checks))
        self.assertEqual([check["expected"] for check in checks],
                         [anchor["expected"] for anchor in frozen["anchors"]])
        self.assertEqual((syntax, frozen), before)

    def test_path_symbol_and_containing_range_are_all_required(self):
        for field, value in (("path", "other.py"), ("symbol", "Other.run"),
                             ("lines", list((0, 10))), ("lines", list((5, 21)))):
            with self.subTest(field=field, value=value):
                syntax, frozen = self.fixture()
                anchor = next(iter(frozen["anchors"]))
                anchor[field] = value
                _, checks = acceptance.locate_anchors(syntax, frozen)
                check = next(iter(checks))
                self.assertFalse(check["body_coverage"])
                self.assertEqual(check["source_ids"], [])

    def test_missing_or_duplicate_anchor_is_rejected(self):
        for mode in ("missing", "duplicate"):
            with self.subTest(mode=mode):
                syntax, frozen = self.fixture()
                if mode == "missing":
                    frozen["anchors"].pop()
                else:
                    frozen["anchors"][-1]["id"] = next(iter(frozen["anchors"]))["id"]
                with self.assertRaisesRegex(ValueError, "frozen_anchor_contract"):
                    acceptance.locate_anchors(syntax, frozen)


class AssetVerificationTests(unittest.TestCase):
    def fixture(self):
        raw = b"fixture"
        asset = {"path": acceptance.support.PARENT + "/fixture.json",
                 "bytes": len(raw), "sha256": acceptance.support.digest(raw),
                 "exact_readback": True}
        return raw, asset

    def test_exact_saved_bytes_are_required(self):
        raw, asset = self.fixture()
        with patch.object(acceptance.support, "read", return_value=raw):
            self.assertEqual(acceptance.checked_asset(asset), raw)
        with patch.object(acceptance.support, "read", return_value=b"changed"):
            with self.assertRaisesRegex(ValueError, "asset_changed"):
                acceptance.checked_asset(asset)

    def test_bad_asset_declaration_is_rejected_before_read(self):
        for field, value in (("path", "elsewhere/fixture.json"), ("bytes", True),
                             ("bytes", -1), ("bytes", 33554433),
                             ("exact_readback", False)):
            with self.subTest(field=field, value=value):
                _, asset = self.fixture()
                asset[field] = value
                with patch.object(acceptance.support, "read") as read:
                    with self.assertRaisesRegex(ValueError, "asset_fields"):
                        acceptance.checked_asset(asset)
                    read.assert_not_called()


class BindingVerificationTests(unittest.TestCase):
    def fixture(self):
        support = acceptance.support
        paths = {"plan_sha256": support.PLAN,
                 "execution_contract_sha256": support.CONTRACT,
                 "adr_sha256": "_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md",
                 "projects_sha256": "config/projects.json",
                 "policy_sha256": "config/distillation.json"}
        files = {path: ("fixture:" + path).encode() for path in paths.values()}
        bound = {key: support.digest(files[path]) for key, path in paths.items()}
        bound.update(plan_contract_version=6, adr_revision=13,
                     execution_contract_revision=14, session_id=None)
        receipt = {"status": "online_transport_saved_trusted_consumption_pending",
                   "failures": [], "execution_instance": support.INSTANCE,
                   "request_id": support.REQUEST_ID, "service_exit_code": 0,
                   "service_cleanup": "confirmed_natural_exit", "binding": bound}
        return files, receipt

    def verify(self, files, receipt, mutation=None):
        raw = json.dumps(receipt).encode()
        files = dict(files)
        files[acceptance.support.PARENT + "/" + acceptance.support.LIVE + ".receipt.json"] = raw
        if mutation is not None:
            files[mutation] = b"changed"
        with patch.object(acceptance, "LIVE_RECEIPT_SHA256", acceptance.support.digest(raw)), \
             patch.object(acceptance.support, "read", side_effect=lambda path: files[path]), \
             patch.object(acceptance.support, "verify_prior_preservation") as prior:
            result = acceptance.binding()
            prior.assert_called_once_with(receipt["binding"])
            return result

    def test_valid_binding_uses_no_new_online_admission(self):
        files, receipt = self.fixture()
        with patch.object(acceptance.support, "binding", side_effect=AssertionError("不得重新准入")):
            self.assertEqual(self.verify(files, receipt), receipt)

    def test_any_bound_file_change_is_rejected(self):
        files, receipt = self.fixture()
        for path in files:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "binding_changed"):
                    self.verify(files, receipt, mutation=path)

    def test_failure_or_nonzero_exit_cannot_become_acceptance(self):
        for field, value in (("status", "blocked"), ("failures", ["failed"]),
                             ("service_exit_code", 2), ("service_exit_code", False),
                             ("service_cleanup", "unknown")):
            with self.subTest(field=field):
                files, receipt = self.fixture()
                receipt[field] = value
                with self.assertRaisesRegex(ValueError, "live_not_complete"):
                    self.verify(files, receipt)

    def test_version_drift_and_fabricated_session_rejected(self):
        for field, value in (("plan_contract_version", 5), ("adr_revision", 12),
                             ("execution_contract_revision", 13), ("session_id", "invented")):
            with self.subTest(field=field):
                files, receipt = self.fixture()
                receipt["binding"][field] = value
                with self.assertRaisesRegex(ValueError, "binding_version"):
                    self.verify(files, receipt)


if __name__ == "__main__":
    unittest.main()
