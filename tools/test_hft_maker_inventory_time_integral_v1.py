from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path

DB=Path('data/hft_forward_paper_v1.db')
OUT=Path('data/research/hourly_novel_tests/hft_maker_inventory_time_integral_v1_report.json')
WINDOW_MS=300000

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

def avg_abs_net(con, strategy, market_id, window_end_ms):
    start=window_end_ms-WINDOW_MS
    fs=con.execute("select side,shares,fill_ms,fill_seq from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='MAKER' order by fill_ms,fill_seq",(strategy,market_id)).fetchall()
    net=0.0; prev=start; area=0.0
    for f in fs:
        t=max(start,min(window_end_ms,int(f['fill_ms'] or start)))
        if t>prev:
            area += abs(net)*(t-prev)
            prev=t
        q=float(f['shares'] or 0.0)
        net += q if str(f['side'])=='UP' else -q
    if window_end_ms>prev:
        area += abs(net)*(window_end_ms-prev)
    return area/WINDOW_MS

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
runs=con.execute("select strategy_key,market_id,window_end_ms,realized_pnl_usdt from hft_forward_runs_v1 where status='COMPLETE' and winner is not null and window_end_ms is not null").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
rows=[]
for m,v in by.items():
    if 'R2' not in v or 'CAP100' not in v: continue
    end=int(v['R2']['window_end_ms'])
    r2=avg_abs_net(con,'R2',m,end); cap=avg_abs_net(con,'CAP100',m,end)
    dp=float(v['CAP100']['realized_pnl_usdt'])-float(v['R2']['realized_pnl_usdt'])
    rows.append({'marketId':m,'r2AvgAbsMakerNet':r2,'capAvgAbsMakerNet':cap,'deltaTimeIntegratedExposure':cap-r2,'deltaPnl':dp})
con.close()
non=[r for r in rows if abs(r['deltaTimeIntegratedExposure'])>1e-12]
rho=spearman([r['deltaTimeIntegratedExposure'] for r in rows],[r['deltaPnl'] for r in rows])
low=[r['deltaPnl'] for r in rows if r['deltaTimeIntegratedExposure']<0]
high=[r['deltaPnl'] for r in rows if r['deltaTimeIntegratedExposure']>0]
eq=[r['deltaPnl'] for r in rows if abs(r['deltaTimeIntegratedExposure'])<=1e-12]
status='TESTED_INCONCLUSIVE' if len(non)<30 else ('TESTED_KEEP_SIGNAL' if rho<=-0.20 and statistics.median(low)>statistics.median(high) else 'TESTED_REJECTED')
out={
 'testId':'HFT_MAKER_INVENTORY_TIME_INTEGRAL_V1',
 'matchedMarkets':len(rows),
 'nonEqualMarkets':len(non),
 'primary':{
  'spearmanDeltaTimeIntegratedExposureVsDeltaPnl':rho,
  'capLowerExposureMarkets':len(low),
  'capHigherExposureMarkets':len(high),
  'equalExposureMarkets':len(eq),
  'medianDeltaPnlCapLowerExposure':statistics.median(low) if low else None,
  'medianDeltaPnlCapHigherExposure':statistics.median(high) if high else None,
  'medianDeltaPnlEqualExposure':statistics.median(eq) if eq else None,
  'medianR2AvgAbsMakerNet':statistics.median([r['r2AvgAbsMakerNet'] for r in rows]) if rows else None,
  'medianCapAvgAbsMakerNet':statistics.median([r['capAvgAbsMakerNet'] for r in rows]) if rows else None
 },
 'status':status,
 'interpretation':('Time-integrated confirmed Maker inventory exposure meets the preregistered signal rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient non-equal markets.' if status=='TESTED_INCONCLUSIVE' else 'Time-integrated Maker inventory exposure does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.'))
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
