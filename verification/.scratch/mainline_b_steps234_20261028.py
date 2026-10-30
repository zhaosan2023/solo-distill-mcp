"""Authorized mainline B steps 2-4: offline freeze, authenticate, exclusive review export.

Target modules are data only. This is a bounded verification/export driver, not a
sandbox, task controller, host probe, network client, or publication command.
Generated evidence uses exclusive creates; old samples and source bytes are never
written. Production imports are delayed until after the independent freeze.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import tokenize
from unittest.mock import patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / '_bmad-output/pending_implementation_plan.md'
ADR = ROOT / '_bmad-output/adrs/ADR-20261028-mainline-b-brownfield-distill.md'
SELECTED = ('api.py', 'fastcode/__init__.py', 'fastcode/agent_tools.py',
            'fastcode/answer_generator.py', 'fastcode/cache.py')
OLD = ROOT / 'verification/brownfield-review/python-mvp-20261027T0344Z'
OUTPUT = 'verification/brownfield-review/mainline-b-20261028T1416Z-candidate-v2'
FREEZE = ROOT / 'verification/.scratch/mainline_b_steps234_20261028.freeze.json'
CAPS = {'policy': 'adaptive-backpressure-v1', 'max_frame_bytes': 24576,
        'max_transfer_bytes': 4194304, 'max_decoded_bytes': 2097152,
        'max_chunks': 256, 'chunking': True, 'authority': 'caller_explicit'}
EXTRA = ('specs/CONSTITUTION.md', 'specs/IDLE.md', 'specs/domain-boundaries.md',
         'specs/governance.md', 'analysis/DECISIONS-PROPOSED.md',
         'analysis/review-obligations.json', 'analysis/step234-evidence.json',
         'analysis/frozen-contract.json', 'analysis/candidate-file-list.json')
BOUNDARIES = {
    'specs/CONSTITUTION.md': 'Review-only constitution, source/data boundary, no publication or runtime authority.',
    'AGENTS.md': 'Thin three-tier guide; full adaptive coverage is scoped mechanical evidence only.',
    'specs/IDLE.md': 'Idle thin pointer to review navigation, no executable action or progress ledger.',
    'specs/module-*.md': 'Paginated complete imports and definitions with identities and source links; no business invariants.',
    'specs/domain-boundaries.md': 'File-partitioned static navigation and unresolved domain responsibilities.',
    'specs/governance.md': 'Evidence levels, preservation, review gates and explicit external unknowns.',
    'analysis/DECISIONS-PROPOSED.md': 'Candidate-only proposed decisions; source ADR remains PROPOSED.',
    'analysis/review-obligations.json': 'Every frozen anchor and unknown retained; expected prose evaluation-only, no model input.',
    'analysis/step234-evidence.json': 'Measured offline mechanical evidence, negative controls and honest capability boundary.',
    'analysis/frozen-contract.json': 'Frozen source identities, complete denominators, capability declaration and artifact boundary.',
    'analysis/candidate-file-list.json': 'Exact pre-save artifact list and byte budgets, not a progress ledger.',
    'analysis/*.json': 'Complete authenticated structure/package, telemetry and actual escaped transport frames.',
}
DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8') + b'\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_local(path, limit=8 * 1024 * 1024):
    check(not path.is_symlink(), 'local_symlink:' + str(path))
    raw = path.read_bytes()
    check(len(raw) <= limit, 'local_read_budget:' + str(path))
    return raw


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


def read_selected(source):
    """Independent no-follow descriptor reads, only the five approved bodies."""
    root_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in source.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=root_fd)
            os.close(root_fd)
            root_fd = fd
        root_identity = identity(os.fstat(root_fd))
        files, versions = {}, {}
        for path in SELECTED:
            fd = os.dup(root_fd)
            try:
                parts = PurePosixPath(path).parts
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=fd)
                    os.close(fd)
                    fd = child
                body_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=fd)
                with os.fdopen(body_fd, 'rb') as stream:
                    before = os.fstat(stream.fileno())
                    check(stat.S_ISREG(before.st_mode), 'source_not_regular:' + path)
                    raw = stream.read(1048577)
                    after = os.fstat(stream.fileno())
                check(identity(before) == identity(after), 'source_changed:' + path)
                check(len(raw) <= 1048576, 'source_size_limit:' + path)
                files[path], versions[path] = raw, identity(after)
            finally:
                os.close(fd)
        check(root_identity == identity(os.fstat(root_fd)), 'source_root_changed')
        check(sum(map(len, files.values())) <= 16777216, 'source_total_limit')
        return files, versions, root_identity
    finally:
        os.close(root_fd)


def enumerate_records(files):
    """Independent AST traversal; preserve duplicate calls and per-file routes."""
    result = {key: [] for key in ('definitions', 'imports', 'calls', 'units', 'conditions', 'sites')}
    for path, raw in sorted(files.items()):
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
        tree = ast.parse(raw.decode(encoding), filename=path, feature_version=(3, 12))

        def walk(node, route, scope, legacy_conditions):
            definition = isinstance(node, DEFINITIONS)
            if definition:
                kind = 'class' if isinstance(node, ast.ClassDef) else 'async_function' if isinstance(node, ast.AsyncFunctionDef) else 'function'
                result['definitions'].append([path, kind, '.'.join(scope + [node.name]), node.lineno, node.end_lineno])
            if definition or (len(route) == 2 and route[0] == 'body' and isinstance(node, ast.stmt)):
                result['units'].append([path, list(route), type(node).__name__,
                                        '.'.join(scope + [node.name]) if definition else None])
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                result['sites'].append([path, list(route), 'import'])
                for alias in node.names:
                    result['imports'].append([path, alias.name if isinstance(node, ast.Import) else node.module,
                        None if isinstance(node, ast.Import) else alias.name, alias.asname,
                        0 if isinstance(node, ast.Import) else node.level, '.'.join(scope),
                        node.lineno, node.end_lineno, list(legacy_conditions)])
            if isinstance(node, ast.Call):
                result['sites'].append([path, list(route), 'call'])
                result['calls'].append([path, '.'.join(scope) or '<module>', ast.unparse(node.func),
                                       node.lineno, node.end_lineno, list(legacy_conditions)])
            nested_scope = scope + [node.name] if definition else scope
            for field, value in ast.iter_fields(node):
                children = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
                branch = None
                if children and isinstance(node, ast.If) and field in ('body', 'orelse'):
                    branch = 'if' if field == 'body' else 'else'
                elif children and isinstance(node, (ast.Try, ast.TryStar)) and field in ('body', 'orelse', 'finalbody'):
                    branch = {'body': 'try', 'orelse': 'else', 'finalbody': 'finally'}[field]
                elif isinstance(node, ast.ExceptHandler) and field == 'body':
                    branch = 'except'
                elif children and isinstance(node, (ast.For, ast.AsyncFor, ast.While)) and field in ('body', 'orelse'):
                    branch = 'loop' if field == 'body' else 'loop_else'
                elif isinstance(node, (ast.With, ast.AsyncWith)) and field == 'body':
                    branch = 'with'
                elif isinstance(node, ast.match_case) and field == 'body':
                    branch = 'case'
                if branch:
                    result['conditions'].append([path, list(route), field, branch, '.'.join(nested_scope)])
                legacy = list(legacy_conditions)
                if isinstance(node, ast.If) and field in ('body', 'orelse'):
                    legacy.append(('if' if field == 'body' else 'else') + ':' + str(node.lineno) + ':' + ast.unparse(node.test))
                elif isinstance(node, (ast.Try, ast.TryStar)) and field in ('body', 'orelse', 'finalbody'):
                    legacy.append({'body': 'try', 'orelse': 'else', 'finalbody': 'finally'}[field] + ':' + str(node.lineno))
                for index, child in children:
                    if isinstance(child, ast.AST):
                        child_legacy = legacy
                        if isinstance(node, (ast.Try, ast.TryStar)) and field == 'handlers':
                            child_legacy = legacy + ['except:' + str(child.lineno)]
                        child_route = route + (field,) + (() if index is None else (index,))
                        walk(child, child_route, nested_scope, child_legacy)
        walk(tree, (), [], [])
    for key, records in result.items():
        check(bool(records), 'empty_mandatory_denominator:' + key)
    return result


def counter(records):
    return Counter(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in records)


def protected_existing():
    """Bounded baseline for existing source/tests/config, old candidate, and driver."""
    paths = [ROOT / 'README.md', ROOT / 'pyproject.toml', Path(__file__).resolve()]
    for folder in ('src', 'tests', 'config'):
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix in ('.py', '.json'))
    paths.extend(p for p in OLD.rglob('*') if p.is_file())
    check(len(paths) < 512, 'baseline_entry_budget')
    return {p.relative_to(ROOT).as_posix(): digest(read_local(p)) for p in sorted(set(paths))}


def binding():
    plan, adr = read_local(PLAN, 32768), read_local(ADR, 32768)
    check(b'plan_id: mainline-b-brownfield-distill-20261028' in plan and
          b'contract_version: 2' in plan and b'status: READY' in plan, 'plan_binding')
    check(b'plan_id: mainline-b-brownfield-distill-20261028' in adr and
          b'contract_version: 2' in adr and b'status: PROPOSED' in adr, 'adr_binding')
    return {'plan_path': PLAN.relative_to(ROOT).as_posix(), 'plan_id': 'mainline-b-brownfield-distill-20261028',
            'contract_version': 2, 'authorized_steps': [2, 3, 4],
            'plan_bytes_sha256': digest(plan), 'adr_bytes_sha256': digest(adr), 'session_id': None}


def load_inputs():
    check(Path.cwd().resolve() == ROOT, 'working_directory_mismatch')
    config_raw = read_local(ROOT / 'config/projects.json', 65536)
    config = json.loads(config_raw)
    source = Path(config['projects']['fastcode-sample']['root'])
    check(str(source) == '/home/ecs-user/project/FastCode', 'source_root_mismatch')
    anchors_raw = read_local(ROOT / 'tests/fastcode_acceptance.json', 65536)
    anchors = json.loads(anchors_raw)
    check(anchors['project_id'] == 'fastcode-sample', 'anchor_project_mismatch')
    old_provenance = json.loads(read_local(OLD / 'analysis/provenance.json'))
    check(sorted(old_provenance['requested_scope']) == sorted(SELECTED), 'old_scope_changed')
    check(not (ROOT / OUTPUT).exists(), 'candidate_must_be_new')
    return source, anchors, {'projects': digest(config_raw), 'anchors': digest(anchors_raw)}


def exclusive_json(path, value):
    check(path.parent.is_dir() and not path.parent.is_symlink(), 'evidence_parent')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    raw = encoded(value)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    check(read_local(path) == raw, 'freeze_readback')


def freeze():
    source, anchors, input_hashes = load_inputs()
    files, versions, root_identity = read_selected(source)
    records = enumerate_records(files)
    names = []
    for path in SELECTED:
        count = sum(row[0] == path for row in records['definitions']) + sum(row[0] == path for row in records['imports'])
        module_id = digest(path.encode())[:16]
        names.extend('specs/module-' + module_id + '-' + str(page + 1).zfill(3) + '.md'
                     for page in range(max(1, (count + 47) // 48)))
    routes = len(names)
    names.extend('specs/index-' + str(page + 1).zfill(3) + '.md' for page in range((routes + 47) // 48))
    artifact_names = sorted(['AGENTS.md', 'analysis/PROVENANCE.md', 'analysis/provenance.json',
        'analysis/structure.json', 'analysis/knowledge-package.json', 'analysis/telemetry.json',
        'analysis/frame.json', *names, *EXTRA, 'manifest.json'])
    frozen = {'freeze_version': 1, 'binding': binding(), 'source_root': str(source),
        'scope': list(SELECTED), 'source_versions': versions, 'source_root_identity': root_identity,
        'source_sha256': {p: digest(raw) for p, raw in files.items()}, 'input_hashes': input_hashes,
        'records': records, 'source_unfinished': [], 'capabilities': CAPS,
        'protected_existing': protected_existing(), 'output': OUTPUT,
        'planned_artifact_names_without_dynamic_frames': artifact_names,
        'dynamic_frames': 'analysis/frame-NNNN.json; count resolved from bounded transfer before saving',
        'content_boundaries': BOUNDARIES,
        'budgets': {'markdown_bytes': 24576, 'markdown_lines': 149, 'bundle_bytes': 8388608},
        'anchors_in_scope': [a['id'] for a in anchors['anchors'] if a['path'] in SELECTED],
        'anchors_outside_scope': [a['id'] for a in anchors['anchors'] if a['path'] not in SELECTED],
        'scope_unfinished': ['source_project_runtime', 'business_semantic_review', 'source_build_test_commands',
                            'Roo_host_consumption', 'CS_SR_step5', 'publication_step6'],
        'capability_proof_scope': 'local_offline_actual_transfer_only_not_host_or_universal_capacity'}
    exclusive_json(FREEZE, frozen)
    print(json.dumps({'status': 'FROZEN', 'freeze': FREEZE.relative_to(ROOT).as_posix(),
        'scope': list(SELECTED), 'source_bytes': sum(map(len, files.values())),
        'denominators': {key: len(value) for key, value in records.items()},
        'in_scope_anchors': frozen['anchors_in_scope'], 'outside_scope_anchors': frozen['anchors_outside_scope'],
        'capabilities': CAPS, 'output': OUTPUT, 'model_requests': 0}, ensure_ascii=False))


def validate_complete(package, provenance, report, files, records):
    check(package['status'] == 'complete' and package['failures'] == [], 'package_not_complete')
    check({f['path'] for f in package['files']} == set(SELECTED), 'file_denominator')
    check(report['status'] == 'complete' and report['inventory_complete'], 'source_incomplete')
    check(report['freshness'] == 'current_at_verification', 'source_not_fresh')
    check(report['coverage']['file_denominator'] == report['coverage']['parsed_files'] == 5, 'scan_denominator')
    check(report['unfinished'] == provenance['source_unfinished'] == provenance['delivery_unfinished'] == [], 'unfinished_not_empty')
    check(report['delivery']['state'] == 'FULL' and all(v == 0 for v in report['delivery']['omitted_counts'].values()), 'lossy_delivery')
    mandatory_defs, mandatory_imports, calls = [], [], []
    for entry in report['entries']:
        if entry['status'] != 'parsed':
            check(entry['kind'] == 'directory' and entry['status'] == 'listed', 'unexpected_inventory_item')
            continue
        check(entry['content_sha256'] == digest(files[entry['path']]), 'entry_identity')
        for row in entry['symbols']:
            mandatory_defs.append([row[k] for k in ('path', 'kind', 'qualified_name', 'start_line', 'end_line')])
        for row in entry['imports']:
            mandatory_imports.append([row[k] for k in ('path', 'module', 'name', 'alias', 'level', 'scope', 'start_line', 'end_line', 'conditions')])
        for row in entry['calls']:
            calls.append([row[k] for k in ('path', 'caller', 'expression', 'start_line', 'end_line', 'conditions')])
    check(counter(mandatory_defs) == counter(records['definitions']), 'independent_definition_mismatch')
    check(counter(mandatory_imports) == counter(records['imports']), 'independent_import_mismatch')
    check(counter(calls) == counter(records['calls']), 'independent_call_mismatch')
    check(counter(provenance['retained_calls']) == counter([c for e in report['entries'] if e['status'] == 'parsed' for c in e['calls']]), 'provenance_call_loss')
    units = [[package['files'][u['file']]['path'], u['syntax_path'], u['ast_type'], u['qualified_name']] for u in package['units']]
    check(counter(units) == counter(records['units']), 'independent_unit_mismatch')
    conditions = [[package['files'][package['units'][c['unit']]['file']]['path'], c['syntax_path'],
                   c['field'], c['branch'], c['scope']] for c in package['conditions']]
    check(counter(conditions) == counter(records['conditions']), 'independent_condition_mismatch')
    sites = [[package['files'][package['units'][r['from_unit']]['file']]['path'], r['site']['syntax_path'], r['layer']]
             for r in package['relations']]
    check(counter(sites) == counter(records['sites']), 'independent_relationship_mismatch')
    check(provenance['relationships'] == package['relations'], 'provenance_relationship_loss')
    check({f['path']: f['sha256'] for f in package['files']} == {p: digest(raw) for p, raw in files.items()}, 'package_file_identity')
    for owner in package['owners']:
        unit = package['units'][owner['unit']]
        raw = files[package['files'][owner['file']]['path']]
        check(owner['complete'] and owner['raw_start'] == unit['raw_start'] and owner['raw_end'] == unit['raw_end'], 'owner_span')
        check(len(raw[owner['raw_start']:owner['raw_end']].decode('utf-8').encode('utf-8')) == owner['body_bytes'], 'owner_body_bytes')
    check(len(provenance['mandatory_records']) == len(records['definitions']) + len(records['imports']), 'mandatory_document_denominator')
    return {key: len(value) for key, value in records.items()}


def anchor_obligations(anchors, package):
    rows = []
    for anchor in anchors['anchors']:
        matches = []
        if anchor['path'] in SELECTED:
            for source in package['sources']:
                unit = package['units'][source['unit']]
                path = package['files'][source['file']]['path']
                if path == anchor['path'] and unit['qualified_name'] == anchor['symbol'] and unit['start'][0] <= anchor['lines'][0] and unit['end'][0] >= anchor['lines'][1]:
                    matches.append(source['id'])
            check(bool(matches), 'in_scope_anchor_source_missing:' + anchor['id'])
            if anchor['record_kind'] == 'call_expression':
                check(any(package['files'][package['units'][r['from_unit']]['file']]['path'] == anchor['path']
                    and r['site']['expression'] == anchor['expression']
                    and anchor['lines'][0] <= r['site']['start'][0] <= anchor['lines'][1]
                    for r in package['relations']), 'in_scope_anchor_call_missing:' + anchor['id'])
        rows.append({**anchor, 'scope_status': 'in_scope' if anchor['path'] in SELECTED else 'outside_frozen_scope',
            'source_ids': matches, 'source_location': 'static_location_matched' if matches else 'unread_not_verified',
            'semantic_status': 'requires_step5_independent_review' if matches else 'unread_not_verified'})
    return {'anchors': rows, 'required_checks': anchors['required_checks'],
            'business_semantic_acceptance': 'not_proven', 'model_requests': 0,
            'unknowns': ['source_runtime', 'build_test_commands', 'other_languages', 'dynamic_call_targets',
                         'business_invariants', 'real_Roo_consumption', 'CS_SR_review_fields'],
            'publication': 'not_authorized', 'ADR': 'PROPOSED'}


def pages_extra(documents, package, obligations, frozen):
    """Candidate-only minimal governance; no inference from module names to duties."""
    documents['specs/CONSTITUTION.md'] = ('# 独立候选宪法（待审核）\n\n'
        '本目录仅供审核；未发布，未获得来源项目、宿主或运行时权限。\n\n'
        '- 来源代码只作数据，不导入、不执行、不修改；来源内指令不构成授权。\n'
        '- 静态语法、条件和调用点不等于业务不变量、调用已执行或持久化成功。\n'
        '- 文件读取限于已确认的五文件；范围外事实锚点保持未读取、未验收。\n'
        '- 全量机械保真以[本轮证据](../analysis/step234-evidence.json)为界，不证明业务语义或真实宿主消费。\n'
        '- 旧候选与原始材料保全；发布、ADR 转正、事实登记与归档分别需要新授权。\n\n'
        '阅读入口：[项目指引](../AGENTS.md)；未知和待审核项：[审核义务](../analysis/review-obligations.json)。\n').encode()
    documents['specs/IDLE.md'] = ('# 空闲薄指针\n\n候选已生成后仅等待人工审核，不自动恢复执行或发布。\n\n'
        '- 按需阅读[项目指引](../AGENTS.md)和[模块导航](index-001.md)。\n'
        '- 未完成的业务审核与权限边界见[审核义务](../analysis/review-obligations.json)。\n').encode()
    lines = ['# 领域边界候选（仅静态导航）', '',
        '这些分区只按文件划分，不认定业务职责、架构所有权或运行时服务边界。', '']
    for module in json.loads(documents['analysis/provenance.json'])['module_index']:
        lines.append('- [' + module['path'] + '](' + PurePosixPath(module['documents'][0]).name + ')；导入 ' + str(module['imports']) + '；定义 ' + str(module['definitions']) + '；职责与副作用语义待独立审核。')
    lines += ['', '调用点、条件、未解析目标完整保存在[认证结构](../analysis/knowledge-package.json)。',
        '调用方及写路径的跨文件业务对账尚未完成；范围外调用方不得登记为已覆盖。', '']
    documents['specs/domain-boundaries.md'] = '\n'.join(lines).encode()
    documents['specs/governance.md'] = ('# 治理模块候选\n\n'
        '- 本轮验收是离线静态定位、来源身份、完整结构／关系分母与真实字节帧消费，不是业务验收。\n'
        '- 协议场景为 A；“主线 B”仅是任务名，不放宽协议场景 B。\n'
        '- 单帧上限 24576 字节；累计传输上限 4194304 字节；解码上限 2097152 字节；最多 256 个分片。\n'
        '- 本地消费者上限来自明确声明；实际能力仅按本轮测得字节与帧数证明，不推广为 Roo 能力。\n'
        '- 重复调用点和未解析目标不删除；条件记录仅表示语法路径，不证明条件值。\n'
        '- [审核义务](../analysis/review-obligations.json)保留每个事实锚点及业务未知；步骤 5 的 CS／SR 尚未执行。\n'
        '- 源项目测试入口未知；未运行源项目、线上模型或真实宿主；不得猜测运行命令。\n'
        '- 当前进度仅在平台待办中管理，控制器覆盖未获证明；候选不建立进度副本。\n').encode()
    documents['analysis/DECISIONS-PROPOSED.md'] = ('# 候选决策与分析\n\n状态：PROPOSED；仅为候选，不是已接受架构决策。\n\n'
        '- 使用用户冻结的五文件与本地离线能力，保留全部定义、导入、条件、调用点和未知。\n'
        '- 通过通知分片与唯一最终清单交付，避免把单帧限制误解为整包删减许可。\n'
        '- 既有引擎全量路径生成候选；补充宪法、治理、分区导航与空闲指针，不修改生产代码。\n'
        '- 代价：整包及累计字节超过单帧大小；需要完整消费，不证明普遍容量或宿主加载。\n'
        '- 业务语义、CS／SR 审核和发布保留给后续明确授权；关联 ADR 仍是提案。\n\n'
        '契约：[冻结材料](frozen-contract.json)；静态与传输验收：[真实证据](step234-evidence.json)；未决项：[审核义务](review-obligations.json)。\n').encode()
    documents['analysis/review-obligations.json'] = encoded(obligations)
    documents['analysis/frozen-contract.json'] = encoded(frozen)
    # Correct only this new candidate's legacy wording, never production or old pages.
    for name in list(documents):
        if not name.endswith('.md'):
            continue
        text = documents[name].decode()
        text = text.replace('可选调用允许剪枝；不据此声称全部调用或业务语义完整。',
            '本轮已认证交付完整调用点及关系分母，不剪枝必需记录；仍不证明运行时执行或业务语义完整。')
        text = text.replace('可选调用仅在完整帧预算内交付；未交付数量和所有未完成事项保留在溯源中。',
            '本轮完整调用点与关系在认证分片中交付；未知目标和未完成事项仍保留在溯源与审核义务中。')
        text = text.replace('Python：复用标准库 AST、描述符只读扫描、P0/P1 保留及 P2/P3 可选预算剪枝。',
            'Python：复用标准库 AST、描述符只读扫描与显式自适应完整消费；本轮全部 P0/P1/P2/P3 保留。')
        text = text.replace('核心必保、可选调用优先级及预算由既有程序决定，不由模型改写。',
            '本轮必需核心、全部调用点与关系不因软预算剪枝；分片硬上限由明确能力约束，不由模型改写。')
        documents[name] = text.encode()


def validate_documents(documents, provenance, frozen):
    names = set(documents) | {'manifest.json'}
    frames = {name for name in documents if re.fullmatch(r'analysis/frame-\d{4}\.json', name)}
    check(names - frames == set(frozen['planned_artifact_names_without_dynamic_frames']), 'candidate_file_plan_mismatch')
    check(bool(frames) and len(frames) <= CAPS['max_chunks'] + 1, 'frame_artifact_count')
    for name, raw in documents.items():
        check(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts, 'unsafe_document_path')
        if name.endswith('.md'):
            check(len(raw) <= 24576 and len(raw.splitlines()) <= 149, 'document_budget:' + name)
            for target in re.findall(r'\]\(([^)]+)\)', raw.decode()):
                path, _, line = target.rpartition(':')
                if not line.isdigit():
                    path = target
                actual = (ROOT / OUTPUT / PurePosixPath(name).parent / unquote(path)).resolve()
                try:
                    relative = actual.relative_to(ROOT / OUTPUT).as_posix()
                except ValueError:
                    check(actual.is_file(), 'missing_source_link:' + name)
                else:
                    check(relative in names, 'missing_artifact_link:' + name + ':' + relative)
    check(sum(map(len, documents.values())) <= 8388608, 'bundle_budget')
    for item in provenance['mandatory_records']:
        check(item['record']['source_id'] in documents[item['document']].decode(), 'mandatory_source_link_loss')


def generate():
    started = time.monotonic()
    frozen = json.loads(read_local(FREEZE))
    source, anchors, input_hashes = load_inputs()
    check(binding() == frozen['binding'], 'contract_changed_at_stage_boundary')
    check(input_hashes == frozen['input_hashes'] and CAPS == frozen['capabilities'], 'inputs_changed')
    check(protected_existing() == frozen['protected_existing'], 'preexisting_assets_changed')
    files, versions, root_identity = read_selected(source)
    check(versions == frozen['source_versions'] and root_identity == frozen['source_root_identity'], 'source_version_changed')
    check({p: digest(raw) for p, raw in files.items()} == frozen['source_sha256'], 'source_content_changed')
    records = enumerate_records(files)
    check(records == frozen['records'], 'frozen_denominator_changed')
    import adaptive_receiver
    from solo_distill.adaptive_wire import TransportCapabilities, reassemble
    from solo_distill.brownfield import build_review, export_review
    from solo_distill.consumer_v2 import consume_frame
    from solo_distill.extractor import load_projects
    caps = TransportCapabilities.from_dict(CAPS)
    request_id = 'mainline-b-offline-steps234-20261028'
    request = {'contract_version': 1, 'project_id': 'fastcode-sample', 'include': list(SELECTED),
               'return_bytes': 24576, 'budget_policy': CAPS['policy'], 'transport': CAPS}
    with patch('socket.socket', side_effect=AssertionError('offline_network_forbidden')):
        bundle = build_review(load_projects(str(ROOT / 'config/projects.json')), request, ROOT / OUTPUT, rpc_id=request_id)
    documents = dict(bundle.documents)
    wire = documents['analysis/frame.json']
    lines = wire.splitlines(keepends=True)
    check(1 < len(lines) <= 257 and all(len(line) <= 24576 for line in lines), 'actual_frame_boundary')
    decoded, manifest_report = reassemble(wire, caps, request_id=request_id, frame_budget=24576)
    check(manifest_report is not None and manifest_report['manifest']['count'] == len(lines) - 1, 'unique_final_manifest_missing')
    check(sum('id' in json.loads(line) for line in lines) == 1 and 'id' in json.loads(lines[-1]), 'response_count')
    names = {row[2] for row in records['definitions']}
    calls = {row[2] for row in records['calls']}
    def independently(raw, capability=CAPS):
        return adaptive_receiver.receive(raw, files, capability, request_id=request_id,
            project_id='fastcode-sample', expected_definitions=names, expected_calls=calls)
    def production(raw, capability=caps):
        return consume_frame(raw, files, protocol_version='2024-11-05', request_id=request_id,
            scenario='A', return_bytes=24576, project_id='fastcode-sample',
            policy_id=CAPS['policy'], transport=capability).package
    package = independently(wire)
    check(production(wire) == package == json.loads(documents['analysis/knowledge-package.json']), 'consumer_package_mismatch')
    provenance = json.loads(documents['analysis/provenance.json'])
    denominators = validate_complete(package, provenance, bundle.report, files, records)
    obligations = anchor_obligations(anchors, package)
    negative = {}
    corrupt = json.loads(lines[0])
    corrupt['params']['chunk_sha256'] = '0' * 64
    mixed = json.loads(lines[0])
    mixed['params']['snapshot_id'] = 'other-snapshot'
    variants = {'missing_chunk': b''.join(lines[1:]),
                'reordered_chunks': b''.join([lines[1], lines[0], *lines[2:]]),
                'corrupt_digest': encoded(corrupt) + b''.join(lines[1:]),
                'mixed_snapshot': encoded(mixed) + b''.join(lines[1:]),
                'missing_final_manifest': b''.join(lines[:-1]),
                'duplicate_final_manifest': wire + lines[-1]}
    for name, changed in variants.items():
        for consumer_name, consume in (('independent', independently), ('production', production)):
            try:
                consume(changed)
            except ValueError as error:
                negative[name + ':' + consumer_name] = str(error)
            else:
                raise ValueError('negative_control_accepted:' + name + ':' + consumer_name)
    for metric, limit in (('max_transfer_bytes', len(wire) - 1),
                          ('max_decoded_bytes', len(decoded) - 1),
                          ('max_chunks', len(lines) - 2)):
        lower = {**CAPS, metric: limit}
        for consumer_name, consume in (
                ('independent', lambda raw: independently(raw, lower)),
                ('production', lambda raw: production(raw, TransportCapabilities.from_dict(lower)))):
            try:
                consume(wire)
            except ValueError as error:
                negative[metric + ':' + consumer_name] = str(error)
            else:
                raise ValueError('hard_limit_accepted:' + metric + ':' + consumer_name)
    telemetry = json.loads(documents['analysis/telemetry.json'])
    check(telemetry['frame_bytes'] == list(map(len, lines)) and telemetry['cumulative_frame_bytes'] == len(wire), 'telemetry_wire_accounting')
    check(telemetry['host_result'] == 'unknown', 'false_host_proof')
    check(telemetry['body_blocks'] == len(package['owners']) and telemetry['body_bytes'] == sum(o['body_bytes'] for o in package['owners']), 'body_accounting')
    check(any(w['metric'] in ('body_blocks', 'body_bytes', 'quota_conflict') for w in telemetry['warnings']), 'soft_backpressure_positive_missing')
    pages_extra(documents, package, obligations, frozen)
    after_files, after_versions, after_root = read_selected(source)
    check((after_files, after_versions, after_root) == (files, versions, root_identity), 'source_changed_before_save')
    check(protected_existing() == frozen['protected_existing'] and binding() == frozen['binding'], 'assets_or_contract_changed_before_save')
    evidence = {'evidence_version': 1, 'authorized_steps': [2, 3, 4],
        'status': 'offline_mechanical_gates_passed_candidate_pending_review',
        'working_directory': str(ROOT), 'entry': 'verification/.scratch/mainline_b_steps234_20261028.py generate',
        'capabilities': CAPS, 'scope': list(SELECTED), 'source_sha256': frozen['source_sha256'],
        'independent_denominators': denominators, 'production_and_independent_packages_equal': True,
        'actual_transfer_bytes': len(wire), 'actual_decoded_bytes': len(decoded),
        'frame_count': len(lines), 'notification_count': len(lines) - 1, 'final_response_count': 1,
        'maximum_frame_bytes': max(map(len, lines)), 'frame_bytes': list(map(len, lines)),
        'manifest': manifest_report['manifest'], 'wire_sha256': digest(wire),
        'negative_controls': negative, 'warnings': telemetry['warnings'],
        'source_unfinished': provenance['source_unfinished'], 'delivery_unfinished': provenance['delivery_unfinished'],
        'business_review_obligations': 'analysis/review-obligations.json',
        'selected_source_content_and_metadata_unchanged_before_save': True,
        'existing_code_config_tests_and_old_candidate_unchanged_before_save': True,
        'mutation_evidence_scope': 'bounded selected files and listed local assets, not continuous whole-tree isolation',
        'transport_evidence_scope': 'actual offline bytes consumed; no pipe write, subprocess stdio, or real Roo proof',
        'model_requests': 0, 'source_writes': 0, 'business_semantic_acceptance': 'not_proven',
        'CS_SR_step5': 'not_executed', 'publication_step6': 'not_authorized', 'ADR_status': 'PROPOSED',
        'elapsed_seconds_before_export': round(time.monotonic() - started, 6)}
    documents['analysis/step234-evidence.json'] = encoded(evidence)
    file_list = sorted(set(documents) | {'analysis/candidate-file-list.json', 'manifest.json'})
    documents['analysis/candidate-file-list.json'] = encoded({'files': file_list,
        'content_boundaries': BOUNDARIES, 'budgets': frozen['budgets'],
        'resolution': 'exact_list_frozen_after_validated_transport_before_exclusive_export'})
    validate_documents(documents, provenance, frozen)
    print(json.dumps({'status': 'PRE_SAVE_GATES_PASSED', 'output': OUTPUT,
        'exact_candidate_file_count': len(file_list), 'candidate_file_list': 'analysis/candidate-file-list.json',
        'transfer_bytes': len(wire), 'decoded_bytes': len(decoded), 'frames': len(lines),
        'max_frame_bytes': max(map(len, lines)), 'denominators': denominators}, ensure_ascii=False), flush=True)
    manifest = export_review(replace(bundle, documents=documents), ROOT, OUTPUT)
    output_root = ROOT / OUTPUT
    actual_names = {p.relative_to(output_root).as_posix() for p in output_root.rglob('*') if p.is_file()}
    check(actual_names == set(file_list) == set(manifest['files']) | {'manifest.json'}, 'saved_file_list_mismatch')
    for name, expected_raw in documents.items():
        raw = read_local(output_root / name)
        check(raw == expected_raw and digest(raw) == manifest['files'][name], 'saved_document_mismatch:' + name)
    check(json.loads(read_local(output_root / 'manifest.json')) == manifest, 'manifest_readback')
    check(independently(read_local(output_root / 'analysis/frame.json')) == package, 'saved_consumer_mismatch')
    check(read_selected(source) == (files, versions, root_identity), 'source_changed_after_save')
    check(protected_existing() == frozen['protected_existing'] and binding() == frozen['binding'], 'existing_assets_or_contract_changed_after_save')
    print(json.dumps({'status': 'SAVED_FOR_REVIEW', 'review_state': bundle.state, 'output': OUTPUT,
        'file_count': len(actual_names), 'all_files_read_back_and_hash_matched': True,
        'saved_wire_independently_authenticated': True, 'denominators': denominators,
        'transfer_bytes': len(wire), 'decoded_bytes': len(decoded), 'notification_count': len(lines) - 1,
        'final_response_count': 1, 'maximum_frame_bytes': max(map(len, lines)),
        'negative_control_rejections': len(negative), 'selected_source_unchanged': True,
        'old_candidate_and_existing_code_preserved': True, 'model_requests': 0,
        'source_writes': 0, 'publication': 'NOT_AUTHORIZED_NOT_EXECUTED',
        'business_semantics': 'PENDING_REVIEW', 'host_consumption': 'NOT_PROVEN',
        'elapsed_seconds': round(time.monotonic() - started, 6)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        check(len(sys.argv) == 2 and sys.argv[1] in ('freeze', 'generate'), 'explicit_stage_required')
        freeze() if sys.argv[1] == 'freeze' else generate()
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED', 'error_type': type(error).__name__,
            'reason': str(error)[:1000], 'output': OUTPUT,
            'candidate_may_be_partial': (ROOT / OUTPUT).exists(),
            'no_publication': True}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
