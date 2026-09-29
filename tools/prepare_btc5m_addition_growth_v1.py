"""Freeze one V32 direction-growth treatment against the completed V31 path."""
import json
import math
import shutil
from prepare_btc5m_held_amplitude_v1 import ROOT,R,START,read,sha,once,compile_source,get,RET,dump_for,load
import btc5m_addition_growth_v1 as growth

PARENT=ROOT/'.lan_worker_v1/fixed15_held_amplitude_2026085_20260913_v1'
BASE=RET/'fixed15-core-loop-2026085-held-amplitude-20260913-v1'
PACKAGE=ROOT/'.lan_worker_v1/fixed15_addition_growth_2026085_20260913_v1'
JOB='fixed15-core-loop-2026085-addition-growth-20260913-v1'
STEM='BTC5M_ADDITION_GROWTH_V1_20260913'
WAVE='addition_growth_wave_20260913.json'


def dump(suffix,obj):return dump_for(STEM,suffix,obj)


def observe(b,bt):
    obj=growth.AdditionGrowth();intent={r['t']:r for r in bt['intent']};observations={r['t']:r for r in bt['observations']}
    for t,row in observations.items():
        own=intent[t];obj.update(t,own['inv'],own['cost'],row['desired'])
    mapped={r['t']:r for r in obj.rows};demand={r['t']:r for r in bt['demand_rows']}
    threshold=15/(1+math.exp(-b['theta'][11]));witness=None
    for p in bt['plans']:
        if p['t'] not in mapped:continue
        row=mapped[p['t']];s=demand[p['t']]['state'];owned=s['inv']['UP']+s['pending_qty']['UP']
        for o in p['operations']:
            if o['kind']=='NEW' and o['side']=='UP' and row['effective_desired']['UP']-owned<15-1e-8:
                witness=dict(t=p['t'],seconds=(p['t']-START)/1000,baseline_new=o,own_state=s,growth=row,
                    baseline_free_desired=row['original_desired']['UP']-owned,new_free_desired=row['effective_desired']['UP']-owned)
                break
        if witness:break
    assert witness is not None,'No full-ticket decision witness; do not dispatch'
    for cut in (10,100,500,len(obj.rows)):
        q=growth.AdditionGrowth()
        for row in obj.rows[:cut]:assert q.update(row['t'],row['inv'],row['cost'],row['original_desired'])==row['effective_desired']
    return dict(status='PASS',anchor=obj.anchor,first_full_ticket_witness=witness,rows=obj.rows,
        selected_before_native=True,scope='Baseline-state executable deficit witness, not prediction of actual earliest fork. Only actual native can establish the changed path.')


