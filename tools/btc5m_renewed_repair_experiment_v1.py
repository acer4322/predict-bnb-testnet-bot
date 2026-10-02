"""V40: one new finite work after confirmed completion; one native intervention.

All local runs are state/decision replays or compile-only. HFT uses LAN worker.
"""
import argparse
from collections import Counter
from copy import deepcopy
import inspect
import json
import shutil
import sys

import prepare_btc5m_transfer_structural_v1 as parent
from prepare_btc5m_transfer_structural_v1 import ROOT, R, read, sha, load, once
from verify_btc5m_transfer_components_v1 import same
from hft244_pair_route_legality_v1 import crossing_owners

STEM = 'BTC5M_RENEWED_REPAIR_WORK_V1_20260913'
BASE = ROOT/'.lan_worker_v1/success_case_benchmark_v38_20260913_v1'
PACKAGE = ROOT/'.lan_worker_v1/renewed_repair_work_1977248_20260913_v1'
BASEJOB = 'fixed15-core-loop-1977248-success-v38-known-20260913-v1'
BASEAUDIT = R/'BTC5M_SUCCESS_CASE_BENCHMARK_V1_20260913_1977248_known_AUDIT.json'


def dump(tag, value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def jobs():
    return [dict(job_id='fixed15-core-loop-1977248-renewed-repair-work-20260913-v1',
        market=1977248, arm='known', mode='ORACLE_DOWN', cwd='.', max_threads=4,
        argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
            '--market-id', '1977248', '--mode', 'ORACLE_DOWN', '--money-mode', 'PARALLEL_QUANTITY',
            '--demand-mode', 'AUTO_REPAIR', '--retention', '0', '--opportunity-mode', 'ONE_ACTIVE'])]


def worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM; w.PACKAGE=PACKAGE; w.dump=dump; w.jobs=jobs
    return w


def modules():
    sys.path.insert(0, str(PACKAGE))
    from roles_runtime import roles
    return roles, load('v40_coord', PACKAGE/'coordination.py'), load('v40_work', PACKAGE/'renewed_work.py')


