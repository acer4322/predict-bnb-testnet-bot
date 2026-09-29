"""V42: two bounded continuation arms; one sizing difference, shared release gate."""
import argparse
from collections import Counter
from copy import deepcopy
import inspect
import json
import shutil
import sys
from types import SimpleNamespace

import prepare_btc5m_transfer_structural_v1 as parent
from prepare_btc5m_transfer_structural_v1 import ROOT,R,read,sha,load,once
from verify_btc5m_transfer_components_v1 import same
from hft244_pair_route_legality_v1 import crossing_owners

STEM='BTC5M_PENDING_REPAIR_PAIR_V1_20260913'
BASE=ROOT/'.lan_worker_v1/repair_growth_hold_1977248_20260913_v1'
BASEJOB='fixed15-core-loop-1977248-repair-growth-hold-20260913-v1'
BASEAUDIT=R/'BTC5M_REPAIR_GROWTH_HOLD_V1_20260913_1977248_known_AUDIT.json'
ARMS=('current','pending')


def package(arm):return ROOT/f'.lan_worker_v1/pending_repair_{arm}_1977248_20260913_v1'
def astem(arm):return STEM+'_'+arm.upper()
def dump(tag,value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def adump(arm,tag,value):dump(arm.upper()+'_'+tag,value)


def job(arm):
    return dict(job_id=f'fixed15-core-loop-1977248-pending-repair-{arm}-20260913-v1',market=1977248,arm='known',mode='ORACLE_DOWN',max_threads=4,cwd='.',
        argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{package(arm).name}/money_runner.py','--market-id','1977248',
            '--mode','ORACLE_DOWN','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'])


def worker(arm):
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=astem(arm);w.PACKAGE=package(arm);w.dump=lambda tag,value:adump(arm,tag,value);w.jobs=lambda:[job(arm)]
    return w


def modules(arm):
    sys.path.insert(0,str(package(arm)))
    from roles_runtime import roles
    return roles,load('v42_coord_'+arm,package(arm)/'coordination.py'),load('v42_service_'+arm,package(arm)/'pending_service.py'),load('v42_hold_'+arm,package(arm)/'growth_hold.py')


