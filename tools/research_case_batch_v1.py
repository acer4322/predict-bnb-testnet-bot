"""Fixed experiment matrices, independent of parameter optimization.

An adapter supplies a trusted Python callback and exact pinned inputs. This module
has no shell, remote, market, model, or trading authority. Every arm/case is saved;
failed/unknown executions stop and are never retried automatically. Completed
identical cells may be reused only with matching execution identity and hashes.
The caller owns the job/process lock (for example the existing WriterLock).
"""
from __future__ import annotations
import hashlib
import itertools
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

VERSION = 'RESEARCH_CASE_BATCH_V1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('wb') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', value):
        raise ValueError('Invalid matrix identifier')
    return value


def cells(spec):
    if spec.get('version') != VERSION or not spec.get('execution_identity'):
        raise ValueError('A version and pinned execution identity are required')
    if not isinstance(spec.get('max_new_evaluations'), int) or isinstance(spec['max_new_evaluations'], bool) or spec['max_new_evaluations'] < 0:
        raise ValueError('Invalid new-evaluation budget')
    for group in ('arms', 'cases'):
        rows = spec.get(group)
        if not isinstance(rows, list) or not rows:
            raise ValueError('Empty matrix dimension: ' + group)
        ids = [validate_id(row['id']) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate IDs in ' + group)
    result = []
    for arm, case in itertools.product(spec['arms'], spec['cases']):
        identity = dict(execution=spec['execution_identity'], arm=arm, case=case)
        result.append(dict(id=arm['id'] + '__' + case['id'], arm=arm, case=case, identity=digest(identity)))
    if len({c['id'] for c in result}) != len(result):
        raise ValueError('Composite cell IDs collide')
    canonical(spec)
    return result


def verify_payload(payload, root):
    if set(payload) != {'metrics', 'artifacts'} or not isinstance(payload['artifacts'], dict) or not payload['artifacts']:
        raise ValueError('Metrics and at least one evidence artifact are required')
    canonical(payload['metrics'])
    root = Path(root).resolve()
    for name, expected in payload['artifacts'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != expected:
            raise ValueError('Cell artifact missing, out of bounds, or changed: ' + name)


def run_matrix(spec: dict, out: Path, execute: Callable, reuse: Optional[Callable] = None,
               before_new: Optional[Callable] = None, on_progress: Optional[Callable] = None):
    """Run an explicit arm x case matrix; no sampling or score-based cell omission.

    execute(cell, evidence_directory) returns {metrics, artifacts}, where artifact
    paths are relative to evidence_directory. reuse has the same callback shape;
    it returns None or {identity, metrics, artifacts, source}. Reuse adapters must
    verify original source state and configuration, then copy/link immutable
    evidence into the new cell directory. Only identical execution identities
    are accepted. A new wrapper version need not invalidate unchanged native
    evidence, but a changed policy/data/fee/clock/backend MUST change identity.
    """
    wanted = cells(spec)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / 'BATCH_STATE.json'
    if state_path.exists():
        state = read_json(state_path)
        if state['spec_hash'] != digest(spec):
            raise ValueError('Frozen matrix changed; create a successor')
        if any(row['state'] != 'COMPLETE' for row in state['cells'].values()):
            raise RuntimeError('Incomplete/failed cell requires forensic recovery, not automatic retry')
        if not set(state['cells']).issubset({c['id'] for c in wanted}):
            raise ValueError('Unexpected saved cell')
    else:
        state = dict(version=VERSION, spec_hash=digest(spec), cells={}, new_evaluations=0, reused_cells=0)
        atomic_json(state_path, state)
    started = time.perf_counter()

    def progress(phase, cell=None):
        row = dict(phase=phase, cell=cell['id'] if cell else None,
            completed=sum(r['state'] == 'COMPLETE' for r in state['cells'].values()),
            total=len(wanted), new_evaluations=state['new_evaluations'], reused_cells=state['reused_cells'],
            elapsed_seconds=time.perf_counter()-started)
        atomic_json(out / 'PROGRESS.json', row)
        if on_progress:
            on_progress(row)

    for cell in wanted:
        evidence = out / 'cells' / cell['id']
        if cell['id'] in state['cells']:
            row = state['cells'][cell['id']]
            if row['identity'] != cell['identity']:
                raise ValueError('Saved cell identity changed')
            verify_payload({k: row[k] for k in ('metrics','artifacts')}, evidence)
            progress('VERIFIED_COMPLETED_CELL', cell)
            continue
        evidence.mkdir(parents=True, exist_ok=True)
        if any(evidence.iterdir()):
            raise RuntimeError('Unrecorded cell evidence exists; inspect before reuse')
        row = dict(state='RUNNING', identity=cell['identity'], arm=cell['arm']['id'],
                   case=cell['case']['id'], attempts=1, origin='UNDECIDED')
        state['cells'][cell['id']] = row
        atomic_json(state_path, state)
        try:
            cached = reuse(cell, evidence) if reuse else None
            if cached is not None:
                if cached['identity'] != cell['identity']:
                    raise ValueError('Reuse identity differs from the frozen execution')
                payload = {k: cached[k] for k in ('metrics','artifacts')}
                row.update(origin='VERIFIED_REUSE', source=cached['source'])
                verify_payload(payload, evidence)
                state['reused_cells'] += 1
            else:
                if state['new_evaluations'] >= spec['max_new_evaluations']:
                    raise RuntimeError('Frozen new-evaluation budget exhausted')
                if before_new:
                    before_new()
                state['new_evaluations'] += 1
                row['origin'] = 'NEW_EXECUTION'
                atomic_json(state_path, state)
                progress('EXECUTING_CELL', cell)
                payload = execute(cell, evidence)
                verify_payload(payload, evidence)
            row.update(payload, state='COMPLETE')
            atomic_json(state_path, state)
            progress('CELL_COMPLETE', cell)
        except Exception as exc:
            row.update(state='FAILED_NEEDS_REVIEW', error=repr(exc))
            atomic_json(state_path, state)
            progress('STOPPED_NEEDS_REVIEW', cell)
            raise
    progress('MATRIX_COMPLETE')
    return state
