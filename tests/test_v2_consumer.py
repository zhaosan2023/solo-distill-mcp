"""E4 independent offline chain, legacy dispatch and task ownership regressions."""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from solo_distill.consumer_v2 import (TaskDelta, consume_frame, consume_task, make_task)
from solo_distill.core_v2 import (SealedCore, canonical, digest, form_core, make_seal)
from solo_distill.graph_v2 import extract_relations
from solo_distill.package_v2 import build_package, finalize
from solo_distill.syntax_v2 import extract_units
from solo_distill.wire_v2 import (DeliveryBlocked, FrameWriter, PROTOCOL_VERSION,
                                 core_report, encode_report, publish)
from test_v2_contract import fixture
from test_v2_wire import admit
from v2_consumer import evaluate_frozen, receive, receive_task

ROOT = Path(__file__).resolve().parents[1]


def legacy_wire(value, request_id=3):
    # V1 uses its existing finite-float JSON domain, not the V2 encoder.
    result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False,
               allow_nan=False, separators=(',', ':'))}],
              'isError': value.get('status') in {'blocked', 'failed'}}
    return json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': result},
                      ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8') + b'\n'


def reseal(value):
    value['core_id'] = digest('core-v2', {k: v for k, v in value.items() if k != 'core_id'})
    return SealedCore(canonical(value))


