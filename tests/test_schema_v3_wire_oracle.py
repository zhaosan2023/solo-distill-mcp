"""独立 V3 接收器的手工分片正控及篡改负控；不访问网关。

不证明生产传输入口、选择独立性、真实发送或人工语义通过。
分片夹具采用固定切点，不复制生产分片尺寸算法。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import canonical, identity
from schema_v3_wire_oracle import expected_publication, reassemble, receive
from test_schema_v3_task_oracle import fixture

SOURCES = {"sample.py": b"def chosen():\n    return target()\n"}
NAMES = {("sample.py", "chosen")}
CAPS = {"policy": "adaptive-backpressure-v1", "authority": "caller_explicit",
        "max_frame_bytes": 22016, "max_transfer_bytes": 1572864,
        "max_decoded_bytes": 1048576, "max_chunks": 512, "chunking": True}
OBSERVATIONS = {"scope": "offline_fixture"}


def encode(report, response_id=7):
    return canonical({"jsonrpc": "2.0", "id": response_id, "result": {
        "content": [{"type": "text", "text": canonical(report).decode("utf-8")}],
        "isError": report["status"] in {"blocked", "failed"}}}) + b"\n"


def manual_chunks(report, response_id=7, width=700):
    decoded = encode(report, response_id)
    core, task = report["core"], report["task_evidence"]
    binding = {"project_id": core["package"]["project_id"],
               "snapshot_id": core["package"]["snapshot_id"],
               "input_id": core["input_id"], "core_id": core["core_id"],
               "selection_id": core["selection_id"],
               "evidence_id": task["evidence_id"], "task_request_id": task["request_id"],
               "question_id": task["question_id"], "post_sha256": task["post_sha256"],
               "policy_id": "adaptive-backpressure-v1", "contract_version": 3,
               "encoding": "base64-jsonrpc-wire-v1", "wire_encoding": "json"}
    parts = [decoded[offset:offset + width] for offset in range(0, len(decoded), width)]
    frames = []
    for index, part in enumerate(parts):
        frames.append(canonical({"jsonrpc": "2.0",
            "method": "notifications/solo_distill/chunk-v1", "params": {
                **binding, "count": len(parts), "index": index,
                "chunk_bytes": len(part), "chunk_sha256": hashlib.sha256(part).hexdigest(),
                "data": base64.b64encode(part).decode("ascii")}}) + b"\n")
    manifest = {**binding, "count": len(parts), "decoded_bytes": len(decoded),
                "sha256": hashlib.sha256(decoded).hexdigest()}
    frames.append(encode({"contract_version": 3, "kind": "chunk_manifest",
                          "status": report["status"], "manifest": manifest}, response_id))
    return b"".join(frames), decoded


def mutate_frame(raw, index, mutate):
    frames = raw.splitlines(keepends=True)
    value = json.loads(frames[index])
    mutate(value)
    frames[index] = canonical(value) + b"\n"
    return b"".join(frames)


def mutate_manifest(raw, mutate):
    def change(envelope):
        report = json.loads(envelope["result"]["content"][0]["text"])
        mutate(report)
        envelope["result"]["content"][0]["text"] = canonical(report).decode("utf-8")
    return mutate_frame(raw, -1, change)


def case(candidate_change=None, failures=None):
    candidate, trusted, *_ = fixture(SOURCES, NAMES)
    if candidate_change is not None:
        candidate_change(candidate)
    trusted = {**trusted, "failures": failures}
    report = expected_publication(candidate, SOURCES, observations=OBSERVATIONS, **trusted)
    return candidate, trusted, report


class WireOracleTests(unittest.TestCase):
    def accept(self, raw, candidate, trusted, capabilities=None, response_id=7):
        with patch("socket.socket", side_effect=AssertionError("禁止联网")):
            return receive(raw, SOURCES, CAPS if capabilities is None else capabilities,
                protocol_version="2024-11-05", response_id=response_id,
                return_bytes=22016, candidate=candidate, observations=OBSERVATIONS, **trusted)

    def reject_wire(self, raw, capabilities=None, response_id=7, return_bytes=22016):
        with self.assertRaises(ValueError):
            reassemble(raw, CAPS if capabilities is None else capabilities,
                       response_id=response_id, return_bytes=return_bytes)

    def test_single_response_complete_source_and_task_authentication(self):
        candidate, trusted, report = case()
        raw = encode(report)
        self.assertLessEqual(len(raw), 22016)
        receipt = self.accept(raw, candidate, trusted)
        self.assertEqual(receipt["publication"], report)
        self.assertEqual(receipt["decoded_bytes"], len(raw))
        self.assertEqual(receipt["transfer_bytes"], len(raw))
        self.assertEqual(receipt["physical_frames"], 1)
        self.assertEqual(receipt["notification_chunks"], 0)
        self.assertEqual(receipt["actual_network_send"], "not_proven_by_this_asset")
        self.assertEqual(receipt["host_loading"], "not_proven_by_this_asset")
        self.assertEqual(receipt["business_semantics"], "requires_real_independent_human_review")
        self.assertEqual(self.accept(raw, candidate, trusted,
            {**CAPS, "chunking": False})["task_status"], "complete")

    def test_manual_fragment_boundaries_and_exact_byte_accounting(self):
        candidate, trusted, report = case()
        raw, decoded = manual_chunks(report, width=113)
        actual, manifest = reassemble(raw, CAPS, response_id=7, return_bytes=22016)
        self.assertEqual(actual, decoded)
        count = raw.count(b"\n") - 1
        self.assertEqual(manifest["manifest"]["count"], count)
        self.assertEqual(manifest["manifest"]["decoded_bytes"], len(decoded))
        self.assertEqual(manifest["manifest"]["sha256"], hashlib.sha256(decoded).hexdigest())
        receipt = self.accept(raw, candidate, trusted)
        self.assertEqual(receipt["physical_frames"], count + 1)
        self.assertEqual(receipt["notification_chunks"], count)
        self.assertEqual(receipt["transfer_bytes"], len(raw))
        self.assertEqual(receipt["publication"], report)

    def test_partial_blocked_and_failed_status_not_overridden_by_complete_core(self):
        for failures, status in ((None, "partial"),
                ([{"kind": "blocked", "reason": "source_changed"}], "blocked"),
                ([{"kind": "failed", "reason": "generation_failed"}], "failed")):
            candidate, trusted, report = case(lambda value: value["conclusions"].pop(), failures)
            self.assertEqual(report["core"]["package"]["status"], "complete")
            for raw in (encode(report), manual_chunks(report)[0]):
                with self.subTest(status=status, frames=raw.count(b"\n")):
                    self.assertEqual(self.accept(raw, candidate, trusted)["task_status"], status)

    def test_response_identity_type_and_task_request_identity_are_separate(self):
        candidate, trusted, report = case()
        for response_id in (7, "7", "中文身份\\\"", "\x00" * 128):
            for raw in (encode(report, response_id), manual_chunks(report, response_id)[0]):
                with self.subTest(identity=repr(response_id), frames=raw.count(b"\n")):
                    result = self.accept(raw, candidate, trusted, response_id=response_id)
                    self.assertEqual(result["publication"]["task_evidence"]["request_id"],
                                     trusted["request_id"])
        self.reject_wire(encode(report, "7"), response_id=7)
        self.reject_wire(encode(report, 7), response_id=True)
        for invalid in (None, False, 1.0, "x" * 129, 10 ** 129):
            with self.subTest(invalid=type(invalid).__name__):
                self.reject_wire(encode(report), response_id=invalid)

    def test_four_measured_hard_limits_exact_pass_and_one_byte_or_chunk_lower_fail(self):
        _, _, report = case()
        raw, decoded = manual_chunks(report)
        actual = {"max_frame_bytes": max(map(len, raw.splitlines(keepends=True))),
                  "max_transfer_bytes": len(raw), "max_decoded_bytes": len(decoded),
                  "max_chunks": raw.count(b"\n") - 1}
        exact = {**CAPS, **actual}
        self.assertEqual(reassemble(raw, exact, response_id=7,
            return_bytes=22016)[0], decoded)
        for metric, size in actual.items():
            with self.subTest(metric=metric):
                self.reject_wire(raw, {**exact, metric: size - 1})
        self.reject_wire(raw, return_bytes=actual["max_frame_bytes"] - 1)
        single = encode(report)
        exact_single = {**CAPS, "max_frame_bytes": len(single),
                        "max_transfer_bytes": len(single), "max_decoded_bytes": len(single)}
        self.assertEqual(reassemble(single, exact_single, response_id=7,
            return_bytes=len(single))[0], single)
        self.reject_wire(single, {**exact_single, "max_decoded_bytes": len(single) - 1})

    def test_capability_authority_types_profile_ceiling_and_chunking_rejected(self):
        _, _, report = case()
        raw = manual_chunks(report)[0]
        variants = [None, {}, {**CAPS, "extra": True},
                    {**CAPS, "policy": "legacy"}, {**CAPS, "authority": "model"},
                    {**CAPS, "chunking": 1}, {**CAPS, "chunking": False},
                    {**CAPS, "max_frame_bytes": 1023},
                    {**CAPS, "max_transfer_bytes": 1024}]
        for key in ("max_frame_bytes", "max_transfer_bytes", "max_decoded_bytes", "max_chunks"):
            variants.extend({**CAPS, key: value} for value in (True, 0, CAPS[key] + 1))
        for number, caps in enumerate(variants):
            with self.subTest(number=number):
                self.reject_wire(raw, caps if caps is not None else {})
        for budget in (True, 0, -1, None):
            self.reject_wire(raw, return_bytes=budget)

    def test_missing_repeated_reordered_truncated_and_extra_frames_rejected(self):
        _, _, report = case()
        raw = manual_chunks(report)[0]
        lines = raw.splitlines(keepends=True)
        variants = [b"".join(lines[1:]), b"".join(lines[:-1]),
                    b"".join([lines[0], *lines]),
                    b"".join([lines[1], lines[0], *lines[2:]]),
                    raw[:-1], raw[:-9], raw + lines[-1], raw + b"\n",
                    b"".join([*lines[:-1], lines[-1], lines[0]]),
                    b"".join([lines[0] + b"\r\n", *lines[1:]])]
        for number, changed in enumerate(variants):
            with self.subTest(number=number):
                self.reject_wire(changed)
        for raw_value in (b"", b"\n", b"{}\n", "not bytes", None):
            self.reject_wire(raw_value)

    def test_chunk_integer_types_fields_method_base64_lengths_and_digests_rejected(self):
        _, _, report = case()
        raw = manual_chunks(report)[0]
        mutations = [lambda v: v.update(method="notifications/unknown"),
                     lambda v: v.update(id=7), lambda v: v.update(jsonrpc="1.0"),
                     lambda v: v["params"].update(index=True),
                     lambda v: v["params"].update(index=1),
                     lambda v: v["params"].update(count=True),
                     lambda v: v["params"].update(chunk_bytes=True),
                     lambda v: v["params"].update(chunk_bytes=0),
                     lambda v: v["params"].update(chunk_bytes=1048577),
                     lambda v: v["params"].update(chunk_sha256="0" * 64),
                     lambda v: v["params"].update(data="%%%"),
                     lambda v: v["params"].update(data="非 ASCII"),
                     lambda v: v["params"].update(data=None),
                     lambda v: v["params"].update(data=v["params"]["data"] + "="),
                     lambda v: v["params"].update(extra="forged"),
                     lambda v: v["params"].pop("core_id")]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                self.reject_wire(mutate_frame(raw, 0, mutate))

    def test_manifest_fields_versions_status_counts_and_digest_rejected(self):
        _, _, report = case()
        raw = manual_chunks(report)[0]
        mutations = [lambda v: v.update(contract_version=2),
                     lambda v: v.update(contract_version=True),
                     lambda v: v.update(kind="sealed_knowledge"),
                     lambda v: v.update(status="partial"),
                     lambda v: v.update(status="unknown"),
                     lambda v: v["manifest"].update(contract_version=2),
                     lambda v: v["manifest"].update(count=True),
                     lambda v: v["manifest"].update(count=0),
                     lambda v: v["manifest"].update(count=513),
                     lambda v: v["manifest"].update(decoded_bytes=True),
                     lambda v: v["manifest"].update(decoded_bytes=0),
                     lambda v: v["manifest"].update(decoded_bytes=1048577),
                     lambda v: v["manifest"].update(sha256="0" * 64),
                     lambda v: v["manifest"].update(sha256="F" * 64),
                     lambda v: v["manifest"].update(extra=True),
                     lambda v: v["manifest"].pop("evidence_id")]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                self.reject_wire(mutate_manifest(raw, mutate))
        self.reject_wire(mutate_frame(raw, -1, lambda v: v["result"].update(isError=True)))

    def test_each_binding_conflict_and_consistently_forged_transport_binding_rejected(self):
        _, _, report = case()
        raw = manual_chunks(report)[0]
        bindings = json.loads(raw.splitlines()[0])["params"]
        keys = {"project_id", "snapshot_id", "input_id", "core_id", "selection_id",
                "evidence_id", "task_request_id", "question_id", "post_sha256",
                "policy_id", "contract_version", "encoding", "wire_encoding"}
        for key in sorted(keys):
            changed = 2 if key == "contract_version" else (
                "0" * 64 if key.endswith("_sha256") or key in {
                    "input_id", "core_id", "evidence_id", "question_id"} else "changed")
            with self.subTest(key=key):
                self.reject_wire(mutate_frame(raw, 0,
                    lambda v, k=key, item=changed: v["params"].update({k: item})))
                self.reject_wire(mutate_manifest(raw,
                    lambda v, k=key, item=changed: v["manifest"].update({k: item})))
        forged = raw
        for index in range(raw.count(b"\n") - 1):
            forged = mutate_frame(forged, index,
                lambda v: v["params"].update(snapshot_id="consistent-forgery"))
        forged = mutate_manifest(forged,
            lambda v: v["manifest"].update(snapshot_id="consistent-forgery"))
        self.reject_wire(forged)
        self.assertEqual(bindings["contract_version"], 3)

    def test_duplicate_json_keys_floats_and_noncanonical_encodings_rejected(self):
        _, _, report = case()
        raw = manual_chunks(report)[0]
        frames = raw.splitlines(keepends=True)
        duplicate = b'{"jsonrpc":"2.0",' + frames[0][1:]
        for first in (duplicate, b'{"number":1.0}\n', b'{"number":NaN}\n',
                      frames[0][:-1] + b" \n",
                      json.dumps(json.loads(frames[0]), indent=2).encode() + b"\n"):
            self.reject_wire(b"".join([first, *frames[1:]]))
        single = encode(report)
        self.reject_wire(single[:-1] + b" \n")
        envelope = json.loads(single)
        envelope["result"]["content"][0]["text"] = json.dumps(report, indent=2)
        self.reject_wire(canonical(envelope) + b"\n")

    def test_publication_versions_fields_observation_and_task_binding_rejected(self):
        _, _, report = case()
        mutations = [lambda v: v.update(contract_version=2),
                     lambda v: v.update(contract_version=True),
                     lambda v: v.update(kind="chunk_manifest"),
                     lambda v: v.update(status="partial"),
                     lambda v: v.update(authenticated=True),
                     lambda v: v.update(observations={"value": "x" * 513}),
                     lambda v: v.update(observations=[]),
                     lambda v: v["task_evidence"].update(core_id="0" * 64),
                     lambda v: v["task_evidence"].update(project_id="other"),
                     lambda v: v["core"].update(policy_id="legacy"),
                     lambda v: v.update(core=None),
                     lambda v: v.update(task_evidence=None),
                     lambda v: v.pop("task_evidence")]
        for number, mutate in enumerate(mutations):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(number=number):
                self.reject_wire(encode(changed))

    def test_rehashed_text_failures_structure_and_unsent_reference_need_external_authentication(self):
        candidate, trusted, report = case()
        mutations = [lambda v: v["task_evidence"].update(summary="forged text"),
                     lambda v: v["task_evidence"]["facts"][0].update(text="forged conclusion"),
                     lambda v: v["task_evidence"].update(unknowns=[]),
                     lambda v: v["task_evidence"]["facts"][0]["sources"][0].update(id="unsent"),
                     lambda v: v["task_evidence"].update(failures=[
                         {"kind": "blocked", "reason": "invented"}], status="blocked"),
                     lambda v: v["core"]["package"]["macro"]["symbols"].pop()]
        for number, mutate in enumerate(mutations):
            changed = copy.deepcopy(report)
            mutate(changed)
            task, core = changed["task_evidence"], changed["core"]
            task["evidence_id"] = identity("task-evidence-v3",
                {key: item for key, item in task.items() if key != "evidence_id"})
            core["core_id"] = identity("core-v3",
                {key: item for key, item in core.items() if key != "core_id"})
            changed["status"] = task["status"]
            for raw in (encode(changed), manual_chunks(changed)[0]):
                with self.subTest(number=number, frames=raw.count(b"\n")), self.assertRaises(ValueError):
                    self.accept(raw, candidate, trusted)

    def test_changed_external_sources_candidate_failures_and_question_rejected(self):
        candidate, trusted, report = case()
        raw = manual_chunks(report)[0]
        altered = copy.deepcopy(candidate)
        altered["summary"] = "different saved output"
        with self.assertRaises(ValueError):
            self.accept(raw, altered, trusted)
        for key, value in (("request_id", "changed"), ("snapshot_id", "changed"),
                ("question", "changed"), ("selection_id", "changed"),
                ("post", trusted["post"] + b"\n"), ("frozen_coverage", {}),
                ("failures", [{"kind": "failed", "reason": "external_failure"}])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.accept(raw, candidate, {**trusted, key: value})
        with self.assertRaises(ValueError):
            receive(raw, {"sample.py": SOURCES["sample.py"] + b"# changed\n"}, CAPS,
                protocol_version="2024-11-05", response_id=7, return_bytes=22016,
                candidate=candidate, observations=OBSERVATIONS, **trusted)
        with self.assertRaises(ValueError):
            receive(raw, SOURCES, CAPS, protocol_version="unknown", response_id=7,
                return_bytes=22016, candidate=candidate, observations=OBSERVATIONS, **trusted)

    def test_cancel_and_deadline_tick_propagate_at_entry_and_during_fragments(self):
        candidate, trusted, report = case()
        raw = manual_chunks(report)[0]
        for stop_at in (1, 3, 8):
            calls = 0
            def stopped():
                nonlocal calls
                calls += 1
                if calls >= stop_at:
                    raise RuntimeError("cancelled_or_deadline")
            with self.subTest(stop_at=stop_at), self.assertRaisesRegex(RuntimeError, "cancelled_or_deadline"):
                reassemble(raw, CAPS, response_id=7, return_bytes=22016, tick=stopped)
            self.assertEqual(calls, stop_at)
        def expired():
            raise RuntimeError("deadline")
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            expected_publication(candidate, SOURCES, observations=OBSERVATIONS,
                                 **{**trusted, "tick": expired})
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            self.accept(raw, candidate, {**trusted, "tick": expired})


if __name__ == "__main__":
    unittest.main()
