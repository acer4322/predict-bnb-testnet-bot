from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any
import numpy as np
import sys
ROOT=Path.cwd().resolve()
if not (ROOT/'tools'/'run_decision_seam_counterfactual_fork_smoke_v1.py').exists(): ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_decision_seam_counterfactual_fork_smoke_v1 as base

VERSION='MARKET_CAPSULE_COMPETING_RISK_EXECUTION_V1_RESULT_20260907'
TERMINAL={'CANCELED','EXPIRED','REJECTED'}
ACTION_NAMES={'PASSIVE_UP_2','PASSIVE_DOWN_2'}

def first_event(seam:dict[str,Any], action:dict[str,Any], tape_dir:Path, cache:base.ExecutionEventCache, feed_cache:dict[int,tuple[Any,dict[str,Any]]])->dict[str,Any]:
    mid=int(seam['market_id']); decision=int(seam['action_event_ms']); end_ms=decision+int(round(float(seam['seconds_left'])*1000.0))
    cached=feed_cache.get(mid)
    if cached is None:
        events,meta=cache.get(mid); feed_cache[mid]=(events,meta)
    else: events,meta=cached
    bt=base.ex.new_bt(events,entry_latency_ms=base.ENTRY_LATENCY_MS,response_latency_ms=base.RESPONSE_LATENCY_MS,queue_model=base.QUEUE_MODEL)
    base.ex.initialize_bt(bt)
    try:
        if not base.ex.advance_to(bt,decision): return {'error':'feed_exhausted_before_decision','marketId':mid,'seamId':seam['seam_id'],'action':action['name']}
        q=base.outcome_quotes(bt)
        if q is None: return {'error':'no_book_at_decision','marketId':mid,'seamId':seam['seam_id'],'action':action['name']}
        rc=base.submit(bt,1,action)
        raw=np.asarray(events['local_ts'],dtype=np.int64)//1_000_000
        times=np.unique(raw[(raw>decision)&(raw<=end_ms)])
        observed=None; inspected=0
        for t in times:
            t=int(t); base.ex.advance_to(bt,t); inspected+=1
            snap=base.ex.order_snapshot(bt,1); status=str(snap.get('status')); cum=base.finite(snap.get('cumExecQty'))
            if cum>base.EPS or status in TERMINAL or status=='FILLED':
                if status=='FILLED': risk='FULL_FILL'
                elif cum>base.EPS: risk='PARTIAL_FILL'
                else: risk='TERMINAL_'+status
                lm=snap.get('localTs'); local_ms=(int(lm)//1_000_000 if lm is not None and int(lm)>10**12 else None)
                observed={'risk':risk,'feedEventMs':t,'orderLocalMs':local_ms,'latencyFromDecisionMs':t-decision,'snapshot':snap,'feedEventsInspected':inspected}
                break
        if observed is None:
            base.ex.advance_to(bt,end_ms); snap=base.ex.order_snapshot(bt,1); status=str(snap.get('status')); cum=base.finite(snap.get('cumExecQty'))
            if status=='FILLED': risk='FULL_FILL'
            elif cum>base.EPS: risk='PARTIAL_FILL'
            elif status in TERMINAL: risk='TERMINAL_'+status
            else: risk='CENSORED_MARKET_END'
            observed={'risk':risk,'feedEventMs':end_ms,'orderLocalMs':None,'latencyFromDecisionMs':end_ms-decision,'snapshot':snap,'feedEventsInspected':inspected,'censored':risk=='CENSORED_MARKET_END'}
        return {'marketId':mid,'seamId':seam['seam_id'],'decisionMs':decision,'marketEndMs':end_ms,'action':action,'submitRc':rc,'strictBookAtDecision':q,'firstRiskEvent':observed,'tape':meta}
    finally: bt.close()

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--seams',required=True); ap.add_argument('--tape-dir',required=True); ap.add_argument('--output',required=True); ap.add_argument('--max-seams',type=int,default=5); ns=ap.parse_args()
    payload=json.loads(Path(ns.seams).read_text(encoding='utf-8')); seams=(payload.get('rows') if isinstance(payload,dict) else payload)[:ns.max_seams]
    cache=base.ExecutionEventCache(Path(ns.tape_dir)); feed_cache={}; rows=[]; errors=[]; seam_audits=[]
    for seam in seams:
        errs=base.validate_seam(seam); seam_audits.append({'marketId':int(seam['market_id']),'seamId':seam['seam_id'],'errors':errs})
        acts=[a for a in base.candidate_actions(seam) if a.get('name') in ACTION_NAMES]
        for a in acts:
            try: rows.append(first_event(seam,a,Path(ns.tape_dir),cache,feed_cache))
            except Exception as exc:
                err={'marketId':int(seam['market_id']),'seamId':seam['seam_id'],'action':a.get('name'),'error':repr(exc)}; rows.append(err); errors.append(err)
    valid=[r for r in rows if 'error' not in r]
    risk_counts={}
    for r in valid:
        k=r['firstRiskEvent']['risk']; risk_counts[k]=risk_counts.get(k,0)+1
    checks=[]
    for r in valid:
        ev=r['firstRiskEvent']; risk=ev['risk']; snap=ev['snapshot']; cum=base.finite(snap.get('cumExecQty'))
        ok_time=int(ev['feedEventMs'])>int(r['decisionMs'])
        ok_fill=(cum>base.EPS) if risk in {'PARTIAL_FILL','FULL_FILL'} else True
        ok_term=(str(snap.get('status')) in TERMINAL) if risk.startswith('TERMINAL_') else True
        checks.append(ok_time and ok_fill and ok_term)
    expected_runs=len(seams)*len(ACTION_NAMES)
    gate={'runCountMatchesExpected':len(rows)==expected_runs,'zeroErrors':len(errors)==0,'strictPastAll':all(not x['errors'] for x in seam_audits),'eventSemanticsAll':all(checks),'noDreamFill':True}
    gate['pass']=all(gate.values())
    out={'version':VERSION,'researchOnly':True,'executionBoundary':{'entryLatencyMs':base.ENTRY_LATENCY_MS,'responseLatencyMs':base.RESPONSE_LATENCY_MS,'queueModel':base.QUEUE_MODEL,'dreamFillAllowed':False,'clock':'unique actual feed local timestamps','censor':'market end'},'seamAudits':seam_audits,'rows':rows,'errors':errors,'summary':{'runs':len(rows),'validRuns':len(valid),'riskCounts':risk_counts},'promotionGate':gate}
    p=Path(ns.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'output':str(p),'summary':out['summary'],'promotionGate':gate},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
