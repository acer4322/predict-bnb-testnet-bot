from __future__ import annotations

import json, math, sys
from pathlib import Path
from typing import Callable

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_target_ledger_executor_v0 as tl
from src.predict_bot import unified_controller_paper_v2 as mod

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OUT=D/'target_ledger_quote_depth_shadow_v0_report.json'
EPS=1e-9
MIDS=[1520549,1521630,1521634,1521898,1522206,1522236,1522287,1522364,1522567,1523086,1524387,1524491,1524504,1524659]


def quote_with_offset(offset_fn:Callable[[dict,str],int]):
    def q(book:dict[str,dict[float,float]],side:str,opposite_price:float|None)->float|None:
        bf=mod.outcome_book(book,None)
        if not bf:return None
        bid=float(bf['up_bid'] if side=='UP' else bf['down_bid'])
        off=max(0,int(offset_fn(bf,side)))
        tick=int(math.floor((bid+1e-9)/mod.GRID))-off
        tick=max(int(round(mod.MIN_PRICE/mod.GRID)),tick)
        price=round(tick*mod.GRID,2)
        if opposite_price is not None:
            while price+float(opposite_price)>mod.MAX_PAIR_PRICE_SUM+EPS:
                tick-=1
                if tick<int(round(mod.MIN_PRICE/mod.GRID)):return None
                price=round(tick*mod.GRID,2)
        return price
    return q

CONFIGS=[
 ('OFFSET_0_BID',lambda bf,s:0),
 ('OFFSET_1_BASE',lambda bf,s:1),
 ('OFFSET_2',lambda bf,s:2),
 ('OFFSET_3',lambda bf,s:3),
 ('ADAPT_SPREAD_1_2_3',lambda bf,s: 1 if ((float(bf['up_ask'] if s=='UP' else bf['down_ask'])-float(bf['up_bid'] if s=='UP' else bf['down_bid']))/mod.GRID)<=1.5 else 2 if ((float(bf['up_ask'] if s=='UP' else bf['down_ask'])-float(bf['up_bid'] if s=='UP' else bf['down_bid']))/mod.GRID)<=3.5 else 3),
]


def agg(rows):
    pnls=[float(r['makerOnlyPnl']) for r in rows if r['makerOnlyPnl'] is not None]
    desired=sum(float(r['desiredShares']) for r in rows); filled=sum(float(r['filledShares']) for r in rows)
    return {
      'markets':len(rows),'totalMakerOnlyPnl':sum(pnls),'wins':sum(x>EPS for x in pnls),'losses':sum(x<-EPS for x in pnls),
      'winRate':sum(x>EPS for x in pnls)/len(pnls) if pnls else None,
      'realizationRate':filled/desired if desired>EPS else None,'desiredShares':desired,'filledShares':filled,
      'meanFinalAbsNet':sum(float(r['finalAbsNet']) for r in rows)/len(rows),'maxFinalAbsNet':max(float(r['finalAbsNet']) for r in rows),
      'totalExposureAreaShareSeconds':sum(float(r['exposureAreaShareSeconds']) for r in rows),
      'submits':sum(int(r['submits']) for r in rows),'rejects':sum(int(r['rejects']) for r in rows)
    }


def main()->int:
    original=tl.r2_passive_quote
    report={'version':'TARGET_LEDGER_QUOTE_DEPTH_SHADOW_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'cohort':'FORWARD23_REFERENCE_ZERO_TAKER14','strategyIntentSource':'FROZEN_R2_PAPER_INTENT_TAPE','dreamFillUsedForPnl':False,'configs':[],'guardrails':['Fixed offsets chosen before results; no PnL sweep.','Maker-only zero-reference-Taker cohort isolates quote-depth execution effect.','Not formal graduation and not online exact Strategy Brain.','Pair cap and HftBacktest physics unchanged.']}
    try:
        for name,fn in CONFIGS:
            tl.r2_passive_quote=quote_with_offset(fn)
            rows=[]
            for i,mid in enumerate(MIDS,1):
                r=tl.run_market(mid); rows.append(r)
                print(json.dumps({'config':name,'progress':i,'marketId':mid,'pnl':r['makerOnlyPnl'],'realization':r['realizationRate'],'absNet':r['finalAbsNet']},ensure_ascii=False),flush=True)
            report['configs'].append({'name':name,'aggregate':agg(rows),'rows':rows})
    finally:
        tl.r2_passive_quote=original
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(OUT),'configs':[{'name':x['name'],**x['aggregate']} for x in report['configs']]},ensure_ascii=False,allow_nan=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
