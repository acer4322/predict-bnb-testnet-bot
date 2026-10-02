import json, sqlite3, statistics, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_maker_to_first_taker_handoff_lag_v1_report.json'
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True)
con.row_factory=sqlite3.Row
runs=con.execute("SELECT strategy_key,market_id,realized_pnl_usdt FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL AND realized_pnl_usdt IS NOT NULL").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=r
fills=con.execute("SELECT strategy_key,market_id,channel,fill_ms FROM hft_forward_fills_v1 ORDER BY market_id,strategy_key,fill_ms,fill_seq").fetchall()
fb={}
for r in fills: fb.setdefault((str(r['strategy_key']),int(r['market_id'])),[]).append((str(r['channel']),int(r['fill_ms'])))
rows=[]
for mid,d in by.items():
    if 'R2' not in d or 'CAP100' not in d: continue
    vals={}
    ok=True
    for sk in ('R2','CAP100'):
        fs=fb.get((sk,mid),[])
        tak=[t for ch,t in fs if ch=='TAKER']
        if not tak: ok=False; break
        ft=min(tak)
        mk=[t for ch,t in fs if ch=='MAKER' and t<=ft]
        if not mk: ok=False; break
        vals[sk]=ft-max(mk)
    if not ok: continue
    dp=float(d['CAP100']['realized_pnl_usdt'])-float(d['R2']['realized_pnl_usdt'])
    rows.append({'marketId':mid,'r2LagMs':vals['R2'],'cap100LagMs':vals['CAP100'],'deltaLagMs':vals['CAP100']-vals['R2'],'deltaPnl':dp})
def ranks(x):
    order=sorted(range(len(x)), key=lambda i:x[i]); out=[0.0]*len(x); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and x[order[j]]==x[order[i]]: j+=1
        r=(i+j-1)/2+1
        for k in range(i,j): out[order[k]]=r
        i=j
    return out
def pear(a,b):
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    num=sum((x-ma)*(y-mb) for x,y in zip(a,b)); da=sum((x-ma)**2 for x in a); db=sum((y-mb)**2 for y in b)
    return num/math.sqrt(da*db) if da>0 and db>0 else float('nan')
rho=pear(ranks([r['deltaLagMs'] for r in rows]),ranks([r['deltaPnl'] for r in rows])) if len(rows)>=2 else float('nan')
longer=[r['deltaPnl'] for r in rows if r['deltaLagMs']>0]; shorter=[r['deltaPnl'] for r in rows if r['deltaLagMs']<0]; equal=[r['deltaPnl'] for r in rows if r['deltaLagMs']==0]
med=lambda a: statistics.median(a) if a else None
if len(rows)<30: status='TESTED_INCONCLUSIVE'
elif rho>=0.20 and longer and shorter and med(longer)>med(shorter): status='TESTED_KEEP_SIGNAL'
else: status='TESTED_REJECTED'
rep={'testId':'HFT_MAKER_TO_FIRST_TAKER_HANDOFF_LAG_V1','testedAt':datetime.now(timezone(timedelta(hours=8))).isoformat(),'axis':'MAKER_TO_FIRST_TAKER_HANDOFF_LAG','executionEvidence':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP','matchedQualifyingMarkets':len(rows),'primary':{'spearmanDeltaHandoffLagVsDeltaPnl':rho,'capLongerLagMarkets':len(longer),'capShorterLagMarkets':len(shorter),'equalLagMarkets':len(equal),'medianDeltaPnlCapLongerLag':med(longer),'medianDeltaPnlCapShorterLag':med(shorter),'medianDeltaPnlEqualLag':med(equal)},'status':status,'interpretation':('Local Maker-to-first-Taker handoff lag meets the preregistered signal rule; retain only as a research signal pending independent validation.' if status=='TESTED_KEEP_SIGNAL' else 'Local Maker-to-first-Taker handoff lag does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Too few qualifying official-HFT markets for a decision.'),'rows':rows}
OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
print(json.dumps({k:v for k,v in rep.items() if k!='rows'},ensure_ascii=False,indent=2,allow_nan=True))
