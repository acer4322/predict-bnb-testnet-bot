from __future__ import annotations
import json, sqlite3, statistics, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_first_maker_fill_latency_v1_report.json'

def ranks(vals):
    order=sorted(range(len(vals)), key=lambda i: vals[i])
    r=[0.0]*len(vals); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and vals[order[j]]==vals[order[i]]: j+=1
        avg=(i+1+j)/2.0
        for k in range(i,j): r[order[k]]=avg
        i=j
    return r

def corr(a,b):
    if len(a)<2:return float('nan')
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    va=sum((x-ma)**2 for x in a); vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return float('nan')
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)

def median(xs): return statistics.median(xs) if xs else None
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
runs=con.execute("select strategy_key,market_id,window_end_ms,realized_pnl_usdt from hft_forward_runs_v1 where status='COMPLETE' and winner is not null").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
rows=[]
for mid,d in by.items():
    if 'R2' not in d or 'CAP100' not in d: continue
    vals={}
    for sk in ('R2','CAP100'):
        f=con.execute("select min(fill_ms) from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='MAKER' and shares>0",(sk,mid)).fetchone()[0]
        if f is None: break
        start=int(d[sk]['window_end_ms'])-300000
        vals[sk]=float(f-start)
    if len(vals)<2: continue
    dp=float(d['CAP100']['realized_pnl_usdt'])-float(d['R2']['realized_pnl_usdt'])
    dl=vals['CAP100']-vals['R2']
    rows.append((mid,dl,dp,vals['R2'],vals['CAP100']))
con.close()
non=[r for r in rows if abs(r[1])>1e-9]
rho=corr(ranks([r[1] for r in rows]),ranks([r[2] for r in rows])) if rows else float('nan')
long=[r[2] for r in rows if r[1]>0]; short=[r[2] for r in rows if r[1]<0]; equal=[r[2] for r in rows if abs(r[1])<=1e-9]
status='TESTED_INCONCLUSIVE' if len(non)<30 else ('TESTED_KEEP_SIGNAL' if rho>=0.20 and median(long)>median(short) else 'TESTED_REJECTED')
rep={
 'testId':'HFT_FIRST_MAKER_FILL_LATENCY_V1','matchedQualifyingMarkets':len(rows),'nonEqualMarkets':len(non),
 'primary':{'spearmanDeltaFirstMakerLatencyVsDeltaPnl':rho,'capLaterMarkets':len(long),'capEarlierMarkets':len(short),'equalMarkets':len(equal),'medianDeltaPnlCapLater':median(long),'medianDeltaPnlCapEarlier':median(short),'medianDeltaPnlEqual':median(equal)},
 'status':status,
 'interpretation':('Later passive realization onset meets preregistered explanatory rule; preserve only as research signal, no policy change.' if status=='TESTED_KEEP_SIGNAL' else 'First confirmed Maker-fill latency does not meet the preregistered explanatory rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Too few non-equal markets for a directional conclusion.'),
 'execution':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP','dreamFillUsed':False
}
OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
print(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True))
