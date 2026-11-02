"""显式 V3 生产选择与独立 Oracle 差分；不证明完整阶段门禁。"""
from __future__ import annotations

import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from schema_v3_selection_oracle import expected_selection
import test_schema_v3_selection_oracle as selection_tests
from test_schema_v3_selection_oracle import resign, trusted


class ProductionSelectionTests(selection_tests.IndependentSelectionOracleTests):
    def choose(self, files, **changes):
        expected = super().choose(files, **changes)
        from solo_distill.scouting_v3 import select_prompt_v3, authenticate_selection
        context = trusted(files, **changes)
        with patch("socket.socket", side_effect=AssertionError("offline only")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")), \
                patch("solo_distill.package_v2.build_package",
                      side_effect=AssertionError("no full legacy knowledge package")):
            messages, post, frozen = select_prompt_v3(files, **context)
            self.assertEqual(post, expected[1])
            self.assertEqual(frozen, expected[2])
            import json
            self.assertEqual(json.loads(messages[-1]["content"]), expected[0])
            self.assertEqual(authenticate_selection(frozen, files, **context), frozen)
        return expected

    def test_production_authentication_rejects_resigned_selection_changes(self):
        from solo_distill.scouting_v3 import select_prompt_v3, authenticate_selection
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        context = trusted(files)
        _, _, frozen = select_prompt_v3(files, **context)
        mutations = [lambda v: v.update(core_unit_ids=[]),
                     lambda v: v["witness"].update(duties=[]),
                     lambda v: v["witness"].update(allowed_source_ids=["invented"]),
                     lambda v: v["witness"].update(candidate_count=True),
                     lambda v: v.update(selection_version="unknown")]
        for mutate in mutations:
            value = copy.deepcopy(frozen)
            mutate(value)
            resign(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                authenticate_selection(value, files, **context)
        self.assertEqual(frozen, expected_selection(files, **context)[2])

    def test_production_strict_inputs_and_source_identity(self):
        from solo_distill.scouting_v3 import select_prompt_v3, authenticate_selection
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        context = trusted(files)
        _, _, frozen = select_prompt_v3(files, **context)
        cases = [{"body_limit": True}, {"post_limit": True},
                 {"project_id": ""}, {"snapshot_id": ""},
                 {"dimensions": context["dimensions"] * 2},
                 {"frozen_coverage": {"parsed_files": 1.0}}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                select_prompt_v3(files, **{**context, **changes})
        changed = {"sample.py": files["sample.py"] + b"# changed\n"}
        with self.assertRaises(ValueError):
            authenticate_selection(frozen, changed, **context)
        self.assertEqual(frozen, expected_selection(files, **context)[2])

    def test_production_cancel_and_operation_local_index(self):
        from solo_distill.scouting_v3 import select_prompt_v3
        from solo_distill.snapshot_v2 import snapshot_metrics, snapshot_scope
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        def cancelled():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            select_prompt_v3(files, **trusted(files), tick=cancelled)

        @snapshot_scope
        def operation():
            first = select_prompt_v3(files, **trusted(files))
            count = snapshot_metrics()["parse_calls"]
            second = select_prompt_v3(files, **trusted(files))
            self.assertEqual(first, second)
            self.assertEqual(snapshot_metrics()["parse_calls"], count)
            return count
        self.assertEqual(operation(), 1)
        self.assertEqual(operation(), 1)
        self.assertEqual(snapshot_metrics()["parse_calls"], 0)

    def test_production_selection_does_not_import_reference_or_oracle_assets(self):
        root = Path(__file__).resolve().parents[1] / "src" / "solo_distill"
        pending, seen = ["scouting_v3"], set()
        while pending:
            module = pending.pop()
            if module in seen:
                continue
            seen.add(module)
            tree = ast.parse((root / (module + ".py")).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    name = node.module or ""
                    self.assertFalse("oracle" in name or name.startswith("tests"), name)
                    if node.level == 1 and (root / (name + ".py")).is_file():
                        pending.append(name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse("oracle" in alias.name or alias.name.startswith("tests"))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("fastcode_acceptance.json", node.value)


if __name__ == "__main__":
    unittest.main()
