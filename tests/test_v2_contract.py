"""E1 source authentication and closed-reference regressions. Offline only."""
from __future__ import annotations

import copy
import json
import threading
import time
import unittest

import test_offline as offline
from solo_distill.evidence import EvidenceBlocked
from solo_distill.evidence_v2 import V2EvidenceReader, attach_roles
from solo_distill.extractor import Scanner
from solo_distill.package_v2 import build_package, decode_package, finalize, validate_package
from solo_distill.syntax_v2 import extract_units
from v2_validator import decode as independent_decode


def wire(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def fixture(raw, selected=None):
    files = {"sample.py": raw}
    units = extract_units("sample.py", raw)["units"]
    keys = selected(units) if selected else [unit["id"] for unit in units]
    return attach_roles(build_package("fixture", "snapshot", files, keys), files), files


class PublicContractTests(unittest.TestCase):
    def accepted(self, package, files):
        validate_package(package, files)
        self.assertEqual(independent_decode(wire(package), files), package)
        self.assertEqual(decode_package(wire(package), files), package)

    def rejected(self, package, files):
        for decoder in (decode_package, independent_decode):
            with self.subTest(decoder=decoder.__module__), self.assertRaises(ValueError):
                decoder(wire(package), files)

    def test_decorated_async_class_and_real_parent_alias(self):
        raw = b"@decorate\nclass Owner:\n @route.command('go')\n async def run(self):\n  self.value = Worker()\n  return self.value\n"
        package, files = fixture(raw)
        self.accepted(package, files)
        self.assertEqual(len(package["owners"]), 1)
        self.assertEqual(package["units"][0]["start"], [1, 0])
        self.assertEqual(package["units"][1]["start"], [3, 1])
        self.assertEqual(package["units"][1]["qualified_name"], "Owner.run")
        self.assertTrue(all(source["owner"] == 0 for source in package["sources"]))
        roles = {proof["role"] for proof in package["role_proofs"]}
        self.assertLessEqual({"entry", "assembly", "state"}, roles)

    def test_same_line_statements_have_distinct_exact_offsets(self):
        package, files = fixture("é = 1; other = 2\n".encode())
        self.accepted(package, files)
        left, right = package["units"]
        self.assertNotEqual(left["id"], right["id"])
        self.assertEqual(right["start"], [1, 8])
        self.assertEqual(right["char_start"], [1, 7])
        self.assertEqual(left["qualified_name"], None)
        corrupted = copy.deepcopy(package)
        corrupted["units"][1]["raw_start"] = left["raw_start"]
        self.rejected(corrupted, files)

    def test_latin1_bom_crlf_and_non_ascii_coordinates(self):
        for raw in (b"# coding: latin-1\r\n\xe9 = 1; result = 2\r\n",
                    b"\xef\xbb\xbf" + "名称 = 1; result = 2\r\n".encode(),
                    "def café():\n return '值'\n".encode(),
                    b"first = 'a\x0bb'; second = 2\n",
                    "first = 'a\u2028b'; second = 2\r\n".encode(),
                    b"first = 1\rsecond = 2\r"):
            with self.subTest(raw=raw):
                package, files = fixture(raw)
                self.accepted(package, files)
                for unit in package["units"]:
                    self.assertGreater(unit["raw_end"], unit["raw_start"])
                self.assertGreater(sum(o["body_bytes"] for o in package["owners"]), 0)

    def test_main_guard_forms_are_versioned_not_guessed(self):
        for expression, kind in (("__name__ == '__main__'", "main_guard"),
                                  ("'__main__' == __name__", "main_guard"),
                                  ("__name__ == '__main__' and enabled", "statement"),
                                  ("alias == '__main__'", "statement"),
                                  ("__name__ != '__main__'", "statement")):
            with self.subTest(expression=expression):
                package, files = fixture((f"if {expression}:\n def start():\n  return 1\nelse:\n pass\n").encode())
                self.accepted(package, files)
                self.assertEqual(package["units"][0]["kind"], kind)
                self.assertEqual(package["units"][0]["qualified_name"], None)
                self.assertEqual(package["conditions"][0]["branch"], "if")
                if kind == "statement":
                    corrupted = copy.deepcopy(package)
                    corrupted["units"][0]["kind"] = "main_guard"
                    self.rejected(corrupted, files)

    def test_selected_conditional_definition_reserves_complete_origin(self):
        raw = b"if enabled:\n def chosen():\n  return 1\nelse:\n def fallback():\n  return 2\n"
        package, files = fixture(raw, lambda units: [next(u["id"] for u in units if u["qualified_name"] == "chosen")])
        self.accepted(package, files)
        self.assertEqual(len(package["owners"]), 1)
        self.assertEqual(package["units"][0]["kind"], "statement")
        self.assertEqual({c["branch"] for c in package["conditions"]}, {"if", "else"})
        self.assertEqual({u["qualified_name"] for u in package["units"]}, {None, "chosen", "fallback"})

    def test_nested_try_loop_with_and_match_conditions(self):
        raw = b"try:\n for i in items:\n  with resource():\n   def nested(): pass\nexcept ValueError:\n def fallback(): pass\nmatch value:\n case 1:\n  def other(): pass\n"
        package, files = fixture(raw)
        self.accepted(package, files)
        self.assertLessEqual({"try", "loop", "with", "except", "case"}, {c["branch"] for c in package["conditions"]})

    def test_invalid_keys_identities_indices_types_and_enums_rejected(self):
        package, files = fixture(b"if enabled:\n def run(): return Worker()\n")
        mutations = [
            lambda p: p.update(extra=True),
            lambda p: p.update(contract_version=True),
            lambda p: p.update(scenario=[]),
            lambda p: p["units"][0].update(file=True),
            lambda p: p["units"][0].update(raw_start=True),
            lambda p: p["units"][0].update(start=[True, 0]),
            lambda p: p["units"][0].update(syntax_path=["body", True]),
            lambda p: p["sources"][0].update(owner=999),
            lambda p: p["sources"][0].update(unit=None),
            lambda p: p["sources"][0].update(id=p["units"][0]["id"]),
            lambda p: p["owners"][0].update(complete=1),
            lambda p: p["owners"][0].update(body_bytes=False),
            lambda p: p["role_proofs"][0].update(role="invented"),
            lambda p: p["role_proofs"][0].update(level="verified"),
            lambda p: p["role_proofs"][0].update(sources=[0, 0]),
            lambda p: p["conditions"][0].update(source=999),
            lambda p: p["conditions"][0].update(branch="else"),
            lambda p: p["units"][1].update(conditions=[]),
            lambda p: p["coverage"].update(scan_files=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                corrupted = copy.deepcopy(package)
                mutate(corrupted)
                self.rejected(corrupted, files)

    def test_duplicate_keys_nonfinite_and_unsupported_version(self):
        for raw in (b'{"contract_version":2,"contract_version":2}',
                    b'{"contract_version":2,"x":NaN}', b'{"contract_version":3}',
                    b'{"contract_version":true}', b'[]'):
            for decoder in (decode_package, independent_decode):
                with self.subTest(raw=raw, decoder=decoder.__module__), self.assertRaises(ValueError):
                    decoder(raw, {})

    def test_stale_bytes_forged_class_header_and_sibling_alias_rejected(self):
        package, files = fixture(b"class Owner:\n def run(self): return 1\ndef other(): return 2\n")
        self.rejected(package, {"sample.py": files["sample.py"] + b"# changed\n"})
        corrupted = copy.deepcopy(package)
        corrupted["owners"][0]["raw_end"] = 12
        self.rejected(corrupted, files)
        corrupted = copy.deepcopy(package)
        corrupted["sources"][-1]["owner"] = 0
        self.rejected(corrupted, files)
        corrupted = copy.deepcopy(package)
        corrupted["units"][0]["ast_type"] = "Module"
        self.rejected(corrupted, files)

    def test_role_tags_do_not_satisfy_unrelated_or_stronger_obligations(self):
        package, files = fixture(b"def run():\n return Worker()\n")
        package["facts"] = [{"id": "fact-1", "text": "Possible assembly, not runtime proof.",
            "level": "sourced_inference", "sources": [0], "relations": [], "conditions": []}]
        package["obligations"] = [
            {"id": "required-state", "required": True, "evidence": "direct_static", "role": "state",
             "state": "unknown", "supports": [{"table": "role_proofs", "index": 0}], "reason": "missing_state"},
            {"id": "stronger-fact", "required": True, "evidence": "direct_static", "role": None,
             "state": "unknown", "supports": [{"table": "facts", "index": 0}], "reason": "insufficient_strength"},
            {"id": "navigation", "required": True, "evidence": "sourced_inference", "role": None,
             "state": "unknown", "supports": [{"table": "facts", "index": 0}], "reason": "pending"},
        ]
        package = finalize(package)
        self.accepted(package, files)
        self.assertEqual(package["status"], "partial")
        self.assertEqual([o["state"] for o in package["obligations"]], ["unknown", "unknown", "satisfied"])
        corrupted = copy.deepcopy(package)
        corrupted["status"] = "complete"
        self.rejected(corrupted, files)
        corrupted = copy.deepcopy(package)
        corrupted["role_proofs"][0].update(role="state", rule="self-attribute-assignment-v1")
        self.rejected(corrupted, files)
        corrupted = copy.deepcopy(package)
        corrupted["facts"][0]["level"] = "direct_static"
        self.rejected(corrupted, files)

    def test_failure_precedence_and_no_not_applicable_escape(self):
        package, files = fixture(b"pass\n")
        package["failures"] = [{"kind": "blocked", "reason": "budget"}]
        package = finalize(package)
        self.accepted(package, files)
        self.assertEqual(package["status"], "blocked")
        package["failures"].append({"kind": "failed", "reason": "invalid_source"})
        package = finalize(package)
        self.accepted(package, files)
        self.assertEqual(package["status"], "failed")

    def test_v1_decoding_returns_existing_representation_unchanged(self):
        value = {"contract_version": 1, "sources": [{"symbol": "Legacy.run", "start_line": 1}],
                 "observations": {"unchanged": True}}
        for decoder in (decode_package, independent_decode):
            self.assertEqual(decoder(wire(value), {}), value)


class V2ReaderTests(unittest.TestCase):
    setUp = offline.ExtractorTests.setUp

    def reader(self):
        scanner = Scanner(self.project, (".",), 10)
        return V2EvidenceReader(scanner, scanner.run(), time.monotonic() + 10, threading.Event())

    def test_authorized_batch_reads_top_import_and_method_without_execution(self):
        marker = self.root / "EXECUTED"
        (self.root / "entry.py").write_text(f"import os\nopen({str(marker)!r}, 'w')\nclass Worker:\n def run(self): return 1\n")
        reader = self.reader()
        inventory = reader.inventory()
        selected = [u["id"] for u in inventory if u["qualified_name"] == "Worker.run" or u["ast_type"] == "Import"]
        package = reader.read(selected)
        reader.verify()
        self.assertFalse(marker.exists())
        self.assertEqual(len(package["owners"]), 2)
        independent_decode(wire(package), reader.raw_files)
        self.assertFalse(any("text" in source for source in package["sources"]))

    def test_changed_snapshot_cancel_and_zero_selection_block(self):
        reader = self.reader()
        reader.inventory()
        with self.assertRaises(ValueError):
            reader.read([])
        selected = list(reader.units)[:1]
        reader.read(selected)
        (self.root / "entry.py").write_text("def changed(): pass\n")
        with self.assertRaises(EvidenceBlocked):
            reader.verify()
        reader.cancelled.set()
        with self.assertRaises(EvidenceBlocked):
            reader.read(selected)

    def test_oversize_complete_unit_commits_no_partial_package(self):
        (self.root / "entry.py").write_text("def huge():\n return '" + "x" * 99000 + "'\n")
        reader = self.reader()
        reader.inventory()
        with self.assertRaises(EvidenceBlocked):
            reader.read(list(reader.units))
        self.assertIsNone(reader.package)

    def test_secret_literal_never_becomes_body_evidence(self):
        (self.root / "entry.py").write_text("API_KEY = 'real-sensitive-value'\n")
        reader = self.reader()
        with self.assertRaises(EvidenceBlocked):
            reader.inventory()
        self.assertEqual(reader.raw_files, {})


if __name__ == "__main__":
    unittest.main()