class FrameConsumerTests(unittest.TestCase):
    def setUp(self):
        self.package, self.files = fixture(b"@decorate\nclass Owner:\n async def run(self):\n  self.value = Worker()\n  return self.value\nif __name__ == '__main__':\n Owner()\n")
        self.core = form_core(self.package, self.files, optional_groups=[
            ('roles', [('role_proofs', proof['id']) for proof in self.package['role_proofs']])])
        self.options = dict(protocol_version=PROTOCOL_VERSION, request_id=3, scenario='A',
                            return_bytes=22016, project_id='fixture', policy_id='lifecycle-v2-1')

    def accepted(self, frame, files=None, options=None):
        files = self.files if files is None else files
        options = self.options if options is None else options
        produced = consume_frame(frame, files, **options)
        checked = receive(frame, files, **options)
        self.assertEqual(produced.version, checked['version'])
        self.assertEqual(produced.package, checked['package'])
        self.assertEqual(produced.status, checked['status'])
        return produced, checked

    def rejected(self, raw, files=None, **changes):
        options = {**self.options, **changes}
        files = self.files if files is None else files
        for decoder in (consume_frame, receive):
            with self.subTest(decoder=decoder.__module__), self.assertRaises(ValueError):
                decoder(raw, files, **options)

    def test_actual_pipe_capture_independent_version_authentication(self):
        frame = publish(self.core, admit(), observations={'elapsed_ms': 3, 'host_root': '/offline'})
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK)
        try:
            receipt = FrameWriter(write_fd).send(frame, deadline=time.monotonic() + 1,
                                                 cancelled=threading.Event())
            raw = os.read(read_fd, 65536)
            self.assertEqual(raw, frame.data)
            self.assertEqual(receipt.written, len(raw))
            produced, checked = self.accepted(raw)
            self.assertEqual(produced.prefix, self.core.prefix)
            self.assertEqual(checked['core'], self.core.document)
            view = produced.package
            view['project_id'] = 'external mutation'
            self.assertEqual(produced.package['project_id'], 'fixture')
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_v1_unchanged_callback_and_original_24576_budget(self):
        legacy = {'contract_version': 1, 'kind': 'knowledge_package', 'status': 'complete',
                  'sources': [], 'conclusions': [], 'observations': {'elapsed_seconds': 1.25},
                  'notes': 'x' * 22500}
        raw = legacy_wire(legacy)
        self.assertGreater(len(raw), 22016)
        self.assertLessEqual(len(raw), 24576)
        calls = []
        def existing(value, files):
            self.assertEqual(value, legacy)
            self.assertEqual(files, self.files)
            calls.append(copy.deepcopy(value))
        options = {**self.options, 'return_bytes': 24576, 'legacy_validator': existing}
        produced, checked = self.accepted(raw, options=options)
        self.assertEqual(produced.package, legacy)
        self.assertEqual(checked['package'], legacy)
        self.assertIsNone(produced.core)
        self.assertEqual(produced.prefix, b'')
        self.assertEqual(calls, [legacy, legacy])
        self.rejected(raw, return_bytes=24576)
        smaller = legacy_wire({**legacy, 'notes': 'small'})
        self.rejected(smaller)

    def test_new_source_kinds_and_encodings_not_legacy_definition_reinterpretation(self):
        for raw in (b"# coding: latin-1\r\n\xe9 = 1; result = 2\r\n",
                    b"\xef\xbb\xbf" + "名称 = 1; result = 2\r\n".encode(),
                    b"if __name__ == '__main__':\n def run(): pass\n"):
            package, files = fixture(raw)
            core = form_core(package, files)
            produced, checked = self.accepted(publish(core, admit()).data, files=files)
            self.assertEqual(produced.prefix, core.prefix)
            self.assertTrue(any(u['kind'] != 'definition' for u in checked['package']['units']))

    def test_tampered_but_rehashed_syntax_proof_is_rejected_independently(self):
        value = self.core.document
        value['package']['units'][0]['raw_start'] += 1
        forged = reseal(value)
        self.rejected(encode_report(core_report(forged, {}), 3))
        value = self.core.document
        proof = next(p for p in value['package']['role_proofs'] if p['level'] == 'direct_static')
        proof['rule'] = 'invented-proof'
        self.rejected(encode_report(core_report(reseal(value), {}), 3))
        value = self.core.document
        value['package']['facts'] = [{'id': 'forged', 'text': 'Execution proven',
            'level': 'direct_static', 'sources': [0], 'relations': [], 'conditions': []}]
        self.rejected(encode_report(core_report(reseal(value), {}), 3))

    def test_stale_project_policy_input_and_no_required_binding(self):
        raw = publish(self.core, admit()).data
        self.rejected(raw, project_id='other')
        self.rejected(raw, policy_id='other')
        self.rejected(raw, project_id=None)
        self.rejected(raw, policy_id=None)
        self.rejected(raw, files={'sample.py': self.files['sample.py'] + b'# edited\n'})
        self.rejected(raw, files={**self.files, 'new.py': b'pass\n'})
        value = self.core.document
        value['input_id'] = 'stale'
        self.rejected(encode_report(core_report(reseal(value), {}), 3))

    def test_wire_version_error_identity_and_frame_negative_controls(self):
        raw = publish(self.core, admit()).data
        for changed in (raw[:-1], raw + raw, b'{"id":3,"id":3}\n'):
            self.rejected(changed)
        self.rejected(raw, return_bytes=len(raw) - 1)
        self.rejected(raw, protocol_version='unknown')
        self.rejected(raw, scenario='B')
        self.rejected(raw, request_id=True)
        envelope = json.loads(raw)
        variants = []
        value = copy.deepcopy(envelope)
        value['id'] = '3'
        variants.append(value)
        value = copy.deepcopy(envelope)
        value['result']['isError'] = True
        variants.append(value)
        value = copy.deepcopy(envelope)
        value['result']['content'].append(copy.deepcopy(value['result']['content'][0]))
        variants.append(value)
        for value in variants:
            self.rejected(canonical(value) + b'\n')
        report = core_report(self.core, {})
        for version in (True, 3, '2'):
            self.rejected(encode_report({**report, 'contract_version': version}, 3))
        report['status'] = 'failed'
        self.rejected(encode_report(report, 3))

    def test_publication_blocked_not_knowledge_or_semantic_success(self):
        frame = publish(self.core, admit(budget=512))
        self.assertFalse(frame.knowledge_delivered)
        produced, checked = self.accepted(frame.data, options={**self.options, 'return_bytes': 512})
        self.assertEqual(produced.status, 'blocked')
        self.assertIsNone(produced.core)
        result = evaluate_frozen(checked, {'version': 1, 'anchors': []})
        self.assertEqual(result['semantic_acceptance'], 'not_proven')
        report = produced.package
        report['knowledge_delivered'] = True
        self.rejected(encode_report(report, 3))

    def test_b_budget_cancel_before_and_during_decode_no_external_side_effect(self):
        package, files = fixture(b'def run(): pass\n')
        package['scenario'] = 'B'
        core = form_core(package, files)
        frame = publish(core, admit('B', budget=24576))
        options = {**self.options, 'scenario': 'B', 'return_bytes': 24576}
        self.accepted(frame.data, files=files, options=options)
        cancelled = threading.Event()
        cancelled.set()
        prefix = core.prefix
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            consume_frame(frame.data, files, **options, cancelled=cancelled)
        cancelled.clear()
        from solo_distill.consumer_v2 import authenticate_seal
        def cancel_after_auth(*args, **kwargs):
            value = authenticate_seal(*args, **kwargs)
            cancelled.set()
            return value
        with patch('solo_distill.consumer_v2.authenticate_seal', cancel_after_auth), self.assertRaisesRegex(ValueError, 'cancelled'):
            consume_frame(frame.data, files, **options, cancelled=cancelled)
        self.assertEqual(core.prefix, prefix)
        # Patch external side-effect boundaries: pure consumers must never call them.
        with patch('socket.socket', side_effect=AssertionError('network')), patch('subprocess.Popen', side_effect=AssertionError('process')), patch('builtins.open', side_effect=AssertionError('filesystem')):
            self.accepted(frame.data, files=files, options=options)
        oversized = encode_report(core_report(core, {'x': 'y' * 13000}), 3)
        self.rejected(oversized, files=files, scenario='B', return_bytes=24576)

    def test_utf8_wire_and_v2_integer_domain_do_not_restrict_finite_v1_floats(self):
        raw = publish(self.core, admit()).data
        self.rejected(raw.decode('utf-8').encode('utf-16'))
        envelope = json.loads(raw)
        report = json.loads(envelope['result']['content'][0]['text'])
        report['observations'] = {'elapsed_ms': 1.5}
        envelope['result']['content'][0]['text'] = json.dumps(report, ensure_ascii=False)
        self.rejected(json.dumps(envelope, ensure_ascii=False).encode() + b'\n')
        legacy = {'contract_version': 1, 'status': 'complete', 'elapsed_seconds': 1.25}
        calls = []
        def existing(value, files):
            calls.append(value)
        options = {**self.options, 'legacy_validator': existing}
        self.accepted(legacy_wire(legacy), options=options)
        self.assertEqual(len(calls), 2)
        envelope = json.loads(legacy_wire(legacy))
        envelope['result']['content'][0]['text'] = '{"contract_version":1,"status":"complete","elapsed_seconds":1e999}'
        bad = json.dumps(envelope).encode() + b'\n'
        for decoder in (consume_frame, receive):
            with self.assertRaises(ValueError):
                decoder(bad, self.files, **options)
        self.assertEqual(len(calls), 2)

    def test_core_canonicalization_never_repairs_invalid_relation(self):
        package, files = fixture(b'def target(): pass\ndef call():\n if enabled:\n  return target()\n')
        package['relations'] = extract_relations(package, files)
        package['relations'][0]['id'] = 'forged'
        with self.assertRaises(ValueError):
            make_seal(package, files, 'lifecycle-v2-1')


