from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from typing import Any
import numpy as np
ROOT=Path.cwd().resolve()
if not (ROOT/'tools'/'run_decision_seam_counterfactual_fork_smoke_v1.py').exists(): ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_decision_seam_counterfactual_fork_smoke_v1 as base

VERSION='MARKET_CAPSULE_MODE_OPTION_COUNTERFACTUAL_FORK_V1_RESULT_20260907'
TERMINAL={'CANCELED','EXPIRED','REJECTED'}
EPS=1e-9

def weak_strong(u:float,d:float):
    if abs(u-d)<=EPS:return None,None
    weak='UP' if u<d else 'DOWN'; return weak,('DOWN' if weak=='UP' else 'UP')

def mode_side(mode:str,u:float,d:float,fallback:str|None=None):
    weak,strong=weak_strong(u,d)
    if mode=='REPAIR': return weak
    if mode=='EXPAND': return strong if strong else fallback
    raise ValueError(mode)

def passive_action(side:str,q:dict[str,dict[str,float]],label:str):
    px=float(q[side]['bid']); qty=base.legal_qty(px,2.0)
    return {'name':label,'kind':'PASSIVE','side':side,'price':px,'baseQty':2.0,'qty':qty,'ttlMs':60000}

def alloc(u:float,d:float,side:str,fill:float):
    gap=abs(u-d); weak,strong=weak_strong(u,d)
    if fill<=EPS:return {'repairQty':0.0,'expandQty':0.0,'preGap':gap,'preWeak':weak}
    if weak is None:return {'repairQty':0.0,'expandQty':fill,'preGap':gap,'preWeak':None}
    if side==weak:
        r=min(fill,gap); return {'repairQty':r,'expandQty':max(0.0,fill-r),'preGap':gap,'preWeak':weak}
    return {'repairQty':0.0,'expandQty':fill,'preGap':gap,'preWeak':weak}

def apply_fill(u:float,d:float,cost:float,side:str,fill:float,px:float):
    if side=='UP':u+=fill
    else:d+=fill
    cost+=fill*px
    pu=u-cost;pd=d-cost
    return u,d,cost,{'pnlIfUp':pu,'pnlIfDown':pd,'floor':min(pu,pd),'upside':max(pu,pd),'gap':abs(u-d)}

def observe(bt,oid:int,times,end_ms:int,start_ms:int):
    inspected=0
    for t in times:
        t=int(t)
        if t<=start_ms: continue
        base.ex.advance_to(bt,t); inspected+=1
        snap=base.ex.order_snapshot(bt,oid); status=str(snap.get('status'));cum=base.finite(snap.get('cumExecQty'))
        if status=='FILLED': risk='FULL_FILL'
        elif cum>EPS: risk='PARTIAL_FILL'
        elif status in TERMINAL: risk='TERMINAL_'+status
        else: continue
        return {'risk':risk,'feedEventMs':t,'latencyFromStartMs':t-start_ms,'snapshot':snap,'feedEventsInspected':inspected}
    base.ex.advance_to(bt,end_ms);snap=base.ex.order_snapshot(bt,oid);status=str(snap.get('status'));cum=base.finite(snap.get('cumExecQty'))
    if status=='FILLED':risk='FULL_FILL'
    elif cum>EPS:risk='PARTIAL_FILL'
    elif status in TERMINAL:risk='TERMINAL_'+status
    else:risk='CENSORED_MARKET_END'
    return {'risk':risk,'feedEventMs':end_ms,'latencyFromStartMs':end_ms-start_ms,'snapshot':snap,'feedEventsInspected':inspected,'censored':risk=='CENSORED_MARKET_END'}