def prepare():
    assert not PACKAGE.exists(), 'Do not rebuild frozen or partial package'
    bm=read(BASE/'manifest.json')
    assert sha(BASE/'manifest.json')=='b935523dc8fd6fd33086e6ed7ae1a40cf98b9c5373e5da6d53b246347278d3b0'
    assert all(sha(BASE/n)==h for n,h in bm['files'].items())
    protocol=dict(status='PREREGISTERED_BEFORE_COMPONENT_AND_NATIVE', market=1977248, mode='ORACLE_DOWN',
        baseline_job=BASEJOB, parent_manifest_sha256=sha(BASE/'manifest.json'),
        hypothesis='After the old finite monetary goal is actually restored and both old coordinator owners are TERMINAL, a later confirmed strong-only fill can create a new finite restoration work. Retire each such work on confirmed weak payoff recovery, including recovery supplied by Passive. Only one extra Active service is authorized in this experiment.',
        dedup='V5/V8 already test residual persistence and terminal rearm. V26 births a goal after strong fills break dual positive; V34 allows a negative previous floor; V38 gives a second service to the SAME fixed anchor; V39 freezes it on successful cases. New intervention adds monetary work completion and next-work birth, retiring intervening Passive-completed works. Not a cap-only increase, rolling high-water or forced zero goal. V10 repair credit is not used as the sole strong-acquisition budget; V11 parallel add/repair remains.',
        controls=dict(passive_new_shares=15, active_shares='variable', minimum_new_notional=1,
            cash_gates=False, active_cash_cap=None, capital_cap=None, theta=bm['theta'], fixed_qref=bm['fixed_qref'],
            original_active_services=3, additional_services=1, maximum_total_active=4,
            strong_policy_unchanged=True, first_service_passive_priority=True, continuation_override=False),
        goal='Inherited detector: min of both confirmed branch payoffs immediately before a new pure strong fill; may be negative, zero or positive. No Target value, timestamp or terminal outcome.',
        lifecycle='Confirmed inventory/cost only can complete work. Missing/SUBMITTED/CANCEL_PENDING/UNKNOWN owners cannot release old service gate. Pending weak orders and same-plan NEW remain reserved by inherited decide.',
        scope='One outcome-selected consumed development market; no direction-learning or unseen-generalization claim. Target paths stay offline.',
        local_checks=['Completion/rebirth sequences in both roles', 'Pending projection does not close work',
            'Zero/partial/full progress and terminal-state variants', 'Inherited minimum/price/depth/net and own-cross gates',
            'Saved baseline first-difference prefix only; no simulated fills', 'Three-mode compile/load-only'],
        evaluation=['Original prefix and three service invariance', 'Raw/canonical receipts, all owners, original pure decisions',
            'Recompute new work memory and every route decision', 'Both conditional payoffs, cost, negative-floor area',
            'Actual additional service fill versus goal completion and later acquisition burden'],
        maximum_native_jobs=1, max_threads=4, model_fits=0, parameter_search=0, local_native_jobs=0, charts=0,
        failure_policy='Single submit. On failure preserve artifacts and UNKNOWN, no automatic rerun.')
    assert not (R/(STEM+'_PROTOCOL.json')).exists()
    dump('PROTOCOL', protocol)
    PACKAGE.mkdir()
    for name in bm['files']:
        dst=PACKAGE/name; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(BASE/name,dst)
    shutil.copy2(ROOT/'tools/btc5m_renewed_repair_work_v1.py',PACKAGE/'renewed_work.py')
    p=PACKAGE/'coordination.py'; s=p.read_text(encoding='utf-8')
    s=once(s,'import math','import math\nfrom renewed_work import RenewalProbe')
    s=once(s,'        self.snapshot = snapshot','        self.snapshot = snapshot\n        self.renewal = RenewalProbe(snapshot)')
    s=once(s,"        if len(self.submissions) >= 2 or not frame['start'] <= frame['t'] < frame['end']:",
        "        if len(self.submissions) >= 2:\n            return self.renewal.apply(frame,producer,operations,validate,crossing,self,detect,decide)\n        if not frame['start'] <= frame['t'] < frame['end']:")
    s=once(s,'len(producer.coordination.submissions)<=3 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=2',
        'len(producer.coordination.submissions)<=4 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=3')
    p.write_text(s,encoding='utf-8')
    p=PACKAGE/'money_runner.py'; s=p.read_text(encoding='utf-8')
    s=once(s,'coordination_rows=producer.coordination.rows,',
        'renewal_rows=producer.coordination.renewal.rows,renewal_events=producer.coordination.renewal.memory.events,renewal_work=producer.coordination.renewal.memory.work,renewal_submissions=producer.coordination.renewal.submissions,coordination_rows=producer.coordination.rows,')
    p.write_text(s,encoding='utf-8')
    m=deepcopy(bm); m.update(version=STEM,parent_manifest_sha256=sha(BASE/'manifest.json'),
        market=1977248,paired_markets=[1977248],maximum_native_jobs=1,panel_maximum_native_jobs=1,
        selection='Confirmed old goal completion then renewed finite work; one additional Active',
        limitations=protocol['scope'],model_fits=0,parameter_search=0)
    m['files']={p.relative_to(PACKAGE).as_posix():sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    changed=[n for n,h in bm['files'].items() if m['files'][n]!=h]
    assert sorted(changed)==['coordination.py','money_runner.py']
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    dump('WAVE',dict(jobs=jobs(),max_threads=4,sequential=True))
    out=components();out.update(changed_files=changed,inherited_unchanged=len(bm['files'])-2,
        manifest_sha256=sha(PACKAGE/'manifest.json'),total_files=len(m['files']))
    dump('COMPONENT',out);dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',native_submissions=0))
    return out


def components():
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):
        compile(parent.compiled(PACKAGE,mode),'V40_COMPILE_ONLY','exec')
    roles,coord,work=modules(); checks=Counter()
    def state(strong_qty,weak_qty,cost):
        inv={roles.strong:strong_qty,roles.weak:weak_qty}
        return dict(inv=inv,cost=cost,payoff={s:q-cost for s,q in inv.items()},
            pending_qty={s:0. for s in inv},pending_cash={s:0. for s in inv},owners=[])
    for side in ('UP','DOWN'):
        roles.configure('KNOWN_FINAL_DIRECTION',side)
        for a in (-10.,0.,10.):
            old=dict(anchor_floor=a); base=state(150,100+a,100)
            for owner_state in (None,'SUBMITTED','CANCEL_PENDING','UNKNOWN'):
                mem=work.WorkMemory()
                z=mem.advance(base,1,old,['TERMINAL',owner_state],coord.detect)
                assert not z['retired'] and not mem.events;checks['nonterminal_release_blocked']+=1
            low=state(150,90+a,100);low['pending_qty'][roles.weak]=50;low['pending_cash'][roles.weak]=2
            mem=work.WorkMemory()
            assert not mem.advance(low,1,old,['TERMINAL']*2,coord.detect)['retired'];checks['pending_cannot_complete']+=1
            assert mem.advance(base,2,old,['TERMINAL']*2,coord.detect)['retired']
            more=state(190,100+a,130)
            born=mem.advance(more,3,old,['TERMINAL']*2,coord.detect)
            assert born['work']['anchor_floor']==a and born['work']['id']==1
            frozen=deepcopy(mem.events)
            for fraction in (0.,.5):
                mid=state(190,100+a+30*fraction,130)
                assert mem.advance(mid,4,old,['TERMINAL']*2,coord.detect)['work']['id']==1
            completed=state(190,130+a,130)
            assert mem.advance(completed,5,old,['TERMINAL']*2,coord.detect)['work'] is None
            renewed=state(230,130+a,160)
            assert mem.advance(renewed,6,old,['TERMINAL']*2,coord.detect)['work']['id']==2
            same(mem.events[:len(frozen)],frozen);checks['completion_rebirth_sequences']+=1
            for price_int in range(1,100):
                for depth in (0.,10.,100.):
                    price=price_int/100
                    ep=born['work'];d=coord.decide(more,[],price,depth,ep,True,crossing_owners)
                    if d['eligible']:
                        assert d['quantity']*price>=1-1e-8 and d['quantity']<=min(depth,d['filled_net_quantity_capacity'],d['restore_quantity_need'])+1e-8
                        for status in ('SUBMITTED','CANCEL_PENDING','UNKNOWN'):
                            blocked=deepcopy(more);blocked['owners']=[dict(key='block',side=roles.strong,limit=round(1-price,10),qty=15.,state=status)]
                            assert not coord.decide(blocked,[dict(kind='CANCEL',key='block')],price,depth,ep,True,crossing_owners)['eligible']
                            checks['retained_own_cross']+=1
                    checks['finite_price_depth_decisions']+=1
    prefix=baseline_prefix()
    dump('PREFIX_DIAGNOSTIC',prefix)
    assert prefix['first_difference'] is not None
    return dict(status='PASS',checks=dict(checks),three_mode_compile=True,
        saved_path_first_difference=prefix['first_difference'],local_native_jobs=0,model_fits=0)


