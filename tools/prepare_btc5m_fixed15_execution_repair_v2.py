"""Pin the collected pre-send failure and freeze its isolated execution repair."""
import ast
import gzip
import json
import shutil
from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once

R = ROOT / 'data/research'
PARENT = ROOT / '.lan_worker_v1/fixed15_core_loop_2026085_20260913_v1'
PACKAGE = ROOT / '.lan_worker_v1/fixed15_core_loop_2026085_20260913_v2'
STEM = 'BTC5M_FIXED15_CORE_LOOP_V2_20260913'


def main():
    prior = json.loads((PARENT / 'manifest.json').read_text())
    assert all(sha(PARENT / n) == h for n, h in prior['files'].items())
    failed = R / 'lan_worker_returns/fixed15-core-loop-2026085-control-20260913-v1'
    failure = json.loads((failed / 'result.json').read_text())
    trace = json.loads(gzip.decompress((failed / 'failure_trace.json.gz').read_bytes()))
    assert failure['error'] == 'ValueError:below passive minimum submitted quantity'
    assert 'open_funding_native_active_adapter_v1.py' in failure['traceback']
    assert trace['native_actions'] == [] and trace['plans'] == []
    assert json.loads((failed / 'failure_receipts.json').read_text()) == []
    source = (PARENT / 'money_runner.py').read_text()
    source = once(source, '_ticket_validator_factory=ticket.make_validator', '_install_ticket_condition=ticket.install')
    ast.parse(source)
    assert not PACKAGE.exists()
    PACKAGE.mkdir()
    for name in prior['files']:
        if name not in ('money_runner.py', 'ticket_condition.py'):
            shutil.copy2(PARENT / name, PACKAGE / name)
    (PACKAGE / 'money_runner.py').write_text(source, encoding='utf-8')
    shutil.copy2(ROOT / 'tools/btc5m_fixed15_execution_condition_v2.py', PACKAGE / 'ticket_condition.py')
    manifest = dict(prior)
    manifest.update(version=STEM, parent_manifest_sha256=sha(PARENT / 'manifest.json'),
        execution_repair=dict(
            failed_job=failed.name, failure_sha256=sha(failed / 'result.json'),
            failure_trace_sha256=sha(failed / 'failure_trace.json.gz'),
            failure_stage='First mixed-envelope validation before gateway/native send',
            observed_native_actions=0, observed_receipts=0, observed_committed_plans=0,
            previous_half='NOT_SUBMITTED_DO_NOT_SUBMIT_V1',
            cause='Producer-only override did not reach adapter/gateway imports and exact-planner minimum18.',
            repair='Install pinned sizing+exact-seam variants in per-job copied tools before imports.',
            required_before_native='Captured first-plan adapter/gateway component reproduction passes; then load-only stage.'),
        files={p.name: sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE / 'manifest.json', R / (STEM + '_PREREGISTERED.json')):
        p.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for tag, retention in (('control', 0.), ('half', .5)):
        wave = dict(progress_artifact=f'data/research/FIXED15_CORE_LOOP_V2_{tag.upper()}_PROGRESS_20260913.json', jobs=[dict(
            job_id=f'fixed15-core-loop-2026085-{tag}-20260913-v2',
            argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                  '--mode', 'ORACLE_UP', '--money-mode', 'PARALLEL_QUANTITY', '--demand-mode', 'AUTO_REPAIR', '--retention', str(retention)],
            cwd='.', max_threads=4, min_free_ram_gb=6, max_start_cpu_pct=90, required_artifact='result.json')])
        (R / f'fixed15_core_loop_v2_{tag}_wave_20260913.json').write_text(json.dumps(wave, indent=2) + '\n')
    print(json.dumps(dict(status='REPAIR_FROZEN_NOT_SUBMITTED', manifest_sha256=sha(PACKAGE / 'manifest.json'))))


if __name__ == '__main__':
    main()
