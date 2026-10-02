from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path

DB=Path('data/hft_forward_paper_v1.db')
OUT=Path('data/research/hourly_novel_tests/hft_maker_inventory_zero_crossing_v1_report.json')

def rankdata(xs):
    order=sorted(range(len(xs)), key=lambda i: xs[i]); ranks=[0.0]*len(xs); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and xs[order[j]]==xs[order[i]]: j+=1
        r=(i+j-1)/2+1
        for k in range(i,j): ranks[order[k]]=r
        i=j
    return ranks

def corr(a,b):
    if len(a)<2:return float('nan')
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    va=sum((x-ma)**2 for x in a); vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return float('nan')
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)

def spearman(a,b): return corr(rankdata(a),rankdata(b))

def zero_crossings(con,strategy,market_id):
    fs=con.execute("select side,shares,fill_ms,fill_seq from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='MAKER' order by fill_ms,fill_seq",(strategy,market_id)).fetchall()
    net=0.0; last_nonzero_sign=0; crossings=0
    for f in fs:
        q=float(f['shares'] or 0.0)
        net += q if str(f['side'])=='UP' else -q
        sign=1 if net>1e-9 else (-1 if net<-1e-9 else 0)
        if sign!=0:
            if last_nonzero_sign!=0 and sign!=last_nonzero_sign: crossings += 1
            last_nonzero_sign=sign
    return crossings

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
runs=con.execute("select strategy_key,market_id,realized_pnl_usdt,total_cost_usdt from hft_forward_runs_v1 where status='COMPLETE' and winner is not null").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
rows=[]
for m,v in by.items():
    if 'R2' not in v or 'CAP100' not in v: continue
    r2=zero_crossings(con,'R2',m); cap=zero_crossings(con,'CAP100',m)
    dp=float(v['CAP100']['realized_pnl_usdt'])-float(v['R2']['realized_pnl_usdt'])
    rows.append({'marketId':m,'r2ZeroCrossings':r2,'capZeroCrossings':cap,'deltaZeroCrossings':cap-r2,'deltaPnl':dp})
con.close()
non=[r for r in rows if r['deltaZeroCrossings']!=0]
rho=spearman([r['deltaZeroCrossings'] for r in rows],[r['deltaPnl'] for r in rows])
low=[r['deltaPnl'] for r in rows if r['deltaZeroCrossings']<0]
high=[r['deltaPnl'] for r in rows if r['deltaZeroCrossings']>0]
eq=[r['deltaPnl'] for r in rows if r['deltaZeroCrossings']==0]
status='TESTED_INCONCLUSIVE' if len(non)<30 else ('TESTED_KEEP_SIGNAL' if rho<=-0.20 and low and high and statistics.median(low)>statistics.median(high) else 'TESTED_REJECTED')
out={
 'testId':'HFT_MAKER_INVENTORY_ZERO_CROSSING_V1',
 'matchedMarkets':len(rows),'nonEqualMarkets':len(non),
 'primary':{
  'spearmanDeltaZeroCrossingsVsDeltaPnl':rho,
  'capFewerCrossingsMarkets':len(low),'capMoreCrossingsMarkets':len(high),'equalCrossingsMarkets':len(eq),
  'medianDeltaPnlCapFewerCrossings':statistics.median(low) if low else None,
  'medianDeltaPnlCapMoreCrossings':statistics.median(high) if high else None,
  'medianDeltaPnlEqualCrossings':statistics.median(eq) if eq else None,
  'medianR2ZeroCrossings':statistics.median([r['r2ZeroCrossings'] for r in rows]) if rows else None,
  'medianCapZeroCrossings':statistics.median([r['capZeroCrossings'] for r in rows]) if rows else None
 },
 'status':status,
 'interpretation':('Confirmed Maker inventory zero-crossing/overshoot meets the preregistered explanatory rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient non-equal markets.' if status=='TESTED_INCONCLUSIVE' else 'Maker inventory zero-crossing/overshoot does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.'))
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
