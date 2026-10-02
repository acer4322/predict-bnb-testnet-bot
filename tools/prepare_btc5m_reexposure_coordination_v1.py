"""Freeze two repair degrees at one causal reexposure event; no host HFT."""
import json
import shutil
from types import SimpleNamespace
from prepare_btc5m_repair_constraints_v1 import ROOT,R,START,read,sha,once,compile_source,get,RET,dump_for,load
from hft244_pair_route_legality_v1 import crossing_owners
import btc5m_reexposure_coordination_v1 as probe

PARENT=ROOT/'.lan_worker_v1/fixed15_repair_both_2026085_20260913_v1'
BASE=RET/'fixed15-core-loop-2026085-repair-both-20260913-v1'
ARMS=dict(minimum='MINIMUM',restore='RESTORE')


def config(arm):
    assert arm in ARMS
    return dict(PACKAGE=ROOT/f'.lan_worker_v1/fixed15_reexposure_{arm}_2026085_20260913_v1',
        JOB=f'fixed15-core-loop-2026085-reexposure-{arm}-20260913-v1',
        STEM=f'BTC5M_REEXPOSURE_{arm.upper()}_V1_20260913',WAVE=f'reexposure_{arm}_wave_20260913.json')


def observation(trace,source):
    books={b['received_ms']:b for b in source['books']};plans={p['t']:p['operations'] for p in trace['plans']}
    key=trace['opportunity_submissions'][0]['key']
    terminal=min(x['t'] for x in trace['demand_owner_rows'] if x['key']==key and x['state']=='TERMINAL' and x['filled']>0)
    previous=None;seen=False;episode=None;rows=[]
    for demand in trace['demand_rows']:
        t=demand['t'];state=demand['state'];confirmed=t>=terminal
        if episode is None:episode=probe.detect(previous,state,confirmed,seen,t)
        if confirmed and previous and state['inv']['DOWN']>previous['inv']['DOWN']+probe.EPS:seen=True
        previous={k:state[k] for k in ('inv','cost','payoff')};book=books[t];assert book['source_ms']<=t
        row=probe.decide(state,plans[t],round(1-book['best_bid'],10),dict(book['bids'])[book['best_bid']],episode,confirmed,crossing_owners)
        row.update(t=t,seconds=(t-START)/1000,state=state,original_operations=plans[t],book_source_ms=book['source_ms'])
        rows.append(row)
        if row['eligible']:break
    assert rows[-1]['eligible'],'No causal event; do not replay an unchanged arm'
    return dict(status='PASS',episode=episode,first=rows[-1],prefix_rows=rows,active_terminal_t=terminal,
                selection='First observed UP-only fill batch breaks a previously both-positive state after confirmed repair; no event time passed to actor')


def component(obs):
    result=probe.self_test(crossing_owners);first=obs['first'];state=first['state']
    obj=probe.CoordinationProbe(lambda f,l:state);obj.previous=obs['episode']['before'];obj.seen_repair=True
    frame=dict(start=START,end=START+300000,t=first['t'],gateway_state_id='COMPONENT_ONLY',own_view=dict(n=10000),
        ledger=SimpleNamespace(carriers={'old':SimpleNamespace(state='TERMINAL',filled=14.21)}),
        quotes=dict(DOWN=dict(ask=first['active_ask'])),book=dict(bids={round(1-first['active_ask'],10):first['visible_depth']}),
        world_profile=dict(asset='BTC',quantity_step=.01,tick=.01,max_live_owners=4096))
    producer=SimpleNamespace(opportunity=SimpleNamespace(submissions=[dict(key='old')]),demand=SimpleNamespace(rows=[dict(t=first['t'])]))
    def validate(asset,route,price,qty,quantity_step):
        assert route=='ACTIVE' and qty*price>=1-1e-8 and abs(qty/.01-round(qty/.01))<1e-6
    ops=first['original_operations'];before=json.dumps(ops,sort_keys=True)
    new=obj.apply(frame,producer,ops,validate,crossing_owners)
    assert new[:-1]==ops and new[-1]['qty']==first['quantity']
    assert obj.apply(frame,producer,ops,validate,crossing_owners)==ops and len(obj.submissions)==1
    assert before==json.dumps(ops,sort_keys=True)
    result.update(current_event_match=True,one_additional_active=True,original_operations_preserved=True,local_native=0)
    return result


