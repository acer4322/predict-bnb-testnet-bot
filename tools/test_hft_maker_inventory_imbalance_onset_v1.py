from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path

DB=Path('data/hft_forward_paper_v1.db')
OUT=Path('data/research/hourly_novel_tests/hft_maker_inventory_imbalance_onset_v1_report.json')

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

con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
runs=con.execute("select strategy_key,market_id,window_end_ms,realized_pnl_usdt from hft_forward_runs_v1 where status='COMPLETE' and winner is not null").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
markets=[m for m,v in by.items() if 'R2' in v and 'CAP100' in v]
rows=[]
for m in markets:
    vals={}
    ok=True
    for k in ('R2','CAP100'):
        rr=by[m][k]; end=int(rr['window_end_ms'] or 0); start=end-300000
        fs=con.execute("select side,shares,fill_ms,fill_seq from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='MAKER' order by fill_ms,fill_seq",(k,m)).fetchall()
        net=0.0; onset=None
        for f in fs:
            q=float(f['shares'] or 0.0)
            net += q if str(f['side'])=='UP' else -q
            if onset is None and abs(net)>1e-9:
                onset=int(f['fill_ms']); break
        if onset is None or end<=0:
            ok=False; break
        vals[k]=max(0, onset-start)
    if not ok: continue
    dp=float(by[m]['CAP100']['realized_pnl_usdt'])-float(by[m]['R2']['realized_pnl_usdt'])
    rows.append({'marketId':m,'r2OnsetLatencyMs':vals['R2'],'capOnsetLatencyMs':vals['CAP100'],'deltaOnsetLatencyMs':vals['CAP100']-vals['R2'],'deltaPnl':dp})
non=[r for r in rows if r['deltaOnsetLatencyMs']!=0]
rho=spearman([r['deltaOnsetLatencyMs'] for r in rows],[r['deltaPnl'] for r in rows])
later=[r['deltaPnl'] for r in rows if r['deltaOnsetLatencyMs']>0]
earlier=[r['deltaPnl'] for r in rows if r['deltaOnsetLatencyMs']<0]
eq=[r['deltaPnl'] for r in rows if r['deltaOnsetLatencyMs']==0]
status='TESTED_INCONCLUSIVE' if len(non)<30 else ('TESTED_KEEP_SIGNAL' if rho>=0.20 and statistics.median(later)>statistics.median(earlier) else 'TESTED_REJECTED')
out={
 'testId':'HFT_MAKER_INVENTORY_IMBALANCE_ONSET_V1','matchedMarkets':len(markets),'qualifyingMarkets':len(rows),'nonEqualMarkets':len(non),
 'primary':{
  'spearmanDeltaOnsetLatencyVsDeltaPnl':rho,
  'capLaterOnsetMarkets':len(later),'capEarlierOnsetMarkets':len(earlier),'equalOnsetMarkets':len(eq),
  'medianDeltaPnlCapLaterOnset':statistics.median(later) if later else None,
  'medianDeltaPnlCapEarlierOnset':statistics.median(earlier) if earlier else None,
  'medianDeltaPnlEqualOnset':statistics.median(eq) if eq else None,
  'medianR2OnsetLatencyMs':statistics.median([r['r2OnsetLatencyMs'] for r in rows]) if rows else None,
  'medianCapOnsetLatencyMs':statistics.median([r['capOnsetLatencyMs'] for r in rows]) if rows else None
 },
 'status':status,
 'interpretation':('Later first confirmed Maker-imbalance onset passes the preregistered explanatory rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient non-equal qualifying markets.' if status=='TESTED_INCONCLUSIVE' else 'First confirmed Maker-imbalance onset does not meet the preregistered explanatory rule; do not tune frozen policies around it.'))
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
