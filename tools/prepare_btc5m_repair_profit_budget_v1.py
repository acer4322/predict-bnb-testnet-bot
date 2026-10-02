"""Freeze exactly two native arms after the V17 observational response audit."""
import ast
import json
import shutil
from prepare_btc5m_oracle_repair_panel_v1 import ROOT,sha,once
from btc5m_repair_profit_budget_v1 import self_test

R=ROOT/'data/research'
PARENT=ROOT/'.lan_worker_v1/whole_oracle_repair_2026085_20260913_v1'
PACKAGE=ROOT/'.lan_worker_v1/repair_profit_budget_2026085_20260913_v1'
STEM='BTC5M_REPAIR_PROFIT_BUDGET_V1_20260913'


def main():
    self_test();prior=json.loads((PARENT/'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(PARENT/n)==h for n,h in prior['files'].items())
    source=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    source=once(source,"ap.add_argument('--repair-route'","ap.add_argument('--retention',type=float,choices=(0.,.5),required=True)\n    ap.add_argument('--repair-route'")
    source=once(source,"demand.self_test()","demand.self_test()\n    budget=load('profit_budget',package/'cash_budget.py');budget.self_test();budget_class=budget.make_capacity(demand.WholeCapacity)")
    source=once(source,'demand.WholeCapacity(selection,mode,args.demand_mode)','budget_class(selection,mode,args.demand_mode,args.retention)')
    source=once(source,'demand_final=producer.demand.final)','demand_final=producer.demand.final,profit_budget_rows=producer.money_gate.budget_rows)')
    source=once(source,"demand_mode=args.demand_mode, demand_visited=","demand_mode=args.demand_mode, profit_retention=args.retention, demand_visited=")
    ast.parse(source)
    assert not PACKAGE.exists(),'immutable package already exists'
    PACKAGE.mkdir()
    for name in prior['files']:
        if name!='money_runner.py':shutil.copy2(PARENT/name,PACKAGE/name)
    shutil.copy2(ROOT/'tools/btc5m_repair_profit_budget_v1.py',PACKAGE/'cash_budget.py')
    (PACKAGE/'money_runner.py').write_text(source,encoding='utf-8')
    m={k:prior[k] for k in ('backend','native_sha256','remote_inputs','fixed_train_total_frames','selection','demand_selection','oracle_source_sha256')}
    m.update(version=STEM,market=2026085,oracle_direction='UP',runtime_eligible=False,max_threads=4,sequential_jobs=1,
        maximum_native_jobs=2,model_fits=0,parameter_search=False,
        parent_manifest_sha256=sha(PARENT/'manifest.json'),
        motivation='V17 observed Target response permits temporary loss and retains favorable payoff. Separate acquisition cost audit suggests a repair cash budget deserves a bounded diagnostic.',
        selection_scope='Exploratory mechanism chosen after consumed Target/OWN observation. Half retention is a simple diagnostic condition, not an estimated Target rule and not held-out confirmation.',
        modes=dict(control=dict(demand='AUTO_REPAIR',retention=0.,meaning='Budget disabled; V16 repeat exact parity required.'),
                   half=dict(demand='AUTO_REPAIR',retention=.5,meaning='Keep half of confirmed UP acquisition gross as conditional UP payoff, including DOWN pending cost.')),
        formula='G=confirmed_UP_qty-paid_UP_cash; room=max(0,(1-retention)*G-paid_DOWN_cash-reserved_DOWN_cash); new_DOWN=min(old_quantity_cap,floor_to_step(room/price)).',
        constraints=['Only confirmed UP acquisitions generate gross credit; unfilled UP creates none.',
            'DOWN cash reservations include cancellation-pending and all current draft operations; terminal release only.',
            'Same UP candidate, DOWN quote, scheduler, maintenance, finite work initiation/end, fees, sizing and legality.',
            'No forced cancellation or market-end fill, no Active, no Target quantity, timing or payoff supplied to actor.',
            'Half retention limits favorable-payoff spending; it does not itself bound weak-side loss.'],
        evaluation=['Exact disabled-control parity before half arm.',
            'Full native/receipt/accounting/pending gates, verify budget in actual states and draft admission.',
            'Keep worst peak as descriptive; report subsequent repair, directional payoff/net, weak loss and both activity sides.',
            'Unfinished finite work is an outcome to preserve, not grounds for forced completion or another run.',
            'No further retention sweep or extra native jobs in this experiment.'],
        files={p.name:sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE/'manifest.json',R/(STEM+'_PREREGISTERED.json')):p.write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    for tag,retention in (('control',0.),('half',.5)):
        wave=dict(progress_artifact=f'data/research/REPAIR_PROFIT_BUDGET_{tag.upper()}_PROGRESS_20260913.json',jobs=[dict(
            job_id=f'repair-profit-budget-2026085-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention',str(retention)],
            cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/f'repair_profit_budget_{tag}_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='FROZEN',files=len(m['files']),jobs=2,manifest_sha256=sha(PACKAGE/'manifest.json'))))


if __name__=='__main__':main()
