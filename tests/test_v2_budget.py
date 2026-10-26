"""E5 lossless transport and focused-slice regressions; no real model calls."""
from __future__ import annotations

import base64
import copy
import json
import os
import random
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from solo_distill.codec_v2 import ENCODING, MAX_DECODED_BYTES, pack_core, unpack_core
from solo_distill.consumer_v2 import consume_frame, strict_load
from solo_distill.core_v2 import CoreBlocked, canonical, form_core, make_seal
from solo_distill.live_v2 import core_selection, select_inventory
from solo_distill.package_v2 import build_package, finalize
from solo_distill.syntax_v2 import extract_units
from solo_distill.wire_v2 import FrameWriter, core_report, encode_report, publish, reserved_size
from test_v2_contract import fixture
from test_v2_wire import admit
from v2_consumer import expand_core, receive


class CompactCoreTests(unittest.TestCase):
    def setUp(self):
        self.package, self.files = fixture("def café():\n return '否定、条件、边界\\n'\n".encode())
        self.options = dict(protocol_version='2024-11-05', request_id=3, scenario='A',
                            return_bytes=22016, project_id='fixture', policy_id='lifecycle-v2-1')

    def compact(self, **changes):
        return form_core(self.package, self.files, wire_encoding=ENCODING, **changes)

    def rejected(self, packed):
        for decoder in (lambda item: unpack_core(item, strict_load), expand_core):
            with self.subTest(decoder=decoder), self.assertRaises(ValueError):
                decoder(packed)

    def test_roundtrip_exact_canonical_bytes_and_prefix(self):
        plain = form_core(self.package, self.files)
        core = self.compact()
        self.assertEqual(core.payload, plain.payload)
        self.assertEqual(core.prefix, plain.prefix)
        packed = pack_core(core.document)
        self.assertEqual(canonical(unpack_core(packed, strict_load)), core.payload)
        self.assertEqual(canonical(expand_core(packed)), core.payload)
        frame = publish(core, admit())
        received = consume_frame(frame.data, self.files, **self.options)
        checked = receive(frame.data, self.files, **self.options)
        self.assertEqual(received.prefix, core.prefix)
        self.assertEqual(checked['core'], core.document)
        self.assertEqual(received.package, checked['package'])

    def test_81_file_denominator_and_input_identity_not_reduced(self):
        files = {f'directory_{i}/sample_{i}.py': f'# file {i}\ndef run(): return {i}\n'.encode()
                 for i in range(81)}
        path = sorted(files)[0]
        key = extract_units(path, files[path])['units'][0]['id']
        package = build_package('fixture', 'snapshot', files, [key])
        plain = make_seal(package, files, 'lifecycle-v2-1')
        self.assertGreater(reserved_size(plain), 22016)
        core = form_core(package, files, wire_encoding=ENCODING)
        self.assertLessEqual(reserved_size(core), 22016)
        frame = publish(core, admit())
        checked = receive(frame.data, files, **self.options)
        self.assertEqual(len(checked['package']['files']), 81)
        self.assertEqual(checked['package']['coverage']['scan_files'], 81)
        self.assertEqual(checked['core']['input_id'], plain.document['input_id'])
        self.assertEqual(checked['core']['scope'], sorted(files))
        changed = {**files, sorted(files)[-1]: b'def run(): return -1\n'}
        for decoder in (consume_frame, receive):
            with self.assertRaises(ValueError):
                decoder(frame.data, changed, **self.options)

    def test_actual_pipe_capture_compact_bytes_and_worst_reserve(self):
        core = self.compact()
        worst = publish(core, admit(request_id='\x00' * 128),
                        observations={'value': '\\"\n' * 60})
        self.assertLessEqual(len(worst.data), reserved_size(core))
        frame = publish(core, admit(), observations={'elapsed_ms': 3})
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK)
        try:
            receipt = FrameWriter(write_fd).send(frame, deadline=time.monotonic() + 2,
                                                cancelled=threading.Event())
            raw = os.read(read_fd, 65536)
            self.assertEqual(raw, frame.data)
            self.assertEqual(receipt.written, len(raw))
            checked = receive(raw, self.files, **self.options)
            self.assertEqual(checked['core'], core.document)
            smaller = publish(core, admit(budget=len(raw) - 1))
            if smaller.knowledge_delivered:
                self.assertEqual(receive(smaller.data, self.files, **self.options)['core'], core.document)
            else:
                self.assertEqual(receive(smaller.data, {}, request_id=3, scenario='A',
                                         return_bytes=22016)['status'], 'blocked')
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_bad_encoding_fields_types_base64_and_exact_length(self):
        packed = pack_core(self.compact().document)
        cases = [dict(packed, extra=1), dict(packed, encoding='unknown'),
                 dict(packed, decoded_bytes=True), dict(packed, decoded_bytes=0),
                 dict(packed, decoded_bytes=MAX_DECODED_BYTES + 1),
                 dict(packed, decoded_bytes=packed['decoded_bytes'] - 1),
                 dict(packed, decoded_bytes=packed['decoded_bytes'] + 1),
                 dict(packed, data=True), dict(packed, data='%%%'),
                 dict(packed, data=packed['data'] + '\n'), dict(packed, data='值')]
        for case in cases:
            self.rejected(case)
        # Padding bits must be canonical, even when both strings decode equally.
        self.rejected({'encoding': ENCODING, 'decoded_bytes': 1, 'data': 'eB=='})

    def test_truncated_trailing_concatenated_stream_and_expansion_bomb(self):
        packed = pack_core(self.compact().document)
        compressed = base64.b64decode(packed['data'])
        for raw in (compressed[:-1], compressed + b'junk', compressed + zlib.compress(b'{}')):
            self.rejected({**packed, 'data': base64.b64encode(raw).decode()})
        self.rejected({'encoding': ENCODING, 'decoded_bytes': 16,
                       'data': base64.b64encode(zlib.compress(b'x' * (MAX_DECODED_BYTES + 1))).decode()})

    def test_duplicate_keys_noncanonical_and_nonfinite_decoded_json(self):
        for raw in (b'{"x":1,"x":2}', b'{ "x": 1 }', b'{"x":NaN}', b'{"x":1.5}'):
            self.rejected({'encoding': ENCODING, 'decoded_bytes': len(raw),
                           'data': base64.b64encode(zlib.compress(raw)).decode()})

    def test_packed_rehashed_syntax_still_authenticated_independently(self):
        from solo_distill.core_v2 import digest
        core = self.compact().document
        core['package']['units'][0]['raw_start'] += 1
        core['core_id'] = digest('core-v2', {key: item for key, item in core.items() if key != 'core_id'})
        report = {'contract_version': 2, 'kind': 'sealed_knowledge', 'status': core['package']['status'],
                  'core': pack_core(core), 'observations': {}}
        raw = encode_report(report, 3)
        for decoder in (consume_frame, receive):
            with self.assertRaises(ValueError):
                decoder(raw, self.files, **self.options)

    def test_default_json_and_B_remain_uncompressed(self):
        plain = form_core(self.package, self.files)
        raw = publish(plain, admit()).data
        report = json.loads(json.loads(raw)['result']['content'][0]['text'])
        self.assertNotIn('encoding', report['core'])
        package = copy.deepcopy(self.package)
        package['scenario'] = 'B'
        core = form_core(package, self.files)
        self.assertTrue(publish(core, admit('B', budget=12288)).knowledge_delivered)
        with self.assertRaises(ValueError):
            form_core(package, self.files, wire_encoding=ENCODING)
        with self.assertRaises(ValueError):
            encode_report(core_report(core, {}), 3, wire_encoding=ENCODING)

    def test_incompressible_required_facts_still_block_without_reduction(self):
        rng = random.Random(517)
        alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        package = copy.deepcopy(self.package)
        package['facts'] = [{'id': f'f{i}', 'text': ''.join(rng.choice(alphabet) for _ in range(4000)),
                             'level': 'sourced_inference', 'sources': [0], 'relations': [], 'conditions': []}
                            for i in range(8)]
        package['obligations'] = [{'id': f'need{i}', 'required': True, 'evidence': 'sourced_inference',
                                  'role': None, 'state': 'unknown',
                                  'supports': [{'table': 'facts', 'index': i}], 'reason': 'pending'}
                                 for i in range(8)]
        package = finalize(package)
        before = canonical(package)
        with self.assertRaisesRegex(CoreBlocked, 'minimum_required_core_over_budget'):
            form_core(package, self.files, wire_encoding=ENCODING)
        self.assertEqual(canonical(package), before)

    def test_optional_group_is_atomic_and_permutation_stable(self):
        package, files = fixture(b'def query(): return 1\ndef optional(): return 2\n')
        options = core_selection(package, 'query')
        left = form_core(package, files, **options)
        right = form_core(package, files, **{**options, 'optional_groups': list(reversed(options['optional_groups']))})
        self.assertEqual(left.payload, right.payload)
        self.assertEqual(publish(left, admit()).data, publish(right, admit()).data)
        self.assertEqual(len(left.document['package']['owners']), 2)
        bad = [('bad', [('sources', 'missing')])]
        with self.assertRaises(ValueError):
            form_core(package, files, required_sources=options['required_sources'],
                      optional_groups=bad, wire_encoding=ENCODING)


    def test_cost_trials_do_not_reparse_per_optional_group_but_final_is_authenticated(self):
        import solo_distill.core_v2 as core_module
        package, files = fixture(b''.join(
            f'def query_{i}(): return {i}\n'.encode() for i in range(12)))
        options = core_selection(package, 'query')
        validator = core_module.validate_package
        with patch.object(core_module, 'validate_package', wraps=validator) as authenticate:
            core = form_core(package, files, **options)
        self.assertEqual(authenticate.call_count, 3)
        self.assertEqual(authenticate.call_args_list[0].args[0], package)
        self.assertEqual(authenticate.call_args_list[-1].args[0], core.document['package'])
        checked = receive(publish(core, admit()).data, files, **self.options)
        self.assertEqual(checked['core'], core.document)
        # The public slice boundary still rejects unauthenticated input, even
        # when the malformed optional source would not be selected.
        forged = copy.deepcopy(package)
        forged['units'][-1]['raw_start'] += 1
        from solo_distill.core_v2 import slice_package
        with self.assertRaises(ValueError):
            slice_package(forged, files, [('sources', options['required_sources'][0])])
        with self.assertRaises(ValueError):
            form_core(forged, files, **options)


