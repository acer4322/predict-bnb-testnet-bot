"""V43 bounded final-service adaptive price and conflict coordination experiment."""
import argparse
from copy import deepcopy
import inspect
import json
import shutil
import sys

import btc5m_pending_repair_pair_v1 as prior
import btc5m_demand_slippage_v1 as adaptive
from verify_btc5m_demand_slippage_v1 import components

ROOT,R,read,sha,once=prior.ROOT,prior.R,prior.read,prior.sha,prior.once
sys.path.insert(0,str(ROOT))
STEM='BTC5M_DEMAND_PRICE_COORDINATION_V1_20260913'
PACKAGE=ROOT/'.lan_worker_v1/demand_price_coordination_1977248_20260913_v2'
BASE=prior.package('pending')
JOB='fixed15-core-loop-1977248-demand-price-coordination-20260913-v2'


def dump(tag,value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def job():
    j=prior.job('pending');j['job_id']=JOB
    j['argv'][1]=f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py'
    return j


def worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=PACKAGE;w.dump=dump;w.jobs=lambda:[job()]
    return w


def integration_checks():
    baseline=read(R/'BTC5M_ACTIVE_SLIPPAGE_FRONTIER_V1_20260913_DIAGNOSTIC.json')
    trace=read(R/'lan_worker_returns'/prior.job('pending')['job_id']/'clock_trace.json.gz')
    row=next(r for r in trace['renewal_rows'] if r['t']==1788635016821)
    state=row['state'];ops=row['original_operations'];legacy=row['decision'];asks=baseline['book']['asks']
    frame_cancel={o['key']:True for o in state['owners']}
    output,decision,diagnostic=adaptive.replan(state,ops,'UP',row['status']['work']['anchor_floor'],asks,frame_cancel,legacy)
    assert not decision['eligible'] and diagnostic['suppressed']==['DOWN_700']
    assert len(output)==5 and all(o['kind']=='CANCEL' for o in output)
    assert len(diagnostic['steps'])==2
    counts=0
    for status in ('CANCEL_PENDING','UNKNOWN'):
        pending=deepcopy(state)
        for owner in pending['owners']:owner['state']=status
        result,order,check=adaptive.replan(pending,ops,'UP',row['status']['work']['anchor_floor'],asks,frame_cancel,legacy)
        assert result==[] and not order['eligible'] and len(check['steps'][-1]['coordination']['conflicts'])==5
        counts+=1
    # Hypothetical confirmed cancellations with NO fills: demand is recalculated,
    # not latched to the old .09 / 157.78 proposal. This is not a native outcome.
    terminal=deepcopy(state);terminal['owners']=[]
    terminal['pending_qty']['DOWN']=0.;terminal['pending_cash']['DOWN']=0.
    result,order,check=adaptive.replan(terminal,[],'UP',row['status']['work']['anchor_floor'],asks,{},legacy)
    assert result==[] and order['eligible'] and order['execution_limit']==.07 and order['quantity']==87.79
    counts+=1
    # A fill during cancellation changes both confirmed capacity and needed repair.
    terminal['inv']['DOWN']+=15.;terminal['cost']+=13.8
    terminal['payoff']={s:terminal['inv'][s]-terminal['cost'] for s in ('UP','DOWN')}
    result,order,check=adaptive.replan(terminal,[],'UP',row['status']['work']['anchor_floor'],asks,{},legacy)
    assert check['steps'][0]['context']['capacity']>state['inv']['DOWN']-state['inv']['UP']
    assert order['quantity']>87.79
    counts+=1
    return dict(status='PASS',receipt_transition_cases=counts,
        first_difference=dict(index=1044,seconds=216.821,baseline_operations=ops,
            output_operations=output,decision=decision,diagnostic=diagnostic),
        post_terminal_no_fill=dict(price=.07,quantity=87.79),native_jobs=0)


def prepare():
    assert not PACKAGE.exists() and not (R/(STEM+'_PROTOCOL.json')).exists()
    bm=read(BASE/'manifest.json');assert all(sha(BASE/k)==v for k,v in bm['files'].items())
    assert sha(BASE/'manifest.json')=='6bf6cd47a9ad387a607226978742ad0eb7148c3d456b503d14e500bfb507847c'
    dump('PROTOCOL',dict(status='PREREGISTERED_BEFORE_NATIVE',market=1977248,mode='ORACLE_DOWN',
        baseline_job=prior.job('pending')['job_id'],max_native_jobs=1,max_threads=4,
        hypothesis='Serve the same bounded final monetary work using demand-derived depth pricing and receipt-safe coordination of crossing strong acquisition owners.',
        dedup='Local demand-price solver is complete. Current-only final selector is identical to V42 and is not rerun. This candidate uses existing V42 pending-burden final service, plus explicit suppression/cancellation/wait/recomputation to make a deeper price request executable. V36 price geometry and V42 maintenance remain prior evidence.',
        first_four_active='Unchanged decisions and receipts until the final-service conflict intervention.',
        trigger='Only the already authorized renewed continuation after first renewed service canonical TERMINAL; same work id; at most one remaining Active service.',
        operations='Suppress only same-plan strong NEW conflicting with required price, recompute demand, cancel currently cancellable conflicting owners, keep all pending/unknown reservations and wait. Recompute fresh demand on each frame; no assumed cancellation success or latched old quantity.',
        price='Lowest displayed depth prefix sufficient for finite expected repair or confirmed net capacity. Cost integral and limit reservation separate. No fixed percent ceiling or hidden extra cash budget.',
        unchanged=['Passive15','Variable Active and NEW>=1','Cash gates OFF, all cash caps null','GTC LIMIT remainder and ordinary maintenance','Original weak NEW priority and Passive availability trigger','First four Active','Source data and frozen theta','Growth hold and WorkMemory','Exact native receipt accounting'],
        native_guard='Only final renewed-continuation Active may exceed current ask, within currently observed side depth and declared tick. All other Active remain current ask; full own-cross remains in gateway.',
        attribution='Combined price-and-conflict execution mechanism, not an isolated proof of slippage benefit. If no higher-price order is realized, report that directly.',
        outcome='Measure actual fill/cost, repair gap, both conditional outcomes, cancelled/retained owners and whether any price above contemporaneous ask is sent/filled.',
        failure='One submit; stop and retain artifacts/UNKNOWN on native failure; no automatic rerun.',model_fits=0,local_native_jobs=0,parameter_search=0))
    core=components();integration=integration_checks()
    PACKAGE.mkdir()
    for name in bm['files']:
        dst=PACKAGE/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,dst)
    s=(ROOT/'tools/btc5m_demand_slippage_v1.py').read_text(encoding='utf-8')
    (PACKAGE/'depth_slippage.py').write_text(s,encoding='utf-8')
    shutil.copy2(ROOT/'tools/hft244_pair_route_legality_v1.py',PACKAGE/'hft244_pair_route_legality_v1.py')
    p=PACKAGE/'renewed_work.py';s=p.read_text(encoding='utf-8')
    s=once(s,'import pending_service','import pending_service\nimport depth_slippage')
    marker="                decision=pending_service.decide(decide,state,operations,ask,depth,status['work'],crossing,frame['world_profile']['quantity_step'],frame['world_profile']['tick'])"
    s=once(s,marker,marker+"\n                asks=sorted((round(1-p,10),q) for p,q in bids.items())\n                operations,decision,adaptive=depth_slippage.replan(state,operations,roles.weak,status['work']['anchor_floor'],asks,frame['cancellable'],decision)\n                row.update(adaptive=adaptive,cancellable=dict(frame['cancellable']))")
    s=once(s,"                q=decision['quantity']","                q=decision['quantity']\n                execution_price=decision.get('execution_limit',ask)")
    s=once(s,"validate(frame['world_profile']['asset'],'ACTIVE',ask,q,","validate(frame['world_profile']['asset'],'ACTIVE',execution_price,q,")
    s=once(s,"abs(ask/frame['world_profile']['tick']-round(ask/frame['world_profile']['tick']))","abs(execution_price/frame['world_profile']['tick']-round(execution_price/frame['world_profile']['tick']))")
    s=once(s,"side=roles.weak,route='ACTIVE',price=ask,qty=q,role=","side=roles.weak,route='ACTIVE',price=execution_price,qty=q,role=")
    p.write_text(s,encoding='utf-8')
    p=PACKAGE/'adapter.py';s=p.read_text(encoding='utf-8')
    old="                if active_ask is None or abs(float(active_ask)-float(a.price))>1e-9:raise PlanRejected('ACTIVE_LIMIT_MUST_EQUAL_CURRENT_ASK')"
    new="""                if op.get('role')=='ACTIVE_RENEWED_FINITE_CONTINUATION':
                    levels=list(frame['book']['asks']) if side=='UP' else [round(1-p,10) for p in frame['book']['bids']]
                    if active_ask is None or not levels or not float(active_ask)-1e-9<=float(a.price)<=max(levels)+1e-9:raise PlanRejected('ACTIVE_PRICE_OUTSIDE_VISIBLE_DEPTH')
                elif active_ask is None or abs(float(active_ask)-float(a.price))>1e-9:raise PlanRejected('ACTIVE_LIMIT_MUST_EQUAL_CURRENT_ASK')"""
    s=once(s,old,new);p.write_text(s,encoding='utf-8')
    m=deepcopy(bm);m.update(version=STEM,parent_manifest_sha256=sha(BASE/'manifest.json'),selection='Final pending-burden demand-price conflict coordination',maximum_native_jobs=1)
    m['files']={p.relative_to(PACKAGE).as_posix():sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):
        compile(prior.parent.compiled(PACKAGE,mode),'V43_COMPILE_ONLY','exec')
    dump('COMPONENT',dict(status='PASS',core=core,integration=integration,three_mode_compile=True,
        files=len(m['files']),manifest_sha256=sha(PACKAGE/'manifest.json'),
        changed_parent_files=[k for k,h in bm['files'].items() if m['files'][k]!=h],
        extra_files=['depth_slippage.py','hft244_pair_route_legality_v1.py'],native_jobs=0))
    dump('WAVE',dict(jobs=[job()]));dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',submissions=0))
    return dict(status='PASS',files=len(m['files']),manifest_sha256=sha(PACKAGE/'manifest.json'))


