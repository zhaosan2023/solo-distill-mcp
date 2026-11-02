"""仅新增的离线请求传递回归；不运行既有离线组、真实来源或在线业务。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

from test_schema_v3_cold_chain import driver
from test_schema_v3_stdio import Fixture


class ColdRetryTests(Fixture):
    def test_oversized_witness_stays_external_without_mutation(self):
        module = driver()
        witness = {"selection_id": "external", "witness": {"full": "x" * 70000}}
        arguments = self.arguments(frozen_selection=witness)
        original = copy.deepcopy(arguments)
        wire, count = module.preflight_wire_arguments(arguments, witness)
        self.assertNotIn("frozen_selection", wire)
        self.assertLessEqual(count, 65536)
        self.assertEqual(arguments, original)
        self.assertEqual(len(witness["witness"]["full"]), 70000)

    def test_conflicting_witness_and_live_are_rejected(self):
        module = driver()
        with self.assertRaisesRegex(ValueError, "caller_frozen_selection_conflict"):
            module.preflight_wire_arguments(self.arguments(frozen_selection={"id": "wrong"}), {"id": "right"})
        with self.assertRaisesRegex(ValueError, "offline_request_required"):
            module.preflight_wire_arguments(self.arguments(operation="live"), {})

    def test_other_request_fields_cannot_bypass_input_limit(self):
        module = driver()
        with self.assertRaisesRegex(ValueError, "request_wire_budget"):
            module.preflight_wire_arguments(self.arguments(question="x" * 70000), {})

    def test_new_real_stdio_path_keeps_full_witness_and_two_consumers(self):
        module = driver()
        arguments = self.arguments(request_id="cold-retry-new-fixture")
        original = copy.deepcopy(arguments)
        result = module.run_preflight(self.root, arguments, "preflight-1")
        from solo_distill.preflight_v3 import ARTIFACT_NAMES, PARENT
        target = self.root / PARENT / "preflight-1"
        frozen = json.loads((target / ARTIFACT_NAMES[0]).read_bytes())
        trace = json.loads((self.root / "verification/.scratch/cold-retry-new-fixture.full.json").read_bytes())
        self.assertEqual(trace["selection"], frozen["selection"])
        self.assertEqual(arguments, original)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["service_exit_code"], 0)
        self.assertEqual(result["service_cleanup"], "confirmed_natural_exit")
        self.assertLessEqual(result["request_wire_bytes"], 65536)
        self.assertEqual(result["gateway_requests"], 0)
        self.assertTrue(result["save"]["exact_readback"])
        self.assertEqual(set(ARTIFACT_NAMES), set(result["save"]["artifact_sha256"]))
        self.assertTrue(json.loads((target / ARTIFACT_NAMES[-1]).read_bytes())["production_and_independent_equal"])

    def test_independent_conflict_blocks_before_service_launch(self):
        module = driver()
        original = module.expected_selection

        def changed(*args, **kwargs):
            messages, post, selection = original(*args, **kwargs)
            selection = copy.deepcopy(selection)
            selection["witness"]["unique_body_bytes"] += 1
            return messages, post, selection

        with patch.object(module, "expected_selection", side_effect=changed):
            with patch.object(module, "StdioClient", side_effect=AssertionError("must not launch")):
                with self.assertRaisesRegex(ValueError, "independent_selection_conflict"):
                    module.run_preflight(self.root, self.arguments(), "preflight-1")
        from solo_distill.preflight_v3 import PARENT
        self.assertFalse((self.root / PARENT / "preflight-1").exists())