def prepare():
    assert not (R/(STEM+'_PROTOCOL.json')).exists() and all(not package(a).exists() for a in ARMS)
    bm=read(BASE/'manifest.json');assert sha(BASE/'manifest.json')=='272004e05317f4e809c13f32d8b53fc16c37cc1e538ab2b18e2d0c0bd12b9e94'
    assert all(sha(BASE/k)==v for k,v in bm['files'].items())
    dump('PROTOCOL',dict(status='PREREGISTERED_BEFORE_COMPONENT_AND_NATIVE',market=1977248,mode='ORACLE_DOWN',baseline_job=BASEJOB,
        hypothesis='With one extra continuation service and the same pending-aware growth release gate in both arms, does adding reserved strong-side spending to finite Active need protect against subsequent committed acquisition?',
        dedup='V9 already used both pending cash sides in a passive-only monetary capacity experiment with strong NEW stopped. V24/V25 commitment repair already uses pending burden for Passive15. V38 already supplies terminal-triggered continuation to one old anchor. V40/V41 diagnose new work and growth release on 1977248, but no matched Active continuation sizing pair exists. Reuse these mechanisms; new test is variable Active sizing of a renewed work under V41 growth control with concurrent strong acquisition.',
        arms={'current':'Current weak payoff plus reserved weak repair only; original finite need.',
            'pending':'Same formula, plus pending strong cash at remaining limit and same-plan strong NEW cash.'},
        shared_changes=['Permit exactly one continuation of the SAME renewed work after its first owner is canonical TERMINAL. Total Active cap 5; first four unchanged.',
            'Growth ceiling release requires ALL renewed service owners terminal and confirmed weak payoff minus current strong pending cash >= original work goal. Pending weak fills are not credited as completed protection. Zero aggregate fill terminal release retained.'],
        attribution='A/B differs only in continuation quantity target. Each vs V41 also changes service availability and common release gate, so cannot attribute all vs-V41 improvement solely to pending-aware quantity.',
        sizing='quantity=floor_step(min(current confirmed net minus weak pending quantity, finite scenario need, current displayed depth)). Never credit pending strong shares as already owned or reverse current net on their assumed arrival. Same-plan CANCEL retains reservation.',
        frozen_controls=dict(passive_new=15,active='variable',minimum_new_notional=1,cash_gates=False,capital_cap=None,active_cash_cap=None,
            theta=bm['theta'],fixed_qref=bm['fixed_qref'],maximum_active_submits=5,first_service_passive_priority=True,
            original_work_memory_completion='Confirmed state as V40; distinguish it from pending-aware growth-release condition.'),
        target_boundary='Final observed net-side bit only; no Target prices, clocks, sizes, payoff, winner or future actions in actor.',
        evaluation=['Zero/partial/full and interleaved pending receipt sequences','First paired plan difference and common state',
            'Full raw/canonical/pending/owner audit','Five Active submits at most; no speculative pending fill credit',
            'Direct new service versus later strong acquisition and growth release','Work completion versus residual committed scenario'],
        maximum_native_jobs=2,sequential=True,max_threads=4,model_fits=0,parameter_search=0,local_native_jobs=0,charts=0,
        failure_policy='One submit per arm, collect and fully audit current arm before pending arm. Stop on engine/accounting failure, preserve UNKNOWN, no automatic rerun.',
        selection='Consumed outcome-selected development market, no generalization or identified Target formula'))
    for arm in ARMS:
        pck=package(arm);pck.mkdir()
        for name in bm['files']:
            p=pck/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,p)
        s=(ROOT/'tools/btc5m_pending_repair_service_v1.py').read_text(encoding='utf-8')
        if arm=='pending':s=once(s,"MODE='CURRENT_ONLY'","MODE='PENDING_BURDEN'")
        (pck/'pending_service.py').write_text(s,encoding='utf-8')
        p=pck/'renewed_work.py';s=p.read_text(encoding='utf-8')
        s=once(s,'from roles_runtime import roles','from roles_runtime import roles\nimport pending_service')
        marker="        if status['work'] is not None and not self.submissions:"
        s=once(s,marker,"        prior=ledger.carriers.get(self.submissions[0]['key']) if self.submissions else None\n        continuation=bool(self.submissions)\n        row.update(continuation=continuation,previous_service_state=getattr(prior,'state',None))\n        ready=pending_service.ready(status['work'],self.submissions,prior) if continuation else True\n        if status['work'] is not None and len(self.submissions)<2 and ready:")
        marker="            decision = decide(state,operations,ask,depth,status['work'],True,crossing,\n                frame['world_profile']['quantity_step'],frame['world_profile']['tick'],continuation=False)"
        s=once(s,marker,"            if continuation:\n                decision=pending_service.decide(decide,state,operations,ask,depth,status['work'],crossing,frame['world_profile']['quantity_step'],frame['world_profile']['tick'])\n            else:\n"+'\n'.join('    '+line for line in marker.splitlines()))
        s=once(s,"role='ACTIVE_RENEWED_FINITE_REPAIR')","role='ACTIVE_RENEWED_FINITE_CONTINUATION' if continuation else 'ACTIVE_RENEWED_FINITE_REPAIR')")
        s=once(s,"                producer.growth_hold.arm(frame,producer,status['work'],op)","                if not continuation:producer.growth_hold.arm(frame,producer,status['work'],op)")
        p.write_text(s,encoding='utf-8')
        p=pck/'coordination.py';s=p.read_text(encoding='utf-8')
        s=once(s,'len(producer.coordination.submissions)<=4 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=3',
            'len(producer.coordination.submissions)<=5 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=4')
        p.write_text(s,encoding='utf-8')
        p=pck/'growth_hold.py';s=p.read_text(encoding='utf-8')
        s=once(s,'from roles_runtime import roles','from roles_runtime import roles\nfrom pending_service import aggregate_receipts')
        s=once(s,'weak_payoff, released=False):','weak_payoff, released=False, strong_pending_cash=0.):')
        s=once(s,"elif terminal and weak_payoff>=arm['goal']-EPS:","elif terminal and weak_payoff-strong_pending_cash>=arm['goal']-EPS:")
        s=once(s,"        owner=frame['ledger'].carriers.get(self.armed['key']) if self.armed else None\n        receipt=dict(state=owner.state,filled=float(owner.filled)) if owner is not None else None\n        decision=decide(growth,self.armed,receipt,payoff,self.released)",
            "        service_receipts=[]\n        for sub in producer.coordination.renewal.submissions:\n            owner=frame['ledger'].carriers.get(sub['key'])\n            service_receipts.append(dict(key=sub['key'],state=owner.state,filled=float(owner.filled)) if owner is not None else None)\n        receipt=aggregate_receipts(service_receipts)\n        strong_cash=sum(float(o.reserved_cash) for o in frame['ledger'].carriers.values() if o.parent_id==roles.pid(roles.strong))\n        decision=decide(growth,self.armed,receipt,payoff,self.released,strong_cash)")
        s=once(s,'receipt=receipt,confirmed_weak_payoff=payoff,','receipt=receipt,service_receipts=service_receipts,strong_pending_cash=strong_cash,confirmed_weak_payoff=payoff,')
        p.write_text(s,encoding='utf-8')
        m=deepcopy(bm);m.update(version=astem(arm),parent_manifest_sha256=sha(BASE/'manifest.json'),selection='One renewed continuation; '+arm+' need; shared pending-aware growth release')
        m['files']={p.relative_to(pck).as_posix():sha(p) for p in pck.rglob('*') if p.is_file()}
        changed=sorted(n for n,h in bm['files'].items() if m['files'][n]!=h);assert changed==['coordination.py','growth_hold.py','renewed_work.py']
        (pck/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
        for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(parent.compiled(pck,mode),'V42_COMPILE_ONLY','exec')
        adump(arm,'WAVE',dict(jobs=[job(arm)]))
    ma=read(package('current')/'manifest.json');mb=read(package('pending')/'manifest.json')
    assert [n for n,h in ma['files'].items() if mb['files'][n]!=h]==['pending_service.py']
    component=components()
    for arm in ARMS:adump(arm,'COMPONENT',dict(status='PASS',**component,manifest_sha256=sha(package(arm)/'manifest.json'),files=37))
    dump('COMPONENT',dict(status='PASS',**component,only_arm_code_difference='pending_service.MODE',unchanged_parent_files=33))
    dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',native_submissions=0))
    return component


def components():
    roles,c,s,h=modules('current');counts=Counter()
    for side in ('UP','DOWN'):
        roles.configure('KNOWN_FINAL_DIRECTION',side)
        state=dict(inv={roles.strong:200.,roles.weak:100.},cost=140.,payoff={roles.strong:60.,roles.weak:-40.},
            pending_qty={roles.strong:30.,roles.weak:10.},pending_cash={roles.strong:24.,roles.weak:.7},
            owners=[dict(key='S',side=roles.strong,limit=.8,qty=30.,state='CANCEL_PENDING'),dict(key='W',side=roles.weak,limit=.07,qty=10.,state='SUBMITTED')])
        for price in (.02,.04,.07,.09):
            for depth in (10.,60.,200.):
                for goal in (-30.,0.,20.):
                    work=dict(anchor_floor=goal,id=2);decisions=[]
                    for mode in ('CURRENT_ONLY','PENDING_BURDEN'):
                        s.MODE=mode;d=s.decide(c.decide,state,[dict(kind='CANCEL',key='S')],price,depth,work,crossing_owners)
                        assert d['strong_pending_burden']==24 and d['filled_net_quantity_capacity']==90
                        assert d['quantity']<=min(90,depth)+1e-8
                        assert not d['pending_strong_qty_credited_to_net_capacity']
                        for sf in (0.,.5,1.):
                            for wf in (0.,.5,1.):
                                after={z:state['payoff'][z]+(d['quantity'] if z==roles.weak else 0.)-d['quantity']*price for z in ('UP','DOWN')}
                                for z,f in ((roles.strong,sf),(roles.weak,wf)):
                                    cash=state['pending_cash'][z]*f;qty=state['pending_qty'][z]*f
                                    after={k:v-cash+(qty if k==z else 0.) for k,v in after.items()}
                                inv=dict(state['inv']);inv[roles.weak]+=d['quantity']+wf*10.;inv[roles.strong]+=sf*30.
                                cost=state['cost']+d['quantity']*price+sf*24.+wf*.7
                                for k in inv:same(after[k],inv[k]-cost)
                                assert inv[roles.strong]>=inv[roles.weak]-1e-8
                                counts['scenario_accounting']+=1
                        decisions.append(d);counts['sizing_decisions']+=1
                    assert decisions[1]['quantity']>=decisions[0]['quantity']
                    g=dict(strong=roles.strong,weight=.8,anchor=dict(original_strong_desired=100.),original_desired={roles.strong:300.,roles.weak:50.},effective_desired={roles.strong:260.,roles.weak:50.})
                    for ownerstate in (None,'SUBMITTED','CANCEL_PENDING','UNKNOWN','TERMINAL'):
                        owner=None if ownerstate is None else SimpleNamespace(state=ownerstate,filled=50.)
                        assert s.ready(work,[dict(work_id=2)],owner)==(ownerstate=='TERMINAL')
                        r=s.aggregate_receipts([dict(state='TERMINAL',filled=25.),None if owner is None else dict(state=ownerstate,filled=50.)])
                        d=h.decide(g,dict(ceiling=.4,goal=goal),r,goal+10,False,24.)
                        assert not d['released'];counts['pending_and_terminal_release_blocked']+=1
                        if ownerstate=='TERMINAL':assert h.decide(g,dict(ceiling=.4,goal=goal),r,goal+10,False,0.)['released']
    prefixes={}
    x=read(R/'BTC5M_REPAIR_GROWTH_HOLD_V1_20260913_REMAINING_WORK_DIAGNOSTIC.json')['first_candidate']
    bt=read(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz')
    assert next(row['original_operations'] for row in bt['renewal_rows'] if row['t']==x['t'])==[]
    for arm in ARMS:
        roles,c,s,h=modules(arm);roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
        s.MODE='CURRENT_ONLY' if arm=='current' else 'PENDING_BURDEN'
        d=s.decide(c.decide,x['state'],[],.07,98.8,dict(id=2,anchor_floor=x['decision']['anchor_floor']),crossing_owners)
        prefixes[arm]=dict(t=x['t'],seconds=x['seconds'],decision=d)
    assert prefixes['current']['decision']['quantity']==87.79 and prefixes['pending']['decision']['quantity']==98.8
    dump('PREFIX_DIAGNOSTIC',dict(status='PASS',source='V41 first post-terminal candidate; current original_operations=[]',arms=prefixes,
        boundary='Current-state sizing only; no subsequent fills simulated or summed'))
    return dict(checks=dict(counts),three_mode_compile=True,first_quantities={a:x['decision']['quantity'] for a,x in prefixes.items()},local_native_jobs=0,model_fits=0)


def audit(arm):
    w=worker(arm);j=job(arm)
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=astem(arm);v.PACKAGE=package(arm);v.dump=lambda t,x:adump(arm,t,x);v.jobs=lambda:[None,j]
    tr=read(R/'lan_worker_returns'/j['job_id']/'clock_trace.json.gz')
    code=once(inspect.getsource(v.inspect_job),"len(tr['coordination_submissions'])<=2","len(tr['coordination_submissions'])<=5")
    code=once(code,"same(effective,obs['desired'])","hold=hold_by_index[row['index']];same(effective,hold['input_desired']);effective=hold['effective_desired'];same(effective,obs['desired'])")
    ns=dict(v.__dict__,hold_by_index={r['index']:r for r in tr['growth_hold_rows']});exec(compile(code,'V42_FULL_STRUCTURAL_AUDIT','exec'),ns);out=ns['inspect_job'](1)
    if out['execution_status']!='PASS':return out
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.STEM=astem(arm);cv.PACKAGE=package(arm);cv.jobs=lambda:[None,j];cv.dump=lambda tag,value:w.save(j,tag,value)
    code=once(inspect.getsource(cv.main),'episode,confirmed,crossing_owners)',"episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns=dict(cv.__dict__);exec(compile(code,'V42_FULL_OLD_CONTINUATION_AUDIT','exec'),ns);ns['main']()
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','preflight','submit','status','collect','audit'));p.add_argument('--arm',choices=ARMS,default='current');a=p.parse_args()
    if a.action=='prepare':out=prepare()
    elif a.action=='audit':out=audit(a.arm)
    elif a.action=='preflight':out=worker(a.arm).preflight()
    elif a.action=='status':
        w=worker(a.arm);out=w.dispatch.cmd_status(w.HOST,job(a.arm)['job_id'])
    else:
        if a.action=='submit' and a.arm=='pending':assert read(R/(astem('current')+'_NEW_AUDIT.json'))['status']=='PASS'
        out=getattr(worker(a.arm),a.action)(0)
    print(json.dumps(out,allow_nan=False))
