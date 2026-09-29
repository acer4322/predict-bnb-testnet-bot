"""Freeze one control and two parallel native continuations, without local HFT."""
import ast
import json
import shutil
from pathlib import Path
from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once
from btc5m_parallel_payoff_gate_v1 import MODES, self_test

PARENT = ROOT / '.lan_worker_v1/payoff_repair_2026085_20260913_v1'
PACKAGE = ROOT / '.lan_worker_v1/parallel_payoff_2026085_20260913_v1'
R = ROOT / 'data/research'


def main():
    self_test()
    prior = json.loads((PARENT / 'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(PARENT / n) == h for n, h in prior['files'].items())
    source = (PARENT / 'money_runner.py').read_text(encoding='utf-8')
    source = once(source, "choices=('INERT', 'ISOLATED_QUANTITY', 'ISOLATED_PAYOFF_ZERO', 'ISOLATED_PAYOFF_MINUS_ONE')", 'choices=' + repr(MODES))
    ast.parse(source)
    assert not PACKAGE.exists(), 'immutable package exists'
    PACKAGE.mkdir()
    for name in ('frozen_runner.py', 'helper_rearm.py', 'adapter.py', 'clock_wrapper.py'):
        shutil.copy2(PARENT / name, PACKAGE / name)
    shutil.copy2(PARENT / 'money_gate.py', PACKAGE / 'isolated_gate.py')
    shutil.copy2(ROOT / 'tools/btc5m_parallel_payoff_gate_v1.py', PACKAGE / 'money_gate.py')
    (PACKAGE / 'money_runner.py').write_text(source, encoding='utf-8')
    m = {k: prior[k] for k in ('backend', 'native_sha256', 'remote_inputs', 'fixed_train_total_frames', 'selection')}
    m.update(version='BTC5M_PARALLEL_PAYOFF_PREREG_V1_20260913', market=2026085,
        oracle=True, runtime_eligible=False, modes=list(MODES), max_threads=4,
        parent_manifest_sha256=sha(PARENT / 'manifest.json'), no_parameter_search=True,
        question='With original NEW UP admission preserved, can reservation-aware weak-side quantity or zero-payoff capacity sustain two-side service and favorable downside geometry?',
        change='Only remove NEW UP suppression from the prior isolated quantity/zero-payoff arms. Original candidates, price, scheduler, ticket validation, pending ownership and numeric parameters stay frozen.',
        control_gate='INERT must match all prior V9 control economic fields and four full trace streams before either treatment submit.',
        metric=dict(gain_to_loss='max(P_UP,P_DOWN)/max(0,-min(P_UP,P_DOWN)), only for positive best and positive loss; else explicit regime and null.',
                    loss_to_gain='max(0,-min(P_UP,P_DOWN))/max(P_UP,P_DOWN), only for positive best.',
                    direction='Also fixed oracle UP payoff and DOWN payoff; rank-selected Best must not hide a reversed net side.',
                    companions='Cost, actual fills, NEW UP counts, retained direction, time-integrated loss, both-positive states and unresolved owners/cash; no single-score promotion.'),
        criteria=['Full source/accounting/terminal lifecycle and control/prefix parity must pass.',
                  'A parallel exercise requires postcut NEW and realized fill on both sides, with inventory-changing observations in both halves of the remaining market.',
                  'Report the final ratio and loss-area with activity and frozen-UP direction. Do not rank stopped/low-activity or reversed-direction arms as learned.',
                  'Zero is a reused constructed diagnostic anchor, not a Target-fitted ratio or inferred strategy threshold. No runtime reward/gate from Target ratio.'],
        limits=['Single consumed market with an offline final-direction bit; not deployable or unseen validation.',
                'A pending full-fill scenario is a capacity scenario, not confirmed repair or a worst-case guarantee.',
                'Weak-side cap cannot invent absent repair candidates. Strong-side spending is not globally budgeted here.',
                'No profits include unknown Target fees or hidden starting assets. Observer event clocks are retrospective only.'],
        files={p.name: sha(p) for p in PACKAGE.iterdir()})
    for path in (PACKAGE / 'manifest.json', R / 'BTC5M_PARALLEL_PAYOFF_PREREGISTERED_20260913.json'):
        path.write_text(json.dumps(m, indent=2) + '\n', encoding='utf-8')
    for tag, mode in zip(('control', 'quantity', 'zero'), MODES):
        wave = dict(progress_artifact=f'data/research/PARALLEL_PAYOFF_{tag.upper()}_PROGRESS_20260913.json', jobs=[dict(
            job_id=f'parallel-payoff-2026085-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py', '--mode', 'ORACLE_UP', '--money-mode', mode],
            cwd='.', max_threads=4, min_free_ram_gb=6, max_start_cpu_pct=90, required_artifact='result.json')])
        (R / f'parallel_payoff_{tag}_wave_20260913.json').write_text(json.dumps(wave, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status='PREPARED', package=str(PACKAGE), files=len(m['files']), modes=MODES)))


if __name__ == '__main__':
    main()
