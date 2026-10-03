"""Supervise the already submitted ETH job; never stage, submit, or replay.

Status reads may retry with recorded errors. Mutating collection/publication
steps run once; ambiguous failures stop for inspection. Native execution stays
on the worker. This is one task-owned foreground helper, not a scheduled job.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

P = Path(__file__).resolve().parent
ROOT = P.parents[2]
JOB = 'original-maker-layer-eth861-20261002-v1r2'
PACKAGE = 'original_maker_layer_eth861_20261002_v1r2'
LOG = P / 'eth_supervision'
STATE = P / 'ETH_SUPERVISION_STATE.json'
INTENT = P / 'ETH_SUPERVISION_INTENT.json'
BTC = [f'original-maker-layer-btc185-20261002-v1r{i}' for i in range(1, 6)] + [
    'original-maker-layer-btc185-rest554-20261002-v1r6',
    'original-maker-layer-btc185-rest382-20261002-v1r7']


def save(obj):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, indent=2), encoding='utf-8')
    os.replace(temp, STATE)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=True), flush=True)


def step(name, args):
    """One invocation, preserving full stdout/stderr and the exit code."""
    path = LOG / name
    path.mkdir(exist_ok=False)
    command = [sys.executable, str(P / args[0]), *args[1:]]
    (path / 'INTENT.json').write_text(json.dumps(dict(command=command, attempts=1)))
    state = dict(job_id=JOB, phase=name, state='POSTPROCESS_RUNNING', started=time.time())
    save(state)
    emit(state)
    with (path / 'stdout.log').open('w', encoding='utf-8') as stdout, (path / 'stderr.log').open('w', encoding='utf-8') as stderr:
        child = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr)
        while child.poll() is None:
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                emit(dict(job_id=JOB, phase=name, state='POSTPROCESS_RUNNING', elapsed_seconds=time.time()-state['started']))
        code = child.returncode
    (path / 'RESULT.json').write_text(json.dumps(dict(return_code=code)))
    emit(dict(job_id=JOB, phase=name, return_code=code))
    if code:
        raise RuntimeError(f'{name} exited {code}; inspect preserved logs; no automatic retry')


def main():
    assert not INTENT.exists(), 'An existing supervisor must be inspected, never restarted silently'
    submitted = json.loads((P / 'ETH_R2_SUBMITTED.json').read_bytes())
    assert submitted['accepted'] and submitted['job_id'] == JOB
    LOG.mkdir(exist_ok=False)
    INTENT.write_text(json.dumps(dict(job_id=JOB, pid=os.getpid(), attempts=1,
        no_stage_or_submit=True, started=time.time()), indent=2))
    while True:
        cp = subprocess.run([sys.executable, str(P / 'read_progress.py'), 'ETH', '--summary'],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if cp.returncode:
            obj = dict(job_id=JOB, state='STATUS_READ_ERROR', error=cp.stderr[-2000:],
                       action='read-only status retry after 45 seconds; no submit')
            save(obj)
            emit(obj)
            time.sleep(45)
            continue
        current = json.loads(cp.stdout.strip().splitlines()[-1])
        current['phase'] = 'WORKER_REPLAY'
        save(current)
        emit(current)
        with (LOG / 'progress.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(current)+'\n')
        if current['state'] not in ('running', 'queued'):
            assert current['state'] in ('failed', 'succeeded'), current
            assert current['completed'] == 2583 and not current['read_errors'], current
            break
        time.sleep(45)

    # Collection ownership belongs to the existing watcher. On failed jobs it
    # exits without collecting; wait for its exact PID to disappear first.
    collector = int(submitted['auto_collector']['pid'])
    while True:
        command = f"Get-CimInstance Win32_Process -Filter 'ProcessId={collector}' | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        cp = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
        assert cp.returncode == 0, cp.stderr
        live = cp.stdout.strip()
        if not live:
            break
        process = json.loads(live)
        assert JOB in (process.get('CommandLine') or ''), 'Collector PID reused; inspect, do not affect it'
        emit(dict(job_id=JOB, phase='WAIT_COLLECTION_OWNER', state=current['state']))
        time.sleep(45)

    returns = ROOT / 'data/research/lan_worker_returns' / JOB
    if current['state'] == 'failed':
        assert not returns.exists(), 'Inspect existing return directory; do not overwrite'
        step('01_collect', ['collect_strict.py', JOB])
    else:
        assert returns.exists(), 'Successful collection belongs to watcher'
    step('02_hashes', ['verify_collections.py', *BTC, JOB])
    step('03_analyze', ['analyze_full.py', PACKAGE])
    report = json.loads((P / (PACKAGE + '_ANALYSIS.json')).read_bytes())
    assert report['observed'] == report['expected_paths'] == 2583 and not report['missing']
    step('04_packets', ['export_packets.py', PACKAGE])
    packets = json.loads((P / 'ETH_PACKETS.json').read_bytes())
    published = []
    for i, packet in enumerate(packets):
        step(f'05_publish_{i:02d}', ['publish_one.py', packet['id']])
        published.append(json.loads((P / (packet['id'] + '_PUBLISHED.json')).read_bytes()))
    step('06_lineage', ['export_lineage.py', 'ALL'])
    lineage = json.loads((P / 'ALL_LINEAGE_PACKET.json').read_bytes())
    step('07_publish_lineage', ['publish_one.py', lineage['id']])
    published.append(json.loads((P / (lineage['id'] + '_PUBLISHED.json')).read_bytes()))
    result = dict(job_id=JOB, phase='FINISHED', state='COLLECTED_AUDITED_PUBLISHED',
        status_counts=report['status_counts'], summary=report['summary'], published=published,
        fits=0, live_changes=0, shadow_test_started=False)
    (P / 'ETH_SUPERVISION_RESULT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    save(result)
    emit(dict(job_id=JOB, state=result['state'], status_counts=result['status_counts'],
              commits=[r['commit'] for r in published]))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        obj = dict(job_id=JOB, state='STOPPED_FOR_INSPECTION', error=repr(exc),
                   no_automatic_resubmission=True)
        save(obj)
        emit(obj)
        raise
