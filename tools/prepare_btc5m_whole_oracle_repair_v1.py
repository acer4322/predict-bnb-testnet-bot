"""Freeze a three-arm whole-market oracle diagnostic, without local HFT."""
import ast
import gzip
import json
import shutil
from prepare_btc5m_oracle_repair_panel_v1 import ROOT,sha,once
from btc5m_whole_oracle_repair_v1 import self_test,MODES

R=ROOT/'data/research'
PARENT=ROOT/'.lan_worker_v1/persistent_repair_demand_2026085_20260913_v1'
PACKAGE=ROOT/'.lan_worker_v1/whole_oracle_repair_2026085_20260913_v1'
STEM='BTC5M_WHOLE_ORACLE_REPAIR_V1_20260913'


def main():
    self_test()
    prior=json.loads((PARENT/'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(PARENT/n)==h for n,h in prior['files'].items())
    target_path=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(target_path)=='d2ce3d621241f0de94aeb888cb2d059345e3dc0afd677d0d1b70547f798c578b'
    target=json.loads(gzip.decompress(target_path.read_bytes()))
    net=sum(a['shares']*(1 if a['side']=='UP' else -1) for a in target['targetActions'])
    assert net>0
    source=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    source=once(source,"choices=('PULSE_CONTROL','PERSIST30')",'choices='+repr(MODES))
    source=once(source,'_MoneyGate=gate.PayoffRepairGate,','_MoneyGate=lambda selection,mode:demand.WholeCapacity(selection,mode,args.demand_mode),')
    ast.parse(source)
    assert not PACKAGE.exists(),'immutable package exists'
    PACKAGE.mkdir()
    for name in prior['files']:
        if name not in ('money_runner.py','demand_gate.py'):shutil.copy2(PARENT/name,PACKAGE/name)
    shutil.copy2(PARENT/'demand_gate.py',PACKAGE/'persistent_gate.py')
    shutil.copy2(PARENT/'money_gate.py',PACKAGE/'parallel_gate.py')
    shutil.copy2(ROOT/'tools/btc5m_whole_oracle_repair_v1.py',PACKAGE/'demand_gate.py')
    (PACKAGE/'money_runner.py').write_text(source,encoding='utf-8')
    m={k:prior[k] for k in ('backend','native_sha256','remote_inputs','fixed_train_total_frames','selection','demand_selection')}
    m.update(version=STEM,market=2026085,oracle=True,runtime_eligible=False,max_threads=4,sequential_jobs=1,
        maximum_native_jobs=3,modes=list(MODES),model_fits=0,parameter_search=False,
        parent_manifest_sha256=sha(PARENT/'manifest.json'),oracle_direction='UP',oracle_source_sha256=sha(target_path),
        oracle_scope='Final observed Target net inventory SIDE only, independently rechecked from the consumed source. Not settlement winner, final quantity, prices, timings or profit targets. Source totals are not passed into the actor.',
        question='Given final net direction UP, can the current finite repair mechanism work repeatedly from OWN state throughout a full market, and which control or execution gaps remain?',
        arms=dict(LEGACY_PERSIST='V15 PERSIST30 plus complete accumulated raw receipt export. Policy behavior must be exact parity.',
                  AUTO_CAP_ONLY='Original frozen oracle-UP actor, quantity capacity from first producer frame; no selected cut and no experimental repair floor.',
                  AUTO_REPAIR='Same as AUTO_CAP_ONLY plus at most one active finite repair floor, automatically started and re-evaluated from current public quotes and OWN inventory, cost and still-reserved service.'),
        automatic_rule=dict(trigger=['No active finite work; no new birth in the frame that just ended the previous work.',
            'Current UP inventory greater than DOWN and DOWN conditional payoff negative.',
            'Original rounded DOWN candidate is below the current Passive minimum.',
            'Existing frozen ticket30 is legal at the current original-policy Passive quote.',
            'Uncovered quantity and zero-payoff pending-full-fill scenario capacities each admit the ticket.',
            'No own cross including cancel-pending, and a valid noncrossing Passive quote.'],
            goal='Fixed current DOWN inventory + all currently committed DOWN quantity + ticket30. No ratcheting within a work item.',
            end='Same V15 confirmation/market-end/current-net-direction/nonnegative-down-payoff withdrawal rules.',
            scope='Hand-declared diagnostic wiring of verified mechanisms, not newly fitted Target policy. Baseline may exceed the finite floor.'),
        frozen=['Actor theta, gross progression and direction magnitude formula, original quote calculation, ticket and quantity step.',
                'Original scheduler, stale-price maintenance, funding/grant semantics and native accounting.',
                'BTC Passive new-order qty>=18 and notional>=1, partial receipts not subject to new-order minima; no Active.'],
        control_gate='LEGACY_PERSIST must match V15 persist17 fields and4 full primary streams,intent,money_rows before AUTO_CAP_ONLY. It is interface and receipt-export parity, not another fitted candidate.',
        contrast='AUTO_REPAIR versus AUTO_CAP_ONLY isolates repeated finite work under the same whole-episode quantity cap. LEGACY versus AUTO_CAP_ONLY also changes startup capacity and removes the selected one-off work; do not treat that as an isolated oracle-information effect.',
        receipt_export='Read the existing native receipt ledger.seen accumulated sequence map and map native ids via native orders. Do not use only the last-drain delta buffer or alter physical_process.',
        evaluation=['All source/native/hash/accounting/pending/receipt gates and original control parity.',
                    'Positive final net alone is not success because quantity cap structurally forbids weak-side overshoot; report magnitude and both conditional payoffs.',
                    'Full market loss area, worst floor, actual activity, pending dwell, raw receipt timing, work births/completions/blocked dust and route absence.',
                    'No promotion, tuning or additional runs merely to improve a score. Preserve any rejected plan and partial artifacts.'],
        limits=['One consumed market and hindsight direction bit; no generalization or live claim.',
                'No Target final quantities or payoff targets used in runtime; comparison to Target is offline scoring only.',
                'Full quantity-cap modes disallow actual reverse net overshoot. Active arbitration is still absent.',
                'An eligible work is not a submitted order; original scheduler, pending reservations and legal gateway must materialize it.'],
        files={p.name:sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE/'manifest.json',R/(STEM+'_PREREGISTERED.json')):p.write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    for tag,mode in zip(('control','capacity','repeat'),MODES):
        wave=dict(progress_artifact=f'data/research/WHOLE_ORACLE_REPAIR_{tag.upper()}_PROGRESS_20260913.json',jobs=[dict(
            job_id=f'whole-oracle-repair-2026085-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode',mode],
            cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/f'whole_oracle_repair_{tag}_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PREPARED',files=len(m['files']),modes=m['modes'],oracle='UP',native_runs=0)))


if __name__=='__main__':main()
