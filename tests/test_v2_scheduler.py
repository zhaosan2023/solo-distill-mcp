"""E2 independent capacity oracle, deterministic generation and topology controls."""
from __future__ import annotations

import copy
from dataclasses import replace
import itertools
import json
import random
import unittest

from solo_distill.evidence_v2 import attach_roles
from solo_distill.graph_v2 import dependency_display, determined_graph, extract_relations
from solo_distill.package_v2 import build_package, validate_package
from solo_distill.scheduler_v2 import Candidate, Group, Scheduler, candidates_from_package
from solo_distill.syntax_v2 import extract_units
from v2_validator import decode as independent_decode


def node(key, pipeline="skeleton", *, path=None, roles=None, cost=10, start=0, end=10,
         route=None, dependencies=(), signal=0):
    return Candidate(key, path or key + ".py", route or ("body", 0), start, end, cost,
                     pipeline, roles or (("entry",) if pipeline == "skeleton" else ("boundary",)),
                     signal, dependencies)


def required(keys):
    return {"need-" + key: (Group("group-" + key, (key,)),) for key in keys}


def graph_package(files):
    selected = [u["id"] for path, raw in sorted(files.items())
                for u in extract_units(path, raw)["units"]]
    package = attach_roles(build_package("fixture", "snapshot", files, selected), files)
    package["relations"] = extract_relations(package, files)
    return package


def wire(value):
    return json.dumps(value, ensure_ascii=False).encode()


