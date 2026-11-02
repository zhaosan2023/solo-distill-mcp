"""Bounded offline CLI acceptance; independent selected-source and artifact checks.

Runs only this project's review CLI. Target Python modules are parsed as data,
never imported. Saves no files itself; the explicitly authorized CLI publishes
one new review directory. Checks do not prove whole-tree mutation isolation.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from urllib.parse import unquote
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('/home/ecs-user/project/FastCode')
SELECTED = ('api.py', 'fastcode/__init__.py', 'fastcode/agent_tools.py',
            'fastcode/answer_generator.py', 'fastcode/cache.py')
OUTPUT = 'verification/brownfield-review/python-mvp-20261027T0344Z'


def read_source(path):
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in SOURCE.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(1048577)
            after = os.fstat(stream.fileno())
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        assert identity(before) == identity(after), 'source changed while checking'
        assert len(data) <= 1048576, 'source size exceeds authorized file budget'
        return data, identity(after)
    finally:
        os.close(descriptor)


def independent_records(path, raw):
    tree = ast.parse(raw, filename=path)
    symbols, imports = [], []
    def visit(node, scope):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = ('class' if isinstance(node, ast.ClassDef) else 'async_function'
                    if isinstance(node, ast.AsyncFunctionDef) else 'function')
            qualified = '.'.join(scope + [node.name])
            symbols.append((path, kind, qualified, node.lineno, node.end_lineno))
            scope = scope + [node.name]
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((path, alias.name, None, alias.asname, 0,
                                '.'.join(scope), node.lineno, node.end_lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.append((path, node.module, alias.name, alias.asname,
                                node.level, '.'.join(scope), node.lineno, node.end_lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)
    visit(tree, [])
    return symbols, imports


def main():
    before = {path: read_source(path) for path in SELECTED}
    expected_symbols, expected_imports = [], []
    for path, (raw, _) in before.items():
        symbols, imports = independent_records(path, raw)
        expected_symbols.extend(symbols)
        expected_imports.extend(imports)
    command = ['timeout', '--kill-after=1s', '25s', sys.executable, '-B',
               '-m', 'solo_distill.brownfield', '--project-id', 'fastcode-sample',
               '--include', *SELECTED, '--output', OUTPUT]
    environment = dict(os.environ, PYTHONPATH='src', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=28)
    assert result.returncode == 0, (result.returncode, result.stdout[:2048], result.stderr[:2048])
    assert not result.stderr, result.stderr[:2048]
    assert len(result.stdout) <= 4096
    summary = json.loads(result.stdout)
    assert summary['status'] == 'SAVED_FOR_REVIEW'
    assert summary['review_state'] == 'READY_FOR_REVIEW'
    output = ROOT / OUTPUT
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert manifest['status'] == 'SAVED_FOR_REVIEW'
    actual_paths = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    assert actual_paths == set(manifest['files']) | {'manifest.json'}
    for name, digest in manifest['files'].items():
        path = output / name
        assert not path.is_symlink()
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == digest, name
        if name.endswith('.md'):
            assert len(payload) <= 24576 and len(payload.splitlines()) < 150, name
            for target in re.findall(r'\]\(([^)]+)\)', payload.decode('utf-8')):
                link_path, _, line = target.rpartition(':')
                if not line.isdigit():
                    link_path = target
                assert (path.parent / unquote(link_path)).resolve().is_file(), (name, target)
    provenance = json.loads((output / 'analysis/provenance.json').read_bytes())
    assert provenance['coverage']['file_denominator'] == len(SELECTED)
    assert provenance['coverage']['parsed_files'] == len(SELECTED)
    assert provenance['source_unfinished'] == []
    delivered_symbols, delivered_imports = [], []
    for item in provenance['mandatory_records']:
        row = item['record']
        assert row['path'] in before
        assert row['content_sha256'] == hashlib.sha256(before[row['path']][0]).hexdigest()
        assert row['source_id'] in (output / item['document']).read_text(encoding='utf-8')
        if item['tier'] == 'P1':
            delivered_symbols.append(tuple(row[key] for key in
                ('path', 'kind', 'qualified_name', 'start_line', 'end_line')))
        else:
            assert item['tier'] == 'P0'
            delivered_imports.append(tuple(row[key] for key in
                ('path', 'module', 'name', 'alias', 'level', 'scope', 'start_line', 'end_line')))
    assert Counter(expected_symbols) == Counter(delivered_symbols)
    assert Counter(expected_imports) == Counter(delivered_imports)
    wire = (output / 'analysis/frame.json').read_bytes()
    assert wire.endswith(b'\n')
    assert len(wire) == manifest['structure_frame_bytes'] == summary['structure_frame_bytes']
    assert len(wire) <= 24576
    report = json.loads(json.loads(wire)['result']['content'][0]['text'])
    assert report == json.loads((output / 'analysis/structure.json').read_bytes())
    assert report['delivery']['core_complete'] is True
    assert report['delivery']['omitted_counts']['p0'] == 0
    assert report['delivery']['omitted_counts']['p1'] == 0
    assert provenance['delivery_unfinished'] == report['unfinished']
    after = {path: read_source(path) for path in SELECTED}
    assert before == after, 'selected source content or metadata changed'
    print(json.dumps({'status': 'PASS', 'cli_exit_code': result.returncode,
        'output': OUTPUT, 'file_count': len(actual_paths), 'parsed_files': len(SELECTED),
        'independently_matched_imports': len(delivered_imports),
        'independently_matched_definitions': len(delivered_symbols),
        'wire_bytes': len(wire), 'wire_limit': 24576,
        'delivery_state': report['delivery']['state'],
        'delivered_counts': report['delivery']['delivered_counts'],
        'omitted_counts': report['delivery']['omitted_counts'],
        'selected_source_content_and_metadata_unchanged': True,
        'mutation_scope': 'only selected files before/after; not whole tree or continuous watch',
        'model_requests': 0, 'stderr_bytes': len(result.stderr)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
