"""Question-view regressions: offline fixtures only, never execute target source."""
from __future__ import annotations

import ast
import copy
import json
import unittest
from unittest.mock import patch

from solo_distill.evidence import EvidenceBlocked
from solo_distill.evidence_v2 import attach_roles
from solo_distill.gateway import encode_request
from solo_distill.graph_v2 import extract_relations
from solo_distill.live_v2 import add_proposal
from solo_distill.package_v2 import build_package, validate_package
from solo_distill.scouting_v2 import BODY_LIMIT, POST_LIMIT, select_prompt
from solo_distill.snapshot_v2 import snapshot_scope, snapshot_metrics
from solo_distill.syntax_v2 import extract_units


def dimension(question, key="interfaces"):
    return {"id": key, "question": question, "required": True, "evidence": "sourced_inference"}


@snapshot_scope
def package_for(files):
    ids = [u["id"] for path, raw in sorted(files.items()) for u in extract_units(path, raw)["units"]]
    package = attach_roles(build_package("fixture", "snapshot", files, ids), files)
    package["relations"] = extract_relations(package, files)
    validate_package(package, files)
    return package


class QuestionViewTests(unittest.TestCase):
    def choose(self, files, questions=None, **limits):
        package = package_for(files)
        questions = questions or [dimension("Describe interfaces")]
        with patch("socket.socket", side_effect=AssertionError("offline only")):
            messages, post, receipt = select_prompt(package, files, "Explain static interfaces", questions,
                {"parsed_files": len(files)}, **limits)
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(encode_request(messages), post)
        self.assertEqual(receipt["post_bytes"], len(post))
        self.assertLessEqual(len(post), limits.get("post_limit", POST_LIMIT))
        self.assertEqual(receipt["unique_body_bytes"], sum(len(s["text"].encode()) for s in payload["sources"]))
        self.assertLessEqual(receipt["unique_body_bytes"], limits.get("body_limit", BODY_LIMIT))
        return package, payload, receipt

    def test_full_static_inventory_is_separate_from_small_prompt(self):
        files = {"interfaces.py": b"def interfaces():\n return 1\n",
                 "unrelated.py": b"def unrelated():\n return '" + b"x" * 100000 + b"'\n"}
        package, payload, receipt = self.choose(files)
        self.assertEqual(len(package["files"]), 2)
        self.assertEqual(len(package["units"]), 2)
        self.assertEqual([s["symbol"] for s in payload["sources"]], ["interfaces"])
        self.assertEqual(receipt["proof_scope"], "bounded_candidates_not_capacity_proof")

    def test_four_independent_dimensions_have_direct_duties(self):
        files = {"sample.py": b"def alpha():\n return 1\ndef beta():\n return 2\ndef gamma():\n return 3\ndef delta():\n return 4\n"}
        _, _, receipt = self.choose(files, [dimension(name, name) for name in ("alpha", "beta", "gamma", "delta")])
        self.assertEqual({d["dimension_id"] for d in receipt["duties"]}, {"alpha", "beta", "gamma", "delta"})
        self.assertTrue(all(d["duty"] == "direct_question" for d in receipt["duties"]))

    def test_required_dimension_with_zero_hits_blocks(self):
        with self.assertRaisesRegex(EvidenceBlocked, "no_direct_body"):
            self.choose({"sample.py": b"def unrelated():\n return 1\n"})

    def test_oversized_required_function_is_not_cut(self):
        with self.assertRaisesRegex(EvidenceBlocked, "required_group_search_exhausted"):
            self.choose({"sample.py": b"def interfaces():\n return '" + b"x" * 100000 + b"'\n"})

    def test_conditional_origin_is_complete_or_blocks(self):
        raw = b"if enabled:\n def interfaces():\n  return 1\nelse:\n fallback = '" + b"x" * 3000 + b"'\n"
        with self.assertRaisesRegex(EvidenceBlocked, "required_group_search_exhausted"):
            self.choose({"sample.py": raw}, body_limit=1000)
        _, payload, _ = self.choose({"sample.py": raw})
        self.assertEqual(len(payload["sources"]), 1)
        self.assertEqual(payload["sources"][0]["text"], raw.decode().rstrip())

    def test_method_body_does_not_send_whole_class_or_sibling_alias(self):
        raw = b"class Owner:\n def interfaces(self):\n  return 1\n def unrelated(self):\n  return '" + b"x" * 100000 + b"'\n"
        package, payload, receipt = self.choose({"sample.py": raw})
        self.assertEqual(len(package["owners"]), 1)
        self.assertEqual([s["symbol"] for s in payload["sources"]], ["Owner.interfaces"])
        forbidden = {s["id"] for s in package["sources"] if package["units"][s["unit"]]["qualified_name"] in {"Owner", "Owner.unrelated"}}
        self.assertFalse(forbidden & set(receipt["allowed_source_ids"]))

    def test_complete_parent_aliases_merge_without_double_cost(self):
        raw = b"def interfaces():\n def interfaces_inner():\n  return 1\n return interfaces_inner()\n"
        _, payload, receipt = self.choose({"sample.py": raw})
        self.assertEqual(len(payload["sources"]), 1)
        self.assertEqual(len(receipt["allowed_source_ids"]), 2)
        self.assertEqual(receipt["unique_body_bytes"], len(raw.rstrip()))

    def test_decorators_are_part_of_complete_body(self):
        _, payload, _ = self.choose({"sample.py": b"@decorate\nasync def interfaces(value):\n return value\n"})
        self.assertTrue(payload["sources"][0]["text"].startswith("@decorate\nasync def"))

    def test_latin1_body_is_budgeted_as_utf8(self):
        raw = b"# coding: latin-1\ndef interfaces():\n return '\xe9'\n"
        _, payload, receipt = self.choose({"sample.py": raw})
        self.assertIn("é", payload["sources"][0]["text"])
        self.assertEqual(receipt["unique_body_bytes"], len(payload["sources"][0]["text"].encode("utf-8")))

    def test_double_json_escaping_uses_exact_post_not_ratio(self):
        literal = "\\\"值" * 200
        raw = ("def interfaces():\n return " + repr(literal) + "\n").encode()
        _, _, receipt = self.choose({"sample.py": raw})
        self.assertGreater(receipt["post_bytes"], receipt["unique_body_bytes"])
        with self.assertRaisesRegex(EvidenceBlocked, "required_group_search_exhausted"):
            self.choose({"sample.py": raw}, post_limit=receipt["post_bytes"] - 1)

    def test_fixed_metadata_over_post_budget_blocks_before_selection(self):
        with self.assertRaisesRegex(EvidenceBlocked, "fixed_question_metadata"):
            self.choose({"sample.py": b"def interfaces():\n return 1\n"}, post_limit=100)

    def test_resolved_two_hop_chain_does_not_expand_to_third(self):
        files = {"sample.py": b"def interfaces():\n return middle()\ndef middle():\n return tail()\ndef tail():\n return end()\ndef end():\n return 1\n"}
        package, payload, receipt = self.choose(files)
        names = {s["symbol"] for s in payload["sources"]}
        self.assertEqual(names, {"interfaces", "middle", "tail"})
        by_id = {u["id"]: u["qualified_name"] for u in package["units"]}
        tail = next(s for s in receipt["selected_evidence"] if by_id[s["unit_id"]] == "tail")
        self.assertEqual(tail["hits"]["interfaces"]["hop"], 2)
        self.assertTrue(all(p["basis"] == "resolved_call" for p in tail["hits"]["interfaces"]["path"]))

    def test_cycle_is_bounded_and_deterministic(self):
        files = {"sample.py": b"def interfaces():\n return helper()\ndef helper():\n return interfaces()\n"}
        first = self.choose(files)[2]
        second = self.choose(files)[2]
        self.assertEqual(first["owners"], second["owners"])
        self.assertEqual(first["post_sha256"], second["post_sha256"])
        self.assertLessEqual(first["edge_visits"], 2048)

    def test_dynamic_same_name_methods_remain_lexical_not_resolved(self):
        raw = b"def interfaces(obj):\n return obj.dispatch()\nclass Left:\n def dispatch(self):\n  return 1\nclass Right:\n def dispatch(self):\n  return 2\n"
        package, _, receipt = self.choose({"sample.py": raw})
        relation = next(r for r in package["relations"] if r["site"]["expression"] == "obj.dispatch")
        self.assertIsNone(relation["to_unit"])
        neighbors = [s for s in receipt["selected_evidence"] if s["hits"]["interfaces"]["hop"]]
        self.assertEqual(len(neighbors), 2)
        self.assertTrue(all(s["hits"]["interfaces"]["path"][0]["basis"].startswith("lexical_unresolved:") for s in neighbors))

    def test_conditional_binding_is_not_promoted_to_resolved_edge(self):
        raw = b"if enabled:\n def helper():\n  return 1\ndef interfaces():\n return helper()\n"
        package, _, _ = self.choose({"sample.py": raw})
        relation = next(r for r in package["relations"] if r["site"]["expression"] == "helper")
        self.assertIsNone(relation["to_unit"])
        self.assertEqual(relation["reason"], "conditional_or_unsafe_binding")

    def test_high_outdegree_reports_incomplete_exploration(self):
        raw = ("def interfaces():\n " + "; ".join(f"f{i}()" for i in range(300)) + "\n" +
               "".join(f"def f{i}():\n return {i}\n" for i in range(300))).encode()
        _, _, receipt = self.choose({"sample.py": raw})
        self.assertLessEqual(receipt["candidate_count"], 256)
        self.assertTrue(receipt["exploration_incomplete"])

    def test_state_and_failure_require_syntactic_support_not_name_only(self):
        for question, raw, expected in (("Explain state ownership", b"def state():\n return 1\n", "state_write"),
                                         ("Explain failure exceptions", b"def failure():\n return 1\n", "failure_boundary")):
            with self.subTest(question=question), self.assertRaisesRegex(EvidenceBlocked, expected):
                self.choose({"sample.py": raw}, [dimension(question)])

    def test_unsent_existing_source_and_cross_snapshot_source_are_rejected(self):
        package, _, receipt = self.choose({"sample.py": b"def interfaces():\n return 1\ndef unrelated():\n return 2\n"})
        forbidden = next(s["id"] for s in package["sources"] if s["id"] not in receipt["allowed_source_ids"])
        for sid in (forbidden, "cross-snapshot-invented"):
            candidate = {"summary": "static", "unknowns": [], "conclusions": [
                {"dimension_id": "interfaces", "text": "Static syntax only.", "source_ids": [sid]}]}
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                add_proposal(package, candidate, [dimension("interfaces")], {"sample.py": b"def interfaces():\n return 1\ndef unrelated():\n return 2\n"},
                    allowed_source_ids=receipt["allowed_source_ids"])

    def test_snapshot_cache_is_detached_and_content_bound(self):
        @snapshot_scope
        def check():
            raw = b"def interfaces():\n return 1\n"
            first = extract_units("sample.py", raw)
            original = copy.deepcopy(first)
            first["units"][0]["qualified_name"] = "tampered"
            self.assertEqual(extract_units("sample.py", raw), original)
            changed = extract_units("sample.py", raw + b"# changed\n")
            self.assertNotEqual(changed["file"]["id"], original["file"]["id"])
            self.assertEqual(snapshot_metrics()["parse_calls"], 2)
        with patch("ast.parse", wraps=ast.parse) as parse:
            check()
            self.assertEqual(parse.call_count, 2)
            check()
            self.assertEqual(parse.call_count, 4)

    def test_cancel_tick_propagates_without_network(self):
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        def stopped():
            raise EvidenceBlocked("cancelled")
        with self.assertRaisesRegex(EvidenceBlocked, "cancelled"):
            select_prompt(package_for(files), files, "interfaces", [dimension("interfaces")], {}, tick=stopped)


