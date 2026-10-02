from __future__ import annotations

import json, math, sqlite3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'hft_forward_paper_v1.db'
OUT = ROOT / 'data' / 'research' / 'hourly_novel_tests' / 'hft_maker_fill_price_dispersion_v1_report.json'
TEST_ID = 'HFT_MAKER_FILL_PRICE_DISPERSION_V1'


def weighted_std(rows):
    vals=[]; w=[]
    for r in rows:
        p=float(r['price']); q=float(r['shares'])
        if math.isfinite(p) and math.isfinite(q) and q>0:
            vals.append(p); w.append(q)
    if len(vals)<2 or sum(w)<=0: return None
    sw=sum(w); mu=sum(x*ww for x,ww in zip(vals,w))/sw
    var=sum(ww*(x-mu)**2 for x,ww in zip(vals,w))/sw
    return math.sqrt(max(0.0,var))

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
runs=con.execute("SELECT strategy_key,market_id,realized_pnl_usdt FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL AND strategy_key IN ('R2','CAP100')").fetchall()
by={}
for r in runs: by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=float(r['realized_pnl_usdt'])
rows=[]
for mid,d in sorted(by.items()):
    if set(d)!={'R2','CAP100'}: continue
    rec={'marketId':mid,'r2Pnl':d['R2'],'cap100Pnl':d['CAP100'],'deltaPnl':d['CAP100']-d['R2']}
    ok=True
    for sk in ('R2','CAP100'):
        fs=con.execute("SELECT price,shares FROM hft_forward_fills_v1 WHERE strategy_key=? AND market_id=? AND channel='MAKER' ORDER BY fill_seq",(sk,mid)).fetchall()
        sd=weighted_std(fs)
        if sd is None: ok=False; break
        rec[sk.lower()+'Dispersion']=sd; rec[sk.lower()+'MakerFillEvents']=len(fs)
    if ok:
        rec['deltaDispersion']=rec['cap100Dispersion']-rec['r2Dispersion']; rows.append(rec)
con.close()

df=pd.DataFrame(rows)
if len(df)>=2:
    rho=float(df['deltaDispersion'].rank(method='average').corr(df['deltaPnl'].rank(method='average')))
else: rho=float('nan')
less=df[df.deltaDispersion < -1e-12]; more=df[df.deltaDispersion > 1e-12]; equal=df[df.deltaDispersion.abs()<=1e-12]
med=lambda x: None if len(x)==0 else float(x.deltaPnl.median())
primary={
 'spearmanDeltaDispersionVsDeltaPnl':rho,
 'capLowerDispersionMarkets':int(len(less)),
 'capHigherDispersionMarkets':int(len(more)),
 'equalDispersionMarkets':int(len(equal)),
 'medianDeltaPnlCapLowerDispersion':med(less),
 'medianDeltaPnlCapHigherDispersion':med(more),
 'medianDeltaPnlEqualDispersion':med(equal),
}
if len(df)<30: status='TESTED_INCONCLUSIVE'
else:
    cond=(rho<=-0.20 and len(less)>0 and len(more)>0 and med(less)>med(more))
    status='TESTED_KEEP_SIGNAL' if cond else 'TESTED_REJECTED'
report={
 'testId':TEST_ID,
 'axis':'MAKER_FILL_PRICE_DISPERSION',
 'executionEvidence':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP',
 'matchedQualifyingMarkets':int(len(df)),
 'metric':'share-weighted within-market Maker fill price standard deviation',
 'primary':primary,
 'keepRule':'Spearman <= -0.20 AND median deltaPnl(CAP lower dispersion) > median deltaPnl(CAP higher dispersion); <30 qualifying => inconclusive.',
 'status':status,
 'interpretation':('Lower CAP100 Maker fill-price dispersion meets the preregistered explanatory signal rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient qualifying markets.' if status=='TESTED_INCONCLUSIVE' else 'Maker fill-price dispersion does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.')),
 'rows':rows,
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
