"""独立选择 Oracle 的手工期望、身份绑定及篡改负控；不调用生产实现。

此组仅验收独立资产，不能代替生产选择对照、全离线门禁或人工语义。
"""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import identity
from schema_v3_selection_oracle import authenticate_selection, expected_selection


def dimension(question, key="interfaces", required=True):
    return {"id": key, "question": question, "required": required,
            "evidence": "sourced_inference"}


def trusted(files, **changes):
    value = {"project_id": "fixture", "snapshot_id": "snapshot",
             "question": "Explain static interfaces",
             "dimensions": [dimension("Describe interfaces")],
             "frozen_coverage": {"parsed_files": len(files)}}
    value.update(changes)
    return value


def resign(value):
    unsigned = {key: item for key, item in value.items() if key != "selection_id"}
    value["selection_id"] = identity("selection-v3", unsigned)
    return value


class IndependentSelectionOracleTests(unittest.TestCase):
    def choose(self, files, **changes):
        context = trusted(files, **changes)
        with patch("socket.socket", side_effect=AssertionError("offline only")), \
                patch("subprocess.Popen", side_effect=AssertionError("source is data")):
            messages, post, frozen = expected_selection(files, **context)
        import json
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(frozen["witness"]["post_bytes"], len(post))
        self.assertEqual(frozen["witness"]["post_sha256"], hashlib.sha256(post).hexdigest())
        self.assertEqual(frozen["witness"]["unique_body_bytes"],
                         sum(len(row["text"].encode("utf-8")) for row in payload["sources"]))
        return payload, post, frozen, context

    def test_manual_single_definition_identity_and_complete_body(self):
        raw = b"def interfaces():\n return 1\n"
        files = {"sample.py": raw}
        payload, _, frozen, context = self.choose(files)
        file_id = identity("file-v2", ["sample.py", hashlib.sha256(raw).hexdigest(),
                                      "python-3.12-ast-v1"])
        span = {"start": [1, 0], "end": [2, 9], "char_start": [1, 0],
                "char_end": [2, 9], "raw_start": 0, "raw_end": len(raw) - 1}
        unit_id = identity("unit-v2", [file_id, ["body", 0], "definition", "interfaces", span])
        self.assertEqual(frozen["core_unit_ids"], [unit_id])
        self.assertEqual(frozen["witness"]["owners"], [unit_id])
        self.assertEqual(payload["sources"], [{"source_id": identity("source-v2", unit_id),
            "alias_source_ids": [identity("source-v2", unit_id)], "path": "sample.py",
            "symbol": "interfaces", "text": raw.decode().rstrip("\n")}])
        self.assertEqual(authenticate_selection(frozen, files, **context)["status"], "pass")

    def test_four_dimensions_have_independent_direct_duties(self):
        names = ("alpha", "beta", "gamma", "delta")
        files = {"sample.py": "".join(f"def {name}():\n return 1\n" for name in names).encode()}
        payload, _, frozen, _ = self.choose(files,
            dimensions=[dimension(name, name) for name in names])
        self.assertEqual({row["symbol"] for row in payload["sources"]}, set(names))
        self.assertEqual({row["dimension_id"] for row in frozen["witness"]["duties"]}, set(names))
        self.assertTrue(all(row["duty"] == "direct_question" for row in frozen["witness"]["duties"]))

    def test_required_zero_hits_and_state_failure_gaps_block(self):
        cases = [("interfaces", b"def unrelated():\n return 1\n", "no_direct_body"),
                 ("state ownership", b"def state():\n return 1\n", "state_write"),
                 ("failure exceptions", b"def failure():\n return 1\n", "failure_boundary")]
        for question, raw, reason in cases:
            files = {"sample.py": raw}
            with self.subTest(question=question), self.assertRaisesRegex(ValueError, reason):
                expected_selection(files, **trusted(files, dimensions=[dimension(question)]))

    def test_method_not_whole_class_or_sibling_alias(self):
        raw = (b"class Owner:\n def interfaces(self):\n  return 1\n"
               b" def unrelated(self):\n  return '" + b"x" * 100000 + b"'\n")
        payload, _, frozen, _ = self.choose({"sample.py": raw})
        self.assertEqual([row["symbol"] for row in payload["sources"]], ["Owner.interfaces"])
        self.assertEqual(len(frozen["core_unit_ids"]), 1)
        self.assertEqual(len(frozen["witness"]["allowed_source_ids"]), 1)
        self.assertNotIn("class Owner", payload["sources"][0]["text"])

    def test_complete_parent_aliases_are_real_and_not_double_charged(self):
        raw = b"def interfaces():\n def interfaces_inner():\n  return 1\n return interfaces_inner()\n"
        payload, _, frozen, _ = self.choose({"sample.py": raw})
        self.assertEqual(len(payload["sources"]), 1)
        self.assertEqual(len(frozen["witness"]["allowed_source_ids"]), 2)
        self.assertEqual(frozen["witness"]["unique_body_bytes"], len(raw) - 1)
        self.assertEqual(payload["sources"][0]["text"], raw.decode().rstrip("\n"))

    def test_outer_guard_body_is_complete_and_budget_cannot_drop_it(self):
        raw = b"if enabled:\n def interfaces():\n  return 1\nelse:\n fallback = '" + b"x" * 1000 + b"'\n"
        files = {"sample.py": raw}
        payload, _, _, _ = self.choose(files)
        self.assertEqual([row["text"] for row in payload["sources"]], [raw.decode().rstrip("\n")])
        with self.assertRaisesRegex(ValueError, "required_group_search_exhausted"):
            expected_selection(files, **trusted(files), body_limit=100)

    def test_decorator_bom_crlf_and_latin1_preserve_complete_text(self):
        cases = [(b"\xef\xbb\xbf@decorate\r\nasync def interfaces(value):\r\n return value\r\n",
                  "@decorate\r\nasync def interfaces(value):\r\n return value"),
                 (b"# coding: latin-1\ndef interfaces():\n return '\xe9'\n",
                  "def interfaces():\n return 'é'")]
        for raw, text in cases:
            with self.subTest(raw=raw):
                payload, _, _, _ = self.choose({"sample.py": raw})
                self.assertEqual(payload["sources"][0]["text"], text)

    def test_two_hop_navigation_stops_before_third_hop(self):
        raw = (b"def interfaces():\n return middle()\ndef middle():\n return tail()\n"
               b"def tail():\n return end()\ndef end():\n return 1\n")
        payload, _, frozen, _ = self.choose({"sample.py": raw})
        self.assertEqual({row["symbol"] for row in payload["sources"]}, {"interfaces", "middle", "tail"})
        indirect = [row["hits"]["interfaces"] for row in frozen["witness"]["selected_evidence"]
                    if row["hits"]["interfaces"]["hop"]]
        self.assertEqual(sorted(row["hop"] for row in indirect), [1, 2])
        self.assertTrue(all(step["basis"] == "resolved_call" for row in indirect for step in row["path"]))

    def test_dynamic_same_name_methods_are_lexical_not_resolved(self):
        raw = (b"def interfaces(obj):\n return obj.dispatch()\nclass Left:\n def dispatch(self):\n  return 1\n"
               b"class Right:\n def dispatch(self):\n  return 2\n")
        payload, _, frozen, _ = self.choose({"sample.py": raw})
        self.assertEqual({row["symbol"] for row in payload["sources"]},
                         {"interfaces", "Left.dispatch", "Right.dispatch"})
        indirect = [row for row in frozen["witness"]["selected_evidence"]
                    if row["hits"]["interfaces"]["hop"]]
        self.assertEqual(len(indirect), 2)
        self.assertTrue(all(row["hits"]["interfaces"]["path"][0]["basis"] ==
                            "lexical_unresolved:obj.dispatch" for row in indirect))

    def test_cycle_and_input_order_are_deterministic(self):
        files = {"b.py": b"def unused():\n return 1\n",
                 "a.py": b"def interfaces():\n return helper()\ndef helper():\n return interfaces()\n"}
        first = self.choose(files)
        second = self.choose(dict(reversed(list(files.items()))))
        self.assertEqual(first[1:3], second[1:3])
        self.assertLessEqual(first[2]["witness"]["edge_visits"], 2048)

    def test_candidate_limit_records_incomplete_exploration(self):
        raw = ("def interfaces():\n " + "; ".join(f"f{i}()" for i in range(300)) + "\n" +
               "".join(f"def f{i}():\n return {i}\n" for i in range(300))).encode()
        _, _, frozen, _ = self.choose({"sample.py": raw})
        self.assertLessEqual(frozen["witness"]["candidate_count"], 256)
        self.assertTrue(frozen["witness"]["exploration_incomplete"])
        self.assertEqual(frozen["witness"]["proof_scope"], "bounded_candidates_not_capacity_proof")

    def test_exact_body_and_post_boundaries_and_metadata_failure(self):
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        _, post, frozen, context = self.choose(files)
        body_bytes = frozen["witness"]["unique_body_bytes"]
        expected_selection(files, **context, body_limit=body_bytes, post_limit=len(post))
        for limits in ({"body_limit": body_bytes - 1}, {"post_limit": len(post) - 1}):
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, "required_group_search_exhausted"):
                expected_selection(files, **context, **limits)
        with self.assertRaisesRegex(ValueError, "fixed_question_metadata"):
            expected_selection(files, **context, post_limit=100)
        for limits in ({"body_limit": True}, {"post_limit": True}, {"body_limit": 98305}, {"post_limit": 150001}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                expected_selection(files, **context, **limits)

    def test_resigned_core_signal_duty_alias_and_search_tampering_rejected(self):
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        _, _, frozen, context = self.choose(files)
        mutations = [lambda v: v.update(core_unit_ids=[]),
                     lambda v: v["witness"].update(owners=[]),
                     lambda v: v["witness"].update(duties=[]),
                     lambda v: v["witness"].update(allowed_source_ids=["invented"]),
                     lambda v: v["witness"].update(candidate_count=0),
                     lambda v: v["witness"].update(edge_visits=True),
                     lambda v: v["witness"].update(omissions=[{"unit_id": "invented"}]),
                     lambda v: v["witness"]["selected_evidence"][0]["hits"]["interfaces"].update(count=99),
                     lambda v: v.update(selection_version="unknown"),
                     lambda v: v.update(extra=True)]
        for mutate in mutations:
            value = copy.deepcopy(frozen)
            mutate(value)
            resign(value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "witness_or_trusted_input_conflict"):
                authenticate_selection(value, files, **context)

    def test_external_source_question_dimension_identity_and_coverage_changes_rejected(self):
        files = {"sample.py": b"def interfaces():\n return 1\n", "other.py": b"# frozen unselected source\n"}
        _, _, frozen, context = self.choose(files)
        changed_files = {**files, "other.py": files["other.py"] + b"# changed\n"}
        cases = [(changed_files, context),
                 (files, {**context, "project_id": "other"}),
                 (files, {**context, "snapshot_id": "other"}),
                 (files, {**context, "question": context["question"] + " details"}),
                 (files, {**context, "dimensions": [dimension("Describe interfaces", required=False)]}),
                 (files, {**context, "frozen_coverage": {"parsed_files": 1}})]
        for sources, modified in cases:
            with self.subTest(modified=modified), self.assertRaises(ValueError):
                authenticate_selection(frozen, sources, **modified)

    def test_cancel_propagates_and_source_instructions_are_not_executed(self):
        files = {"sample.py": b"raise RuntimeError('must remain data')\ndef interfaces():\n return 1\n"}
        self.choose(files)
        def stopped():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            expected_selection(files, **trusted(files), tick=stopped)

    def test_oracle_dependency_closure_has_no_production_imports(self):
        root = Path(__file__).parent
        pending = ["schema_v3_selection_oracle"]
        seen = set()
        while pending:
            module = pending.pop()
            if module in seen:
                continue
            seen.add(module)
            tree = ast.parse((root / (module + ".py")).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imports = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                           else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for imported in imports:
                    self.assertFalse(imported.startswith("solo_distill"), (module, imported))
                    if imported.startswith("schema_v3_"):
                        pending.append(imported)
        self.assertIn("schema_v3_local_oracle", seen)
        self.assertIn("schema_v3_task_oracle", seen)


if __name__ == "__main__":
    unittest.main()