class ConditionIndexRegressionTests(unittest.TestCase):
    def fixture(self):
        from solo_distill.core_v2 import canonical_package
        files = {
            "sample.py": b"def helper():\n return 1\nif enabled:\n def interfaces():\n  if ready:\n   helper()\n   helper()\n  else:\n   helper()\nelse:\n fallback = 0\n",
            "other.py": b"def unrelated():\n if ready:\n  print('other')\n",
        }
        return files, canonical_package(package_for(files))

    def test_prefix_index_preserves_naive_condition_order_and_identity(self):
        from v2_validator import decode, digest
        files, package = self.fixture()
        for relation in package["relations"]:
            origin = package["units"][relation["from_unit"]]
            expected = list(origin["conditions"])
            route = relation["site"]["syntax_path"]
            for index, condition in enumerate(package["conditions"]):
                file_index = package["units"][condition["unit"]]["file"]
                prefix = condition["syntax_path"] + [condition["field"]]
                if file_index == origin["file"] and route[:len(prefix)] == prefix and index not in expected:
                    expected.append(index)
            self.assertEqual(relation["conditions"], expected)
            target = None if relation["to_unit"] is None else package["units"][relation["to_unit"]]["id"]
            self.assertEqual(relation["id"], digest("relation-v2", [origin["id"], relation["site"],
                relation["layer"], target, expected, relation["reason"]]))
        self.assertEqual(decode(json.dumps(package).encode(), files), package)

    def test_independent_authentication_parses_once_per_file_not_per_site(self):
        from solo_distill.core_v2 import canonical_package
        from v2_validator import decode
        files = {"sample.py": ("def interfaces():\n " + "; ".join("print('x')" for _ in range(120)) + "\n").encode()}
        package = canonical_package(package_for(files))
        self.assertEqual(len(package["relations"]), 120)
        with patch("ast.parse", wraps=ast.parse) as parser:
            self.assertEqual(decode(json.dumps(package).encode(), files), package)
        self.assertEqual(parser.call_count, len(files))

    def test_local_tree_reuse_does_not_accept_changed_source_or_forged_site(self):
        from v2_validator import decode
        files, package = self.fixture()
        encoded = json.dumps(package).encode()
        self.assertEqual(decode(encoded, files), package)
        changed = {**files, "sample.py": files["sample.py"] + b"# source changed\n"}
        with self.assertRaises(ValueError):
            decode(encoded, changed)
        forged = copy.deepcopy(package)
        forged["relations"][0]["site"]["syntax_path"] = ["body", 9999]
        with self.assertRaises(ValueError):
            decode(json.dumps(forged).encode(), files)