def main():
    assert not PACKAGE.exists(),'Existing frozen package: never rebuild'
    parent=read(PARENT/'manifest.json');assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert read(R/'BTC5M_HELD_AMPLITUDE_V1_20260913_RESULT.json')['verification']=='PASS'
    b,bt=get(BASE);obs=observe(b,bt);checked=growth.self_test();dump('OBSERVATION',obs);dump('COMPONENT',checked)
    runner=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    marker="    source=maintenance_scope.instrument(source,replace)"
    runner=once(runner,marker,marker+"\n    addition_growth=load('addition_growth',package/'addition_growth.py');addition_growth.self_test()\n    source=addition_growth.instrument(source,replace)")
    runner=once(runner,'_ExposureIntent=ExposureIntent,','_ExposureIntent=ExposureIntent, _AdditionGrowth=addition_growth.AdditionGrowth,')
    runner=once(runner,'payload = dict(states=trace.states,','payload = dict(addition_growth_rows=producer.addition_growth.rows, states=trace.states,')
    PACKAGE.mkdir()
    for name in parent['files']:
        if name=='money_runner.py':(PACKAGE/name).write_text(runner,encoding='utf-8')
        else:shutil.copy2(PARENT/name,PACKAGE/name)
    shutil.copy2(ROOT/'tools/btc5m_addition_growth_v1.py',PACKAGE/'addition_growth.py')
    m=dict(parent);m.update(version=STEM,parent_manifest_sha256=sha(PARENT/'manifest.json'),
        existing_control=str(BASE.relative_to(ROOT)),baseline_result_sha256=sha(BASE/'result.json'),baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),
        observation_sha256=sha(R/(STEM+'_OBSERVATION.json')),maximum_native_jobs=1,panel_maximum_native_jobs=1,
        hypothesis='Keep initial directional demand at first confirmed weak-side acquisition in a losing weak branch; scale only subsequent strong-side target growth by current positive strong payoff / (positive strong payoff + weak loss).',
        rule='A=original desired.UP at first confirmed DOWN>0, UP>DOWN, P_DOWN<0. f=1 when weak loss=0, otherwise max(P_UP,0)/(max(P_UP,0)+max(-P_DOWN,0)). effectiveUP=min(originalUP,A+f*max(originalUP-A,0)). effectiveDOWN=originalDOWN.',
        interpretation='Soft current-state growth feedback, no repair-credit bank or hard prior-gain spending cap. Preserves initial demand anchor; may still retain too much or too little exposure. Hypothesis only, not Target identification.',
        unchanged='17 parent modules byte-identical. Held direction/amplitude, DOWN desired and all repair functions unchanged. UP effective target affects NEW deficit and surplus maintenance; resulting later OWN states and both routes may differ.',
        dedup='V9 stopped all UP NEW. V10/V11 found prior-repair-gain-only budgets inadequate. V14/V18 constrained DOWN cash and were later disabled. V31 held amplitude only. New: causal first-repair desired anchor and soft monetary scaling of subsequent UP target growth only. V21 p*=G/(G+L) already known as repair-price geometry; reuse is explicit, not a new formula discovery.',
        evaluation=['First actual plan fork with same execution and OWN prefix; every growth observation, anchor and unchanged DOWN allocation',
            'All raw/canonical accounting, finite demand work items, Active and commitment pure decisions, pending and NEW legality',
            'UP acquisition and peak-response cost, weak recovery, direction retention, activity and path shape; no score-only promotion'],
        limitations='Single consumed oracle-UP market, no parameter search. Cash gates remain OFF. Cross-market known-direction and no-direction evaluation is separately preregistered and follows this adjustment.',
        files={f.name:sha(f) for f in PACKAGE.glob('*.py')})
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    transformed=compile_source(PACKAGE);baseline=compile_source(PARENT)
    for a,z in [(str(PACKAGE).replace('\\','\\\\'),str(PARENT).replace('\\','\\\\')),(str(PACKAGE),str(PARENT)),(PACKAGE.as_posix(),PARENT.as_posix())]:transformed=transformed.replace(a,z)
    transformed=once(transformed,';self.addition_growth=_AdditionGrowth()','')
    transformed=once(transformed,'    desired=self.addition_growth.apply(f,desired)\n','')
    assert transformed==baseline
    assert sum(sha(PACKAGE/n)==h for n,h in parent['files'].items())==17
    dump('LOCAL_PREFLIGHT',dict(status='PASS',unchanged_parent_modules=17,only_two_source_hooks=True,local_native_jobs=0))
    dump('PREREGISTERED',m)
    wave=dict(progress_artifact='data/research/'+STEM+'_PROGRESS.json',jobs=[dict(job_id=JOB,
        argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--mode','ORACLE_UP',
            '--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'],
        cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
    (R/WAVE).write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    dump('PROGRESS',dict(status='FROZEN',job_id=JOB,model_fits=0,maximum_native_jobs=1))
    print(json.dumps(dict(status='FROZEN',job_id=JOB,manifest_sha256=sha(PACKAGE/'manifest.json'),
        observation_witness_seconds=obs['first_full_ticket_witness']['seconds'])))


if __name__=='__main__':main()