def main():
    parent=read(PARENT/'manifest.json');assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert read(R/'BTC5M_REPAIR_CONSTRAINTS_BOTH_V1_20260913_RESULT.json')['verification']=='PASS'
    assert all(not (config(a)['PACKAGE']/'manifest.json').exists() for a in ARMS),'Frozen panel exists; do not rebuild'
    baseline,trace=get(BASE)
    input_path=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(input_path)==parent['oracle_source_sha256'];source_input=read(input_path)
    parent_source=compile_source(PARENT);arms={}
    for arm,mode in ARMS.items():
        probe.MODE=mode;obs=observation(trace,source_input);checked=component(obs)
        c=config(arm);package=c['PACKAGE'];stem=c['STEM'];job=c['JOB']
        dump_for(stem,'OBSERVATION',obs);dump_for(stem,'COMPONENT',checked)
        module=(ROOT/'tools/btc5m_reexposure_coordination_v1.py').read_text(encoding='utf-8')
        module=once(module,"MODE = 'MINIMUM'",'MODE = '+repr(mode))
        runner=(PARENT/'money_runner.py').read_text(encoding='utf-8')
        runner=once(runner,'    source=commitment_repair.instrument(source,replace)',
            "    source=commitment_repair.instrument(source,replace)\n    coordination=load('reexposure_coordination',package/'coordination.py')\n    source=coordination.instrument(source,replace)")
        runner=once(runner,'_CommitmentRepairProbe=commitment_repair.CommitmentRepairProbe,',
            '_CommitmentRepairProbe=commitment_repair.CommitmentRepairProbe, _CoordinationProbe=coordination.CoordinationProbe,')
        runner=once(runner,'commitment_maintenance_rows=producer.commitment_repair.maintenance_rows,',
            'coordination_rows=producer.coordination.rows,coordination_episode=producer.coordination.episode,coordination_submissions=producer.coordination.submissions,commitment_maintenance_rows=producer.commitment_repair.maintenance_rows,')
        package.mkdir(exist_ok=True)
        for name in parent['files']:
            assert not (package/name).exists()
            if name=='money_runner.py':(package/name).write_text(runner,encoding='utf-8')
            else:shutil.copy2(PARENT/name,package/name)
        (package/'coordination.py').write_text(module,encoding='utf-8')
        m=dict(parent)
        m.update(version=stem,parent_manifest_sha256=sha(PARENT/'manifest.json'),existing_control=str(BASE.relative_to(ROOT)),
            baseline_result_sha256=sha(BASE/'result.json'),baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),
            observation_sha256=sha(R/(stem+'_OBSERVATION.json')),panel_maximum_native_jobs=2,maximum_native_jobs=1,
            coordination_mode=mode,selection='Receipt-triggered first post-repair UP-only fill batch changes both-positive into negative DOWN; one additional Active when legal Passive15 is unavailable.',
            coordination_reference='Previous confirmed min(P_UP,P_DOWN); current filled damage plus still-reserved DOWN supply only. Pending UP is not counted in this recovery target.',
            coordination_size='ceil_to_step(1/current_ask)' if mode=='MINIMUM' else 'floor_to_step(min(pre-addition-floor restoration need, filled UP net minus all pending DOWN, current top depth))',
            unchanged='15 parent modules byte-identical; V25 concurrent repair, legal quote and maintenance unchanged. Original UP producer operations, first Active, theta, sizing, cash OFF, native unchanged. Later endogenous behavior may differ.',
            active_quantity='Original first Active unchanged; additional Active has preregistered MINIMUM or RESTORE variable quantity.',
            evaluation=['Two new same-trigger repair degrees vs reused V25 both, all preregistered before native',
                'Causal episode and pre-intervention full-prefix equality; original Active unchanged',
                'Current depth, price, minimum notional, finite quantity and pending own-cross',
                'Two Active owners separated in raw/canonical receipt and accounting audits',
                'Direct repair vs later UP additions, Passive replacement, return of loss, both branch terminal payoff'],
            limitations='One additional diagnostic intervention, not a learned recurrent controller. Anchor is a causal experimental hypothesis, not Target private objective. Keeping UP policy fixed isolates repair response; no claim all pending orders will fill.',
            files={p.name:sha(p) for p in package.glob('*.py')})
        (package/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
        transformed=compile_source(package)
        for a,b in [(str(package).replace('\\','\\\\'),str(PARENT).replace('\\','\\\\')),(str(package),str(PARENT)),(package.as_posix(),PARENT.as_posix())]:transformed=transformed.replace(a,b)
        transformed=once(transformed,';self.coordination=_CoordinationProbe(_OWN_SNAPSHOT)','')
        transformed=once(transformed,'   ops=producer.coordination.apply(f,producer,ops,validate_size,_goal_crossing)\n','')
        transformed=once(transformed,'  result.update(coordination_episode=producer.coordination.episode,coordination_submissions=producer.coordination.submissions)\n','')
        transformed=once(transformed,"active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)+len(producer.coordination.submissions)<=2 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=1 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))",
            "active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)<=1 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))")
        transformed=once(transformed,'active_births=producer.active_births+producer.opportunity.submissions+producer.coordination.submissions,','active_births=producer.active_births+producer.opportunity.submissions,')
        assert transformed==parent_source,'Undeclared producer change'
        frozen=load('checked_coordination_'+arm,package/'coordination.py');assert frozen.MODE==mode and frozen.self_test(crossing_owners)['status']=='PASS'
        dump_for(stem,'LOCAL_PREFLIGHT',dict(status='PASS',actual_runner_prefix_compile_only=True,unchanged_parent_modules=15,
            exact_source_parity_except_five_declared_hooks=True,local_native=0))
        dump_for(stem,'PREREGISTERED',m)
        wave=dict(progress_artifact='data/research/REEXPOSURE_COORDINATION_PROGRESS_20260913.json',jobs=[dict(job_id=job,
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{package.name}/money_runner.py','--mode','ORACLE_UP',
            '--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'],
            cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/c['WAVE']).write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
        arms[arm]=dict(job_id=job,package=str(package.relative_to(ROOT)),manifest_sha256=sha(package/'manifest.json'),
            first_seconds=obs['first']['seconds'],qty=obs['first']['quantity'],ask=obs['first']['active_ask'],anchor=obs['episode']['anchor_floor'])
    assert arms['minimum']['first_seconds']==arms['restore']['first_seconds']
    panel=dict(status='FROZEN',arms=arms,new_native_jobs_planned=2,local_native_jobs=0,model_fits=0,parameter_search=0)
    dump_for('BTC5M_REEXPOSURE_COORDINATION_PANEL_V1_20260913','PREREGISTERED',panel)
    print(json.dumps(panel))


if __name__=='__main__':main()