class GatewayCaptureTests(unittest.TestCase):
    def test_opt_in_capture_preserves_exact_sse_and_actual_post(self):
        import base64
        import hashlib
        import threading
        import time
        from solo_distill.gateway import Gateway
        from test_distillation import Connection, Response, normal_stream
        stream = normal_stream()
        messages = [{"role": "user", "content": "引号\"和反斜杠\\"}]
        for capture in (False, True):
            connection = Connection(Response([stream[i:i + 7] for i in range(0, len(stream), 7)]))
            with patch("solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
                answer, metrics = Gateway(capture_stream=capture).generate(messages, time.monotonic() + 5, threading.Event())
            self.assertEqual(answer, '{"ok":true}')
            self.assertEqual(len(connection.calls), 1)
            self.assertEqual(connection.calls[0][1]["body"], encode_request(messages))
            self.assertTrue(connection.closed)
            if capture:
                self.assertEqual(base64.b64decode(metrics["raw_sse_base64"], validate=True), stream)
                self.assertEqual(metrics["raw_sse_bytes"], len(stream))
                self.assertEqual(metrics["raw_sse_sha256"], hashlib.sha256(stream).hexdigest())
            else:
                self.assertNotIn("raw_sse_base64", metrics)

    def test_failed_capture_keeps_partial_bytes_without_retry(self):
        import base64
        import threading
        import time
        from solo_distill.gateway import Gateway, GatewayError
        from test_distillation import Connection, Response, normal_stream
        partial = normal_stream().replace(b"data: [DONE]\n\n", b"")
        connection = Connection(Response([partial]))
        with patch("solo_distill.gateway.http.client.HTTPConnection", return_value=connection):
            with self.assertRaises(GatewayError) as caught:
                Gateway(capture_stream=True).generate([], time.monotonic() + 5, threading.Event())
        self.assertEqual(base64.b64decode(caught.exception.observations["raw_sse_base64"], validate=True), partial)
        self.assertEqual(len(connection.calls), 1)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
