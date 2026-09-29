from __future__ import annotations

import json, math, sqlite3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'hft_forward_paper_v1.db'
OUT = ROOT / 'data' / 'research' / 'hourly_novel_tests' / 'hft_maker_fill_price_drift_v1_report.json'
TEST_ID = 'HFT_MAKER_FILL_PRICE_DRIFT_V1'
EPS = 1e-12


def vwap(rows):
    sw=sum(float(r['shares']) for r in rows if float(r['shares'])>0)
    if sw<=0: return None
    return sum(float(r['price'])*float(r['shares']) for r in rows if float(r['shares'])>0)/sw


def strategy_drift(con, sk, mid):
    side_stats=[]
    total_weight=0.0
    for side in ('UP','DOWN'):
        fs=con.execute("SELECT fill_ms,price,shares FROM hft_forward_fills_v1 WHERE strategy_key=? AND market_id=? AND channel='MAKER' AND side=? ORDER BY fill_ms,fill_seq",(sk,mid,side)).fetchall()
        if len(fs)<2: continue
        cut=len(fs)//2
        early=fs[:cut]; late=fs[cut:]
        if not early or not late: continue
        ev=vwap(early); lv=vwap(late)
        if ev is None or lv is None or not (math.isfinite(ev) and math.isfinite(lv)): continue
        w=sum(float(r['shares']) for r in fs if float(r['shares'])>0)
        if w<=0: continue
        side_stats.append({'side':side,'earlyVwap':ev,'lateVwap':lv,'drift':lv-ev,'shares':w,'events':len(fs)})
        total_weight += w
    if not side_stats or total_weight<=0: return None, side_stats
    drift=sum(s['drift']*s['shares'] for s in side_stats)/total_weight
    return drift, side_stats

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
runs=con.execute("SELECT strategy_key,market_id,realized_pnl_usdt FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL AND strategy_key IN ('R2','CAP100')").fetchall()
by={}
for r in runs:
    by.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=float(r['realized_pnl_usdt'])
rows=[]
for mid,d in sorted(by.items()):
    if set(d)!={'R2','CAP100'}: continue
    r2, r2s = strategy_drift(con,'R2',mid)
    cap, caps = strategy_drift(con,'CAP100',mid)
    if r2 is None or cap is None: continue
    rows.append({
        'marketId':mid,'r2Pnl':d['R2'],'cap100Pnl':d['CAP100'],'deltaPnl':d['CAP100']-d['R2'],
        'r2MakerPriceDrift':r2,'cap100MakerPriceDrift':cap,'deltaMakerPriceDrift':cap-r2,
        'r2Sides':r2s,'cap100Sides':caps,
    })
con.close()

df=pd.DataFrame(rows)
if len(df)>=2:
    rho=float(df['deltaMakerPriceDrift'].rank(method='average').corr(df['deltaPnl'].rank(method='average')))
else: rho=float('nan')
less=df[df.deltaMakerPriceDrift < -EPS]; more=df[df.deltaMakerPriceDrift > EPS]; equal=df[df.deltaMakerPriceDrift.abs()<=EPS]
med=lambda x: None if len(x)==0 else float(x.deltaPnl.median())
primary={
    'spearmanDeltaMakerPriceDriftVsDeltaPnl':rho,
    'capLowerDriftMarkets':int(len(less)),
    'capHigherDriftMarkets':int(len(more)),
    'equalDriftMarkets':int(len(equal)),
    'medianDeltaPnlCapLowerDrift':med(less),
    'medianDeltaPnlCapHigherDrift':med(more),
    'medianDeltaPnlEqualDrift':med(equal),
}
if len(df)<30:
    status='TESTED_INCONCLUSIVE'
else:
    cond=(rho<=-0.20 and len(less)>0 and len(more)>0 and med(less)>med(more))
    status='TESTED_KEEP_SIGNAL' if cond else 'TESTED_REJECTED'
report={
    'testId':TEST_ID,
    'axis':'MAKER_FILL_PRICE_TEMPORAL_DRIFT',
    'executionEvidence':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP',
    'matchedQualifyingMarkets':int(len(df)),
    'metric':'Within each side, late-half share-weighted Maker fill VWAP minus early-half VWAP; side drifts aggregated by Maker shares.',
    'primary':primary,
    'keepRule':'At least 30 qualifying markets AND Spearman(delta drift, delta PnL) <= -0.20 AND median relative PnL when CAP100 drift<R2 drift is greater than when CAP100 drift>R2 drift.',
    'status':status,
    'interpretation':('Lower CAP100 early-to-late Maker price drift meets the preregistered explanatory-signal rule.' if status=='TESTED_KEEP_SIGNAL' else ('Insufficient qualifying markets.' if status=='TESTED_INCONCLUSIVE' else 'Early-to-late Maker fill price drift does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.')),
    'rows':rows,
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
