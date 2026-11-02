"""阶段 4 操作内复用回归；隔离来源、完整双消费及输入负控。"""
from __future__ import annotations

import ast
import copy
import unittest
from unittest.mock import patch

from schema_v3_core_oracle import expected_core
from schema_v3_selection_oracle import expected_selection
from schema_v3_snapshot_oracle import oracle_metrics, oracle_scope, parsed_tree
from test_schema_v3_cold_chain import driver
from test_schema_v3_stdio import Fixture


class ColdReuseTests(Fixture):
    def test_parent_and_service_parse_once_per_source_and_keep_two_consumers(self):
        module = driver()
        result = module.run_preflight(self.root, self.arguments(), "preflight-1")
        self.assertEqual(result["status"], "pass")
        reuse = result["syntax_reuse"]
        for name in ("production_driver", "service_preparation"):
            self.assertEqual(reuse[name]["parse_calls"], len(self.files))
        self.assertEqual(reuse["independent_oracle"]["parse_calls"], len(self.files))
        self.assertEqual(reuse["independent_oracle"]["inventory_builds"], len(self.files))
        self.assertEqual(reuse["independent_oracle"]["core_builds"], 1)
        self.assertTrue(result["save"]["exact_readback"])
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertEqual(oracle_metrics()["parse_calls"], 0)
        from solo_distill.snapshot_v2 import snapshot_metrics
        self.assertEqual(snapshot_metrics()["parse_calls"], 0)

    def test_independent_snapshot_matches_uncached_complete_core_and_isolates_mutation(self):
        raw = self.files
        _, _, selection = expected_selection(raw, project_id="fixture", snapshot_id="snapshot",
            question="interfaces", dimensions=self.arguments()["dimensions"],
            frozen_coverage={"parsed_files": len(raw)})
        trusted = {"project_id": "fixture", "snapshot_id": "snapshot",
            "required_core_unit_ids": selection["core_unit_ids"],
            "selection_id": selection["selection_id"]}
        baseline = expected_core(raw, **trusted)

        @oracle_scope
        def check():
            first = expected_core(raw, **trusted)
            self.assertEqual(first, baseline)
            first["package"]["macro"]["files"].clear()
            self.assertEqual(expected_core(dict(raw), **trusted), baseline)
            metrics = oracle_metrics()
            self.assertEqual(metrics["core_builds"], 1)
            self.assertEqual(metrics["parse_calls"], len(raw))
            for changes in ({"required_core_unit_ids": []},
                            {"required_core_unit_ids": trusted["required_core_unit_ids"] * 2},
                            {"required_core_unit_ids": ["not-a-source"]}):
                with self.assertRaises(ValueError):
                    expected_core(raw, **{**trusted, **changes})
            changed = dict(raw)
            changed["sample.py"] = b"def interfaces():\n return 2\n"
            with self.assertRaises(ValueError):
                expected_core(changed, **trusted)
            calls = []
            def stopped():
                calls.append(True)
                raise RuntimeError("cancelled-cache-hit")
            with self.assertRaisesRegex(RuntimeError, "cancelled-cache-hit"):
                expected_core(raw, **trusted, tick=stopped)
            self.assertTrue(calls)
        check()
        self.assertEqual(oracle_metrics()["core_builds"], 0)

    def test_production_structure_reuse_keeps_full_comparison_and_detached_views(self):
        from solo_distill.package_v3 import build_package, validate_package
        from solo_distill.snapshot_v2 import snapshot_metrics, snapshot_scope
        from solo_distill.macro_v3 import build_macro
        from test_schema_v3_core import CoreV3Tests
        from test_schema_v3_local import SOURCES
        trusted = CoreV3Tests().trusted()
        baseline = expected_core(SOURCES, **trusted)["package"]

        @snapshot_scope
        def check():
            with patch("solo_distill.package_v3.build_macro", wraps=build_macro) as macro:
                first = build_package(SOURCES, **trusted)
                self.assertEqual(first, baseline)
                first["macro"]["symbols"].clear()
                self.assertEqual(build_package(dict(SOURCES), **trusted), baseline)
                validate_package(baseline, SOURCES, **trusted)
                forged = copy.deepcopy(baseline)
                forged["macro"]["symbols"].pop()
                with self.assertRaises(ValueError):
                    validate_package(forged, SOURCES, **trusted)
                self.assertEqual(macro.call_count, 1)
                self.assertEqual(snapshot_metrics()["structure_builds"], 1)
                for changes in ({"required_core_unit_ids": []},
                                {"required_core_unit_ids": trusted["required_core_unit_ids"] * 2},
                                {"required_core_unit_ids": ["not-a-source"]},
                                {"project_id": True}, {"selection_id": False}):
                    with self.subTest(changes=changes), self.assertRaises(ValueError):
                        build_package(SOURCES, **{**trusted, **changes})
                for key in ("project_id", "snapshot_id", "selection_id"):
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        validate_package(baseline, SOURCES, **{**trusted, key: "other"})
                for raw in ({k: v for k, v in SOURCES.items() if k != "unused.py"},
                            {**SOURCES, "unused.py": SOURCES["unused.py"] + b"# drift\n"},
                            {**SOURCES, "new.py": b"# added\n"}):
                    with self.assertRaises(ValueError):
                        validate_package(baseline, raw, **trusted)
                def stopped():
                    raise RuntimeError("structure-cache-hit-cancelled")
                with self.assertRaisesRegex(RuntimeError, "structure-cache-hit-cancelled"):
                    build_package(SOURCES, **trusted, tick=stopped)
        check()
        self.assertEqual(snapshot_metrics()["structure_builds"], 0)
        self.assertEqual(build_package(SOURCES, **trusted), baseline)

    def test_structure_scope_releases_after_failure_and_does_not_cache_failed_build(self):
        from solo_distill.snapshot_v2 import cached_structure, snapshot_metrics, snapshot_scope
        raw = {"sample.py": b"pass\n"}
        calls = []
        def build():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("failed-build")
            return {"complete": [1]}

        @snapshot_scope
        def check():
            with self.assertRaisesRegex(RuntimeError, "failed-build"):
                cached_structure("fixture", raw, b"trusted", build, lambda: None)
            self.assertEqual(snapshot_metrics()["structure_builds"], 0)
            value = cached_structure("fixture", raw, b"trusted", build, lambda: None)
            value["complete"].clear()
            self.assertEqual(cached_structure("fixture", raw, b"trusted", build,
                                             lambda: None), {"complete": [1]})
            self.assertEqual(len(calls), 2)
            raise RuntimeError("scope-failure")
        with self.assertRaisesRegex(RuntimeError, "scope-failure"):
            check()
        self.assertEqual(snapshot_metrics()["structure_builds"], 0)
        self.assertEqual(cached_structure("fixture", raw, b"trusted", build,
                                         lambda: None), {"complete": [1]})
        self.assertEqual(len(calls), 3)

    def test_oracle_tree_keys_bind_path_bytes_and_scope_does_not_survive_failure(self):
        raw = b"def interfaces():\n return 1\n"
        @oracle_scope
        def check():
            first = parsed_tree(raw, "a.py")
            self.assertIs(parsed_tree(raw, "a.py"), first)
            self.assertIsNot(parsed_tree(raw, "b.py"), first)
            self.assertIsNot(parsed_tree(raw + b"# changed\n", "a.py"), first)
            self.assertEqual(oracle_metrics()["parse_calls"], 3)
            raise RuntimeError("scope-failure")
        with self.assertRaisesRegex(RuntimeError, "scope-failure"):
            check()
        self.assertEqual(oracle_metrics()["parse_calls"], 0)
        with patch("schema_v3_snapshot_oracle.ast.parse", wraps=ast.parse) as parse:
            parsed_tree(raw, "a.py")
            parsed_tree(raw, "a.py")
            self.assertEqual(parse.call_count, 2)


if __name__ == "__main__":
    unittest.main()
