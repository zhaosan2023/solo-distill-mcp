"""新治理蓝图定向验收；隔离临时目录，不安装到来源或旧蓝图。"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_schema_v3_wire_oracle import OBSERVATIONS, SOURCES, case, encode
from test_schema_v3_wire import admission
from solo_distill.brownfield_v3 import (
    OUTPUT_ROOT, GOVERNANCE_MODULES, build_blueprint_v3, export_blueprint_v3,
    validate_blueprint_v3,
)


class GovernanceV3Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = self.base / "source"
        self.workspace = self.base / "workspace"
        self.source.mkdir()
        self.workspace.mkdir()
        for path, raw in SOURCES.items():
            (self.source / path).write_bytes(raw)
        self.candidate, self.trusted, self.report = case()
        self.raw = encode(self.report)

    def build(self, **options):
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            return build_blueprint_v3(self.raw, SOURCES, artifact_root=self.workspace,
                source_root=self.source, admission=admission(), candidate=self.candidate,
                observations=OBSERVATIONS, **self.trusted, **options)

    def test_fixed_tree_and_nonbinding_pointer(self):
        bundle = self.build()
        self.assertTrue(set(GOVERNANCE_MODULES) <= set(bundle.documents))
        self.assertEqual(len(GOVERNANCE_MODULES), 6)
        self.assertEqual(bundle.documents["specs/tasks/_active.md"], b"---\ncontract: null\n---\n")
        self.assertLess(len(bundle.documents["AGENTS.md"].splitlines()), 150)
        self.assertIn("specs/domains/index-001.md", bundle.documents)
        self.assertIn("docs/adrs/README.md", bundle.documents)
        self.assertIn("docs/analysis/README.md", bundle.documents)
        self.assertIn("docs/references/README.md", bundle.documents)
        self.assertIn("tests/README.md", bundle.documents)
        self.assertIn(".scratch/README.md", bundle.documents)
        self.assertEqual(json.loads(bundle.documents["verification/schema-v3/publication.json"]), self.report)
        self.assertEqual(bundle.documents["verification/schema-v3/transport.jsonl"], self.raw)
        self.assertEqual(bundle.report["task_status"], self.report["status"])
        self.assertEqual(bundle.state, "SAVED_CANDIDATE_REQUIRED_REVIEW")
        validate_blueprint_v3(bundle)

    def test_bound_task_is_thin_pointer_not_progress_copy(self):
        body = b"# TU contribution contract\n\nGoal and authorization only.\n"
        bundle = self.build(active_task={"id": "mainline-a-schema-v3", "contract": body})
        pointer = bundle.documents["specs/tasks/_active.md"].decode()
        self.assertEqual(pointer, "---\ncontract: TU-mainline-a-schema-v3.md\n---\n")
        self.assertEqual(bundle.documents["specs/tasks/TU-mainline-a-schema-v3.md"], body)
        self.assertNotIn("Goal", pointer)
        self.assertNotIn("progress", pointer)

    def test_invalid_active_task_rejected(self):
        for task in ({}, {"id": "../escape", "contract": b"contract"},
                     {"id": "ok", "contract": "not bytes"},
                     {"id": "ok", "contract": b""},
                     {"id": "ok", "contract": b"contract", "progress": []}):
            with self.subTest(task=task), self.assertRaises(ValueError):
                self.build(active_task=task)

    def test_exclusive_export_manifest_hashes_and_source_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        legacy = self.workspace / "_bmad-output/blueprints/mainline-b-20261028"
        legacy.mkdir(parents=True)
        (legacy / "AGENTS.md").write_bytes(b"owner legacy bytes\n")
        bundle = self.build()
        manifest = export_blueprint_v3(bundle, self.workspace)
        target = self.workspace / OUTPUT_ROOT
        self.assertEqual(json.loads((target / "manifest.json").read_bytes()), manifest)
        for path, digest in manifest["files"].items():
            saved = (target / path).read_bytes()
            self.assertEqual(saved, bundle.documents[path])
            self.assertEqual(hashlib.sha256(saved).hexdigest(), digest)
        self.assertTrue(manifest["exact_readback"])
        self.assertEqual(manifest["source_writes"], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        self.assertEqual((legacy / "AGENTS.md").read_bytes(), b"owner legacy bytes\n")
        with self.assertRaises(FileExistsError):
            export_blueprint_v3(bundle, self.workspace)

    def test_path_whitelist_and_reference_escape_block_before_writes(self):
        bundle = self.build()
        for path in ("../escape.md", "specs/other.md", "specs/governance/06-extra.md",
                     "docs/analysis/deeper/bad.md", "verification/schema-v3/evil.py",
                     "specs/domains//bad.md", "/absolute.md"):
            documents = {**bundle.documents, path: b"bad"}
            with self.subTest(path=path), self.assertRaises(ValueError):
                export_blueprint_v3(replace(bundle, documents=documents), self.workspace)
        for link in ("missing.md", "../../../../escape.md", "https://evil.example/x",
                     "%2e%2e/%2e%2e/escape", "/absolute.md"):
            documents = {**bundle.documents, "docs/analysis/README.md":
                         ("[bad](" + link + ")\n").encode()}
            with self.subTest(link=link), self.assertRaises(ValueError):
                export_blueprint_v3(replace(bundle, documents=documents), self.workspace)
        self.assertFalse((self.workspace / OUTPUT_ROOT).exists())

    def test_missing_module_tampered_machine_or_pointer_rejected(self):
        bundle = self.build()
        variants = []
        missing = dict(bundle.documents)
        missing.pop(GOVERNANCE_MODULES[-1])
        variants.append(missing)
        variants.append({**bundle.documents, "verification/schema-v3/publication.json": b"{}\n"})
        variants.append({**bundle.documents, "specs/tasks/_active.md": b"---\ncontract: missing.md\n---\n"})
        for documents in variants:
            with self.assertRaises(ValueError):
                validate_blueprint_v3(replace(bundle, documents=documents))

    def test_budgets_equal_pass_and_overflow_reject(self):
        bundle = self.build()
        for path, content in (("docs/analysis/README.md", b"x" * 24576),
                              ("AGENTS.md", b"line\n" * 149)):
            validate_blueprint_v3(replace(bundle, documents={**bundle.documents, path: content}))
        for path, content in (("docs/analysis/README.md", b"x" * 24577),
                              ("AGENTS.md", b"line\n" * 150),
                              ("verification/schema-v3/extra.json", b"x" * 8388608)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_blueprint_v3(replace(bundle, documents={**bundle.documents, path: content}))

    def test_symlink_parent_and_failed_save_leave_no_success_manifest(self):
        bundle = self.build()
        outside = self.base / "outside"
        outside.mkdir()
        (self.workspace / "_bmad-output").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            export_blueprint_v3(bundle, self.workspace)
        self.assertEqual(list(outside.iterdir()), [])
        (self.workspace / "_bmad-output").unlink()
        with patch("solo_distill.brownfield_v3.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                export_blueprint_v3(bundle, self.workspace)
        target = self.workspace / OUTPUT_ROOT
        self.assertFalse((target / "manifest.json").exists())
        with self.assertRaises(FileExistsError):
            export_blueprint_v3(bundle, self.workspace)

    def test_source_overlap_output_binding_and_publication_forgery_reject(self):
        with self.assertRaises(ValueError):
            build_blueprint_v3(self.raw, SOURCES, artifact_root=self.source,
                source_root=self.source, admission=admission(), candidate=self.candidate,
                observations=OBSERVATIONS, **self.trusted)
        bundle = self.build()
        with self.assertRaises(ValueError):
            export_blueprint_v3(bundle, self.base)
        with self.assertRaises(ValueError):
            build_blueprint_v3(self.raw, {"sample.py": b"def changed(): pass\n"},
                artifact_root=self.workspace, source_root=self.source,
                admission=admission(), candidate=self.candidate,
                observations=OBSERVATIONS, **self.trusted)

    def test_cancel_build_and_export(self):
        def stop():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.build(tick=stop)
        bundle = self.build()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            export_blueprint_v3(bundle, self.workspace, tick=stop)
        self.assertFalse((self.workspace / OUTPUT_ROOT).exists())


if __name__ == "__main__":
    unittest.main()
