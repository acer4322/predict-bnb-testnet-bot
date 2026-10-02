from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path

DB=Path('data/hft_forward_paper_v1.db')
OUT=Path('data/research/hourly_novel_tests/hft_maker_inventory_build_velocity_v1_report.json')

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
markets=sorted(m for m,v in by.items() if 'R2' in v and 'CAP100' in v)
rows=[]
for m in markets:
    vals={}; qualifying=True
    for k in ('R2','CAP100'):
        fs=con.execute("select side,shares,fill_ms,fill_seq from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='MAKER' order by fill_ms,fill_seq",(k,m)).fetchall()
        net=0.0; path=[]
        for f in fs:
            q=float(f['shares'] or 0.0)
            net += q if str(f['side'])=='UP' else -q
            path.append((int(f['fill_ms']),abs(net)))
        nz=[x for x in path if x[1]>1e-12]
        if not nz:
            qualifying=False; break
        onset_ms=nz[0][0]
        peak=max(x[1] for x in nz)
        peak_ms=next(t for t,a in nz if abs(a-peak)<=1e-12)
        seconds=max(1.0,(peak_ms-onset_ms)/1000.0)
        vals[k]={'velocity':peak/seconds,'peak':peak,'onsetMs':onset_ms,'peakMs':peak_ms,'buildSeconds':seconds}
    if not qualifying: continue
    dp=float(by[m]['CAP100']['realized_pnl_usdt'])-float(by[m]['R2']['realized_pnl_usdt'])
    dv=vals['CAP100']['velocity']-vals['R2']['velocity']
    rows.append({'marketId':m,'r2BuildVelocity':vals['R2']['velocity'],'capBuildVelocity':vals['CAP100']['velocity'],'deltaBuildVelocity':dv,'deltaPnl':dp,'r2Peak':vals['R2']['peak'],'capPeak':vals['CAP100']['peak'],'r2BuildSeconds':vals['R2']['buildSeconds'],'capBuildSeconds':vals['CAP100']['buildSeconds']})

non=[r for r in rows if abs(r['deltaBuildVelocity'])>1e-12]
rho=spearman([r['deltaBuildVelocity'] for r in rows],[r['deltaPnl'] for r in rows])
low=[r['deltaPnl'] for r in rows if r['deltaBuildVelocity']<0]
high=[r['deltaPnl'] for r in rows if r['deltaBuildVelocity']>0]
eq=[r['deltaPnl'] for r in rows if abs(r['deltaBuildVelocity'])<=1e-12]
status='TESTED_INCONCLUSIVE' if len(non)<30 else ('TESTED_KEEP_SIGNAL' if rho<=-0.20 and low and high and statistics.median(low)>statistics.median(high) else 'TESTED_REJECTED')
out={
 'testId':'HFT_MAKER_INVENTORY_BUILD_VELOCITY_V1',
 'matchedQualifyingMarkets':len(rows),'nonEqualMarkets':len(non),
 'primary':{
   'spearmanDeltaBuildVelocityVsDeltaPnl':rho,
   'capLowerVelocityMarkets':len(low),'capHigherVelocityMarkets':len(high),'equalVelocityMarkets':len(eq),
   'medianDeltaPnlCapLowerVelocity':statistics.median(low) if low else None,
   'medianDeltaPnlCapHigherVelocity':statistics.median(high) if high else None,
   'medianDeltaPnlEqualVelocity':statistics.median(eq) if eq else None,
   'medianR2BuildVelocity':statistics.median([r['r2BuildVelocity'] for r in rows]) if rows else None,
   'medianCapBuildVelocity':statistics.median([r['capBuildVelocity'] for r in rows]) if rows else None,
   'medianR2BuildSeconds':statistics.median([r['r2BuildSeconds'] for r in rows]) if rows else None,
   'medianCapBuildSeconds':statistics.median([r['capBuildSeconds'] for r in rows]) if rows else None
 },
 'status':status,
 'interpretation':('Slower pre-peak confirmed Maker inventory build velocity meets the preregistered explanatory rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient non-equal qualifying markets.' if status=='TESTED_INCONCLUSIVE' else 'Pre-peak Maker inventory build velocity does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.'))
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
