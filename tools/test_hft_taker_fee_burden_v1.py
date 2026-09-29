from __future__ import annotations

import json, math, sqlite3
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_taker_fee_burden_v1_report.json'


def rankdata(xs):
    order=sorted(range(len(xs)), key=lambda i: xs[i])
    ranks=[0.0]*len(xs); i=0
    while i<len(order):
        j=i
        while j+1<len(order) and xs[order[j+1]]==xs[order[i]]: j+=1
        r=(i+j+2)/2.0
        for k in range(i,j+1): ranks[order[k]]=r
        i=j+1
    return ranks

def pearson(a,b):
    if len(a)<2:return float('nan')
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    va=sum((x-ma)**2 for x in a); vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return float('nan')
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)

def spearman(a,b): return pearson(rankdata(a),rankdata(b))

c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
rows=c.execute('''
SELECT a.market_id,
 a.taker_fees_usdt r_fee,a.total_cost_usdt r_cost,a.realized_pnl_usdt r_pnl,
 b.taker_fees_usdt c_fee,b.total_cost_usdt c_cost,b.realized_pnl_usdt c_pnl
FROM hft_forward_runs_v1 a JOIN hft_forward_runs_v1 b USING(market_id)
WHERE a.strategy_key='R2' AND b.strategy_key='CAP100'
AND a.status='COMPLETE' AND b.status='COMPLETE'
AND a.winner IS NOT NULL AND b.winner IS NOT NULL
AND a.realized_pnl_usdt IS NOT NULL AND b.realized_pnl_usdt IS NOT NULL
ORDER BY a.market_id
''').fetchall()
vals=[]
for r in rows:
    rc=float(r['r_cost'] or 0); cc=float(r['c_cost'] or 0)
    if rc<=0 or cc<=0: continue
    rb=float(r['r_fee'] or 0)/rc; cb=float(r['c_fee'] or 0)/cc
    vals.append({'marketId':r['market_id'],'r2Burden':rb,'capBurden':cb,'deltaBurden':cb-rb,'deltaPnl':float(r['c_pnl'])-float(r['r_pnl'])})
non=[x for x in vals if abs(x['deltaBurden'])>1e-12]
rho=spearman([x['deltaBurden'] for x in non],[x['deltaPnl'] for x in non]) if len(non)>=2 else float('nan')
lo=[x['deltaPnl'] for x in non if x['deltaBurden']<0]; hi=[x['deltaPnl'] for x in non if x['deltaBurden']>0]; eq=[x['deltaPnl'] for x in vals if abs(x['deltaBurden'])<=1e-12]
if len(non)<30: status='TESTED_INCONCLUSIVE'
elif math.isfinite(rho) and rho<=-0.20 and lo and hi and median(lo)>median(hi): status='TESTED_KEEP_SIGNAL'
else: status='TESTED_REJECTED'
out={'testId':'HFT_TAKER_FEE_BURDEN_V1','matchedSettledMarkets':len(rows),'qualifyingMarkets':len(vals),'nonEqualMarkets':len(non),'primary':{'spearmanDeltaFeeBurdenVsDeltaPnl':rho,'capLowerFeeBurdenMarkets':len(lo),'capHigherFeeBurdenMarkets':len(hi),'equalFeeBurdenMarkets':len(eq),'medianDeltaPnlCapLowerFeeBurden':median(lo) if lo else None,'medianDeltaPnlCapHigherFeeBurden':median(hi) if hi else None,'medianDeltaPnlEqualFeeBurden':median(eq) if eq else None},'status':status,'interpretation':('Confirmed Taker fee burden meets the preregistered explanatory rule; preserve only as a research signal pending independent validation.' if status=='TESTED_KEEP_SIGNAL' else 'Confirmed Taker fee burden does not meet the preregistered explanatory rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Too few non-equal markets for a stable conclusion.')}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
