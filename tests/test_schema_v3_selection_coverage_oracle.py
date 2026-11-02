"""显式新选择版本的独立正负控；不调用生产选择器或参考答案。

手工断言完整正文、同一入口义务、解析分支与可信版本绑定。
通过仅证明独立资产的隔离语法规则，不证明生产、在线或人工审核。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import identity
from schema_v3_selection_oracle import (
    COVERAGE_VERSION, VERSION, _coverage_duties, _coverage_roles,
    authenticate_selection, expected_selection,
)


GENERATION = (
    "def generate(query, context):\n"
    " input_tokens = len(context)\n"
    " available_input_tokens = 100\n"
    " if input_tokens > available_input_tokens:\n"
    "  context = context[:100]\n"
    " try:\n"
    "  return provider.generate(query, context)\n"
    " except Exception as error:\n"
    "  return {'error': str(error)}\n"
)
PARSER = (
    "def parse_file(path, content):\n"
    " if path == 'first':\n"
    "  return parser.first(content)\n"
    " elif path == 'second':\n"
    "  return parser.second(content)\n"
    " return parser.other(content)\n"
)
GENERATION_ROLES = {
    "generation_entry", "generation_input_limit", "generation_failure",
}


def dimension(question, key="generation", required=True):
    return {"id": key, "question": question, "required": required,
            "evidence": "sourced_inference"}


def context(files, **changes):
    value = {
        "project_id": "isolated-fixture", "snapshot_id": "frozen-fixture",
        "question": "Explain source generation and parser boundaries without execution.",
        "dimensions": [dimension("Explain generation exception handling and input/context limits.")],
        "frozen_coverage": {"parsed_files": len(files)},
        "selection_version": COVERAGE_VERSION,
    }
    value.update(changes)
    return value


def resign(value):
    value["selection_id"] = identity("selection-v3", {
        key: item for key, item in value.items() if key != "selection_id"})
    return value


class IndependentCoverageOracleTests(unittest.TestCase):
    def choose(self, files, **changes):
        trusted = context(files, **changes)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("source execution forbidden")):
            messages, post, frozen = expected_selection(files, **trusted)
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(frozen["witness"]["post_bytes"], len(post))
        self.assertEqual(frozen["witness"]["post_sha256"], hashlib.sha256(post).hexdigest())
        self.assertEqual(frozen["witness"]["unique_body_bytes"],
                         sum(len(row["text"].encode("utf-8")) for row in payload["sources"]))
        return payload, post, frozen, trusted

    def roles(self, source):
        return _coverage_roles(ast.parse(source).body[0], lambda: None)

    def test_manual_complete_generation_entry_and_joint_duties(self):
        files = {"sample.py": GENERATION.encode()}
        payload, _, frozen, trusted = self.choose(files)
        self.assertEqual([row["text"] for row in payload["sources"]], [GENERATION.rstrip("\n")])
        self.assertEqual([row["symbol"] for row in payload["sources"]], ["generate"])
        self.assertEqual(self.roles(GENERATION), GENERATION_ROLES)
        duties = {row["duty"]: row for row in frozen["witness"]["duties"]}
        self.assertTrue(GENERATION_ROLES <= set(duties))
        for duty in GENERATION_ROLES:
            self.assertEqual(duties[duty]["units"], frozen["witness"]["owners"])
        self.assertEqual(authenticate_selection(frozen, files, **trusted)["proof_scope"],
                         "deterministic_question_scout_v2_syntax_only")

    def test_manual_parser_dispatch_keeps_all_real_branches(self):
        files = {"sample.py": PARSER.encode()}
        payload, _, frozen, _ = self.choose(files,
            dimensions=[dimension("Explain parser support and language boundaries.", "parsing")])
        self.assertEqual(self.roles(PARSER), {"parser_dispatch"})
        self.assertEqual([row["text"] for row in payload["sources"]], [PARSER.rstrip("\n")])
        duties = [row for row in frozen["witness"]["duties"] if row["duty"] == "parser_dispatch"]
        self.assertEqual(len(duties), 1)
        self.assertEqual(duties[0]["units"], frozen["core_unit_ids"])

    def test_both_dimensions_reserve_their_own_complete_boundaries(self):
        files = {"sample.py": (GENERATION + PARSER).encode()}
        payload, _, frozen, _ = self.choose(files, dimensions=[
            dimension("Explain generation exception handling and input/context limits."),
            dimension("Explain parser support and routing boundaries.", "parsing")])
        self.assertEqual({row["text"] for row in payload["sources"]},
                         {GENERATION.rstrip("\n"), PARSER.rstrip("\n")})
        self.assertEqual({row["dimension_id"] for row in frozen["witness"]["duties"]},
                         {"generation", "parsing"})

    def test_default_and_explicit_old_version_keep_identical_bytes_and_witness(self):
        files = {"sample.py": GENERATION.encode()}
        trusted = context(files)
        trusted.pop("selection_version")
        implicit = expected_selection(files, **trusted)
        explicit = expected_selection(files, **trusted, selection_version=VERSION)
        self.assertEqual(implicit, explicit)
        self.assertEqual(implicit[2]["selection_version"], VERSION)
        self.assertFalse(GENERATION_ROLES & {row["duty"] for row in implicit[2]["witness"]["duties"]})
        new = expected_selection(files, **trusted, selection_version=COVERAGE_VERSION)
        self.assertEqual(implicit[:2], new[:2])
        self.assertNotEqual(implicit[2]["selection_id"], new[2]["selection_id"])
        self.assertEqual(implicit[2]["input_id"], new[2]["input_id"])

    def test_unknown_and_nonstring_versions_are_rejected(self):
        files = {"sample.py": GENERATION.encode()}
        for version in ("question-scout-v3", "", None, True, [], {}):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "unknown_version"):
                expected_selection(files, **context(files, selection_version=version))

    def test_question_signals_not_dimension_id_or_optional_status_activate_duties(self):
        self.assertEqual(_coverage_duties({"interfaces"}), [])
        self.assertEqual(_coverage_duties({"generation"}), ["generation_entry"])
        self.assertEqual(_coverage_duties({"parser"}), [])
        self.assertEqual(_coverage_duties({"parser", "support"}), ["parser_dispatch"])
        files = {"sample.py": b"def interfaces():\n return 1\n"}
        _, _, frozen, _ = self.choose(files, dimensions=[
            dimension("Describe interfaces.", "generation_input_limit"),
            dimension("Describe generation exception handling and context limits.", "optional", False)])
        self.assertFalse(GENERATION_ROLES & {row["duty"] for row in frozen["witness"]["duties"]})

    def test_comments_strings_and_name_only_do_not_manufacture_generation_roles(self):
        cases = [
            "def generate(context):\n # provider.generate(); input_tokens > max_tokens\n return 'try generate exception'\n",
            "def generate(context):\n return context\n",
            "def unrelated(context):\n return provider.generate(context)\n",
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(self.roles(source), set())
        files = {"sample.py": cases[0].encode()}
        with self.assertRaisesRegex(ValueError, "generation_entry"):
            expected_selection(files, **context(files,
                dimensions=[dimension("Describe generation.")]))

    def test_nested_callable_syntax_cannot_supply_parent_coverage(self):
        source = "def generate(context):\n def inner():\n" + "".join(
            "  " + line + "\n" for line in GENERATION.splitlines()) + " return context\n"
        self.assertEqual(self.roles(source), set())
        class_source = "class Generator:\n" + "".join(
            " " + line + "\n" for line in GENERATION.splitlines())
        self.assertEqual(self.roles(class_source), set())

    def test_limit_requires_real_comparison_with_both_identifier_families(self):
        cases = [
            "def generate(context):\n limit = 100\n return provider.generate(context)\n",
            "def generate(context):\n if size > limit:\n  return provider.generate(context)\n",
            "def generate(context):\n if input_tokens > count:\n  return provider.generate(context)\n",
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(self.roles(source), {"generation_entry"})
        with self.assertRaisesRegex(ValueError, "required_question_duty_unresolved"):
            expected_selection({"sample.py": cases[1].encode()}, **context(
                {"sample.py": cases[1].encode()},
                dimensions=[dimension("Describe generation input limits.")]))

    def test_generation_failure_requires_generation_call_in_try_body_not_handler(self):
        source = ("def generate(context):\n try:\n  read(context)\n"
                  " except Exception:\n  return provider.generate(context)\n")
        self.assertEqual(self.roles(source), {"generation_entry"})
        files = {"sample.py": source.encode()}
        with self.assertRaisesRegex(ValueError, "required_question_duty_unresolved"):
            expected_selection(files, **context(files,
                dimensions=[dimension("Describe generation exceptions.")]))

    def test_separate_functions_cannot_combine_limit_and_failure_for_one_entry(self):
        limit_only = ("def generate_limit(context):\n if input_tokens > max_tokens:\n"
                      "  return provider.generate(context)\n return context\n")
        failure_only = ("def generate_failure(context):\n try:\n"
                        "  return provider.generate(context)\n except Exception:\n  return None\n")
        self.assertEqual(self.roles(limit_only), {"generation_entry", "generation_input_limit"})
        self.assertEqual(self.roles(failure_only), {"generation_entry", "generation_failure"})
        files = {"sample.py": (limit_only + failure_only).encode()}
        with self.assertRaisesRegex(ValueError, "required_question_duty_unresolved"):
            expected_selection(files, **context(files))

    def test_parser_requires_multiple_real_compare_branches_and_return_calls(self):
        cases = [
            "def parse_file(path):\n return 'if path: return parser.first(); elif path: return parser.second()'\n",
            "def parse_file(path):\n if path == 'one':\n  return parser.first(path)\n return parser.other(path)\n",
            "def parse_file(path):\n if path == 'one':\n  return 1\n elif path == 'two':\n  return 2\n return 3\n",
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(self.roles(source), set())
                files = {"sample.py": source.encode()}
                with self.assertRaisesRegex(ValueError, "parser_dispatch"):
                    expected_selection(files, **context(files,
                        dimensions=[dimension("Describe parser support.", "parsing")]))

    def test_mandatory_full_body_is_reserved_before_large_optional_decoy(self):
        decoy = "def generation_context_input_limits():\n return '" + "x" * 5000 + "'\n"
        files = {"sample.py": (decoy + GENERATION).encode()}
        payload, _, frozen, _ = self.choose(files, body_limit=800)
        self.assertEqual([row["symbol"] for row in payload["sources"]], ["generate"])
        self.assertTrue(GENERATION_ROLES <= {row["duty"] for row in frozen["witness"]["duties"]})
        self.assertTrue(frozen["witness"]["omissions"])

    def test_required_syntax_candidate_beyond_initial_eight_is_not_lost(self):
        decoys = "".join("def generation_context_input_limits_%d():\n return 1\n" % index
                         for index in range(20))
        payload, _, frozen, _ = self.choose({"sample.py": (decoys + GENERATION).encode()})
        self.assertIn(GENERATION.rstrip("\n"), [row["text"] for row in payload["sources"]])
        self.assertTrue(GENERATION_ROLES <= {row["duty"] for row in frozen["witness"]["duties"]})

    def test_exact_body_and_post_limits_block_without_truncating_or_dropping_duties(self):
        files = {"sample.py": GENERATION.encode()}
        _, post, frozen, trusted = self.choose(files)
        size = frozen["witness"]["unique_body_bytes"]
        expected_selection(files, **trusted, body_limit=size, post_limit=len(post))
        for limits in ({"body_limit": size - 1}, {"post_limit": len(post) - 1}):
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, "required_group_search_exhausted"):
                expected_selection(files, **trusted, **limits)

    def test_guard_and_nested_aliases_keep_full_owner_text(self):
        source = ("if enabled:\n" + "".join(" " + line + "\n" for line in GENERATION.splitlines())
                  + "else:\n disabled = True\n")
        payload, _, frozen, _ = self.choose({"sample.py": source.encode()})
        self.assertEqual([row["text"] for row in payload["sources"]], [source.rstrip("\n")])
        self.assertGreater(len(frozen["witness"]["allowed_source_ids"]), 1)
        self.assertEqual(len(frozen["witness"]["owners"]), 1)

    def test_resigned_version_role_duty_or_complete_core_tampering_is_rejected(self):
        files = {"sample.py": GENERATION.encode()}
        _, _, frozen, trusted = self.choose(files)
        mutations = [
            lambda value: value.update(selection_version=VERSION),
            lambda value: value["witness"].update(version=VERSION),
            lambda value: value["witness"].update(duties=[]),
            lambda value: value["witness"]["selected_evidence"][0].update(roles=[]),
            lambda value: value.update(core_unit_ids=[]),
            lambda value: value["witness"].update(allowed_source_ids=[]),
        ]
        for mutate in mutations:
            value = copy.deepcopy(frozen)
            mutate(value)
            resign(value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "witness_or_trusted_input_conflict"):
                authenticate_selection(value, files, **trusted)
        with self.assertRaisesRegex(ValueError, "witness_or_trusted_input_conflict"):
            authenticate_selection(frozen, files, **{**trusted, "selection_version": VERSION})

    def test_file_order_is_deterministic_and_unselected_source_changes_are_bound(self):
        files = {"b.py": b"# source data only\n", "a.py": GENERATION.encode()}
        first = self.choose(files)
        second = self.choose(dict(reversed(list(files.items()))))
        self.assertEqual(first[1:3], second[1:3])
        changed = {**files, "b.py": b"# changed source data\n"}
        with self.assertRaises(ValueError):
            authenticate_selection(first[2], changed, **first[3])

    def test_cancel_propagates_during_coverage_syntax_walk(self):
        def cancelled():
            raise RuntimeError("fixture cancellation")
        with self.assertRaisesRegex(RuntimeError, "fixture cancellation"):
            _coverage_roles(ast.parse(GENERATION).body[0], cancelled)
        files = {"sample.py": GENERATION.encode()}
        with self.assertRaisesRegex(RuntimeError, "fixture cancellation"):
            expected_selection(files, **context(files), tick=cancelled)


if __name__ == "__main__":
    unittest.main()
