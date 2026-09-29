"""Freeze four small native branches; no native execution or fitting here."""
import ast,json,shutil
from pathlib import Path
from prepare_btc5m_oracle_repair_panel_v1 import once,sha,ROOT
from btc5m_payoff_repair_gate_v1 import self_test,MODES,instrument

PARENT=ROOT/'.lan_worker_v1/oracle_repair_2026085_20260913_v1'
PACKAGE=ROOT/'.lan_worker_v1/payoff_repair_2026085_20260913_v1'
R=ROOT/'data/research'


def main():
    self_test();old=json.loads((PARENT/'manifest.json').read_text(encoding='utf-8'))
    for name,h in old['files'].items():assert sha(PARENT/name)==h
    source=(PARENT/'oracle_runner.py').read_text(encoding='utf-8')
    source=once(source,"    ap.add_argument('--repair-route'", "    ap.add_argument('--money-mode', choices="+repr(MODES)+", required=True)\n    ap.add_argument('--repair-route'")
    source=once(source,"    wrapper = load('intent_clock_wrapper'", "    assert args.mode=='ORACLE_UP' and args.repair_route=='NONE'\n    gate=load('payoff_repair_gate',package/'money_gate.py');gate.self_test()\n    wrapper = load('intent_clock_wrapper'")
    source=once(source,"    compile(source, str(package/'frozen_runner.py'), 'exec')", "    source=gate.instrument(source,replace)\n    compile(source, str(package/'frozen_runner.py'), 'exec')")
    source=once(source,"_ExposureIntent=ExposureIntent)","_ExposureIntent=ExposureIntent, _MoneyGate=gate.PayoffRepairGate, _MONEY_MODE=args.money_mode, _MONEY_SELECTION=manifest['selection'])")
    source=once(source,"intent=producer.intent.rows)","intent=producer.intent.rows, money_events=producer.money_gate.events, money_rows=producer.money_gate.rows)")
    source=once(source,"intent_mode=args.mode, actual_replay_frames=actual_frames,","intent_mode=args.mode, money_mode=args.money_mode, actual_replay_frames=actual_frames,")
    ast.parse(source)
    # Check all source replacements without importing the native wrapper.
    probe=(PARENT/'frozen_runner.py').read_text(encoding='utf-8')
    probe=probe.replace('self.total_frames=1','self.total_frames=1;self.intent=_ExposureIntent(_MODE)',1)
    ast.parse(instrument(probe,once))
    assert not PACKAGE.exists(),'immutable package exists'
    PACKAGE.mkdir()
    for name in ('frozen_runner.py','helper_rearm.py','adapter.py','clock_wrapper.py'):shutil.copy2(PARENT/name,PACKAGE/name)
    shutil.copy2(ROOT/'tools/btc5m_payoff_repair_gate_v1.py',PACKAGE/'money_gate.py')
    (PACKAGE/'money_runner.py').write_text(source,encoding='utf-8')
    selection=json.loads((R/'BTC5M_REPAIR_WAIT_PREREGISTERED_20260913.json').read_text(encoding='utf-8'))['selection']
    manifest={k:old[k] for k in ('backend','native_sha256','remote_inputs','fixed_train_total_frames')}
    manifest.update(version='BTC5M_PAYOFF_REPAIR_PREREG_V1',market=2026085,oracle=True,runtime_eligible=False,selection=selection,
        modes=list(MODES),max_threads=4,no_parameter_search=True,
        primary='Two hypothetical settlement payoff paths and paired exposure; FIFO old lot is descriptive, not a mandatory target.',
        formulas=dict(up='Q_UP-C',down='Q_DOWN-C',quantity_capacity='max(0,Q_UP-Q_DOWN-pending_DOWN_qty)',
            pending_down_payoff='Q_DOWN-C+pending_DOWN_qty-pending_DOWN_cash-pending_UP_cash',
            cash_capacity='max(0,(bound-pending_down_payoff)/(1-passive_limit))',
            admitted='min(frozen candidate,quantity_capacity,cash_capacity), rounded DOWN; original venue validation unchanged'),
        boundaries='0 and -1 are explicit diagnostic anchors (zero payoff and historical loss aspiration), not inferred Target thresholds or intermediate hard floor gates.',
        isolation='All three isolated arms prohibit NEW UP after common cut, including fallback paths. Existing UP 276.495953846 and DOWN 90 remain endogenous under original lifecycle; no immediate pending release.',
        limits=['INERT must reproduce prior oracle UP full traces and 17 result fields before other submissions.',
            'Frozen candidate pricing, ticket and scheduling retained; monetary capacity can reduce but cannot invent an absent candidate.',
            'Pending full-fill scenario only allocates service capacity, never counts pending as confirmed repair.',
            'Money cap may leave shortfall smaller than venue minimum; no forced minimum overshoot or erased debt.',
            'No guarantee of reaching the anchor; retain all failures, partial states, costs and residuals.',
            'All are offline oracle consumed single-market diagnostics; no similarity or economic promotion.'],
        parent_manifest_sha256=sha(PARENT/'manifest.json'),files={p.name:sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE/'manifest.json',R/'BTC5M_PAYOFF_REPAIR_PREREGISTERED_20260913.json'):p.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    for tag,mode in zip(('control','quantity','zero','minus-one'),MODES):
        wave=dict(progress_artifact=f'data/research/PAYOFF_REPAIR_{tag.upper().replace("-","_")}_PROGRESS_20260913.json',jobs=[dict(
            job_id=f'payoff-repair-2026085-{tag}-20260913-v1',argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--mode','ORACLE_UP','--money-mode',mode],
            cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/f'payoff_repair_{tag}_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PREPARED',package=str(PACKAGE),files=manifest['files'])))


if __name__=='__main__':main()
