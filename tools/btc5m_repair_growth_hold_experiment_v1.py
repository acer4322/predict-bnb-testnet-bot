"""V41: one held-growth intervention; reuse V40 receipts and worker machinery."""
import argparse
from collections import Counter
from copy import deepcopy
import inspect
import json
import shutil
import sys

import prepare_btc5m_transfer_structural_v1 as parent
from prepare_btc5m_transfer_structural_v1 import ROOT,R,read,sha,load,once
from verify_btc5m_transfer_components_v1 import same

STEM='BTC5M_REPAIR_GROWTH_HOLD_V1_20260913'
BASE=ROOT/'.lan_worker_v1/renewed_repair_work_1977248_20260913_v1'
PACKAGE=ROOT/'.lan_worker_v1/repair_growth_hold_1977248_20260913_v1'
BASEJOB='fixed15-core-loop-1977248-renewed-repair-work-20260913-v1'
BASEAUDIT=R/'BTC5M_RENEWED_REPAIR_WORK_V1_20260913_1977248_known_AUDIT.json'


def dump(tag,value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def jobs():
    return [dict(job_id='fixed15-core-loop-1977248-repair-growth-hold-20260913-v1',market=1977248,arm='known',mode='ORACLE_DOWN',cwd='.',max_threads=4,
        argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--market-id','1977248',
            '--mode','ORACLE_DOWN','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR',
            '--retention','0','--opportunity-mode','ONE_ACTIVE'])]


def worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=PACKAGE;w.dump=dump;w.jobs=jobs
    return w


def modules():
    sys.path.insert(0,str(PACKAGE))
    from roles_runtime import roles
    return roles,load('v41_growth',PACKAGE/'addition_growth.py'),load('v41_hold',PACKAGE/'growth_hold.py')


