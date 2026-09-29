"""V31: one first-confirmed-OWN amplitude treatment, no fits or local native."""
import json
import shutil
from prepare_btc5m_repair_maintenance_scope_v1 import ROOT,R,START,read,sha,once,compile_source,get,RET,dump_for,load

PARENT=ROOT/'.lan_worker_v1/fixed15_maintenance_legal_2026085_20260913_v1'
BASE=RET/'fixed15-core-loop-2026085-maintenance-legal-20260913-v1'
PACKAGE=ROOT/'.lan_worker_v1/fixed15_held_amplitude_2026085_20260913_v1'
JOB='fixed15-core-loop-2026085-held-amplitude-20260913-v1'
STEM='BTC5M_HELD_AMPLITUDE_V1_20260913'
WAVE='held_amplitude_wave_20260913.json'


def dump(suffix,obj):return dump_for(STEM,suffix,obj)


def patched_runner():
    source=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    source=once(source,'self.mode = mode; self.sign = 0; self.birth = None; self.rows = []',
        'self.mode = mode; self.sign = 0; self.birth = None; self.rows = []\n        self.held_amplitude = None; self.amplitude_birth = None; self.amplitude_rows = []')
    source=once(source,'        return self.sign * abs(legacy)', '''        inv = frame['own_view']['inv']
        # Confirmed inventory only. Pending orders cannot create this reference.
        if self.held_amplitude is None and abs(inv['UP']-inv['DOWN']) > 1e-8:
            self.held_amplitude = abs(legacy)
            self.amplitude_birth = dict(t=int(frame['t']), index=int(frame['index']),
                inv=dict(inv), amplitude=self.held_amplitude, provenance='FIRST_CONFIRMED_OWN_NET')
        amplitude = abs(legacy) if self.held_amplitude is None else self.held_amplitude
        applied = self.sign * amplitude
        self.amplitude_rows.append(dict(t=int(frame['t']), index=int(frame['index']), inv=dict(inv),
            current_amplitude=abs(legacy), held_amplitude=self.held_amplitude,
            applied_exposure=applied, amplitude_birth=self.amplitude_birth))
        return applied''')
    a=source.index('def self_test():');b=source.index('\n\ndef main():',a)
    source=source[:a]+'''def self_test():
    def frame(t,u,d):return dict(t=t,index=t,own_view=dict(inv=dict(UP=u,DOWN=d)),pending_qty=dict(UP=100.,DOWN=0.))
    for mode in MODES:
        sign=1 if mode=='ORACLE_UP' else -1
        c=ExposureIntent(mode)
        assert c.apply(.2,99.,frame(1,0.,0.))==sign*.2 and c.held_amplitude is None
        assert c.apply(-.6,1.,frame(2,10.,0.))==sign*.6
        for t,u,d in [(3,10.,9.),(4,10.,10.),(5,10.,20.)]:
            assert c.apply(.01,0.,frame(t,u,d))==sign*.6
        assert c.amplitude_birth['t']==2 and c.amplitude_birth['inv']==dict(UP=10.,DOWN=0.)
        fresh=ExposureIntent(mode)
        assert fresh.apply(.01,0.,frame(6,10.,20.))==sign*.01

'''+source[b:]
    source=once(source,'observations=producer.clock_observations, intent=producer.intent.rows,',
        'observations=producer.clock_observations, intent=producer.intent.rows, exposure_amplitude_rows=producer.intent.amplitude_rows,')
    source=once(source,"first_direction_birth=producer.intent.birth, controller_rows=", 
        "first_direction_birth=producer.intent.birth, amplitude_mode='FIRST_CONFIRMED_OWN_HELD', amplitude_birth=producer.intent.amplitude_birth, held_amplitude=producer.intent.held_amplitude, controller_rows=")
    return source