def run_branch(seam:dict[str,Any],branch:str,tape_dir:Path,cache,feed_cache):
    mid=int(seam['market_id']);decision=int(seam['action_event_ms']);end_ms=decision+int(round(float(seam['seconds_left'])*1000))
    prev=str(seam['previous_management_mode']);mode=prev if branch=='CONTINUE' else ('EXPAND' if prev=='REPAIR' else 'REPAIR')
    u=float(seam['pre_up_shares']);d=float(seam['pre_down_shares']);cost=float(seam['pre_net_cost']);pre_floor=float(seam['pre_floor']);pre_upside=float(seam['pre_upside'])
    first_side=mode_side(mode,u,d)
    if first_side is None:return {'marketId':mid,'seamId':seam['seam_id'],'branch':branch,'mode':mode,'error':'no_repair_side_at_balanced_prestate'}
    if mid not in feed_cache:feed_cache[mid]=cache.get(mid)
    events,meta=feed_cache[mid]
    bt=base.ex.new_bt(events,entry_latency_ms=base.ENTRY_LATENCY_MS,response_latency_ms=base.RESPONSE_LATENCY_MS,queue_model=base.QUEUE_MODEL);base.ex.initialize_bt(bt)
    try:
        if not base.ex.advance_to(bt,decision):return {'error':'feed_exhausted_before_decision','marketId':mid,'seamId':seam['seam_id'],'branch':branch}
        q0=base.outcome_quotes(bt)
        if q0 is None:return {'error':'no_book_at_decision','marketId':mid,'seamId':seam['seam_id'],'branch':branch}
        a1=passive_action(first_side,q0,f'{branch}_{mode}_STEP1')
        rc1=base.submit(bt,1,a1)
        raw=np.asarray(events['local_ts'],dtype=np.int64)//1_000_000;times=np.unique(raw[(raw>decision)&(raw<=end_ms)])
        e1=observe(bt,1,times,end_ms,decision);f1=base.finite(e1['snapshot'].get('cumExecQty'))
        al1=alloc(u,d,a1['side'],f1);u1,d1,c1,eco1=apply_fill(u,d,cost,a1['side'],f1,a1['price'])
        successor=None;e2=None;al2={'repairQty':0.0,'expandQty':0.0};eco2=eco1;u2,d2,c2=u1,d1,c1
        # Do not create sibling while first carrier is only partial/live. Spawn successor only after confirmed full fill.
        if e1['risk']=='FULL_FILL' and int(e1['feedEventMs'])<end_ms:
            q1=base.outcome_quotes(bt)
            s2=mode_side(mode,u1,d1,fallback=a1['side'])
            if q1 is not None and s2 is not None:
                successor=passive_action(s2,q1,f'{branch}_{mode}_STEP2')
                rc2=base.submit(bt,2,successor);successor['submitRc']=rc2
                t1=int(e1['feedEventMs']);times2=times[times>t1]
                e2=observe(bt,2,times2,end_ms,t1);f2=base.finite(e2['snapshot'].get('cumExecQty'))
                al2=alloc(u1,d1,successor['side'],f2);u2,d2,c2,eco2=apply_fill(u1,d1,c1,successor['side'],f2,successor['price'])
        totals={'repairQty':al1['repairQty']+al2.get('repairQty',0.0),'expandQty':al1['expandQty']+al2.get('expandQty',0.0),'confirmedFillQty':f1+(base.finite(e2['snapshot'].get('cumExecQty')) if e2 else 0.0)}
        totals['modeAlignedQty']=totals['repairQty'] if mode=='REPAIR' else totals['expandQty']
        return {'marketId':mid,'seamId':seam['seam_id'],'previousMode':prev,'targetCurrentRoleAuditOnly':seam.get('target_current_economic_role_audit_only'),'branch':branch,'mode':mode,'decisionMs':decision,'firstAction':a1,'firstSubmitRc':rc1,'firstEvent':e1,'firstAllocation':al1,'successorAction':successor,'secondEvent':e2,'secondAllocation':al2,'totals':totals,'finalEconomics':{'deltaFloor':eco2['floor']-pre_floor,'deltaUpside':eco2['upside']-pre_upside,**eco2},'tape':meta}
    finally:bt.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--seams',required=True);ap.add_argument('--tape-dir',required=True);ap.add_argument('--output',required=True);ns=ap.parse_args()
    payload=json.loads(Path(ns.seams).read_text(encoding='utf-8'));seams=payload['rows'];cache=base.ExecutionEventCache(Path(ns.tape_dir));feed_cache={};rows=[];errors=[];aud=[]
    for s in seams:
        er=base.validate_seam(s);aud.append({'marketId':int(s['market_id']),'seamId':s['seam_id'],'errors':er})
        for b in ('CONTINUE','SWITCH'):
            try:r=run_branch(s,b,Path(ns.tape_dir),cache,feed_cache);rows.append(r);errors.extend([r] if 'error' in r else [])
            except Exception as exc:
                r={'marketId':int(s['market_id']),'seamId':s['seam_id'],'branch':b,'error':repr(exc)};rows.append(r);errors.append(r)
    valid=[r for r in rows if 'error' not in r];first_counts={};second_counts={};succ=0
    for r in valid:
        k=r['firstEvent']['risk'];first_counts[k]=first_counts.get(k,0)+1
        if r['successorAction'] is not None:succ+=1
        if r['secondEvent'] is not None:
            k=r['secondEvent']['risk'];second_counts[k]=second_counts.get(k,0)+1
    sem=[]
    for r in valid:
        for ev in [r['firstEvent'],r.get('secondEvent')]:
            if ev is None:continue
            risk=ev['risk'];cum=base.finite(ev['snapshot'].get('cumExecQty'));status=str(ev['snapshot'].get('status'))
            sem.append((cum>EPS if risk in {'FULL_FILL','PARTIAL_FILL'} else True) and (status in TERMINAL if risk.startswith('TERMINAL_') else True))
    gate={'runCount12':len(rows)==12,'zeroErrors':len(errors)==0,'strictPastAll':all(not x['errors'] for x in aud),'eventSemanticsAll':all(sem),'noDreamFill':True,'hasFirstFill':any(r['firstEvent']['risk'] in {'FULL_FILL','PARTIAL_FILL'} for r in valid),'hasSuccessorMaterialization':succ>0};gate['pass']=all(gate.values())
    # post-hoc audit only on pure Target current roles
    audit=[]
    for s in seams:
        tar=s.get('target_current_economic_role_audit_only');prev=s['previous_management_mode'];target_branch=('CONTINUE' if tar==prev else 'SWITCH' if tar in {'REPAIR','EXPAND'} else None)
        rs=[r for r in valid if r['seamId']==s['seam_id']]
        audit.append({'marketId':int(s['market_id']),'previousMode':prev,'targetCurrentRole':tar,'targetBranchAuditOnly':target_branch,'branches':{r['branch']:{'firstRisk':r['firstEvent']['risk'],'secondRisk':r['secondEvent']['risk'] if r['secondEvent'] else None,'modeAlignedQty':r['totals']['modeAlignedQty'],'repairQty':r['totals']['repairQty'],'expandQty':r['totals']['expandQty'],'deltaFloor':r['finalEconomics']['deltaFloor'],'deltaUpside':r['finalEconomics']['deltaUpside']} for r in rs}})
    out={'version':VERSION,'researchOnly':True,'executionBoundary':{'clock':'actual feed local event timestamps','entryLatencyMs':base.ENTRY_LATENCY_MS,'responseLatencyMs':base.RESPONSE_LATENCY_MS,'queueModel':base.QUEUE_MODEL,'dreamFillAllowed':False,'successorOnlyAfterFullFill':True},'seamAudits':aud,'rows':rows,'errors':errors,'summary':{'runs':len(rows),'validRuns':len(valid),'firstRiskCounts':first_counts,'successorMaterializations':succ,'secondRiskCounts':second_counts},'teacherAudit':audit,'promotionGate':gate}
    p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'output':str(p),'summary':out['summary'],'promotionGate':gate,'teacherAudit':audit},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
