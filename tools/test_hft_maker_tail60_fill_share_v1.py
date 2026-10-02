from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path
try:
    from scipy.stats import spearmanr
except Exception:
    spearmanr = None

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_maker_tail60_fill_share_v1_report.json'
TEST_ID='HFT_MAKER_TAIL60_FILL_SHARE_V1'


def median(xs):
    return statistics.median(xs) if xs else None

def rankdata(vals):
    idx=sorted(range(len(vals)), key=lambda i: vals[i]); r=[0.0]*len(vals); j=0
    while j<len(idx):
        k=j+1
        while k<len(idx) and vals[idx[k]]==vals[idx[j]]: k+=1
        rr=(j+1+k)/2.0
        for z in range(j,k): r[idx[z]]=rr
        j=k
    return r

def corr(a,b):
    if len(a)<2:return float('nan')
    ra,rb=rankdata(a),rankdata(b); ma=sum(ra)/len(ra); mb=sum(rb)/len(rb)
    num=sum((x-ma)*(y-mb) for x,y in zip(ra,rb)); da=sum((x-ma)**2 for x in ra); db=sum((y-mb)**2 for y in rb)
    return num/math.sqrt(da*db) if da>0 and db>0 else float('nan')

c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row
runs=c.execute("SELECT strategy_key,market_id,window_end_ms,realized_pnl_usdt FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL AND realized_pnl_usdt IS NOT NULL AND strategy_key IN ('R2','CAP100')").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
matched={m:v for m,v in by.items() if 'R2' in v and 'CAP100' in v}
rows=[]
for mid,v in sorted(matched.items()):
    metrics={}
    ok=True
    for key in ('R2','CAP100'):
        end=int(v[key]['window_end_ms'] or 0)
        fs=c.execute("SELECT shares,fill_ms FROM hft_forward_fills_v1 WHERE strategy_key=? AND market_id=? AND channel='MAKER'",(key,mid)).fetchall()
        total=sum(float(x['shares'] or 0.0) for x in fs)
        if end<=0 or total<=0: ok=False; break
        tail=sum(float(x['shares'] or 0.0) for x in fs if int(x['fill_ms'] or 0)>=end-60000)
        metrics[key]=(tail/total,total,tail)
    if not ok: continue
    dp=float(v['CAP100']['realized_pnl_usdt'])-float(v['R2']['realized_pnl_usdt'])
    dt=metrics['CAP100'][0]-metrics['R2'][0]
    rows.append({'marketId':mid,'r2Tail60Share':metrics['R2'][0],'cap100Tail60Share':metrics['CAP100'][0],'deltaTail60Share':dt,'deltaPnl':dp})
c.close()
xs=[r['deltaTail60Share'] for r in rows]; ys=[r['deltaPnl'] for r in rows]
rho=float(spearmanr(xs,ys).statistic) if spearmanr and len(rows)>1 else corr(xs,ys)
low=[r['deltaPnl'] for r in rows if r['deltaTail60Share']< -1e-12]
high=[r['deltaPnl'] for r in rows if r['deltaTail60Share']> 1e-12]
eq=[r['deltaPnl'] for r in rows if abs(r['deltaTail60Share'])<=1e-12]
keep=len(rows)>=30 and math.isfinite(rho) and rho<=-0.20 and low and high and median(low)>median(high)
status='TESTED_KEEP_SIGNAL' if keep else ('TESTED_INCONCLUSIVE' if len(rows)<30 else 'TESTED_REJECTED')
report={
 'testId':TEST_ID,'axis':'MAKER_TAIL60_FILL_SHARE','matchedSettledMarkets':len(matched),'qualifyingMarkets':len(rows),
 'primary':{'spearmanDeltaTail60ShareVsDeltaPnl':rho,'capLowerTail60ShareMarkets':len(low),'capHigherTail60ShareMarkets':len(high),'equalTail60ShareMarkets':len(eq),'medianDeltaPnlCapLowerTail60Share':median(low),'medianDeltaPnlCapHigherTail60Share':median(high),'medianDeltaPnlEqualTail60Share':median(eq)},
 'fixedKeepRule':{'minQualifyingMarkets':30,'spearmanMax':-0.20,'groupMedianRule':'lower tail60 share median delta PnL > higher tail60 share median delta PnL'},
 'status':status,
 'interpretation':('Terminal Maker-fill concentration meets the preregistered signal rule; keep as research signal only, no frozen-policy change.' if keep else ('Too few qualifying markets; no inference.' if len(rows)<30 else 'Terminal Maker-fill concentration does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.')),
 'rows':rows
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
