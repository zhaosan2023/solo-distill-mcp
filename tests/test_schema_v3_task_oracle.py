"""任务证据独立 Oracle 的手工期望及篡改负控；不访问网关。

这些测试不证明生产任务接口、选择独立性、真实发送或人工语义通过。
"""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import canonical, identity
from schema_v3_task_oracle import (
    authenticate_task, expected_prompt, expected_task,
)
from test_schema_v3_local import SOURCES, core_ids

QUESTION = "Explain service handling, state ownership and helper calls."
DIMENSIONS = [
    {"id": "handling", "question": "Explain handle behavior.",
     "required": True, "evidence": "sourced_inference"},
    {"id": "state", "question": "Explain state ownership.",
     "required": True, "evidence": "sourced_inference"},
    {"id": "failure", "question": "Explain failure boundaries.",
     "required": True, "evidence": "sourced_inference"},
    {"id": "calls", "question": "Explain helper callers and callees.",
     "required": True, "evidence": "sourced_inference"},
]
COVERAGE = {"parsed_files": 4, "inventory_complete": True}


def trusted_structure(files=SOURCES, names=None):
    names = names if names is not None else {
        ("pkg/base.py", "helper"), ("pkg/service.py", "Service.handle")}
    return {"project_id": "fixture", "snapshot_id": "snapshot-1",
            "selection_id": "external-selection-1",
            "required_core_unit_ids": core_ids(files, names)}


def fixture(files=SOURCES, names=None):
    structure = trusted_structure(files, names)
    messages, post, allowed, core = expected_prompt(files, **structure,
        question=QUESTION, dimensions=DIMENSIONS, frozen_coverage=COVERAGE)
    candidate = {"summary": "Static evidence only.", "conclusions": [
        {"dimension_id": dimension["id"], "text": "Source-backed inference: " + dimension["id"],
         "source_ids": [allowed[0]]} for dimension in DIMENSIONS],
        "unknowns": ["Runtime execution and persistence remain unverified."]}
    task_inputs = {**structure, "request_id": "fixture-request-1",
                   "question": QUESTION, "dimensions": copy.deepcopy(DIMENSIONS),
                   "post": post, "frozen_coverage": copy.deepcopy(COVERAGE)}
    return candidate, task_inputs, messages, allowed, core


