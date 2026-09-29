from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path

DB=Path('data/hft_forward_paper_v1.db')
OUT=Path('data/research/hourly_novel_tests/hft_maker_max_inventory_excursion_v1_report.json')

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

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
runs=con.execute("select strategy_key,market_id,realized_pnl_usdt from hft_forward_runs_v1 where status='COMPLETE' and winner is not null").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
markets=[m for m,v in by.items() if 'R2' in v and 'CAP100' in v]
rows=[]
for m in markets:
    vals={}
    for k in ('R2','CAP100'):
        fs=con.execute("select side,shares,fill_ms,fill_seq from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='MAKER' order by fill_ms,fill_seq",(k,m)).fetchall()
        net=0.0; mx=0.0
        for f in fs:
            q=float(f['shares'] or 0)
            net += q if str(f['side'])=='UP' else -q
            mx=max(mx,abs(net))
        vals[k]=mx
    dp=float(by[m]['CAP100']['realized_pnl_usdt'])-float(by[m]['R2']['realized_pnl_usdt'])
    rows.append({'marketId':m,'r2MaxAbsMakerNet':vals['R2'],'capMaxAbsMakerNet':vals['CAP100'],'deltaExcursion':vals['CAP100']-vals['R2'],'deltaPnl':dp})
non=[r for r in rows if abs(r['deltaExcursion'])>1e-12]
rho=spearman([r['deltaExcursion'] for r in rows],[r['deltaPnl'] for r in rows])
low=[r['deltaPnl'] for r in rows if r['deltaExcursion']<0]; high=[r['deltaPnl'] for r in rows if r['deltaExcursion']>0]; eq=[r['deltaPnl'] for r in rows if abs(r['deltaExcursion'])<=1e-12]
status='TESTED_INCONCLUSIVE' if len(non)<30 else ('TESTED_KEEP_SIGNAL' if rho<=-0.20 and statistics.median(low)>statistics.median(high) else 'TESTED_REJECTED')
out={'testId':'HFT_MAKER_MAX_INVENTORY_EXCURSION_V1','matchedMarkets':len(rows),'nonEqualMarkets':len(non),'primary':{'spearmanDeltaExcursionVsDeltaPnl':rho,'capLowerExcursionMarkets':len(low),'capHigherExcursionMarkets':len(high),'equalExcursionMarkets':len(eq),'medianDeltaPnlCapLowerExcursion':statistics.median(low) if low else None,'medianDeltaPnlCapHigherExcursion':statistics.median(high) if high else None,'medianDeltaPnlEqualExcursion':statistics.median(eq) if eq else None},'status':status,'interpretation':'Peak confirmed Maker inventory excursion meets preregistered signal rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient non-equal markets.' if status=='TESTED_INCONCLUSIVE' else 'Peak confirmed Maker inventory excursion does not meet preregistered explanatory-signal rule; do not tune frozen policies around it.')}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
