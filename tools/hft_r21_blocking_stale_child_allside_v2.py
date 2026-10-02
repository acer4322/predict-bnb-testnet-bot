from __future__ import annotations

import argparse,json,math,sys,warnings
from pathlib import Path
from typing import Any
import joblib

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke

OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
MODEL_PATH=OUT/'hft_r21_long_resting_patience_model_v2.joblib'
EPS=1e-9

def finite(v,d=0.0):
    try:x=float(v)
    except:return float(d)
    return x if math.isfinite(x) else float(d)

def vec(es:dict[str,Any],child:dict[str,Any],side:str)->list[float]:
    b=es.get('outcomeBook') or {}; p=es.get('actualPortfolio') or {}
    bid=finite(b.get('up_bid' if side=='UP' else 'down_bid')); ask=finite(b.get('up_ask' if side=='UP' else 'down_ask'))
    price=finite(child.get('price')); rq=finite(child.get('requestedQty')); cq=finite(child.get('cumExecQty')); rem=finite(child.get('leavesQty'))
    return [finite(child.get('ageMs')),price,rem,0.0 if rq<=EPS else max(0.0,min(1.0,cq/rq)),1.0,1.0 if any(c for s,c in (es.get('activeMakerChildren') or {}).items() if s!=side) else 0.0,(bid-price)/0.01 if bid and price else 0.0,bid,ask,finite(b.get('up_spread_ticks' if side=='UP' else 'down_spread_ticks')),0.0,0.0,finite(p.get('maker_abs_net')),finite(p.get('combined_abs_net')),finite(p.get('combined_paired_coverage')),finite(p.get('worst_case_floor'))]

class Behavior:
    def __init__(self,age_ms:int|None,prob_cut:float=0.20):
        art=joblib.load(MODEL_PATH); self.model=art['model'] if isinstance(art,dict) else art; self.age_ms=age_ms; self.prob_cut=prob_cut; self.retired=set(); self.events=[]
    def __call__(self,state:dict[str,Any])->dict[str,Any]|None:
        if self.age_ms is None:return None
        es=state.get('executionState') or {}; now=int(state.get('atMs') or 0)
        candidates=[]
        for side,c in (es.get('activeMakerChildren') or {}).items():
            if not c or side in self.retired: continue
            if int(c.get('ageMs') or 0)<self.age_ms: continue
            if finite(c.get('cumExecQty'))>EPS: continue
            if bool(c.get('cancelPending')): continue
            status=str(c.get('status') or 'NONE')
            if status not in {'NEW','PARTIALLY_FILLED'}: continue
            pr=float(self.model.predict_proba([vec(es,c,side)])[0][1])
            candidates.append((pr,-int(c.get('ageMs') or 0),side,c))
        if not candidates:return None
        candidates.sort()
        pr,neg_age,side,c=candidates[0]
        if pr>self.prob_cut:return None
        self.retired.add(side);self.events.append({'atMs':now,'side':side,'ageMs':-neg_age,'pFill30':pr,'orderNum':c.get('orderNum'),'action':'REQUEST_RETIRE'})
        return {'retireMakerSide':side}

def run_one(mid:int,age:int|None)->dict[str,Any]:
    beh=Behavior(age)
    rep=run_smoke(mid,passive_mode='offset0',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,behavior_policy_override=beh,behavior_ownstate_reentry=True,fault_reentry_enabled=True,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
    a=rep['actualExecution']; lac=rep['lifecycleAudit']; p=a['finalPortfolio']
    return {'marketId':mid,'thresholdSec':None if age is None else age/1000,'requestEvents':beh.events,'requestCount':len(beh.events),'cancelRequested':int(lac.get('actionCounts',{}).get('ALLSIDE_STALE_RETIRE_CANCEL_REQUESTED',0)),'ownershipRetired':int(lac.get('ownershipEventCounts',{}).get('OWNERSHIP_RETIRED_TO_CONTROLLER',0)),'area':float(a.get('targetErrorAreaShareSeconds') or 0.0),'residual':float(a.get('finalAbsTrackingError') or 0.0),'makerFilledShares':float(a.get('makerFilledShares') or 0.0),'takerFilledShares':float(a.get('takerFilledShares') or 0.0),'pnlAudit':a.get('realizedPnl'),'floorAudit':finite(p.get('worst_case_floor')),'violations':int(rep.get('cycleInvariantViolationCount') or 0),'semanticGate':rep.get('semanticGate'),'actionCounts':lac.get('actionCounts') or {},'ownershipEvents':lac.get('ownershipEventCounts') or {}}

def red(b,c):return None if abs(b)<=EPS else (b-c)/abs(b)

def main():
    warnings.filterwarnings('ignore')
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--ages',default='60,90,120');ap.add_argument('--output',default='hft_r21_blocking_stale_child_allside_v2_report.json');args=ap.parse_args()
    mids=[int(x) for x in args.market_ids.split(',') if x];ages=[int(float(x)*1000) for x in args.ages.split(',') if x];rows=[]
    for m in mids:
        for a in [None]+ages:
            r=run_one(m,a);rows.append(r);print(json.dumps({'m':m,'age':r['thresholdSec'],'req':r['requestCount'],'cancel':r['cancelRequested'],'retired':r['ownershipRetired'],'area':r['area'],'resid':r['residual'],'maker':r['makerFilledShares'],'viol':r['violations']},ensure_ascii=False),flush=True)
    comps=[]
    for m in mids:
        b=next(r for r in rows if r['marketId']==m and r['thresholdSec'] is None)
        for a in ages:
            c=next(r for r in rows if r['marketId']==m and r['thresholdSec']==a/1000)
            comps.append({'marketId':m,'thresholdSec':a/1000,'requestCount':c['requestCount'],'cancelRequested':c['cancelRequested'],'ownershipRetired':c['ownershipRetired'],'areaReduction':red(b['area'],c['area']),'residualReduction':red(b['residual'],c['residual']),'makerFillDelta':c['makerFilledShares']-b['makerFilledShares'],'takerFillDelta':c['takerFilledShares']-b['takerFilledShares'],'pnlAuditDelta':None if b['pnlAudit'] is None or c['pnlAudit'] is None else c['pnlAudit']-b['pnlAudit'],'floorAuditDelta':c['floorAudit']-b['floorAudit'],'violations':c['violations']})
    payload={'version':'HFT_R21_BLOCKING_STALE_CHILD_ALLSIDE_V2','researchOnly':True,'graduationEligible':False,'rows':rows,'comparisons':comps,'decision':'PILOT_ONLY_NO_PROMOTION'}
    (OUT/args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT/args.output),'comparisons':comps},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
