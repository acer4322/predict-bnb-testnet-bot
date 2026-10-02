from __future__ import annotations
import argparse, json, math, types
from pathlib import Path
from typing import Any
import joblib, pandas as pd
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ART=D/'r2_fill_quality_explicit_v1.joblib'
OUT=D/'r2_markout_adapter_v0_report.json'

from tools import hftbacktest_r2_execution_school_v0 as school
from src.predict_bot import unified_controller_paper_v2 as mod

BUNDLE=joblib.load(ART)
MODEL=BUNDLE['models']['markout1s']
FEATURES=list(BUNDLE['features'])
GRID=float(mod.GRID)
EPS=1e-9


def fnum(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan


def hypothetical_frame(c:mod.UnifiedControllerPaperV2, side:str, offset:int, price:float)->pd.DataFrame:
    bf=mod.outcome_book(c.book.book,None) or {}
    bid=bf.get('up_bid') if side=='UP' else bf.get('down_bid')
    ask=bf.get('up_ask') if side=='UP' else bf.get('down_ask')
    native_side='bids' if side=='UP' else 'asks'
    native_price=round(price if side=='UP' else 1.0-price,10)
    init=float(c.book.book.get(native_side,{}).get(native_price,0.0))
    opp='DOWN' if side=='UP' else 'UP'
    same=[o for o in c.orders.values() if o.side==side]
    other=[o for o in c.orders.values() if o.side==opp]
    now=int(c.last_snapshot_ms or 0)
    port=c.inventory.features(now); port.pop('_combined_net',None)
    vals={
      'side_is_up':float(side=='UP'),'order_age_ms':0.0,'quote_price':float(price),
      'status_none':1.0,'status_new':0.0,'status_partial':0.0,'cum_exec_qty':0.0,
      'remaining_qty':float(mod.SHARES),'remaining_ratio':1.0,'partial_fill_ratio':0.0,
      'active_same_count':float(len(same)),'active_opp_count':float(len(other)),
      'quote_offset_ticks':float(offset),'current_bid':fnum(bid),'current_ask':fnum(ask),
      'current_spread_ticks':((float(ask)-float(bid))/GRID) if bid is not None and ask is not None else math.nan,
      'initial_depth':init,'public_cum_depletion':0.0,'public_depletion_ratio':0.0,
      'public_any_depletion':0.0,
    }
    vals.update(port)
    return pd.DataFrame([{f:vals.get(f,math.nan) for f in FEATURES}],columns=FEATURES)


def install_adapter(c:mod.UnifiedControllerPaperV2, audit:list[dict[str,Any]]):
    orig_quote=c._quote
    def adapted(self,side:str,offset:int=1):
        base=orig_quote(side,offset)
        if base is None:return None
        btick,bpx=base
        pred=float(MODEL.predict(hypothetical_frame(self,side,int(offset),float(bpx)))[0])
        # Natural price-grid mapping, preregistered: adverse expected markout is converted to integer ticks.
        # Base R2 already rests 1 tick behind best bid. Adapter can add at most two more ticks.
        extra=min(2,max(0,int(math.floor(max(0.0,-pred)+1e-12))))
        requested=int(offset)+extra
        chosen=orig_quote(side,requested)
        if chosen is None:chosen=base; requested=int(offset); extra=0
        audit.append({'atMs':int(self.last_snapshot_ms or 0),'side':side,'baseOffset':int(offset),
                      'predMarkout1sTicks':pred,'extraConcessionTicks':extra,'chosenOffset':requested,
                      'basePrice':float(bpx),'chosenPrice':float(chosen[1])})
        return chosen
    c._quote=types.MethodType(adapted,c)


def run_one(mid:int,adapter:bool)->dict[str,Any]:
    # Local copy of school.run_market hook: intercept new_controller only for this invocation.
    audit=[]
    orig_new=school.new_controller
    def hooked(a):
        c=orig_new(a)
        if adapter: install_adapter(c,audit)
        return c
    school.new_controller=hooked
    try:
        rep=school.run_market(mid)
    finally:
        school.new_controller=orig_new
    s=rep.get('studentRollout') or {}
    p=s.get('finalPortfolio') or {}
    return {'marketId':mid,'adapter':adapter,'makerPlacements':s.get('makerPlacements'),
            'makerFillEvents':s.get('makerFillEvents'),'makerFilledShares':s.get('makerFilledShares'),
            'makerCostUsdt':s.get('makerCostUsdt'),'takerFills':s.get('takerFills'),
            'makerAbsNet':p.get('maker_abs_net'),'makerPairedCoverage':p.get('maker_paired_coverage'),
            'combinedAbsNet':p.get('combined_abs_net'),'auditCount':len(audit),'audit':audit}


def summarize(rows:list[dict[str,Any]])->dict[str,Any]:
    def avg(k):
        z=[float(r[k]) for r in rows if r.get(k) is not None and math.isfinite(float(r[k]))]
        return sum(z)/len(z) if z else None
    ex=[a['extraConcessionTicks'] for r in rows for a in r.get('audit',[])]
    return {'markets':len(rows),'meanMakerPlacements':avg('makerPlacements'),'meanMakerFilledShares':avg('makerFilledShares'),
            'meanMakerAbsNet':avg('makerAbsNet'),'meanMakerPairedCoverage':avg('makerPairedCoverage'),
            'meanCombinedAbsNet':avg('combinedAbsNet'),'placementAudits':len(ex),
            'concessionCounts':{str(i):sum(x==i for x in ex) for i in (0,1,2)}}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--out',type=Path,default=OUT);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    base=[];adapt=[]
    for i,mid in enumerate(mids,1):
        base.append(run_one(mid,False));adapt.append(run_one(mid,True))
        print(json.dumps({'progress':i,'marketId':mid,'baseFilled':base[-1]['makerFilledShares'],'adaptFilled':adapt[-1]['makerFilledShares'],'adaptCoverage':adapt[-1]['makerPairedCoverage']},ensure_ascii=False),flush=True)
    rep={'version':'R2_MARKOUT_ADAPTER_V0','researchOnly':True,'liveTradingChanges':False,'dreamFillAllowed':False,
         'frozenR2Modified':False,'candidateV2Modified':False,
         'policy':'New placements only: base R2 quote offset plus floor(max(0,-predicted 1s adverse markout ticks)), capped at +2 ticks. Existing working orders are never repriced/cancelled by this adapter.',
         'base':summarize(base),'adapter':summarize(adapt),'rows':{'base':base,'adapter':adapt},
         'guardrails':['No Target/winner/settlement/PnL runtime input.','No dream fill.','No threshold sweep.','No old-queue cancellation.','Same Frozen R2 intent process; only new quote depth changes.']}
    a.out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(a.out),'base':rep['base'],'adapter':rep['adapter']},ensure_ascii=False))
if __name__=='__main__':main()
