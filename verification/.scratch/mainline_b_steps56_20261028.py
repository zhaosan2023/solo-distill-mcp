"""Authorized five-file static review and exclusive local blueprint deployment.
No network, target imports, source writes, host activation, or task controller.
Only publish creates release files; retire removes the bound active plan after
native archive creation and exact readback. Other stages only inspect evidence.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.parse import unquote
import mainline_b_steps234_20261028 as prior

ROOT = prior.ROOT
OLD = prior.OLD
NEW = ROOT / prior.OUTPUT
DEST = ROOT / '_bmad-output/blueprints/mainline-b-20261028'
REVIEW = ROOT / 'verification/brownfield-review/mainline-b-20261028T1443Z-CS-SR'
PLAN_ID = 'mainline-b-brownfield-distill-20261028'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def raw(path, limit=8388608):
    path = Path(path)
    relative = path.relative_to(ROOT)
    current = ROOT
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), 'symlink:' + str(relative))
    data = path.read_bytes()
    require(len(data) <= limit, 'byte_budget:' + str(relative))
    return data


def obj(path):
    return json.loads(raw(path))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(path):
    require(path.is_dir() and not path.is_symlink(), 'directory:' + str(path))
    items = list(path.rglob('*'))
    require(len(items) < 256, 'tree_entry_budget')
    require(all(not p.is_symlink() for p in items), 'tree_symlink')
    result = {p.relative_to(path).as_posix(): raw(p) for p in items if p.is_file()}
    require(sum(map(len, result.values())) <= 8388608, 'tree_byte_budget')
    return result


def manifest_tree(path):
    files = tree(path)
    manifest = json.loads(files['manifest.json'])
    require(set(files) == set(manifest['files']) | {'manifest.json'}, 'manifest_names')
    require(all(sha(files[n]) == h for n, h in manifest['files'].items()), 'manifest_hash')
    return files, manifest


def binding():
    plan, adr = raw(prior.PLAN, 32768), raw(prior.ADR, 32768)
    for data in (plan, adr):
        require(('plan_id: ' + PLAN_ID).encode() in data and b'contract_version: 3\n' in data,
                'contract_binding')
    require(b'status: READY\n' in plan and b'status: PROPOSED\n' in adr, 'stage_binding')
    require(b'_bmad-output/blueprints/mainline-b-20261028/' in plan, 'publication_binding')
    return {'plan_id': PLAN_ID, 'contract_version': 3, 'session_id': None,
            'plan_sha256': sha(plan), 'adr_sha256': sha(adr)}


def rows(records):
    return Counter(json.dumps({k: v for k, v in r.items() if k != 'source_id'},
                              sort_keys=True, ensure_ascii=False) for r in records)


def links(files, base):
    count = 0
    for name, data in files.items():
        if not name.endswith('.md'):
            continue
        require(len(data) <= 24576 and len(data.splitlines()) <= 149, 'markdown_budget:' + name)
        for target in re.findall(r'\]\(([^)]+)\)', data.decode('utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            target = target.split('#')[0]
            path, colon, line = target.rpartition(':')
            path = path if colon and line.isdigit() else target
            resolved = (base / PurePosixPath(name).parent / unquote(path)).resolve()
            try:
                internal = resolved.relative_to(base).as_posix()
            except ValueError:
                require(resolved.is_file(), 'external_link:' + name + ':' + target)
            else:
                require(internal in files, 'internal_link:' + name + ':' + target)
            count += 1
    return count


def review():
    bound = binding()
    require(not DEST.exists() and not DEST.is_symlink(), 'release_target_exists')
    old, old_manifest = manifest_tree(OLD)
    new, new_manifest = manifest_tree(NEW)
    frozen = json.loads(new['analysis/frozen-contract.json'])
    require(prior.protected_existing() == frozen['protected_existing'], 'existing_assets_changed')
    # This reads only the original five explicitly authorized source bodies.
    files, versions, root_identity = prior.read_selected(Path(frozen['source_root']))
    require(versions == frozen['source_versions'] and root_identity == frozen['source_root_identity'],
            'source_metadata_changed')
    require({p: sha(b) for p, b in files.items()} == frozen['source_sha256'], 'source_content_changed')
    records = prior.enumerate_records(files)
    require(records == frozen['records'], 'frozen_records_changed')
    op = json.loads(old['analysis/provenance.json'])
    np = json.loads(new['analysis/provenance.json'])
    report = json.loads(new['analysis/structure.json'])
    package = json.loads(new['analysis/knowledge-package.json'])
    require(set(op['requested_scope']) == set(np['requested_scope']) == set(prior.SELECTED), 'scope_changed')
    require({m['path']: m['content_sha256'] for m in op['module_index']} == frozen['source_sha256'],
            'old_source_identity_differs')
    old_core = rows([r['record'] for r in op['mandatory_records']])
    new_core = rows([r['record'] for r in np['mandatory_records']])
    require(old_core == new_core, 'mandatory_record_difference')
    old_calls, new_calls = rows(op['retained_calls']), rows(np['retained_calls'])
    require(not old_calls - new_calls, 'old_call_loss')
    require(set(op['unknowns']) <= set(np['unknowns']), 'unknown_loss')
    denominators = prior.validate_complete(package, np, report, files, records)
    import adaptive_receiver
    from solo_distill.consumer_v2 import consume_frame
    from solo_distill.adaptive_wire import TransportCapabilities
    caps = TransportCapabilities.from_dict(prior.CAPS)
    wire = new['analysis/frame.json']
    arguments = dict(request_id='mainline-b-offline-steps234-20261028', project_id='fastcode-sample')
    def independent(data):
        return adaptive_receiver.receive(data, files, prior.CAPS, **arguments,
            expected_definitions={r[2] for r in records['definitions']},
            expected_calls={r[2] for r in records['calls']})
    def production(data):
        return consume_frame(data, files, protocol_version='2024-11-05', scenario='A',
            return_bytes=24576, policy_id=prior.CAPS['policy'], transport=caps, **arguments).package
    require(independent(wire) == production(wire) == package, 'saved_consumer_difference')
    frames = wire.splitlines(keepends=True)
    evidence = json.loads(new['analysis/step234-evidence.json'])
    require(b''.join(new['analysis/frame-' + str(i + 1).zfill(4) + '.json']
                     for i in range(len(frames))) == wire, 'frame_artifact_difference')
    require(list(map(len, frames)) == evidence['frame_bytes'] and sha(wire) == evidence['wire_sha256'],
            'wire_measurement_difference')
    require(len(wire) <= prior.CAPS['max_transfer_bytes'] and max(map(len, frames)) <= 24576,
            'frame_limits')
    corrupt = json.loads(frames[0]); corrupt['params']['chunk_sha256'] = '0' * 64
    mixed = json.loads(frames[0]); mixed['params']['snapshot_id'] = 'other-snapshot'
    variants = {'missing_chunk': b''.join(frames[1:]),
        'reordered_chunks': b''.join([frames[1], frames[0], *frames[2:]]),
        'corrupt_digest': prior.encoded(corrupt) + b''.join(frames[1:]),
        'mixed_snapshot': prior.encoded(mixed) + b''.join(frames[1:]),
        'missing_final_manifest': b''.join(frames[:-1]),
        'duplicate_final_manifest': wire + frames[-1]}
    negative = {}
    for name, data in variants.items():
        for label, receiver in (('independent', independent), ('production', production)):
            try:
                receiver(data)
            except ValueError as error:
                negative[name + ':' + label] = str(error)
            else:
                raise ValueError('negative_control_accepted:' + name + ':' + label)
    obligations = json.loads(new['analysis/review-obligations.json'])
    expected = obj(ROOT / 'tests/fastcode_acceptance.json')
    require([{k: r[k] for k in a} for r, a in zip(obligations['anchors'], expected['anchors'])]
            == expected['anchors'] and len(obligations['anchors']) == len(expected['anchors']), 'anchor_loss')
    outside = [a['id'] for a in obligations['anchors'] if a['scope_status'] == 'outside_frozen_scope']
    inside = [a['id'] for a in obligations['anchors'] if a['scope_status'] == 'in_scope']
    require(outside == frozen['anchors_outside_scope'] and inside == frozen['anchors_in_scope'], 'anchor_scope')
    require(all(a['semantic_status'] == 'unread_not_verified' for a in obligations['anchors']
                if a['id'] in outside), 'false_outside_acceptance')
    require(op['source_unfinished'] == np['source_unfinished'] == [], 'source_unfinished_changed')
    require(np['delivery_unfinished'] == [], 'delivery_incomplete')
    result = {'status': 'STATIC_REVIEW_PASSED', 'binding': bound,
        'old_files': len(old), 'new_files': len(new), 'old_wire_bytes': len(old['analysis/frame.json']),
        'new_wire_bytes': len(wire), 'decoded_bytes': evidence['actual_decoded_bytes'],
        'frame_count': len(frames), 'notification_count': len(frames)-1, 'maximum_frame_bytes': max(map(len, frames)),
        'denominators': denominators, 'mandatory_records_unchanged': sum(old_core.values()),
        'old_calls_retained': sum(old_calls.values()), 'new_calls_retained': sum(new_calls.values()),
        'added_call_sites': sum((new_calls-old_calls).values()), 'lost_call_sites': 0,
        'old_delivery': op['delivery'], 'new_delivery': np['delivery'],
        'added_artifacts': sorted(set(new)-set(old)), 'removed_artifacts': sorted(set(old)-set(new)),
        'module_index': np['module_index'], 'negative_control_rejections': negative,
        'historical_negative_control_rejections': len(evidence['negative_controls']),
        'candidate_links_checked': links(new, NEW), 'outside_scope_anchors': outside, 'in_scope_static_anchors': inside,
        'unknowns_retained': np['unknowns'], 'business_semantics': 'NOT_VERIFIED', 'stdio': 'NOT_VERIFIED',
        'Roo': 'NOT_VERIFIED', 'source_and_existing_assets_preserved': True,
        'old_manifest_sha256': sha(old['manifest.json']), 'new_manifest_sha256': sha(new['manifest.json']),
        'model_requests': 0, 'source_writes': 0, 'controller_coverage': 'NOT_PROVEN',
        'required_remaining_before_publication': ['targeted_offline_tests', 'native_CS_SR_materials']}
    require(binding() == bound and prior.protected_existing() == frozen['protected_existing'], 'review_boundary_changed')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def release_inputs():
    bound = binding()
    candidate, manifest = manifest_tree(NEW)
    audit = obj(REVIEW / 'review-evidence.json')
    tests = obj(REVIEW / 'offline-tests.receipt.json')
    require(audit['status'] == 'STATIC_REVIEW_PASSED' and audit['binding'] == bound, 'review_contract_changed')
    require(sha(candidate['manifest.json']) == audit['new_manifest_sha256'], 'candidate_changed')
    require(tests['exit_code'] == 0 and tests['tests_run'] > 0 and tests['failures'] == 0
            and tests['errors'] == 0 and tests['skipped'] == 0, 'test_receipt_missing_or_failed')
    frozen = json.loads(candidate['analysis/frozen-contract.json'])
    require(prior.protected_existing() == frozen['protected_existing'], 'assets_changed_after_review')
    source_files, source_versions, source_root_identity = prior.read_selected(Path(frozen['source_root']))
    require(source_versions == frozen['source_versions'] and source_root_identity == frozen['source_root_identity']
            and {p: sha(b) for p, b in source_files.items()} == frozen['source_sha256'], 'source_changed_after_review')
    files = {n: b for n, b in candidate.items() if n != 'manifest.json'}
    files['analysis/candidate-manifest.json'] = candidate['manifest.json']
    for name in ('CS-RECONCILIATION.md', 'SR-REVIEW.md', 'review-evidence.json', 'offline-tests.receipt.json'):
        files['analysis/' + name] = raw(REVIEW / name)
    files['RELEASE.md'] = raw(REVIEW / 'RELEASE.md')
    return files, bound, manifest


def publish():
    files, bound, candidate_manifest = release_inputs()
    require(not DEST.exists() and not DEST.is_symlink(), 'release_target_exists')
    checked = links({**files, 'manifest.json': b''}, DEST)
    # Authorized deployment program: exclusive directory/file creation only.
    for parent in (ROOT / '_bmad-output', ROOT / '_bmad-output/blueprints'):
        require(not parent.is_symlink(), 'publication_parent_symlink')
        if not parent.exists():
            parent.mkdir(mode=0o700)
        require(parent.is_dir(), 'publication_parent_not_directory')
    DEST.mkdir(mode=0o700)
    for folder in ('specs', 'analysis'):
        (DEST / folder).mkdir(mode=0o700)
    for name, data in sorted(files.items()):
        require(name == PurePosixPath(name).as_posix() and '..' not in PurePosixPath(name).parts,
                'unsafe_release_name')
        with (DEST / name).open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        require(raw(DEST / name) == data, 'publication_readback:' + name)
    manifest = {'status': 'PUBLISHED_STATIC_BLUEPRINT', 'plan_id': PLAN_ID, 'contract_version': 3,
        'scope': list(prior.SELECTED), 'files': {n: sha(b) for n, b in files.items()},
        'candidate_manifest_sha256': sha(raw(NEW / 'manifest.json')), 'snapshot_id': candidate_manifest['snapshot_id'],
        'publication': 'exclusive_directory_not_atomic', 'acceptance_scope': 'five_file_offline_static_only',
        'business_semantics': 'NOT_VERIFIED', 'stdio': 'NOT_VERIFIED', 'Roo': 'NOT_VERIFIED',
        'source_writes': 0, 'model_requests': 0}
    with (DEST / 'manifest.json').open('xb') as stream:
        stream.write(prior.encoded(manifest)); stream.flush(); os.fsync(stream.fileno())
    require(binding() == bound, 'publication_contract_changed')
    print(json.dumps(check_release(), ensure_ascii=False, sort_keys=True))


def check_release():
    files, manifest = manifest_tree(DEST)
    candidate, _ = manifest_tree(NEW)
    require(manifest['status'] == 'PUBLISHED_STATIC_BLUEPRINT' and manifest['plan_id'] == PLAN_ID
            and manifest['contract_version'] == 3, 'release_manifest_binding')
    require(sha(candidate['manifest.json']) == manifest['candidate_manifest_sha256'], 'candidate_manifest_changed')
    require(all(files[n] == data for n, data in candidate.items() if n != 'manifest.json'), 'candidate_content_changed')
    require(files['analysis/candidate-manifest.json'] == candidate['manifest.json'], 'original_manifest_loss')
    frozen = json.loads(candidate['analysis/frozen-contract.json'])
    require(prior.protected_existing() == frozen['protected_existing'], 'protected_assets_changed')
    source_files, source_versions, source_root_identity = prior.read_selected(Path(frozen['source_root']))
    require(source_versions == frozen['source_versions'] and source_root_identity == frozen['source_root_identity']
            and {p: sha(b) for p, b in source_files.items()} == frozen['source_sha256'], 'source_changed_at_readback')
    require(manifest_tree(OLD)[1]['files'] == json.loads(raw(OLD / 'manifest.json'))['files'], 'old_manifest_changed')
    return {'status': 'PUBLICATION_READBACK_PASSED', 'destination': DEST.relative_to(ROOT).as_posix(),
        'file_count': len(files), 'links_checked': links(files, DEST),
        'release_manifest_sha256': sha(files['manifest.json']), 'all_files_readback_hash_matched': True,
        'candidate_and_old_assets_preserved': True, 'source_writes': 0, 'model_requests': 0,
        'business_semantics': 'NOT_VERIFIED', 'stdio': 'NOT_VERIFIED', 'Roo': 'NOT_VERIFIED'}


def retire(target):
    archive = ROOT / target
    require(archive.parent == ROOT / '_bmad-output/archive/plans' and PLAN_ID in archive.name
            and re.fullmatch(r'\d{8}T\d{6}Z-' + PLAN_ID + r'\.md', archive.name), 'archive_target')
    receipt = obj(REVIEW / 'publication.receipt.json')
    require(receipt['status'] == 'PUBLICATION_READBACK_PASSED', 'publication_receipt')
    require(check_release()['release_manifest_sha256'] == receipt['release_manifest_sha256'], 'release_changed')
    source = raw(prior.PLAN, 32768)
    require(source == raw(archive, 32768), 'archive_not_exact')
    require(('plan_id: ' + PLAN_ID).encode() in source and b'contract_version: 3\n' in source
            and b'status: COMPLETED\n' in source, 'completed_plan_binding')
    adr = raw(prior.ADR, 32768)
    require(b'status: ACCEPTED\n' in adr and b'contract_version: 3\n' in adr, 'adr_not_accepted')
    require(raw(prior.PLAN, 32768) == source and raw(archive, 32768) == source, 'source_changed_before_retire')
    prior.PLAN.unlink()
    require(not prior.PLAN.exists() and raw(archive, 32768) == source, 'archive_final_readback')
    print(json.dumps({'status': 'ARCHIVE_SAVED_VERIFIED_ACTIVE_SOURCE_RETIRED',
        'archive': target, 'plan_id': PLAN_ID, 'contract_version': 3,
        'archive_sha256': sha(source), 'bytes': len(source), 'source_removed_after_exact_readback': True,
        'ADR': 'ACCEPTED'}, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    try:
        require(Path.cwd().resolve() == ROOT, 'working_directory')
        stage = sys.argv[1] if len(sys.argv) > 1 else ''
        if stage == 'review' and len(sys.argv) == 2:
            review()
        elif stage == 'publish' and len(sys.argv) == 2:
            publish()
        elif stage == 'readback' and len(sys.argv) == 2:
            print(json.dumps(check_release(), ensure_ascii=False, sort_keys=True))
        elif stage == 'retire' and len(sys.argv) == 3:
            retire(sys.argv[2])
        else:
            raise ValueError('explicit_authorized_stage_required')
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED', 'type': type(error).__name__, 'reason': str(error)[:1000],
            'release_may_be_partial': DEST.exists(), 'source_writes': 0}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