class TaskConsumerTests(unittest.TestCase):
    def setUp(self):
        self.files = {'a.py': b'def target(): pass\n', 'b.py': b'from a import target\ndef caller(): return target()\n'}
        keys = [u['id'] for path, raw in self.files.items() for u in extract_units(path, raw)['units']]
        package = build_package('fixture', 'snapshot', self.files, keys)
        package['relations'] = extract_relations(package, self.files)
        call = next(r for r in package['relations'] if r['to_unit'] is not None)
        package['facts'] = [{'id': 'cross', 'text': 'Static cross-file relation, no runtime proof.',
            'level': 'sourced_inference', 'sources': call['sources'], 'relations': [package['relations'].index(call)], 'conditions': []}]
        self.core = form_core(package, self.files, optional_groups=[('graph', [('facts', 'cross')])])
        self.scope = ['b.py']
        keys = [u['id'] for u in extract_units('b.py', self.files['b.py'])['units']]
        self.evidence = build_package('fixture', 'task-snapshot', {'b.py': self.files['b.py']}, keys, scenario='B')
        self.references = [{'space': 'task', 'table': 'sources', 'index': 0}]
        self.delta = make_task(self.core, self.files, task_id='task-1', scope=self.scope,
                               evidence=self.evidence, references=self.references)

    def rejected(self, document, *, scope=None, task_id='task-1', core=None):
        unsigned = {k: v for k, v in document.items() if k != 'delta_id'}
        document = {**unsigned, 'delta_id': digest('task-v2', unsigned)}
        payload = canonical(document)
        core = self.core if core is None else core
        scope = self.scope if scope is None else scope
        for decoder in ('production', 'independent'):
            with self.subTest(decoder=decoder), self.assertRaises(ValueError):
                if decoder == 'production':
                    consume_task(TaskDelta(payload), core, self.files, task_id=task_id, scope=scope)
                else:
                    receive_task(payload, core.document, self.files, task_id=task_id, scope=scope)

    def test_namespaces_independent_package_and_task_identity_do_not_mutate_core(self):
        prefix = self.core.prefix
        value = consume_task(self.delta, self.core, self.files, task_id='task-1', scope=self.scope)
        independent = receive_task(self.delta.payload, self.core.document, self.files, task_id='task-1', scope=self.scope)
        self.assertEqual(value, independent)
        self.assertEqual(value['evidence']['scenario'], 'B')
        other = make_task(self.core, self.files, task_id='task-2', scope=self.scope,
                          evidence=self.evidence, references=self.references)
        self.assertNotEqual(other.document['delta_id'], self.delta.document['delta_id'])
        value['evidence']['project_id'] = 'mutated-view'
        self.assertEqual(self.delta.document['project_id'], 'fixture')
        self.assertEqual(self.core.prefix, prefix)

    def test_forged_namespace_boolean_index_duplicates_and_dangling_indices(self):
        for reference in ({'table': 'sources', 'index': 0},
                          {'space': 'implicit', 'table': 'sources', 'index': 0},
                          {'space': 'task', 'table': 'sources', 'index': True},
                          {'space': 'task', 'table': 'sources', 'index': 100},
                          {'space': 'task', 'table': 'files', 'index': 0}):
            value = self.delta.document
            value['references'] = [reference]
            self.rejected(value)
        value = self.delta.document
        value['references'] *= 2
        self.rejected(value)

    def test_scope_project_task_and_input_forgery_never_reuse_history(self):
        for key, replacement in (('project_id', 'other'), ('input_id', 'stale'),
                                 ('core_id', 'other'), ('task_id', ''), ('delta_version', True),
                                 ('scope', ['unknown.py'])):
            value = self.delta.document
            value[key] = replacement
            self.rejected(value)
        self.rejected(self.delta.document, task_id='task-2')
        self.rejected(self.delta.document, scope=['a.py', 'b.py'])
        value = self.delta.document
        value['evidence']['scenario'] = 'A'
        self.rejected(value)
        value = self.delta.document
        value['evidence']['sources'][0]['owner'] = 99
        self.rejected(value)

    def test_core_reference_full_transitive_dependency_cannot_escape_task_scope(self):
        package = self.core.document['package']
        for table, index in (('facts', 0), ('relations', next(i for i, r in enumerate(package['relations']) if r['to_unit'] is not None))):
            value = self.delta.document
            value['references'] = [{'space': 'core', 'table': table, 'index': index}]
            self.rejected(value)
        b_source = next(i for i, s in enumerate(package['sources']) if package['files'][s['file']]['path'] == 'b.py')
        value = self.delta.document
        value['references'] = [{'space': 'core', 'table': 'sources', 'index': b_source}]
        delta = make_task(self.core, self.files, task_id='task-1', scope=self.scope,
                          evidence=self.evidence, references=value['references'])
        self.assertEqual(consume_task(delta, self.core, self.files, task_id='task-1', scope=self.scope),
                         receive_task(delta.payload, self.core.document, self.files, task_id='task-1', scope=self.scope))

    def test_delta_budget_cancel_and_duplicate_encoding(self):
        prefix = self.core.prefix
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            consume_task(self.delta, self.core, self.files, task_id='task-1', scope=self.scope, cancelled=cancelled)
        for raw in (b'x' * 12289, b'{"delta_version":1,"delta_version":1}'):
            with self.assertRaises(ValueError):
                consume_task(TaskDelta(raw), self.core, self.files, task_id='task-1', scope=self.scope)
            with self.assertRaises(ValueError):
                receive_task(raw, self.core.document, self.files, task_id='task-1', scope=self.scope)
        self.assertEqual(prefix, self.core.prefix)


