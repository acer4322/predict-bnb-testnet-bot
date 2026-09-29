from __future__ import annotations

import argparse, json, math, sys, warnings
from pathlib import Path
from typing import Any
import joblib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools import hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter as base

OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
MODEL_PATH = OUT / 'hft_r21_long_resting_patience_model_v2.joblib'
FEATURES = ['orderAgeMs','price','remainingQty','partialFillRatio','activeSameCount','activeOppCount','quoteOffsetTicks','currentBid','currentAsk','currentSpreadTicks','initialDepth','publicCumDepletion','maker_abs_net','combined_abs_net','combined_paired_coverage','worst_case_floor']
EPS=1e-9

def finite(v, d=0.0):
    try: x=float(v)
    except: return float(d)
    return x if math.isfinite(x) else float(d)

def vector_from_child(state:dict[str,Any], child:dict[str,Any], side:str)->list[float]:
    es=state.get('executionState') or {}
    book=es.get('outcomeBook') or {}
    p=es.get('actualPortfolio') or {}
    bid=finite(book.get('up_bid' if side=='UP' else 'down_bid'))
    ask=finite(book.get('up_ask' if side=='UP' else 'down_ask'))
    price=finite(child.get('price'))
    offset=(bid-price)/0.01 if bid and price else 0.0
    return [
        finite(child.get('ageMs')), price, finite(child.get('leavesQty')),
        0.0 if finite(child.get('requestedQty'))<=EPS else max(0.0,min(1.0,finite(child.get('cumExecQty'))/finite(child.get('requestedQty')))),
        sum(1 for s,c in (es.get('activeMakerChildren') or {}).items() if c and s==side),
        sum(1 for s,c in (es.get('activeMakerChildren') or {}).items() if c and s!=side),
        offset,bid,ask,finite(book.get('up_spread_ticks' if side=='UP' else 'down_spread_ticks')),
        0.0,0.0,finite(p.get('maker_abs_net')),finite(p.get('combined_abs_net')),finite(p.get('combined_paired_coverage')),finite(p.get('worst_case_floor'))
    ]

class AllSidePolicy:
    def __init__(self, age_ms:int|None, prob_cut:float=0.20):
        art=joblib.load(MODEL_PATH); self.model=art['model'] if isinstance(art,dict) else art
        self.age_ms=age_ms; self.prob_cut=prob_cut; self.retired=set(); self.returned=set(); self.events=[]
    def __call__(self,state:dict[str,Any])->str:
        default=str(state.get('defaultAction') or 'WAIT_FOR_CLARITY')
        if self.age_ms is None: return default
        es=state.get('executionState') or {}; children=es.get('activeMakerChildren') or {}
        now=int(state.get('atMs') or 0)
        # If an explicit retire has reached controller-owned unresolved state, return that side to passive repair.
        for side,owner in (es.get('remainderOwner') or {}).items():
            if owner and str(owner.get('state') or '')=='RETURNED_UNRESOLVED' and side not in self.returned:
                # lifecycle API can act only on state.side; request return only when scheduler reaches that side.
                if str(state.get('side') or '')==side:
                    self.returned.add(side); self.events.append({'atMs':now,'side':side,'action':'RETURN_TO_PASSIVE_REPAIR'})
                    return 'RETURN_TO_PASSIVE_REPAIR'
        # The adapter action API is side-scoped. To test an opposite stale child without changing Frozen R2,
        # temporarily select it only when lifecycle scheduler is already evaluating that side.
        cur=str(state.get('side') or '')
        c=children.get(cur)
        if not c or cur in self.retired: return default
        age=int(c.get('ageMs') or 0)
        if age<self.age_ms or finite(c.get('cumExecQty'))>EPS: return default
        vec=vector_from_child(state,c,cur)
        prob=float(self.model.predict_proba([vec])[0][1])
        if prob>self.prob_cut: return default
        self.retired.add(cur); self.events.append({'atMs':now,'side':cur,'action':'RETIRE_OBLIGATION','ageMs':age,'pFill30':prob,'childOrderNum':c.get('orderNum')})
        return 'RETIRE_OBLIGATION'

def run_one(mid:int, age:int|None)->dict[str,Any]:
    pol=AllSidePolicy(age)
    rep=run_smoke(mid,passive_mode='offset0',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,lifecycle_action_override=pol,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
    a=rep['actualExecution']; p=a['finalPortfolio']; lac=rep['lifecycleAudit']
    return {'marketId':mid,'thresholdSec':None if age is None else age/1000,'events':pol.events,'retireCount':sum(e['action']=='RETIRE_OBLIGATION' for e in pol.events),'returnCount':sum(e['action']=='RETURN_TO_PASSIVE_REPAIR' for e in pol.events),'area':float(a.get('targetErrorAreaShareSeconds') or 0.0),'residual':float(a.get('finalAbsTrackingError') or 0.0),'makerFilledShares':float(a.get('makerFilledShares') or 0.0),'pnlAudit':a.get('realizedPnl'),'violations':int(rep.get('cycleInvariantViolationCount') or 0),'ownershipEvents':lac.get('ownershipEventCounts') or {},'actionCounts':lac.get('actionCounts') or {},'blocked':sum(x.get('action')=='PASSIVE_RETURN_BLOCKED_LIVE_CHILD' for x in lac.get('decisions') or []),'semanticGate':rep.get('semanticGate')}

def red(b,c):
    return None if abs(b)<=EPS else (b-c)/abs(b)

def main():
    warnings.filterwarnings('ignore')
    ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--ages',default='60,90,120'); ap.add_argument('--output',default='hft_r21_blocking_stale_child_allside_v1_report.json'); args=ap.parse_args()
    mids=[int(x) for x in args.market_ids.split(',') if x]; ages=[int(float(x)*1000) for x in args.ages.split(',') if x]
    rows=[]
    for m in mids:
        for a in [None]+ages:
            r=run_one(m,a); rows.append(r); print(json.dumps({'m':m,'age':r['thresholdSec'],'retire':r['retireCount'],'return':r['returnCount'],'area':r['area'],'resid':r['residual'],'maker':r['makerFilledShares'],'viol':r['violations']},ensure_ascii=False),flush=True)
    comps=[]
    for m in mids:
        b=next(r for r in rows if r['marketId']==m and r['thresholdSec'] is None)
        for a in ages:
            c=next(r for r in rows if r['marketId']==m and r['thresholdSec']==a/1000)
            comps.append({'marketId':m,'thresholdSec':a/1000,'retireCount':c['retireCount'],'returnCount':c['returnCount'],'areaReduction':red(b['area'],c['area']),'residualReduction':red(b['residual'],c['residual']),'makerFillDelta':c['makerFilledShares']-b['makerFilledShares'],'pnlAuditDelta':None if b['pnlAudit'] is None or c['pnlAudit'] is None else c['pnlAudit']-b['pnlAudit'],'violations':c['violations'],'blocked':c['blocked']})
    payload={'version':'HFT_R21_BLOCKING_STALE_CHILD_ALLSIDE_V1','researchOnly':True,'graduationEligible':False,'rows':rows,'comparisons':comps,'decision':'PILOT_ONLY_NO_PROMOTION'}
    (OUT/args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT/args.output),'comparisons':comps},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