def prepare():
    assert not PACKAGE.exists() and not (R/(STEM+'_PROTOCOL.json')).exists()
    bm=read(BASE/'manifest.json')
    assert sha(BASE/'manifest.json')=='a01d6f2f630d3d8bb6463576bd1e60a7dc211c397481eceb2c244a4e536141e8'
    assert all(sha(BASE/k)==v for k,v in bm['files'].items())
    protocol=dict(status='PREREGISTERED_BEFORE_COMPONENT_AND_NATIVE',baseline_job=BASEJOB,market=1977248,mode='ORACLE_DOWN',
        hypothesis='Holding the growth coefficient at its pre-renewal-service ceiling while that monetary work remains unfinished will retain more of the repair improvement and reduce subsequent strong acquisition.',
        dedup='V9 stopped strong NEW; V10 used repair gains as a cash budget; V19 observed repair-dependent timing without isolating it; V32 scales strong demand growth by current gain/(gain+loss); V40 creates new monetary works and diagnoses coefficient release after repair. This new intervention caps ONLY the V32 growth coefficient after the V40 additional service, while preserving raw demand growth, risk tightening and all repair authority. No prior held-coefficient native found in handoffs/protocols/tools.',
        rule='At V40 renewed Active NEW, remember the current OUR growth coefficient and work anchor. In later frames use min(current coefficient, remembered ceiling). Release after confirmed weak payoff reaches the anchor AND the service owner is canonical TERMINAL, or after terminal zero-fill. Fully-filled nonterminal, UNKNOWN or missing owner cannot release. Do not cancel or ignore pending reservations.',
        causal_scope='The ceiling holds all coefficient increases after service arming, including later Passive improvement. It does not claim to subtract only the selected Active fill contribution. Raw desired may still grow and current risk may lower the coefficient.',
        controls=dict(passive_new_shares=15,active_shares='variable',minimum_new_notional=1,cash_gates=False,
            capital_cap=None,active_cash_cap=None,max_active_submits=4,theta=bm['theta'],fixed_qref=bm['fixed_qref'],
            weak_desired_unchanged_at_same_state=True,original_growth_formula_unchanged=True,new_parameter_search=False),
        direction='Final observed Target net side bit only; no winner or Target price, clock, quantity or payoff in actor.',
        evaluation=['Both settlement-conditional payoffs and raw cost','Canonical repair flow equality or explicitly report differences',
            'Strong pending/NEW/actual acquisition after service','Monetary goal gap and floor-loss area',
            'Source-index exact prefix, original three services and renewed service receipts',
            'Reconstruct every growth hold row plus original complete execution audit'],
        local_checks=['Side symmetry','No-arm parity','Partial/full/nonterminal/unknown lifecycle','Zero-fill release',
            'Negative/zero/positive goals','Raw growth allowed and worsening risk tighter','Prefix invariance',
            'Saved V40 first changed desired without simulating subsequent fills'],
        maximum_native_jobs=1,max_threads=4,local_native_jobs=0,model_fits=0,parameter_search=0,charts=0,
        selection='Consumed outcome-selected development case, not unseen validation or learned Target controller',
        failure_policy='One submit; preserve failure and UNKNOWN, never auto-resubmit.')
    dump('PROTOCOL',protocol);PACKAGE.mkdir()
    for name in bm['files']:
        p=PACKAGE/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,p)
    shutil.copy2(ROOT/'tools/btc5m_repair_growth_hold_v1.py',PACKAGE/'growth_hold.py')
    p=PACKAGE/'renewed_work.py';s=p.read_text(encoding='utf-8')
    s=once(s,"                submission=dict(t=int(frame['t']),**op)",
        "                producer.growth_hold.arm(frame,producer,status['work'],op)\n                submission=dict(t=int(frame['t']),**op)")
    p.write_text(s,encoding='utf-8')
    p=PACKAGE/'money_runner.py';s=p.read_text(encoding='utf-8')
    s=once(s,'    source=addition_growth.instrument(source,replace)',
        "    source=addition_growth.instrument(source,replace)\n    growth_hold=load('growth_hold',package/'growth_hold.py')\n    source=growth_hold.instrument(source,replace)")
    s=once(s,'_AdditionGrowth=addition_growth.AdditionGrowth,','_AdditionGrowth=addition_growth.AdditionGrowth, _GrowthHold=growth_hold.GrowthHold,')
    s=once(s,'payload = dict(direction_rows=roles.rows,','payload = dict(growth_hold_rows=producer.growth_hold.rows,growth_hold_arm=producer.growth_hold.armed,growth_hold_events=producer.growth_hold.events,direction_rows=roles.rows,')
    p.write_text(s,encoding='utf-8')
    m=deepcopy(bm);m.update(version=STEM,parent_manifest_sha256=sha(BASE/'manifest.json'),selection=protocol['hypothesis'])
    m['files']={p.relative_to(PACKAGE).as_posix():sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    changed=[n for n,h in bm['files'].items() if m['files'][n]!=h];assert sorted(changed)==['money_runner.py','renewed_work.py']
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    dump('WAVE',dict(jobs=jobs(),max_threads=4))
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(parent.compiled(PACKAGE,mode),'V41_COMPILE_ONLY','exec')
    out=components();out.update(status='PASS',changed_files=changed,inherited_unchanged=len(bm['files'])-2,
        total_files=len(m['files']),manifest_sha256=sha(PACKAGE/'manifest.json'),three_mode_compile=True)
    dump('COMPONENT',out)
    # Reuse the already validated V39 prefix of the unchanged V40 repair service.
    dump('PREFIX_DIAGNOSTIC',read(R/'BTC5M_RENEWED_REPAIR_WORK_V1_20260913_PREFIX_DIAGNOSTIC.json'))
    dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',native_submissions=0))
    return out


def components():
    roles,growth,hold=modules();counts=Counter()
    for side in ('UP','DOWN'):
        roles.configure('KNOWN_FINAL_DIRECTION',side);weak=roles.weak
        for goal in (-20.,0.,20.):
            arm=dict(ceiling=.4,goal=goal)
            for weight in (.1,.4,.8,1.):
                for raw in (50.,100.,200.,500.):
                    g=dict(strong=side,weight=weight,anchor=dict(original_strong_desired=100.),original_desired={side:raw,weak:70.},
                        effective_desired={side:min(raw,100+weight*max(0.,raw-100)),weak:70.})
                    same(hold.decide(g,None,None,goal-1)['effective_desired'],g['effective_desired']);counts['unarmed_parity']+=1
                    for state in (None,'SUBMITTED','CANCEL_PENDING','UNKNOWN','TERMINAL'):
                        for fill in (0.,12.,24.):
                            receipt=None if state is None else dict(state=state,filled=fill)
                            for payoff in (goal-1,goal,goal+1):
                                d=hold.decide(g,arm,receipt,payoff)
                                release=state=='TERMINAL' and (fill==0 or payoff>=goal)
                                assert d['released']==release
                                same(d['applied_weight'],weight if release else min(weight,.4))
                                assert d['effective_desired'][weak]==70.
                                if not release:assert d['effective_desired'][side]>=min(raw,100.)
                                counts['finite_goal_lifecycle_decisions']+=1
                    assert hold.decide(g,arm,None,goal-1,released=True)['released'];counts['latched_release']+=1
    prefix=saved_prefix();dump('HOLD_PREFIX_DIAGNOSTIC',prefix)
    assert prefix['first_changed_desired'] is not None
    return dict(checks=dict(counts),first_changed_desired=prefix['first_changed_desired'],native_jobs=0,model_fits=0)


def saved_prefix():
    roles,growth,hold=modules();roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    tr=read(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz')
    n=read(R/'lan_worker_returns'/BASEJOB/'result.json')
    sub=tr['renewal_submissions'][0];owner=next(o for o in read(BASEAUDIT)['receipt_accounting']['orders'] if o['key']==sub['key'])
    arm_index=next(i for i,(d,p) in enumerate(zip(tr['direction_rows'],tr['plans'])) if any(o.get('key')==sub['key'] and o['kind']=='NEW' for o in p['operations']))
    armed_source_index=tr['direction_rows'][arm_index]['index']
    at=next(g for i,g in zip(tr['intent'],tr['addition_growth_rows']) if i['index']==armed_source_index)
    arm=dict(key=sub['key'],work_id=sub['work_id'],goal=sub['anchor'],ceiling=at['weight'],t=sub['t'],index=armed_source_index)
    first=None
    for intent,g,demand in zip(tr['intent'],tr['addition_growth_rows'],tr['demand_rows']):
        if intent['index']<=armed_source_index:continue
        # Reconstruct only already canonical receipts; no final filled quantity before its clock.
        filled=sum(f['fill_increment'] for e in n['atomic_responsibility_events'] if e['t']<=intent['t'] for f in e['fill_rows'] if f['key']==sub['key'])
        state='TERMINAL' if intent['t']>=owner['terminal_observed_t'] else next((o['state'] for o in demand['state']['owners'] if o['key']==sub['key']),None)
        receipt=dict(state=state,filled=filled) if state is not None else None
        d=hold.decide(g,arm,receipt,g['inv']['UP']-g['cost'])
        if d['effective_desired']!=g['effective_desired']:
            first=dict(index=intent['index'],t=intent['t'],seconds=(intent['t']-1788634800000)/1000,
                input_desired=g['effective_desired'],decision=d,arm=arm)
            break
    return dict(status='PASS',first_changed_desired=first,baseline_trace_sha256=sha(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz'),
        boundary='Saved states only, stop at first changed desired. No claim that a NEW plan differs immediately or that later baseline fills remain valid.')


def audit():
    w=worker();j=jobs()[0]
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=STEM;v.PACKAGE=PACKAGE;v.dump=dump;v.jobs=lambda:[None,j]
    tr=read(R/'lan_worker_returns'/j['job_id']/'clock_trace.json.gz')
    code=once(inspect.getsource(v.inspect_job),"len(tr['coordination_submissions'])<=2","len(tr['coordination_submissions'])<=4")
    code=once(code,"same(effective,obs['desired'])", "hold=hold_by_index[row['index']];same(effective,hold['input_desired']);effective=hold['effective_desired'];same(effective,obs['desired'])")
    ns=dict(v.__dict__,hold_by_index={r['index']:r for r in tr['growth_hold_rows']});exec(compile(code,'V41_FULL_STRUCTURAL_AUDIT','exec'),ns);out=ns['inspect_job'](1)
    if out['execution_status']!='PASS':return out
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.STEM=STEM;cv.PACKAGE=PACKAGE;cv.jobs=lambda:[None,j];cv.dump=lambda tag,value:w.save(j,tag,value)
    code=once(inspect.getsource(cv.main),'episode,confirmed,crossing_owners)',"episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns=dict(cv.__dict__);exec(compile(code,'V41_FULL_CONTINUATION_AUDIT','exec'),ns);ns['main']()
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','preflight','submit','status','collect','audit'));a=p.parse_args()
    if a.action in ('prepare','audit'):out=globals()[a.action]()
    elif a.action=='preflight':out=worker().preflight()
    elif a.action=='status':
        w=worker();out=w.dispatch.cmd_status(w.HOST,jobs()[0]['job_id'])
    else:out=getattr(worker(),a.action)(0)
    print(json.dumps(out,allow_nan=False))