class FrozenEvaluationTests(unittest.TestCase):
    def test_frozen_thirteen_anchors_all_expected_and_required_checks_preserved(self):
        path = ROOT / 'tests' / 'fastcode_acceptance.json'
        before = path.read_bytes()
        frozen = json.loads(before)
        pristine = copy.deepcopy(frozen)
        self.assertEqual(len(frozen['anchors']), 13)
        self.assertEqual(len(frozen['required_checks']), 7)
        legacy = {'sources': [{'source_id': a['id'], 'path': a['path'], 'symbol': a['symbol'],
                   'start_line': a['lines'][0], 'end_line': a['lines'][1]} for a in frozen['anchors']]}
        checked = evaluate_frozen({'version': 1, 'core': None, 'package': legacy}, frozen)
        self.assertTrue(all(c['body_coverage'] for c in checked['checks']))
        self.assertEqual([c['expected'] for c in checked['checks']], [a['expected'] for a in frozen['anchors']])
        self.assertEqual(checked['semantic_acceptance'], 'requires_independent_review')
        self.assertTrue(all(c['semantic_support'] == 'requires_independent_review' for c in checked['checks']))
        self.assertEqual(frozen, pristine)
        self.assertEqual(path.read_bytes(), before)

    def test_new_statement_and_main_guard_sources_do_not_waive_frozen_semantics(self):
        package, files = fixture(b'value = 1\nif __name__ == "__main__":\n def run(): pass\n')
        core = form_core(package, files)
        checked = receive(publish(core, admit()).data, files, request_id=3, scenario='A',
                          return_bytes=22016, project_id='fixture', policy_id='lifecycle-v2-1')
        frozen = {'version': 1, 'anchors': [
            {'id': 'statement', 'path': 'sample.py', 'symbol': None, 'lines': [1, 1], 'expected': 'Assignment, not an executed result.'},
            {'id': 'definition', 'path': 'sample.py', 'symbol': 'run', 'lines': [3, 3], 'expected': 'Conditional definition, not unconditional execution.'},
            {'id': 'missing', 'path': 'sample.py', 'symbol': 'missing', 'lines': [1, 1], 'expected': 'Must not be waived.'}]}
        results = evaluate_frozen(checked, frozen)
        self.assertEqual([c['body_coverage'] for c in results['checks']], [True, True, False])
        self.assertEqual(results['semantic_acceptance'], 'requires_independent_review')
        real = evaluate_frozen(checked, json.loads((ROOT / 'tests' / 'fastcode_acceptance.json').read_bytes()))
        self.assertEqual(len(real['checks']), 13)
        self.assertFalse(any(c['body_coverage'] for c in real['checks']))

    def test_generator_and_independent_validator_have_no_answer_or_production_oracle_imports(self):
        for name in ('syntax_v2.py', 'package_v2.py', 'evidence_v2.py', 'graph_v2.py',
                     'scheduler_v2.py', 'core_v2.py', 'wire_v2.py', 'consumer_v2.py'):
            text = (ROOT / 'src' / 'solo_distill' / name).read_text()
            self.assertNotIn('fastcode_acceptance', text)
            self.assertNotIn('v2_consumer', text)
            self.assertNotIn('v2_validator', text)
        for name in ('v2_validator.py', 'v2_consumer.py'):
            tree = ast.parse((ROOT / 'tests' / name).read_text())
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or '')
            self.assertFalse(any(name.startswith('solo_distill') for name in imported))


if __name__ == '__main__':
    unittest.main()