def baseline_prefix():
    roles,coord,work=modules();roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    tr=read(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz')
    audit=read(BASEAUDIT);owners={o['key']:o for o in audit['receipt_accounting']['orders']}
    inp=read(BASE/'inputs/public_1977248.json.gz');start=inp['market']['window_start_ms']
    plans={d['index']:p['operations'] for d,p in zip(tr['direction_rows'],tr['plans'])}
    books={d['index']:b for d,b in zip(tr['direction_rows'],inp['books'])}
    mem=work.WorkMemory();first=None;reasons=Counter();cap_only=None;examined=0
    for intent,row in zip(tr['intent'],tr['commitment_repair_rows']):
        t=row['t'];index=intent['index'];assert intent['t']==t
        if t<=tr['coordination_submissions'][1]['t']:continue
        states=[]
        for op in tr['coordination_submissions'][:2]:
            o=owners[op['key']]
            terminal=o['terminal_observed_t']
            states.append('TERMINAL' if terminal is not None and terminal<=t else next((x['state'] for x in row['state']['owners'] if x['key']==o['key']),None))
        z=mem.advance(row['state'],t,tr['coordination_episode'],states,coord.detect)
        b=books[index];ask=b['best_ask'];depth=b['asks'][0][1] if b['asks'] else 0.
        if cap_only is None and all(s=='TERMINAL' for s in states):
            c=coord.decide(row['state'],plans[index],ask,depth,tr['coordination_episode'],True,crossing_owners,continuation=True)
            if c['eligible']:cap_only=dict(t=t,seconds=(t-start)/1000,decision=c)
        examined+=1
        if z['work'] is None:continue
        d=coord.decide(row['state'],plans[index],ask,depth,z['work'],True,crossing_owners,continuation=False)
        reasons[d['reason']]+=1
        if d['eligible']:
            first=dict(t=t,seconds=(t-start)/1000,work=z['work'],decision=d,state=row['state'],operations=plans[index],old_service_states=states)
            break
    return dict(status='PASS',scope='Saved baseline prefix stops at first altered plan; no fill or economic counterfactual beyond this point',
        baseline_trace_sha256=sha(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz'),
        examined=examined,first_difference=first,events=mem.events,reason_counts=dict(reasons),
        cap_only_first=cap_only,cap_only_is_separate_saved_state_diagnostic=True)


def audit():
    w=worker();j=jobs()[0]
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=STEM;v.PACKAGE=PACKAGE;v.dump=dump;v.jobs=lambda:[None,j]
    code=once(inspect.getsource(v.inspect_job),"len(tr['coordination_submissions'])<=2","len(tr['coordination_submissions'])<=4")
    ns=dict(v.__dict__);exec(compile(code,'V40_FULL_STRUCTURAL_AUDIT','exec'),ns);out=ns['inspect_job'](1)
    if out['execution_status']!='PASS':return out
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.STEM=STEM;cv.PACKAGE=PACKAGE;cv.jobs=lambda:[None,j];cv.dump=lambda tag,value:w.save(j,tag,value)
    code=once(inspect.getsource(cv.main),'episode,confirmed,crossing_owners)',"episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns=dict(cv.__dict__);exec(compile(code,'V40_FULL_CONTINUATION_AUDIT','exec'),ns);ns['main']()
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','preflight','submit','status','collect','audit'));a=p.parse_args()
    if a.action in ('prepare','audit'):out=globals()[a.action]()
    elif a.action=='preflight':out=worker().preflight()
    elif a.action=='status':
        w=worker();out=w.dispatch.cmd_status(w.HOST,jobs()[0]['job_id'])
    else:out=getattr(worker(),a.action)(0)
    print(json.dumps(out,allow_nan=False))
