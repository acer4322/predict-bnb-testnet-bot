"""Read collected traces, check a causal demand contrast, pin one worker job."""
import ast
import collections
import json
import shutil
from types import SimpleNamespace

from prepare_btc5m_pending_ticket_probe_v1 import ROOT,R,BASE,START,read,sha,once,compile_source
from aggregate_btc5m_exposure_intent_ablation_v1 import get,RET
from hft244_pair_route_legality_v1 import crossing_owners
import btc5m_commitment_repair_probe_v1 as probe

PARENT=ROOT/'.lan_worker_v1/fixed15_unbudgeted_opportunity_2026085_20260913_v1'
PACKAGE=ROOT/'.lan_worker_v1/fixed15_commitment_repair_2026085_20260913_v1'
JOB='fixed15-core-loop-2026085-commitment-repair-20260913-v1'
STEM='BTC5M_COMMITMENT_REPAIR_V1_20260913'


def dump(suffix,obj):
    (R/(STEM+'_'+suffix+'.json')).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def observation(trace,source):
    books={x['received_ms']:x for x in source['books']}
    plans={x['t']:x['operations'] for x in trace['plans']}
    active=trace['opportunity_submissions'][0]['key']
    terminal=min(x['t'] for x in trace['demand_owner_rows'] if x['key']==active and x['state']=='TERMINAL' and x['filled']>0)
    rows=[]
    for d in trace['demand_rows']:
        t=d['t'];book=books[t];assert book['source_ms']<=t
        row=probe.decide(d['state'],plans[t],d['eligibility']['price'],round(1-book['best_bid'],10),t>=terminal,[],crossing_owners)
        row.update(t=t,seconds=(t-START)/1000,state=d['state'],original_operations=plans[t],book_source_ms=book['source_ms'])
        rows.append(row)
    eligible=[x for x in rows if x['eligible']]
    assert eligible and eligible[0]['old_filled_quantity_capacity']<15
    out=dict(status='PASS',first=eligible[0],first_prefix=[x for x in rows if x['t']<=eligible[0]['t']],
        active_terminal_t=terminal,rows=len(rows),conditional_eligible_baseline_frames=len(eligible),
        eligible_below_old_full_ticket=sum(x['old_filled_quantity_capacity']<15 for x in eligible),
        reason_counts=dict(collections.Counter(x['reason'] for x in rows)),
        limitation='Later baseline opportunities are descriptive only; no simulated fills or predicted submission count. Native feedback and one-outstanding-owner gate decide actual recurrence.',
        selection='First causal event after canonical Active fill; actor uses current ledger and current public book only. No chosen event time is passed to actor.')
    dump('OBSERVATION',out);return out


def component(first):
    result=probe.self_test(crossing_owners)
    state=first['state'];obj=probe.CommitmentRepairProbe(lambda f,l:state)
    ledger=SimpleNamespace(carriers={'active':SimpleNamespace(state='TERMINAL',filled=14.21)})
    producer=SimpleNamespace(opportunity=SimpleNamespace(submissions=[dict(key='active')]),
        demand=SimpleNamespace(rows=[dict(t=first['t'],eligibility=dict(price=first['price']))]),passive_births=0)
    frame=dict(start=START,end=START+300000,t=first['t'],ledger=ledger,gateway_state_id='COMPONENT_ONLY',
        own_view=dict(n=10000),quotes=dict(DOWN=dict(ask=first['ask'])),
        world_profile=dict(asset='BTC',quantity_step=.01,tick=.01,max_live_owners=10000))
    def validate(asset,route,price,qty,quantity_step):
        assert asset=='BTC' and route=='PASSIVE' and qty==15 and price*qty>=1.
    ops=first['original_operations'];before=json.dumps(ops,sort_keys=True)
    a=obj.apply(frame,producer,ops,validate,crossing_owners)
    assert a[:-1]==ops and len(obj.submissions)==1
    key=a[-1]['key']
    # Missing owner, UNKNOWN, partial and cancel-pending must all prevent duplication.
    assert obj.apply(frame,producer,ops,validate,crossing_owners)==ops
    for status in ('UNKNOWN','SUBMITTED','PARTIALLY_FILLED','CANCEL_PENDING'):
        ledger.carriers[key]=SimpleNamespace(state=status,filled=3.)
        assert obj.apply(frame,producer,ops,validate,crossing_owners)==ops
    ledger.carriers[key]=SimpleNamespace(state='TERMINAL',filled=15.)
    frame['own_view']['n']+=1
    b=obj.apply(frame,producer,ops,validate,crossing_owners)
    assert b[:-1]==ops and b[-1]['key']!=key and len(obj.submissions)==producer.passive_births==2
    assert json.dumps(ops,sort_keys=True)==before
    result.update(lifecycle_recurrence='PASS',one_nonterminal_extra_owner=True,
                  original_operations_unchanged=True,local_native=0)
    dump('COMPONENT',result)