class QuotaTests(unittest.TestCase):
    def test_eighteen_required_skeleton_five_context_borrow_exactly_two(self):
        values = [node("s" + str(i)) for i in range(18)] + [node("c" + str(i), "context") for i in range(5)]
        result = Scheduler(values).select(required([v.id for v in values]))
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.borrowed, 2)
        self.assertEqual(result.counts, (18, 5, 0))
        self.assertEqual(len(result.required_owners), 23)

    def test_seventeen_skeleton_twelve_context_is_policy_not_capacity(self):
        values = [node("s" + str(i)) for i in range(17)] + [node("c" + str(i), "context") for i in range(12)]
        result = Scheduler(values).select(required([v.id for v in values]))
        self.assertEqual((result.status, result.reason), ("blocked", "policy_rejected"))
        self.assertEqual(result.owners, ())
        self.assertEqual(result.proof_scope, "enumerated_alternatives")

    def test_exhaustive_small_formula_oracle_and_total_invariants(self):
        for s, c, e in itertools.product(range(15, 20), range(9, 14), range(3, 6)):
            with self.subTest(s=s, c=c, e=e):
                values = ([node(f"s{i}") for i in range(s)] +
                          [node(f"c{i}", "context") for i in range(c)] +
                          [node(f"e{i}", "exception") for i in range(e)])
                result = Scheduler(values).select(required([v.id for v in values]))
                b = max(0, s - 16)
                possible = c <= 12 and e <= 4 and b <= 12 - c
                self.assertEqual(result.status == "selected", possible)
                if possible:
                    self.assertLessEqual(len(result.owners), 32)
                    self.assertEqual(result.borrowed, b)
                    self.assertEqual(result.body_bytes, 10 * (s + c + e))

    def test_optional_skeleton_never_triggers_borrow_or_spends_freed_loan(self):
        values = [node(f"s{i}") for i in range(20)]
        for count in (15, 16, 18):
            result = Scheduler(values).select(required([v.id for v in values[:count]]),
                                               optional=[v.id for v in values[count:]])
            self.assertEqual(result.counts[0], max(16, count))
            self.assertEqual(result.borrowed, max(0, count - 16))
            self.assertTrue(result.optional_omissions)

    def test_context_cannot_borrow_skeleton_or_exception_allowance(self):
        values = [node(f"c{i}", "context") for i in range(13)]
        result = Scheduler(values).select(required([v.id for v in values]))
        self.assertEqual(result.reason, "policy_rejected")
        values = [node(f"e{i}", "exception") for i in range(5)]
        result = Scheduler(values).select(required([v.id for v in values]))
        self.assertEqual(result.reason, "policy_rejected")
        with self.assertRaises(ValueError):
            node("helper", roles=("boundary",))

    def test_optional_context_per_file_two_required_exempt(self):
        values = [node(f"c{i}", "context", path="same.py", route=("body", i), start=i * 20, end=i * 20 + 10)
                  for i in range(5)]
        result = Scheduler(values).select({}, optional=[v.id for v in values])
        self.assertEqual(result.counts, (0, 2, 0))
        result = Scheduler(values).select(required([v.id for v in values[:3]]), optional=[v.id for v in values[3:]])
        self.assertEqual(result.counts, (0, 5, 0))

    def test_unique_parent_owner_and_multi_label_cost_are_independent(self):
        parent = node("parent", path="same.py", roles=("entry", "assembly", "state", "persistence"),
                      route=("body", 0), start=0, end=100, cost=100)
        method = node("method", path="same.py", roles=("state",),
                      route=("body", 0, "body", 0), start=20, end=80, cost=60)
        result = Scheduler([parent, method]).select(required(["parent", "method"]))
        self.assertEqual(result.owners, ("parent",))
        self.assertEqual(result.counts, (1, 0, 0))
        self.assertEqual(result.body_bytes, 100)
        self.assertEqual(dict(result.aliases), {"method": "parent", "parent": "parent"})

    def test_parent_replacement_recomputes_all_costs_and_commits_atomically(self):
        parent = node("parent", path="same.py", route=("body", 0), end=100, cost=100)
        method = node("method", path="same.py", route=("body", 0, "body", 0), start=20, end=80, cost=60)
        scheduler = Scheduler([parent, method], body_budget=90)
        first = scheduler.select(required(["method"]))
        self.assertEqual(first.owners, ("method",))
        failure = scheduler.select(required(["parent", "method"]))
        self.assertEqual(failure.reason, "capacity_proven")
        self.assertEqual(scheduler.current, first)
        result = scheduler.select(required(["method"]), optional=["parent"])
        self.assertEqual(result.owners, ("method",))
        self.assertEqual(result.optional_omissions, ("parent",))
        result = Scheduler([parent, method]).select(required(["method"]), optional=["parent"])
        self.assertEqual(result.owners, ("parent",))
        self.assertEqual(result.body_bytes, 100)

    def test_required_closure_and_cycles_reserved_before_optional_fill(self):
        values = [node(f"s{i}") for i in range(17)]
        values += [node(f"c{i}", "context") for i in range(12)]
        values[0] = replace(values[0], dependencies=tuple(f"c{i}" for i in range(12)))
        result = Scheduler(values).select(required([f"s{i}" for i in range(17)]))
        self.assertEqual(result.reason, "policy_rejected")
        values = [node("a", dependencies=("b",)), node("b", "context", dependencies=("a",))]
        result = Scheduler(values).select(required(["a"]))
        self.assertEqual(set(result.required_owners), {"a", "b"})
        self.assertEqual(result.counts, (1, 1, 0))

    def test_stable_alternative_search_and_failure_classes(self):
        values = [node("large", cost=100), node("small", cost=10)]
        alternatives = {"need": (Group("b-small", ("small",)), Group("a-large", ("large",)))}
        result = Scheduler(values, body_budget=50).select(alternatives)
        self.assertEqual(result.chosen_groups, (("need", "b-small"),))
        self.assertIn("capacity_proven", result.rejected_reasons)
        failure = Scheduler(values, body_budget=50, max_states=2).select(alternatives)
        self.assertEqual(failure.reason, "search_exhausted")
        self.assertEqual(failure.proof_scope, "none")
        self.assertEqual(failure.owners, ())
        failure = Scheduler(values, stopped=lambda: True).select(alternatives)
        self.assertEqual(failure.reason, "deadline_or_cancelled")
        self.assertEqual(Scheduler(values).select({}).reason, "zero_body_hits")

    def test_role_round_robin_signal_before_center_and_unknown_degree_absent(self):
        values = [node("entry-low", signal=1), node("entry-high", signal=2),
                  node("state", roles=("state",)), node("boundary", "context")]
        adjacency = {"entry-low": {"state", "boundary"}, "entry-high": set()}
        scheduler = Scheduler(values)
        ranked = scheduler.rank_round_robin([v.id for v in values], adjacency)
        self.assertEqual(ranked[:3], ("entry-high", "state", "boundary"))
        values += [node("entry-peer", signal=1)]
        scheduler = Scheduler(values)
        ranked = scheduler.rank_round_robin(["entry-low", "entry-peer"], adjacency)
        self.assertEqual(ranked[0], "entry-low")

    def test_once_only_neighbor_expansion_does_not_reseed_context(self):
        values = [node("s"), node("c1", "context"), node("c2", "context")]
        result = Scheduler(values).select(required(["s"]), adjacency={"s": {"c1"}, "c1": {"c2"}})
        self.assertEqual(set(result.owners), {"s", "c1"})

    def test_deterministic_permutations_do_not_depend_on_input_order(self):
        randomizer = random.Random(20261026)
        values = [node(f"s{i}", roles=(("entry", "state") if i % 2 else ("assembly",)), signal=i % 3)
                  for i in range(24)]
        values += [node(f"c{i}", "context") for i in range(20)]
        mandatory = required([v.id for v in values[:12]])
        optional = [v.id for v in values[12:]]
        adjacency = {f"s{i}": {f"c{j}" for j in range(i % 5)} for i in range(24)}
        expected = Scheduler(values).select(mandatory, optional=optional, adjacency=adjacency)
        for _ in range(30):
            shuffled = list(values)
            randomizer.shuffle(shuffled)
            randomizer.shuffle(optional)
            pairs = list(mandatory.items())
            randomizer.shuffle(pairs)
            self.assertEqual(Scheduler(shuffled).select(dict(pairs), optional=optional, adjacency=adjacency), expected)

    def test_candidate_schema_and_dangling_reference_negative_controls(self):
        for override in ({"raw_start": True}, {"body_bytes": False}, {"syntax_path": ()},
                         {"pipeline": "unknown"}, {"path": "../outside.py"}, {"roles": ("entry", "entry")}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                replace(node("a"), **override)
        with self.assertRaises(ValueError):
            Scheduler([node("a", dependencies=("missing",))])
        with self.assertRaises(ValueError):
            Scheduler([node("a"), node("a")])
        with self.assertRaises(ValueError):
            Scheduler([node("a")], max_states=True)

    def test_authenticated_syntax_adapter_reserves_condition_parent(self):
        files = {"entry.py": b"if __name__ == '__main__':\n def go():\n  return Worker()\n"}
        package = graph_package(files)
        validate_package(package, files)
        independent_decode(wire(package), files)
        candidates = candidates_from_package(package, files)
        selected = next(c.id for c in candidates if c.syntax_path == ("body", 0, "body", 0))
        result = Scheduler(candidates).select(required([selected]))
        self.assertEqual(len(result.owners), 1)
        self.assertEqual(result.counts, (1, 0, 0))
        self.assertEqual(dict(result.aliases)[selected], next(c.id for c in candidates if c.syntax_path == ("body", 0)))


class TopologyTests(unittest.TestCase):
    def checked(self, files):
        package = graph_package(files)
        validate_package(package, files)
        independent_decode(wire(package), files)
        return package

    def names(self, package):
        return {u["id"]: u["qualified_name"] for u in package["units"]}

    def known(self, package):
        return {(package["units"][r["from_unit"]]["qualified_name"],
                 package["units"][r["to_unit"]]["qualified_name"])
                for r in package["relations"] if r["to_unit"] is not None}

    def test_direct_bindings_duplicate_neighbors_and_conditions(self):
        files = {"entry.py": b"def dep(): pass\ndef caller():\n if enabled:\n  dep()\n dep()\n dep()\n"}
        package = self.checked(files)
        self.assertEqual(self.known(package), {("caller", "dep")})
        graph = determined_graph(package)
        caller = next(u["id"] for u in package["units"] if u["qualified_name"] == "caller")
        self.assertEqual(len(graph[caller]), 1)
        self.assertEqual(len([r for r in package["relations"] if r["to_unit"] is not None]), 3)
        self.assertTrue(any(r["conditions"] for r in package["relations"]))

    def test_rebinding_shadow_parameters_dynamic_and_conditional_targets_unknown(self):
        cases = [b"def dep(): pass\ndep = other\ndef caller(): dep()\n",
                 b"def dep(): pass\ndef caller(dep): dep()\n",
                 b"if enabled:\n def dep(): pass\ndef caller(): dep()\n",
                 b"def dep(): pass\ndef dep(): pass\ndef caller(): dep()\n",
                 b"def dep(): pass\ndef caller():\n global dep\n dep()\n",
                 b"def caller(self): self.dep()\n",
                 b"def dep(): pass\ncaller = lambda dep: dep()\n"]
        for raw in cases:
            with self.subTest(raw=raw):
                package = self.checked({"entry.py": raw})
                self.assertFalse(self.known(package))
                self.assertTrue(any(r["reason"] for r in package["relations"]))
                self.assertTrue(all(not values for values in determined_graph(package).values()))

    def test_cross_file_import_binding_and_unknown_external_import(self):
        files = {"pkg/worker.py": b"def run(): pass\n",
                 "pkg/entry.py": b"from .worker import run as task\nimport pkg.worker as worker\nimport external\ndef main():\n task()\n worker.run()\n external.run()\n"}
        package = self.checked(files)
        self.assertEqual(self.known(package), {("main", "run")})
        self.assertTrue(any(r["layer"] == "import" and r["to_unit"] is None for r in package["relations"]))
        self.assertTrue(any(r["site"]["expression"] == "external.run" and r["to_unit"] is None for r in package["relations"]))

    def test_cycles_self_loop_and_explicit_display_direction(self):
        package = self.checked({"entry.py": b"def a(): b()\ndef b(): a()\ndef self_loop(): self_loop()\ndef start(): a()\n"})
        graph = determined_graph(package)
        display = dependency_display(graph)
        names = self.names(package)
        cycles = [{names[key] for key in display["components"][index]} for index in display["cyclic_components"]]
        self.assertIn({"a", "b"}, cycles)
        self.assertIn({"self_loop"}, cycles)
        self.assertEqual(display["meaning"], "static_dependency_not_runtime_execution")
        inverse = dependency_display(graph, direction="dependency_first")
        self.assertEqual(set(inverse["edges"]), {(b, a) for a, b in display["edges"]})
        for view in (display, inverse):
            positions = {value: i for i, value in enumerate(view["order"])}
            self.assertTrue(all(positions[a] < positions[b] for a, b in view["edges"]))

    def test_iterative_scc_handles_chain_above_python_recursion_limit(self):
        graph = {f"n{i:04}": ({f"n{i + 1:04}"} if i < 1499 else set()) for i in range(1500)}
        display = dependency_display(graph)
        self.assertEqual(len(display["components"]), 1500)
        self.assertEqual(display["cyclic_components"], ())
        self.assertEqual(display, dependency_display(dict(reversed(list(graph.items())))))

    def test_source_site_condition_and_known_target_forgery_rejected(self):
        files = {"entry.py": b"def dep(): pass\ndef other(): pass\ndef caller():\n if enabled:\n  dep()\n"}
        package = self.checked(files)
        index = next(i for i, r in enumerate(package["relations"]) if r["to_unit"] is not None)
        for mutate in (lambda p: p["relations"][index]["site"].update(start=[999, 0]),
                       lambda p: p["relations"][index].update(conditions=[]),
                       lambda p: p["relations"][index].update(to_unit=True),
                       lambda p: p["relations"][index].update(direction="dependency_to_caller")):
            with self.subTest(mutate=mutate):
                corrupted = copy.deepcopy(package)
                mutate(corrupted)
                for validator in (lambda p: validate_package(p, files),
                                  lambda p: independent_decode(wire(p), files)):
                    with self.assertRaises(ValueError):
                        validator(corrupted)


class BindingForgeryTests(unittest.TestCase):
    def test_forged_target_with_recomputed_identity_still_fails_both_oracles(self):
        from solo_distill.syntax_v2 import identity
        files = {"entry.py": b"def dep(): pass\ndef other(): pass\ndef caller(): dep()\n"}
        package = graph_package(files)
        relation = next(r for r in package["relations"] if r["to_unit"] is not None)
        relation["to_unit"] = next(i for i, u in enumerate(package["units"]) if u["qualified_name"] == "other")
        relation["id"] = identity("relation-v2", [package["units"][relation["from_unit"]]["id"],
            relation["site"], relation["layer"], package["units"][relation["to_unit"]]["id"],
            relation["conditions"], relation["reason"]])
        for validator in (lambda: validate_package(package, files),
                          lambda: independent_decode(wire(package), files)):
            with self.assertRaises(ValueError):
                validator()


if __name__ == "__main__":
    unittest.main()