class FocusedSliceTests(unittest.TestCase):
    def test_large_class_replaced_by_complete_focused_method_not_class_head(self):
        package, files = fixture(b'class Owner:\n def query(self): return self.value\n'
                                 b' def unrelated(self):\n' + b'  value = 1\n' * 1500 + b'  return value\n')
        units = extract_units('sample.py', files['sample.py'])['units']
        class Reader:
            report = {'project_id': 'fixture', 'snapshot_id': 'snapshot'}
            raw_files = files
            cancelled = threading.Event()
            deadline = time.monotonic() + 5
            def tick(self):
                pass
            def inventory(self):
                self.units = {unit['id']: ('sample.py', unit) for unit in units}
                return units
            def read(self, selected):
                return build_package('fixture', 'snapshot', files, selected)
        selected, _ = select_inventory(Reader(), 'query state')
        required = core_selection(selected, 'query state')['required_sources']
        from solo_distill.core_v2 import slice_package
        minimum = slice_package(selected, files, [('sources', key) for key in required])
        self.assertEqual({unit['qualified_name'] for unit in minimum['units']}, {'Owner.query'})
        self.assertTrue(all(owner['complete'] for owner in minimum['owners']))
        checked = receive(publish(form_core(selected, files, **core_selection(selected, 'query state')), admit()).data,
                          files, request_id=3, scenario='A', return_bytes=22016,
                          project_id='fixture', policy_id='lifecycle-v2-1')
        self.assertIn('Owner.query', {unit['qualified_name'] for unit in checked['package']['units']})

    def test_conditions_and_required_support_are_never_optional(self):
        package, files = fixture(b'if enabled:\n def query(): return 1\nelse:\n def fallback(): return 0\n')
        options = core_selection(package, 'query')
        core = form_core(package, files, **options)
        checked = receive(publish(core, admit()).data, files, request_id=3, scenario='A', return_bytes=22016,
                          project_id='fixture', policy_id='lifecycle-v2-1')
        self.assertEqual(len(checked['package']['conditions']), 2)
        self.assertEqual({u['qualified_name'] for u in checked['package']['units']}, {None, 'query', 'fallback'})
        self.assertEqual(len(checked['package']['owners']), 1)

    def test_selection_has_no_frozen_asset_reads_or_model_calls(self):
        import inspect
        from solo_distill import live_v2
        source = inspect.getsource(live_v2)
        self.assertNotIn('fastcode_acceptance', source)
        self.assertNotIn('evaluate_frozen', source)
        self.assertNotIn('anchors', source)
        package, _ = fixture(b'def query(): return 1\ndef x(): return 0\n')
        left = core_selection(package, 'query state')
        right = core_selection(package, 'query state')
        self.assertEqual(left, right)
        self.assertEqual(len(left['required_sources']), 1)