class TaskOracleTests(unittest.TestCase):
    def rehash(self, value):
        value["evidence_id"] = identity("task-evidence-v3",
            {key: item for key, item in value.items() if key != "evidence_id"})
        return value

    def mutate_post(self, post, mutate):
        envelope = json.loads(post)
        payload = json.loads(envelope["messages"][1]["content"])
        mutate(envelope, payload)
        envelope["messages"][1]["content"] = json.dumps(payload,
            ensure_ascii=False, separators=(",", ":"))
        return json.dumps(envelope, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")

    def test_manual_four_dimensions_and_typed_sources(self):
        candidate, trusted, _, allowed, core = fixture()
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            value = expected_task(candidate, SOURCES, **trusted)
            receipt = authenticate_task(value, candidate, SOURCES, **trusted)
        self.assertEqual(value["status"], "complete")
        self.assertEqual(len(value["facts"]), 4)
        self.assertEqual(len(value["obligations"]), 4)
        self.assertEqual(value["core_id"], core["core_id"])
        self.assertEqual(value["post_sha256"], hashlib.sha256(trusted["post"]).hexdigest())
        self.assertEqual(value["question_id"], identity("question-v3",
            {"question": QUESTION, "dimensions": DIMENSIONS}))
        for number, fact in enumerate(value["facts"]):
            self.assertEqual(fact["id"], identity("task-fact-v3", [trusted["request_id"],
                core["core_id"], value["question_id"], number, candidate["conclusions"][number]]))
            self.assertEqual(fact["sources"], [
                {"space": "local_v3", "table": "sources", "id": allowed[0]}])
            self.assertEqual(fact["level"], "sourced_inference")
            self.assertEqual(fact["semantic_support"], "requires_independent_review")
            obligation = value["obligations"][number]
            self.assertEqual(obligation["supports"], [
                {"space": "task_evidence", "table": "facts", "id": fact["id"]}])
            self.assertEqual(obligation["state"], "satisfied")
            self.assertIsNone(obligation["reason"])
        self.assertEqual(receipt["status"], "pass")
        self.assertEqual(receipt["actual_network_send"], "not_proven_by_this_asset")
        self.assertEqual(receipt["business_semantics"], "requires_real_independent_human_review")

    def test_required_omission_and_empty_candidate_derive_partial(self):
        candidate, trusted, *_ = fixture()
        candidate["conclusions"].pop()
        value = expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(value["status"], "partial")
        self.assertEqual(value["obligations"][-1]["state"], "unknown")
        self.assertEqual(value["obligations"][-1]["supports"], [])
        self.assertEqual(value["obligations"][-1]["reason"], "required_semantic_evidence_missing")
        candidate["conclusions"] = []
        self.assertEqual(expected_task(candidate, SOURCES, **trusted)["status"], "partial")

    def test_optional_missing_dimension_is_preserved_without_blocking(self):
        candidate, trusted, *_ = fixture()
        trusted["dimensions"][-1]["required"] = False
        _, trusted["post"], _, _ = expected_prompt(SOURCES,
            **{key: trusted[key] for key in trusted_structure()},
            question=QUESTION, dimensions=trusted["dimensions"], frozen_coverage=COVERAGE)
        candidate["conclusions"].pop()
        value = expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(value["status"], "complete")
        self.assertEqual(value["obligations"][-1]["state"], "unknown")
        self.assertFalse(value["obligations"][-1]["required"])

    def test_failure_precedence_and_unknowns_are_not_erased(self):
        candidate, trusted, *_ = fixture()
        candidate["conclusions"] = []
        for failures, status in (([{"kind": "blocked", "reason": "source_changed"}], "blocked"),
                ([{"kind": "blocked", "reason": "cancelled"},
                  {"kind": "failed", "reason": "generation_failed"}], "failed")):
            value = expected_task(candidate, SOURCES, **trusted, failures=failures)
            self.assertEqual(value["status"], status)
            self.assertEqual(value["failures"], failures)
            self.assertEqual(value["unknowns"], candidate["unknowns"])
            self.assertEqual(len(value["obligations"]), 4)

    def test_rehashed_identity_status_reference_and_obligation_forgery_rejected(self):
        candidate, trusted, *_ = fixture()
        original = expected_task(candidate, SOURCES, **trusted)
        mutations = [
            lambda value: value.update(evidence_version=True),
            lambda value: value.update(core_id="0" * 64),
            lambda value: value.update(input_id="0" * 64),
            lambda value: value.update(selection_id="changed"),
            lambda value: value.update(request_id="changed"),
            lambda value: value.update(question_id="0" * 64),
            lambda value: value.update(post_sha256="0" * 64),
            lambda value: value.update(status="partial"),
            lambda value: value["facts"][0].update(level="direct_static"),
            lambda value: value["facts"][0]["sources"][0].update(space="core"),
            lambda value: value["facts"][0]["sources"][0].update(table="symbols"),
            lambda value: value["facts"][0]["sources"][0].update(index=0),
            lambda value: value["facts"][0].update(dimension_id="calls"),
            lambda value: value["obligations"][0].update(required=False),
            lambda value: value["obligations"][0].update(supports=[]),
            lambda value: value["obligations"].pop(),
            lambda value: value["facts"].append(copy.deepcopy(value["facts"][0])),
            lambda value: value.update(unknowns=[]),
            lambda value: value.update(authenticated=True),
        ]
        for number, mutate in enumerate(mutations):
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(number=number), self.assertRaises(ValueError):
                authenticate_task(self.rehash(value), candidate, SOURCES, **trusted)

    def test_missing_required_support_cannot_be_rehashed_as_complete(self):
        candidate, trusted, *_ = fixture()
        candidate["conclusions"].pop()
        value = expected_task(candidate, SOURCES, **trusted)
        value["status"] = "complete"
        value["obligations"][-1].update(state="satisfied", reason=None)
        with self.assertRaises(ValueError):
            authenticate_task(self.rehash(value), candidate, SOURCES, **trusted)

    def test_frozen_post_source_alias_body_question_model_and_coverage_mutations_rejected(self):
        candidate, trusted, *_ = fixture()
        mutations = [
            lambda envelope, payload: payload["sources"].pop(),
            lambda envelope, payload: payload["sources"].append(copy.deepcopy(payload["sources"][0])),
            lambda envelope, payload: payload["sources"][0].update(text="truncated"),
            lambda envelope, payload: payload["sources"][0].update(path="unused.py"),
            lambda envelope, payload: payload["sources"][0].update(symbol="unrelated"),
            lambda envelope, payload: payload["sources"][0].update(source_id="forged"),
            lambda envelope, payload: payload["sources"][0].update(alias_source_ids=["forged"]),
            lambda envelope, payload: payload.update(question="changed"),
            lambda envelope, payload: payload["dimensions"][0].update(required=False),
            lambda envelope, payload: payload.update(coverage={"parsed_files": 1}),
            lambda envelope, payload: envelope.update(model="unknown"),
            lambda envelope, payload: envelope.update(stream=1),
            lambda envelope, payload: envelope["messages"][0].update(content="forged system prompt"),
            lambda envelope, payload: envelope.update(authenticated=True),
        ]
        for number, mutate in enumerate(mutations):
            post = self.mutate_post(trusted["post"], mutate)
            with self.subTest(number=number), self.assertRaises(ValueError):
                expected_task(candidate, SOURCES, **{**trusted, "post": post})

    def test_post_duplicate_keys_float_noncanonical_and_nonbytes_rejected(self):
        candidate, trusted, *_ = fixture()
        raw = trusted["post"]
        for post in (b'{"model":"duplicate",' + raw[1:], raw + b"\n",
                     b'{"stream":1.0}', b'{"stream":NaN}', raw.decode(), b"[]", None):
            with self.subTest(post=type(post).__name__), self.assertRaises(ValueError):
                expected_task(candidate, SOURCES, **{**trusted, "post": post})

    def test_invalid_model_fields_and_unsent_or_duplicate_source_ids_rejected(self):
        candidate, trusted, _, allowed, core = fixture()
        hidden_symbol = next(row[0] for row in core["package"]["macro"]["symbols"]
                             if row[4] == "Service.unrelated")
        mutations = [
            lambda value: value.update(status="complete"),
            lambda value: value.update(summary=None),
            lambda value: value.update(unknowns=[False]),
            lambda value: value["conclusions"][0].update(dimension_id="unknown"),
            lambda value: value["conclusions"][0].update(text=" "),
            lambda value: value["conclusions"][0].update(level="direct_static"),
            lambda value: value["conclusions"][0].update(source_ids=[]),
            lambda value: value["conclusions"][0].update(source_ids=[allowed[0], allowed[0]]),
            lambda value: value["conclusions"][0].update(source_ids=[True]),
            lambda value: value["conclusions"][0].update(source_ids=[hidden_symbol]),
            lambda value: value["conclusions"][0].update(source_ids=["forged"]),
        ]
        for number, mutate in enumerate(mutations):
            value = copy.deepcopy(candidate)
            mutate(value)
            with self.subTest(number=number), self.assertRaises(ValueError):
                expected_task(value, SOURCES, **trusted)

    def test_trusted_source_request_question_and_dimension_changes_rejected(self):
        candidate, trusted, *_ = fixture()
        value = expected_task(candidate, SOURCES, **trusted)
        for key, replacement in (("request_id", "changed"), ("question", "changed"),
                ("selection_id", "changed"), ("project_id", "changed"),
                ("snapshot_id", "changed"), ("frozen_coverage", {"parsed_files": 1}),
                ("required_core_unit_ids", trusted["required_core_unit_ids"][:1]),
                ("dimensions", list(reversed(trusted["dimensions"])))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                authenticate_task(value, candidate, SOURCES, **{**trusted, key: replacement})
        changed = {**SOURCES, "unused.py": SOURCES["unused.py"] + b"# changed\n"}
        with self.assertRaises(ValueError):
            authenticate_task(value, candidate, changed, **trusted)

    def test_guard_owner_real_nested_alias_is_allowed(self):
        files = {"guard.py": b"if enabled:\n    def chosen():\n        return target()\n"}
        candidate, trusted, messages, _, _ = fixture(files, {("guard.py", "chosen")})
        records = json.loads(messages[1]["content"])["sources"]
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["alias_source_ids"]), 2)
        self.assertTrue(records[0]["text"].startswith("if enabled:"))
        alias = next(key for key in records[0]["alias_source_ids"] if key != records[0]["source_id"])
        candidate["conclusions"][0]["source_ids"] = [alias]
        value = expected_task(candidate, files, **trusted)
        self.assertEqual(value["facts"][0]["sources"][0]["id"], alias)

    def test_long_unicode_prose_and_all_unknowns_preserved_and_detached(self):
        candidate, trusted, *_ = fixture()
        long_text = "完整推断：\"\\\n" * 2000
        candidate["summary"] = long_text
        candidate["conclusions"][0]["text"] = long_text
        candidate["unknowns"] = [long_text, "", "保留重复", "保留重复"]
        value = expected_task(candidate, SOURCES, **trusted)
        self.assertEqual(value["summary"], long_text)
        self.assertEqual(value["facts"][0]["text"], long_text)
        self.assertEqual(value["unknowns"], candidate["unknowns"])
        candidate["unknowns"].clear()
        trusted["dimensions"].clear()
        self.assertEqual(len(value["unknowns"]), 4)
        self.assertEqual(len(value["obligations"]), 4)

    def test_invalid_request_dimension_and_failure_shapes_rejected(self):
        candidate, trusted, *_ = fixture()
        for request_id in (None, "", "../escape", "x" * 129, True):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                expected_task(candidate, SOURCES, **{**trusted, "request_id": request_id})
        for dimensions in ([], DIMENSIONS + [DIMENSIONS[0]],
                [{**DIMENSIONS[0], "required": 1}], [{**DIMENSIONS[0], "selector": {}}],
                [{**DIMENSIONS[0], "evidence": "direct_static"}]):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                expected_task(candidate, SOURCES, **{**trusted, "dimensions": dimensions})
        for failures in ({}, [{"kind": "unknown", "reason": "missing"}],
                [{"kind": "blocked", "reason": ""}],
                [{"kind": "failed", "reason": "error", "status": "complete"}]):
            with self.subTest(failures=failures), self.assertRaises(ValueError):
                expected_task(candidate, SOURCES, **trusted, failures=failures)

    def test_complete_body_and_exact_post_hard_budgets(self):
        files = {"large.py": ("def chosen():\n    return " + repr("x" * 98304) + "\n").encode()}
        with self.assertRaisesRegex(ValueError, "body_budget"):
            expected_prompt(files, **trusted_structure(files, {("large.py", "chosen")}),
                question=QUESTION, dimensions=DIMENSIONS, frozen_coverage=COVERAGE)
        huge_coverage = {"detail": "x" * 150000}
        with self.assertRaisesRegex(ValueError, "post_budget"):
            expected_prompt(SOURCES, **trusted_structure(), question=QUESTION,
                dimensions=DIMENSIONS, frozen_coverage=huge_coverage)

    def test_cancel_tick_propagates_without_network(self):
        candidate, trusted, *_ = fixture()
        def stopped():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            expected_task(candidate, SOURCES, **trusted, tick=stopped)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            expected_prompt(SOURCES, **trusted_structure(), question=QUESTION,
                dimensions=DIMENSIONS, frozen_coverage=COVERAGE, tick=stopped)


if __name__ == "__main__":
    unittest.main()
