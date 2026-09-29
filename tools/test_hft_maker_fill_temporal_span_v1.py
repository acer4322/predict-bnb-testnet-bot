from __future__ import annotations
import json, sqlite3
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_maker_fill_temporal_span_v1_report.json'
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
        if len(fs)<2: ok=False; break
        t=[int(x['fill_ms']) for x in fs]
        rec[sk.lower()+'SpanMs']=max(t)-min(t); rec[sk.lower()+'MakerFillEvents']=len(t)
    if ok:
        rec['deltaSpanMs']=rec['cap100SpanMs']-rec['r2SpanMs']; rows.append(rec)
con.close()
df=pd.DataFrame(rows)
rho=float(df['deltaSpanMs'].rank(method='average').corr(df['deltaPnl'].rank(method='average'))) if len(df)>=2 else float('nan')
shorter=df[df.deltaSpanMs<0]; longer=df[df.deltaSpanMs>0]; equal=df[df.deltaSpanMs==0]
med=lambda x: None if len(x)==0 else float(x.deltaPnl.median())
primary={'spearmanDeltaSpanVsDeltaPnl':rho,'capShorterSpanMarkets':int(len(shorter)),'capLongerSpanMarkets':int(len(longer)),'equalSpanMarkets':int(len(equal)),'medianDeltaPnlCapShorterSpan':med(shorter),'medianDeltaPnlCapLongerSpan':med(longer),'medianDeltaPnlEqualSpan':med(equal)}
if len(df)<30: status='TESTED_INCONCLUSIVE'
else:
    keep=(rho<=-0.20 and len(shorter)>0 and len(longer)>0 and med(shorter)>med(longer))
    status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
report={'testId':'HFT_MAKER_FILL_TEMPORAL_SPAN_V1','axis':'MAKER_FILL_TEMPORAL_SPAN','executionEvidence':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP','matchedQualifyingMarkets':int(len(df)),'metric':'milliseconds from first to last confirmed Maker fill within market','primary':primary,'keepRule':'>=30 AND Spearman <= -0.20 AND median deltaPnl(CAP shorter span) > median deltaPnl(CAP longer span)','status':status,'interpretation':('Temporal span meets preregistered explanatory rule; retain only as research signal pending independent validation.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient qualifying markets.' if status=='TESTED_INCONCLUSIVE' else 'Maker fill temporal span does not meet preregistered explanatory-signal rule; do not tune frozen policies around it.')),'rows':rows}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
