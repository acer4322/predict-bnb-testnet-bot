from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_maker_fill_interarrival_burstiness_v1_report.json'
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
runs=con.execute("SELECT strategy_key,market_id,realized_pnl_usdt FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL AND strategy_key IN ('R2','CAP100')").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=float(r['realized_pnl_usdt'])
rows=[]
for mid,d in sorted(by.items()):
    if set(d)!={'R2','CAP100'}: continue
    rec={'marketId':mid,'r2Pnl':d['R2'],'cap100Pnl':d['CAP100'],'deltaPnl':d['CAP100']-d['R2']}; ok=True
    for sk in ('R2','CAP100'):
        fs=con.execute("SELECT fill_ms FROM hft_forward_fills_v1 WHERE strategy_key=? AND market_id=? AND channel='MAKER' ORDER BY fill_ms,fill_seq",(sk,mid)).fetchall()
        if len(fs)<3: ok=False; break
        t=[int(x['fill_ms']) for x in fs]
        gaps=[max(0,t[i]-t[i-1]) for i in range(1,len(t))]
        mean=sum(gaps)/len(gaps)
        cv=(statistics.pstdev(gaps)/mean) if mean>0 else 0.0
        rec[sk.lower()+'BurstinessCv']=float(cv)
        rec[sk.lower()+'MeanGapMs']=float(mean)
        rec[sk.lower()+'MakerFillEvents']=len(t)
    if ok:
        rec['deltaBurstinessCv']=rec['cap100BurstinessCv']-rec['r2BurstinessCv']
        rows.append(rec)
con.close()
df=pd.DataFrame(rows)
rho=float(df['deltaBurstinessCv'].rank(method='average').corr(df['deltaPnl'].rank(method='average'))) if len(df)>=2 else float('nan')
less=df[df.deltaBurstinessCv<0]; more=df[df.deltaBurstinessCv>0]; equal=df[df.deltaBurstinessCv==0]
med=lambda x: None if len(x)==0 else float(x.deltaPnl.median())
primary={'spearmanDeltaBurstinessVsDeltaPnl':rho,'capLessBurstyMarkets':int(len(less)),'capMoreBurstyMarkets':int(len(more)),'equalBurstinessMarkets':int(len(equal)),'medianDeltaPnlCapLessBursty':med(less),'medianDeltaPnlCapMoreBursty':med(more),'medianDeltaPnlEqualBurstiness':med(equal)}
if len(df)<30: status='TESTED_INCONCLUSIVE'
else:
    keep=(math.isfinite(rho) and rho<=-0.20 and len(less)>0 and len(more)>0 and med(less)>med(more))
    status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
report={'testId':'HFT_MAKER_FILL_INTERARRIVAL_BURSTINESS_V1','axis':'MAKER_FILL_INTERARRIVAL_BURSTINESS','executionEvidence':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP','matchedQualifyingMarkets':int(len(df)),'metric':'CV of consecutive confirmed Maker fill gaps within each market; delta = CAP100 - R2','primary':primary,'keepRule':'>=30 AND Spearman <= -0.20 AND median deltaPnl(CAP less bursty) > median deltaPnl(CAP more bursty)','status':status,'interpretation':('Inter-arrival burstiness meets preregistered explanatory rule; retain only as research signal pending independent validation.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient qualifying markets.' if status=='TESTED_INCONCLUSIVE' else 'Maker fill inter-arrival burstiness does not meet preregistered explanatory-signal rule; do not tune frozen policies around it.')),'rows':rows}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
