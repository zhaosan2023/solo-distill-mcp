"""Authorized V2 wiring regressions. Fake gateway only; target code stays data."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import selectors
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import live_v2_driver as driver
import test_offline as offline
from solo_distill.client import ClientError, StdioClient
from solo_distill.core_v2 import CoreBlocked
from solo_distill.distillation import load_policy
from solo_distill.extractor import InputError, Project
from solo_distill.gateway import GatewayError
from solo_distill.live_v2 import LiveV2Distiller, TOOL_NAME, add_proposal
from solo_distill.server import Server, encode
from solo_distill.server_v2 import V2Server, serve_v2
from test_v2_contract import fixture
from v2_consumer import load, receive


class FakeGateway:
    def __init__(self, mutate=None, failure=None):
        self.calls = []
        self.mutate = mutate
        self.failure = failure
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def generate(self, messages, deadline, cancelled):
        payload = json.loads(messages[-1]['content'])
        self.calls.append(payload)
        if self.failure:
            raise GatewayError(self.failure, {'endpoint': 'http://127.0.0.1:8080/v1/chat/completions',
                'request_model': 'gpt-6-sol', 'outer_replays': 0, 'client_auto_retries': 0})
        candidate = {'summary': 'Not runtime proof.', 'conclusions': [
            {'dimension_id': item['id'], 'text': 'The supplied body has a return statement.',
             'source_ids': [payload['sources'][0]['source_id']]}
            for item in payload['dimensions']], 'unknowns': []}
        if self.mutate:
            self.mutate(candidate)
        return json.dumps(candidate), {'endpoint': 'http://127.0.0.1:8080/v1/chat/completions',
            'request_model': 'gpt-6-sol', 'outer_replays': 0, 'client_auto_retries': 0,
            'http_status': 200, 'finish_reason': 'stop', 'done_marker': True}


class LiveV2Tests(unittest.TestCase):
    def setUp(self):
        target = tempfile.TemporaryDirectory()
        artifacts = tempfile.TemporaryDirectory()
        self.addCleanup(target.cleanup)
        self.addCleanup(artifacts.cleanup)
        self.root = Path(target.name)
        self.artifacts = Path(artifacts.name)
        self.raw = b'def run():\n return 1\n'
        (self.root / 'sample.py').write_bytes(self.raw)
        self.project = Project.from_config('fixture', offline.configuration(self.root))
        policy = load_policy(str(offline.ROOT / 'config/distillation.json'))
        policy['authorized_project_ids'] = ['fixture']
        self.gateway = FakeGateway()
        self.distiller = LiveV2Distiller({'fixture': self.project}, policy, self.gateway,
                                        artifact_root=self.artifacts)

    def arguments(self, **changes):
        value = driver.request('offline-v2-1', int(time.time() * 1000))
        value['project_id'] = 'fixture'
        value.update(changes)
        return value

    def run_frame(self, **changes):
        arguments = self.arguments(**changes)
        frame = self.distiller.distill_frame(arguments, rpc_id=3)
        trace, _ = driver.read_trace(self.artifacts, arguments['request_id'])
        return arguments, frame, trace

    def test_complete_chain_independent_frame_and_actual_trace(self):
        arguments, frame, trace = self.run_frame()
        checked = receive(frame.data, {'sample.py': self.raw}, request_id=3, scenario='A',
                          return_bytes=22016, project_id='fixture', policy_id='lifecycle-v2-1')
        self.assertEqual(checked['status'], 'complete', trace.get('failure'))
        self.assertEqual(checked['core'], trace['core'])
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(len(trace['model_outputs']), 1)
        self.assertEqual(trace['freshness'], 'current_at_final_verification')
        self.assertEqual(driver.check_trace(trace, frame.data, arguments), trace['gateway_requests'])
        files = driver.source_inputs(self.project, arguments, trace, time.monotonic() + 3)
        self.assertEqual(files, {'sample.py': self.raw})
        self.assertTrue(all(item['level'] == 'sourced_inference' for item in checked['package']['facts']))
        self.assertEqual(checked['package']['coverage']['semantic_supported'], 4)
        self.assertEqual(trace['semantic_acceptance'], 'requires_independent_review')
        self.assertLessEqual(len(frame.data), 22016)

    def test_request_has_no_frozen_expected_anchors_or_direct_selectors(self):
        self.run_frame()
        payload = self.gateway.calls[0]
        self.assertEqual(payload['operation'], 'compression')
        self.assertNotIn('seeds', payload)
        self.assertNotIn('anchors', payload)
        self.assertNotIn('expected', json.dumps(payload))
        self.assertTrue(all('selector' not in item for item in payload['dimensions']))

    def test_invalid_version_scenario_budget_seed_and_evidence_precede_gateway(self):
        changes = [dict(contract_version=True), dict(contract_version=1), dict(scenario='B'),
                   dict(return_bytes=24576), dict(seeds=[{'path': 'sample.py'}]),
                   dict(dimensions=[{'id': 'x', 'question': 'x', 'required': True,
                                     'evidence': 'direct_static'}]), dict(project_id='other')]
        for change in changes:
            with self.subTest(change=change), self.assertRaises((InputError, ValueError)):
                self.distiller.distill_frame(self.arguments(**change), rpc_id=3)
        self.assertFalse(self.gateway.calls)
        self.assertFalse(self.distiller.used)

    def test_single_use_rejects_second_business_without_replay(self):
        self.run_frame()
        with self.assertRaises(InputError):
            self.distiller.distill_frame(self.arguments(request_id='second'), rpc_id=4)
        self.assertEqual(len(self.gateway.calls), 1)

    def test_expired_window_blocks_before_post(self):
        old = int(time.time() * 1000) - 200000
        _, frame, trace = self.run_frame(started_at_unix_ms=old, deadline_unix_ms=old + 180000)
        self.assertFalse(frame.knowledge_delivered)
        self.assertIsNotNone(trace['failure'])
        self.assertFalse(self.gateway.calls)

    def test_cancellation_blocks_before_post(self):
        cancelled = threading.Event()
        cancelled.set()
        frame = self.distiller.distill_frame(self.arguments(), cancelled, rpc_id=3)
        self.assertFalse(frame.knowledge_delivered)
        self.assertFalse(self.gateway.calls)

    def test_oversized_minimum_blocks_without_post_or_denominator_reduction(self):
        with patch('solo_distill.live_v2.form_core', side_effect=CoreBlocked('minimum_required_core_over_budget')):
            _, frame, trace = self.run_frame()
        self.assertFalse(frame.knowledge_delivered)
        self.assertEqual(trace['failure'], 'minimum_required_core_over_budget')
        self.assertEqual(trace['scope'], ['sample.py'])
        self.assertEqual(trace['structure']['coverage']['parsed_files'], 1)
        self.assertFalse(self.gateway.calls)

    def test_zero_hits_blocks_without_post(self):
        (self.root / 'sample.py').write_bytes(b'# no units\n')
        _, frame, trace = self.run_frame()
        self.assertFalse(frame.knowledge_delivered)
        self.assertEqual(trace['failure'], 'zero_v2_syntax_units')
        self.assertFalse(self.gateway.calls)

    def test_gateway_failure_persists_no_retry(self):
        self.gateway.failure = 'gateway_http_error'
        _, frame, trace = self.run_frame()
        self.assertFalse(frame.knowledge_delivered)
        self.assertEqual(trace['failure'], 'gateway_http_error')
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(len(trace['gateway_requests']), 1)
        self.assertEqual(trace['outer_replays'], 0)

    def test_source_mutation_after_generation_blocks_freshness(self):
        self.gateway.mutate = lambda candidate: (self.root / 'sample.py').write_bytes(self.raw + b'# changed\n')
        _, frame, trace = self.run_frame()
        self.assertFalse(frame.knowledge_delivered)
        self.assertEqual(trace['failure'], 'source_changed_after_selection')
        self.assertEqual(len(self.gateway.calls), 1)

    def test_untrusted_target_is_parsed_never_executed(self):
        marker = self.root / 'EXECUTED'
        (self.root / 'sample.py').write_text(f"open({str(marker)!r}, 'w').write('bad')\nraise RuntimeError('data')\n")
        self.run_frame()
        self.assertFalse(marker.exists())

    def test_missing_required_prose_remains_partial_not_complete(self):
        self.gateway.mutate = lambda candidate: candidate.update(conclusions=[])
        _, frame, trace = self.run_frame()
        checked = receive(frame.data, {'sample.py': self.raw}, request_id=3, scenario='A',
                          return_bytes=22016, project_id='fixture', policy_id='lifecycle-v2-1')
        self.assertEqual(checked['status'], 'partial', trace.get('failure'))
        self.assertTrue(all(item['state'] == 'unknown' for item in checked['package']['obligations']))

    def test_invalid_unsourced_or_promoted_proposal_is_blocked(self):
        mutations = [lambda c: c['conclusions'][0].update(source_ids=['invented']),
                     lambda c: c['conclusions'][0].update(evidence_level='direct_static'),
                     lambda c: c['conclusions'][0].update(text='x' * 4097),
                     lambda c: c.update(extra=True)]
        package, files = fixture(self.raw)
        source_id = package['sources'][0]['id']
        candidate = {'summary': 'x', 'unknowns': [], 'conclusions': [
            {'dimension_id': 'x', 'text': 'sourced', 'source_ids': [source_id]}]}
        dimensions = [{'id': 'x', 'question': 'x', 'required': True, 'evidence': 'sourced_inference'}]
        for mutate in mutations:
            changed = copy.deepcopy(candidate)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                add_proposal(package, changed, dimensions, files)

    def test_trace_actual_frame_and_request_conflicts_are_rejected(self):
        arguments, frame, trace = self.run_frame()
        for key, value in [('frozen_frame_bytes', len(frame.data) - 1),
                           ('frozen_frame_sha256', '0' * 64), ('outer_replays', 1),
                           ('request_id', 'other')]:
            altered = {**trace, key: value}
            with self.subTest(key=key), self.assertRaises(ClientError):
                driver.check_trace(altered, frame.data, arguments)

    def test_scratch_symlink_or_conflicting_exclusive_path_blocks(self):
        verification = self.artifacts / 'verification'
        verification.mkdir()
        (verification / '.scratch').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.distiller.distill_frame(self.arguments(), rpc_id=3)
        self.assertFalse((self.root / 'offline-v2-1.full.json').exists())
        with self.assertRaises(OSError):
            driver.read_trace(self.artifacts, 'offline-v2-1')

    def test_default_v1_server_does_not_discover_or_dispatch_v2(self):
        server = Server({'fixture': self.project})
        server.dispatch(offline.initialization())
        server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        tools = server.dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        self.assertNotIn(TOOL_NAME, {item['name'] for item in tools['result']['tools']})
        result = server.dispatch({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': TOOL_NAME, 'arguments': self.arguments()}})
        self.assertIn('error', result)
        self.assertFalse(self.gateway.calls)

    def test_v2_client_requires_explicit_online_flag_before_process(self):
        with patch('solo_distill.client.subprocess.Popen') as launch:
            with self.assertRaises(ClientError):
                StdioClient(offline.ROOT, online=False, deadline=time.monotonic() + 3, v2=True)
            launch.assert_not_called()

    def test_managed_pipe_capture_and_writer_receipt_after_natural_exit(self):
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        result = []
        server = V2Server({'fixture': self.project}, self.distiller)
        def serve():
            try:
                result.append(serve_v2(server, input_read, output_write))
            finally:
                os.close(input_read)
                os.close(output_write)
        thread = threading.Thread(target=serve, daemon=False)
        thread.start()
        buffer = bytearray()
        def message(value):
            raw = encode(value) + b'\n'
            self.assertEqual(os.write(input_write, raw), len(raw))
        def frame():
            deadline = time.monotonic() + 3
            with selectors.DefaultSelector() as selector:
                selector.register(output_read, selectors.EVENT_READ)
                while b'\n' not in buffer:
                    self.assertGreater(deadline, time.monotonic())
                    self.assertTrue(selector.select(max(0, deadline - time.monotonic())))
                    chunk = os.read(output_read, 24577)
                    self.assertTrue(chunk)
                    buffer.extend(chunk)
            line, _, tail = buffer.partition(b'\n')
            buffer[:] = tail
            return bytes(line) + b'\n'
        try:
            message(offline.initialization())
            self.assertEqual(load(frame())['result']['protocolVersion'], '2024-11-05')
            message({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            self.assertEqual([tool['name'] for tool in load(frame())['result']['tools']], [TOOL_NAME])
            arguments = self.arguments()
            message({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                     'params': {'name': TOOL_NAME, 'arguments': arguments}})
            raw = frame()
            checked = receive(raw, {'sample.py': self.raw}, request_id=3, scenario='A',
                              return_bytes=22016, project_id='fixture', policy_id='lifecycle-v2-1')
            self.assertEqual(checked['status'], 'complete')
            os.close(input_write)
            input_write = None
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [0])
            delivery, _ = driver.read_trace(self.artifacts, arguments['request_id'], '.delivery')
            self.assertEqual(delivery['written'], len(raw))
            self.assertEqual(delivery['frame_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(delivery['status'], 'frame_written_not_host_consumption')
            self.assertTrue(delivery['knowledge_delivered'])
        finally:
            if input_write is not None:
                os.close(input_write)
            self.distiller.cancel()
            thread.join(3)
            os.close(output_read)
            self.assertFalse(thread.is_alive(), 'owned worker survived pipe fixture')