def audit():
    import verify_btc5m_transfer_structural_v1 as v
    w=worker();v.STEM=STEM;v.PACKAGE=PACKAGE;v.jobs=lambda:[None,job()];v.dump=dump
    tr=read(R/'lan_worker_returns'/JOB/'clock_trace.json.gz')
    code=once(inspect.getsource(v.inspect_job),"len(tr['coordination_submissions'])<=2","len(tr['coordination_submissions'])<=5")
    code=once(code,"same(effective,obs['desired'])","hold=hold_by_index[row['index']];same(effective,hold['input_desired']);effective=hold['effective_desired'];same(effective,obs['desired'])")
    code=once(code,"            else:same(op['price'],ask)","            elif op.get('role')=='ACTIVE_RENEWED_FINITE_CONTINUATION':assert op['price']>=ask-1e-8\n            else:same(op['price'],ask)")
    ns=dict(v.__dict__,hold_by_index={r['index']:r for r in tr['growth_hold_rows']})
    exec(compile(code,'V43_STRUCTURAL_AUDIT','exec'),ns)
    return ns['inspect_job'](1)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','preflight','submit','status','collect','audit'));a=p.parse_args()
    if a.action=='prepare':result=prepare()
    elif a.action=='audit':result=audit()
    elif a.action=='status':result=worker().dispatch.cmd_status(worker().HOST,JOB)
    elif a.action=='preflight':result=worker().preflight()
    else:result=getattr(worker(),a.action)(0)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
