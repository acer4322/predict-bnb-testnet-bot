from __future__ import annotations

import json, math, sqlite3, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_taker_execution_price_cost_v1_report.json'

def ranks(vals):
    order=sorted(range(len(vals)), key=lambda i: vals[i]); r=[0.0]*len(vals); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and vals[order[j]]==vals[order[i]]: j+=1
        rr=(i+j-1)/2+1
        for k in range(i,j): r[order[k]]=rr
        i=j
    return r

def corr(x,y):
    if len(x)<2:return math.nan
    mx=sum(x)/len(x); my=sum(y)/len(y)
    sx=sum((a-mx)**2 for a in x); sy=sum((b-my)**2 for b in y)
    if sx<=0 or sy<=0:return math.nan
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sx*sy)

con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True,timeout=20); con.row_factory=sqlite3.Row
rows=con.execute('''
WITH tp AS (
 SELECT strategy_key,market_id,
        SUM(price*shares)/SUM(shares) AS wavg_taker_price,
        SUM(shares) AS taker_shares
 FROM hft_forward_fills_v1
 WHERE channel='TAKER' AND shares>0
 GROUP BY strategy_key,market_id
)
SELECT r.market_id,r.realized_pnl_usdt r2_pnl,c.realized_pnl_usdt cap_pnl,
       tr.wavg_taker_price r2_price,tc.wavg_taker_price cap_price,
       tr.taker_shares r2_taker_shares,tc.taker_shares cap_taker_shares
FROM hft_forward_runs_v1 r
JOIN hft_forward_runs_v1 c ON c.market_id=r.market_id AND c.strategy_key='CAP100'
JOIN tp tr ON tr.market_id=r.market_id AND tr.strategy_key='R2'
JOIN tp tc ON tc.market_id=r.market_id AND tc.strategy_key='CAP100'
WHERE r.strategy_key='R2' AND r.status='COMPLETE' AND c.status='COMPLETE'
 AND r.winner IS NOT NULL AND c.winner IS NOT NULL
 AND r.realized_pnl_usdt IS NOT NULL AND c.realized_pnl_usdt IS NOT NULL
ORDER BY r.window_end_ms,r.market_id
''').fetchall(); con.close()

data=[]
for z in rows:
    dp=float(z['cap_price'])-float(z['r2_price']); pnl=float(z['cap_pnl'])-float(z['r2_pnl'])
    data.append({'marketId':int(z['market_id']),'r2WeightedTakerPrice':float(z['r2_price']),'capWeightedTakerPrice':float(z['cap_price']),'deltaWeightedTakerPrice':dp,'deltaPnl':pnl,'r2TakerShares':float(z['r2_taker_shares']),'capTakerShares':float(z['cap_taker_shares'])})

x=[d['deltaWeightedTakerPrice'] for d in data]; y=[d['deltaPnl'] for d in data]
rho=corr(ranks(x),ranks(y))
lower=[d['deltaPnl'] for d in data if d['deltaWeightedTakerPrice']<0]
higher=[d['deltaPnl'] for d in data if d['deltaWeightedTakerPrice']>0]
equal=[d['deltaPnl'] for d in data if abs(d['deltaWeightedTakerPrice'])<=1e-12]
neq=len(lower)+len(higher)
med_lower=statistics.median(lower) if lower else None; med_higher=statistics.median(higher) if higher else None; med_equal=statistics.median(equal) if equal else None
if neq<40 or not math.isfinite(rho): status='TESTED_INCONCLUSIVE'
elif rho<=-0.20 and med_lower is not None and med_higher is not None and med_lower>med_higher: status='TESTED_KEEP_SIGNAL'
else: status='TESTED_REJECTED'
rep={'testId':'HFT_TAKER_EXECUTION_PRICE_COST_V1','axis':'TAKER_EXECUTION_PRICE_COST','matchedQualifyingMarkets':len(data),'nonEqualMarkets':neq,'primary':{'spearmanDeltaWeightedTakerPriceVsDeltaPnl':rho,'capLowerTakerPriceMarkets':len(lower),'capHigherTakerPriceMarkets':len(higher),'equalTakerPriceMarkets':len(equal),'medianDeltaPnlCapLowerTakerPrice':med_lower,'medianDeltaPnlCapHigherTakerPrice':med_higher,'medianDeltaPnlEqualTakerPrice':med_equal},'status':status,'interpretation':('Confirmed Taker execution-price cost meets the preregistered signal rule; preserve for independent validation only.' if status=='TESTED_KEEP_SIGNAL' else 'Confirmed Taker execution-price cost does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Not enough matched markets where both strategies have confirmed Taker fills to decide this price-cost hypothesis.'),'execution':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP','dreamFillUsed':False,'rows':data}
OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:rep[k] for k in ['testId','matchedQualifyingMarkets','nonEqualMarkets','primary','status','interpretation']},ensure_ascii=False,indent=2))