def component(source,trace):
    ns={'__name__':'component_only'};exec(compile(source[:source.index('def main():')],'held_intent_component','exec'),ns)
    ns['self_test']();rows=trace['intent'];c=ns['ExposureIntent']('ORACLE_UP');outputs=[]
    for row in rows:
        f=dict(t=row['t'],index=row['index'],own_view=dict(inv=row['inv']))
        value=c.apply(row['legacy_exposure'],0.,f);outputs.append(value)
    first=next(r for r in rows if abs(r['inv']['UP']-r['inv']['DOWN'])>1e-8)
    held=abs(first['legacy_exposure']);assert all(abs(x-held)<1e-12 for r,x in zip(rows,outputs) if r['t']>=first['t'])
    for cut in (10,100,500,len(rows)):
        q=ns['ExposureIntent']('ORACLE_UP')
        assert [q.apply(r['legacy_exposure'],0.,dict(t=r['t'],index=r['index'],own_view=dict(inv=r['inv']))) for r in rows[:cut]]==outputs[:cut]
    demand={r['t']:r for r in trace['demand_rows']};obs={r['t']:r for r in trace['observations']}
    gate=load('held_capacity_component',PARENT/'demand_gate.py')
    t=START+8616;state=demand[t]['state'];gross=sum(obs[t]['desired'].values())
    desired=dict(UP=gross*(1+held)/2,DOWN=gross*(1-held)/2)
    capacity=gate.economic_capacity(state,desired,demand[t]['eligibility']['price'],15.,.01)
    assert capacity['economically_eligible'] and capacity['original_request']<15
    goal=gate.old.FiniteGoal(state,15.);effective=goal.desired(desired)
    assert abs(effective['DOWN']-state['inv']['DOWN']-state['pending_qty']['DOWN']-15)<1e-7
    pending_cancel=sum(o['qty'] for o in state['owners'] if o['side']=='DOWN' and o['state']=='CANCEL_PENDING')
    assert pending_cancel>0 and state['pending_qty']['DOWN']>=pending_cancel
    t2=START+22781;row=next(r for r in trace['demand_maintenance_rows'] if r['t']==t2 and r['key']=='UP_115')
    held_up=sum(obs[t2]['desired'].values())*(1+held)/2
    assert row['surplus'] and not row['stale'] and row['cancellable'] and held_up>demand[t2]['state']['inv']['UP']
    return dict(status='PASS',self_test='pending-only cannot latch; repaired, flat and reversed states retain history; side symmetry; new episode resets',
        baseline_intent_rows=len(rows),prefix_checks=4,amplitude_birth=first,held_amplitude=held,
        finite_goal_witness=dict(seconds=8.616,capacity=capacity,pending_cancel_down=pending_cancel,effective_desired=effective,
            conclusion='Finite goal can restore a full 15-share repair despite smaller original allocation. Not a predicted order veto.'),
        maintenance_witness=dict(seconds=22.781,baseline_row=row,held_up_desired=held_up,confirmed_up=demand[t2]['state']['inv']['UP'],
            conclusion='On this baseline state, UP surplus cancellation can become KEEP; finite DOWN goals do not override UP. Actual first plan fork must be measured in native.'),
        local_native_jobs=0)


def main():
    assert not PACKAGE.exists(),'Frozen package exists; do not rebuild'
    parent=read(PARENT/'manifest.json');assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert read(R/'BTC5M_MAINTENANCE_LEGAL_V1_20260913_RESULT.json')['verification']=='PASS'
    result,trace=get(BASE);source=patched_runner();checked=component(source,trace);dump('COMPONENT',checked)
    PACKAGE.mkdir()
    for name in parent['files']:
        if name=='money_runner.py':(PACKAGE/name).write_text(source,encoding='utf-8')
        else:shutil.copy2(PARENT/name,PACKAGE/name)
    m=dict(parent)
    m.update(version=STEM,parent_manifest_sha256=sha(PARENT/'manifest.json'),existing_control=str(BASE.relative_to(ROOT)),
        baseline_result_sha256=sha(BASE/'result.json'),baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),
        observation_sha256=sha(R/(STEM+'_COMPONENT.json')),maximum_native_jobs=1,panel_maximum_native_jobs=1,
        hypothesis='Hold abs(legacy exposure) from first confirmed nonzero OWN inventory for this episode; prevent successful repair from mechanically erasing directional demand.',
        amplitude_reference='First nonzero confirmed OUR net only; no hard-coded time, amplitude or Target inventory. Existing authorized oracle UP sign unchanged.',
        active_quantity='Same first and additional Active decision functions; quantities, timing and even occurrence may change endogenously.',
        unchanged='17 parent Python modules byte-identical; only ExposureIntent amplitude state and its telemetry/self-test change. Finite goals, quotes, pending lifecycle, repair and Active rules remain frozen.',
        evaluation=['All actual intent decisions from confirmed OWN prefixes; first actual plan divergence and pre-fork execution identity',
            'Canonical receipts, fixed15 passive/min1, cash gates OFF and pending reservations; inherited Active, repair and maintenance decisions',
            'Directional retention, concurrent additions/repair, phase cash flows and later response to worst temporary loss'],
        dedup='V6 held sign only. V13/V16 diagnosed amplitude collapse; V30 ran a baseline-state shadow only. First native held-amplitude treatment on completed V27 LEGAL control.',
        limitations='One consumed oracle-direction market. Mechanism contrast, no model fit, parameter search, held-out evidence or deployable Target identification.',
        files={f.name:sha(f) for f in PACKAGE.glob('*.py')})
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    transformed=compile_source(PACKAGE);baseline=compile_source(PARENT)
    for a,b in [(str(PACKAGE).replace('\\','\\\\'),str(PARENT).replace('\\','\\\\')),(str(PACKAGE),str(PARENT)),(PACKAGE.as_posix(),PARENT.as_posix())]:transformed=transformed.replace(a,b)
    assert transformed==baseline,'Unexpected generated policy change'
    assert sum(sha(PACKAGE/n)==h for n,h in parent['files'].items())==17
    dump('LOCAL_PREFLIGHT',dict(status='PASS',unchanged_parent_modules=17,generated_source_unchanged=True,local_native_jobs=0))
    dump('PREREGISTERED',m)
    wave=dict(progress_artifact='data/research/'+STEM+'_PROGRESS.json',jobs=[dict(job_id=JOB,
        argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--mode','ORACLE_UP',
            '--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'],
        cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
    (R/WAVE).write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    dump('PROGRESS',dict(status='FROZEN',job_id=JOB,maximum_native_jobs=1,local_native_jobs=0,model_fits=0,parameter_search=0))
    print(json.dumps(dict(status='FROZEN',job_id=JOB,manifest_sha256=sha(PACKAGE/'manifest.json'),component=checked['status'])))


if __name__=='__main__':main()