def main():
    assert not (PACKAGE/'manifest.json').exists(), 'Frozen experiment exists; inspect status, do not rebuild or resubmit'
    parent=read(PARENT/'manifest.json')
    assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    base,trace=get(BASE);assert base['cash_budget_enabled'] is False
    source_path=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(source_path)==parent['oracle_source_sha256']
    obs=observation(trace,read(source_path));component(obs['first'])
    runner=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    runner=once(runner,'    source=opportunity.instrument(source,replace)',
        "    source=opportunity.instrument(source,replace)\n    commitment_repair=load('commitment_repair_probe',package/'commitment_repair.py')\n    source=commitment_repair.instrument(source,replace)")
    runner=once(runner,'_ActiveOpportunity=opportunity.ActiveOpportunity,',
        '_ActiveOpportunity=opportunity.ActiveOpportunity, _CommitmentRepairProbe=commitment_repair.CommitmentRepairProbe,')
    runner=once(runner,'opportunity_rows=producer.opportunity.rows,',
        'commitment_repair_rows=producer.commitment_repair.rows,commitment_repair_first=producer.commitment_repair.first,commitment_repair_submissions=producer.commitment_repair.submissions,opportunity_rows=producer.opportunity.rows,')
    ast.parse(runner);PACKAGE.mkdir(exist_ok=True)
    for name in parent['files']:
        assert not (PACKAGE/name).exists(),name
        if name=='money_runner.py':(PACKAGE/name).write_text(runner,encoding='utf-8')
        else:shutil.copy2(PARENT/name,PACKAGE/name)
    shutil.copy2(ROOT/'tools/btc5m_commitment_repair_probe_v1.py',PACKAGE/'commitment_repair.py')
    manifest=dict(parent)
    manifest.update(version=STEM,parent_manifest_sha256=sha(PARENT/'manifest.json'),
        existing_control=str(BASE.relative_to(ROOT)),baseline_result_sha256=sha(BASE/'result.json'),
        baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),maximum_native_jobs=1,model_fits=0,parameter_search=0,
        observation_sha256=sha(R/(STEM+'_OBSERVATION.json')),
        selection='Recurrent current-commitment floor repair after existing Active canonical fill; no time trigger; one extra nonterminal owner; original DOWN NEW priority.',
        repair_reference='Current confirmed min(P_UP,P_DOWN), not zero. Debt=max(0,current_floor - conditional_P_DOWN).',
        conditional_scenario='Current SUBMITTED UP excluding same-plan CANCEL, plus same-plan UP NEW, all DOWN reservations and DOWN NEW at limit cost. Cancel-pending DOWN stays reserved. Not a predicted fill path.',
        finite_authority='At most one appended Passive15 per plan and one extra nonterminal owner globally. Reevaluate only after terminal. Each ticket must improve conditional floor and confirmed floor if only that ticket fills. No minimum old filled-only quantity capacity.',
        unchanged='13 parent modules identical; existing Active/quotes/scheduler/maintenance/theta/native/fixed15/cash gates OFF unchanged. Original operations keep priority.',
        hypothesis='Replacing one-shot filled residual with recurrent commitment deterioration may yield incremental filled protection instead of full replacement.',
        evaluation=['Exact full pre-intervention prefix vs V20; unchanged Active',
            'Every pure decision and pending/cancel/UNKNOWN exclusion reconstructed; every extra owner terminal before another',
            'Native receipt/accounting/sizing/legality audit and source/binary/shared-file pins',
            'Economic canonical-flow difference, terminal both-branch payoff and exposure trajectory; no winner-based success',
            'Separate submitted, filled, replacements, maintenance cancellation, and direct vs downstream contributions'],
        limitations='One consumed oracle market. Full current commitment scenario, not probability model; parameter is engineering experiment, not identified Target rule. A full15 ticket may overshoot fractional demand and briefly reverse filled net.',
        files={p.name:sha(p) for p in PACKAGE.glob('*.py')})
    (PACKAGE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    transformed=compile_source(PACKAGE)
    baseline=compile_source(PARENT)
    for a,b in [(str(PACKAGE).replace('\\','\\\\'),str(PARENT).replace('\\','\\\\')),(str(PACKAGE),str(PARENT)),(PACKAGE.as_posix(),PARENT.as_posix())]:
        transformed=transformed.replace(a,b)
    transformed=once(transformed,';self.commitment_repair=_CommitmentRepairProbe(_OWN_SNAPSHOT)','')
    transformed=once(transformed,'   ops=producer.commitment_repair.apply(f,producer,ops,validate_size,_goal_crossing)\n','')
    transformed=once(transformed,'  result.update(commitment_repair_first=producer.commitment_repair.first,commitment_repair_submissions=producer.commitment_repair.submissions)\n','')
    assert transformed==baseline, 'Unexpected original behavior change'
    dump('LOCAL_PREFLIGHT',dict(status='PASS',actual_runner_prefix_compile_only=True,
        parent_source_equal_after_removing_three_hooks=True,unchanged_parent_modules=13,local_native=0))
    dump('PREREGISTERED',manifest)
    wave=dict(progress_artifact='data/research/COMMITMENT_REPAIR_PROGRESS_20260913.json',jobs=[dict(job_id=JOB,
        argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
              '--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR',
              '--retention','0','--opportunity-mode','ONE_ACTIVE'],cwd='.',max_threads=4,min_free_ram_gb=6,
        max_start_cpu_pct=90,required_artifact='result.json')])
    (R/'commitment_repair_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='FROZEN',job=JOB,files=len(manifest['files']),manifest_sha256=sha(PACKAGE/'manifest.json'),
                         first_seconds=obs['first']['seconds'],deterioration=obs['first']['deterioration_debt'],local_native=0)))


if __name__=='__main__':main()
