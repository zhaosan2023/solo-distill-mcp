"""生产任务证据与独立期望差分；不访问网关或执行来源。

结构支持不是选择独立性、网络发送或人工语义通过证明。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from schema_v3_macro_oracle import canonical, identity
from schema_v3_task_oracle import authenticate_task, expected_task
from test_schema_v3_local import SOURCES
from test_schema_v3_task_oracle import fixture, trusted_structure, QUESTION, DIMENSIONS, COVERAGE
from solo_distill.core_v3 import SealedCore
from solo_distill.snapshot_v2 import snapshot_scope
from solo_distill.task_v3 import build_prompt, build_task, validate_task


class ProductionTaskTests(unittest.TestCase):
    def setup_case(self, files=SOURCES, names=None):
        candidate, trusted, messages, allowed, document = fixture(files, names)
        return candidate, trusted, messages, allowed, SealedCore(canonical(document))

    def rehash(self, value):
        value['evidence_id'] = identity('task-evidence-v3',
            {key: item for key, item in value.items() if key != 'evidence_id'})
        return value

    @snapshot_scope
    def compare(self, candidate, files, core, trusted, failures=None):
        with patch('socket.socket', side_effect=AssertionError('禁止联网')):
            value = build_task(candidate, core, files, **trusted, failures=failures)
            checked = validate_task(value, core, files, **trusted)
        expected = expected_task(candidate, files, **trusted, failures=failures)
        self.assertEqual(canonical(value), canonical(expected))
        self.assertEqual(checked, value)
        self.assertEqual(authenticate_task(value, candidate, files,
            **trusted, failures=failures)['status'], 'pass')
        return value

    def test_exact_prompt_matches_independent_full_owner_view_and_gateway_encoder(self):
        candidate, trusted, messages, allowed, core = self.setup_case()
        with patch('socket.socket', side_effect=AssertionError('禁止联网')):
            actual_messages, actual_post, actual_allowed = build_prompt(core, SOURCES,
                **trusted_structure(), question=QUESTION, dimensions=DIMENSIONS,
                frozen_coverage=COVERAGE)
        self.assertEqual(actual_messages, messages)
        self.assertEqual(actual_post, trusted['post'])
        self.assertEqual(actual_allowed, allowed)
        from solo_distill.gateway import encode_request
        self.assertEqual(encode_request(actual_messages), actual_post)
        self.compare(candidate, SOURCES, core, trusted)

    def test_required_optional_and_failure_statuses_match_independent_oracle(self):
        candidate, trusted, _, _, core = self.setup_case()
        candidate['conclusions'].pop()
        self.assertEqual(self.compare(candidate, SOURCES, core, trusted)['status'], 'partial')
        for failures, status in (([{'kind': 'blocked', 'reason': 'source_changed'}], 'blocked'),
                ([{'kind': 'blocked', 'reason': 'cancelled'},
                  {'kind': 'failed', 'reason': 'generation_failed'}], 'failed')):
            self.assertEqual(self.compare(candidate, SOURCES, core, trusted, failures)['status'], status)
        trusted['dimensions'][-1]['required'] = False
        _, trusted['post'], _ = build_prompt(core, SOURCES, **trusted_structure(),
            question=QUESTION, dimensions=trusted['dimensions'], frozen_coverage=COVERAGE)
        value = self.compare(candidate, SOURCES, core, trusted)
        self.assertEqual(value['status'], 'complete')
        self.assertEqual(value['obligations'][-1]['state'], 'unknown')

    def test_all_model_text_unknowns_and_failures_are_preserved_and_detached(self):
        candidate, trusted, _, _, core = self.setup_case()
        text = '完整推断："\\\n' * 2000
        candidate['summary'] = text
        candidate['conclusions'][0]['text'] = text
        candidate['unknowns'] = [text, '', '重复', '重复']
        failures = [{'kind': 'blocked', 'reason': 'cancelled'}]
        value = self.compare(candidate, SOURCES, core, trusted, failures)
        self.assertEqual(value['summary'], text)
        self.assertEqual(value['facts'][0]['text'], text)
        candidate['unknowns'].clear()
        candidate['conclusions'].clear()
        failures[0]['reason'] = 'changed'
        self.assertEqual(len(value['unknowns']), 4)
        self.assertEqual(len(value['facts']), 4)
        self.assertEqual(value['failures'][0]['reason'], 'cancelled')
        checked = validate_task(value, core, SOURCES, **trusted)
        checked['facts'].clear()
        self.assertEqual(len(value['facts']), 4)

    def test_real_nested_aliases_are_allowed_without_sibling_or_macro_reference(self):
        files = {'guard.py': b'if enabled:\n    def chosen():\n        return target()\n'}
        candidate, trusted, messages, _, core = self.setup_case(files, {('guard.py', 'chosen')})
        record = json.loads(messages[1]['content'])['sources'][0]
        alias = next(key for key in record['alias_source_ids'] if key != record['source_id'])
        candidate['conclusions'][0]['source_ids'] = [alias]
        value = self.compare(candidate, files, core, trusted)
        self.assertEqual(value['facts'][0]['sources'][0]['id'], alias)

    def test_rehashed_fields_namespaces_supports_states_and_duplicate_facts_rejected(self):
        candidate, trusted, _, _, core = self.setup_case()
        original = self.compare(candidate, SOURCES, core, trusted)
        mutations = [
            lambda v: v.update(evidence_version=True),
            lambda v: v.update(kind='knowledge_package'),
            lambda v: v.update(core_id='0' * 64),
            lambda v: v.update(input_id='0' * 64),
            lambda v: v.update(selection_id='changed'),
            lambda v: v.update(project_id='changed'),
            lambda v: v.update(request_id='changed'),
            lambda v: v.update(question_id='0' * 64),
            lambda v: v.update(post_sha256='0' * 64),
            lambda v: v.update(status='partial'),
            lambda v: v.update(authenticated=True),
            lambda v: v['facts'][0].update(level='direct_static'),
            lambda v: v['facts'][0].update(semantic_support='passed'),
            lambda v: v['facts'][0]['sources'][0].update(space='core'),
            lambda v: v['facts'][0]['sources'][0].update(table='symbols'),
            lambda v: v['facts'][0]['sources'][0].update(index=0),
            lambda v: v['facts'][0]['sources'][0].update(id='forged'),
            lambda v: v['facts'][0].update(id='0' * 64),
            lambda v: v['facts'][0].update(dimension_id='calls'),
            lambda v: v['facts'][0].update(sources=[]),
            lambda v: v['facts'].append(copy.deepcopy(v['facts'][0])),
            lambda v: v['obligations'][0].update(required=False),
            lambda v: v['obligations'][0].update(state='unknown'),
            lambda v: v['obligations'][0].update(supports=[]),
            lambda v: v['obligations'][0]['supports'][0].update(space='local_v3'),
            lambda v: v['obligations'].pop(),
        ]
        for number, mutate in enumerate(mutations):
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(number=number), self.assertRaises(ValueError):
                validate_task(self.rehash(value), core, SOURCES, **trusted)

    def test_missing_required_evidence_cannot_be_rehashed_as_complete(self):
        candidate, trusted, _, _, core = self.setup_case()
        candidate['conclusions'].pop()
        value = self.compare(candidate, SOURCES, core, trusted)
        value['status'] = 'complete'
        value['obligations'][-1].update(state='satisfied', reason=None)
        with self.assertRaises(ValueError):
            validate_task(self.rehash(value), core, SOURCES, **trusted)

    def test_invalid_candidate_and_unsent_sources_are_not_salvaged(self):
        candidate, trusted, _, allowed, core = self.setup_case()
        hidden = next(row[0] for row in core.document['package']['macro']['symbols']
                      if row[4] == 'Service.unrelated')
        mutations = [
            lambda v: v.update(status='complete'),
            lambda v: v.update(summary=None),
            lambda v: v.update(unknowns=[False]),
            lambda v: v['conclusions'][0].update(dimension_id='unknown'),
            lambda v: v['conclusions'][0].update(text=' '),
            lambda v: v['conclusions'][0].update(level='direct_static'),
            lambda v: v['conclusions'][0].update(source_ids=[]),
            lambda v: v['conclusions'][0].update(source_ids=[True]),
            lambda v: v['conclusions'][0].update(source_ids=[allowed[0], allowed[0]]),
            lambda v: v['conclusions'][0].update(source_ids=[hidden]),
            lambda v: v['conclusions'][0].update(source_ids=[allowed[0][:-1]]),
        ]
        for number, mutate in enumerate(mutations):
            value = copy.deepcopy(candidate)
            mutate(value)
            with self.subTest(number=number), self.assertRaises(ValueError):
                build_task(value, core, SOURCES, **trusted)

    def test_post_tamper_duplicate_keys_noncanonical_and_types_rejected(self):
        candidate, trusted, _, _, core = self.setup_case()
        raw = trusted['post']
        envelope = json.loads(raw)
        payload = json.loads(envelope['messages'][1]['content'])
        payload['sources'][0]['text'] = 'truncated'
        envelope['messages'][1]['content'] = json.dumps(payload, separators=(',', ':'))
        forged = json.dumps(envelope, separators=(',', ':')).encode()
        for post in (forged, b'{"model":"duplicate",' + raw[1:], raw + b'\n',
                     b'{"stream":1.0}', b'{"stream":NaN}', raw.decode(), b'[]', None):
            with self.subTest(post=type(post).__name__), self.assertRaises(ValueError):
                build_task(candidate, core, SOURCES, **{**trusted, 'post': post})

    def test_changed_trusted_inputs_or_rehashed_structural_core_rejected(self):
        candidate, trusted, _, _, core = self.setup_case()
        value = self.compare(candidate, SOURCES, core, trusted)
        for key, replacement in (('request_id', 'changed'), ('question', 'changed'),
                ('selection_id', 'changed'), ('project_id', 'changed'),
                ('snapshot_id', 'changed'), ('frozen_coverage', {'parsed_files': 1}),
                ('required_core_unit_ids', trusted['required_core_unit_ids'][:1]),
                ('dimensions', list(reversed(trusted['dimensions'])))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_task(value, core, SOURCES, **{**trusted, key: replacement})
        changed = {**SOURCES, 'unused.py': SOURCES['unused.py'] + b'# changed\n'}
        with self.assertRaises(ValueError):
            build_task(candidate, core, changed, **trusted)
        document = core.document
        document['package']['macro']['symbols'].pop()
        document['core_id'] = identity('core-v3',
            {key: item for key, item in document.items() if key != 'core_id'})
        with self.assertRaises(ValueError):
            build_task(candidate, SealedCore(canonical(document)), SOURCES, **trusted)

    def test_invalid_request_questions_dimensions_failures_and_value_shapes_rejected(self):
        candidate, trusted, _, _, core = self.setup_case()
        for request_id in (None, '', '../escape', 'x' * 129, True):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                build_task(candidate, core, SOURCES, **{**trusted, 'request_id': request_id})
        for dimensions in ([], DIMENSIONS + [DIMENSIONS[0]],
                [{**DIMENSIONS[0], 'required': 1}], [{**DIMENSIONS[0], 'selector': {}}],
                [{**DIMENSIONS[0], 'evidence': 'direct_static'}]):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                build_task(candidate, core, SOURCES, **{**trusted, 'dimensions': dimensions})
        for question in (None, '', ' ', 'x' * 4097):
            with self.subTest(question=type(question).__name__), self.assertRaises(ValueError):
                build_task(candidate, core, SOURCES, **{**trusted, 'question': question})
        for failures in ({}, [{'kind': 'unknown', 'reason': 'missing'}],
                [{'kind': 'blocked', 'reason': ''}],
                [{'kind': 'failed', 'reason': 'error', 'status': 'complete'}]):
            with self.subTest(failures=failures), self.assertRaises(ValueError):
                build_task(candidate, core, SOURCES, **trusted, failures=failures)
        for invalid in (None, [], {}, {'facts': None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_task(invalid, core, SOURCES, **trusted)

    def test_prompt_metadata_budget_blocks_before_any_network(self):
        _, _, _, _, core = self.setup_case()
        with patch('socket.socket', side_effect=AssertionError('禁止联网')):
            with self.assertRaisesRegex(ValueError, 'post_budget'):
                build_prompt(core, SOURCES, **trusted_structure(), question=QUESTION,
                    dimensions=DIMENSIONS, frozen_coverage={'detail': 'x' * 150000})

    def test_production_does_not_call_independent_expected_task(self):
        candidate, trusted, _, _, core = self.setup_case()
        with (
            patch('schema_v3_task_oracle.expected_task', side_effect=AssertionError('禁止调用 Oracle')),
            patch('schema_v3_core_oracle.expected_core', side_effect=AssertionError('禁止调用 Oracle')),
        ):
            value = build_task(candidate, core, SOURCES, **trusted)
            self.assertEqual(validate_task(value, core, SOURCES, **trusted)['status'], 'complete')

    def test_cancel_tick_reaches_each_public_boundary(self):
        candidate, trusted, _, _, core = self.setup_case()
        value = self.compare(candidate, SOURCES, core, trusted)
        def stopped():
            raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            build_prompt(core, SOURCES, **trusted_structure(), question=QUESTION,
                dimensions=DIMENSIONS, frozen_coverage=COVERAGE, tick=stopped)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            build_task(candidate, core, SOURCES, **trusted, tick=stopped)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            validate_task(value, core, SOURCES, **trusted, tick=stopped)


if __name__ == '__main__':
    unittest.main()
