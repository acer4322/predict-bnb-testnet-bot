from __future__ import annotations

import json, math, sqlite3, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'hft_forward_paper_v1.db'
OUT = ROOT / 'data' / 'research' / 'hourly_novel_tests' / 'hft_first_taker_intervention_latency_v1_report.json'


def ranks(vals):
    order=sorted(range(len(vals)), key=lambda i: vals[i]); r=[0.0]*len(vals); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and vals[order[j]]==vals[order[i]]: j+=1
        rr=(i+j-1)/2+1
        for k in range(i,j): r[order[k]]=rr
        i=j
    return r


def corr(x,y):
    if len(x)<2: return math.nan
    mx=sum(x)/len(x); my=sum(y)/len(y)
    sx=sum((a-mx)**2 for a in x); sy=sum((b-my)**2 for b in y)
    if sx<=0 or sy<=0: return math.nan
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sx*sy)

con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro', uri=True, timeout=20)
con.row_factory=sqlite3.Row
rows=con.execute('''
WITH ft AS (
  SELECT strategy_key, market_id, MIN(fill_ms) AS first_taker_ms
  FROM hft_forward_fills_v1
  WHERE channel='TAKER' AND shares>0
  GROUP BY strategy_key, market_id
)
SELECT r.market_id, r.window_end_ms, r.realized_pnl_usdt r2_pnl, c.realized_pnl_usdt cap_pnl,
       fr.first_taker_ms r2_first_taker_ms, fc.first_taker_ms cap_first_taker_ms
FROM hft_forward_runs_v1 r
JOIN hft_forward_runs_v1 c ON c.market_id=r.market_id AND c.strategy_key='CAP100'
JOIN ft fr ON fr.market_id=r.market_id AND fr.strategy_key='R2'
JOIN ft fc ON fc.market_id=r.market_id AND fc.strategy_key='CAP100'
WHERE r.strategy_key='R2'
  AND r.status='COMPLETE' AND c.status='COMPLETE'
  AND r.winner IS NOT NULL AND c.winner IS NOT NULL
  AND r.realized_pnl_usdt IS NOT NULL AND c.realized_pnl_usdt IS NOT NULL
ORDER BY r.window_end_ms, r.market_id
''').fetchall()
con.close()

data=[]
for z in rows:
    start=int(z['window_end_ms'])-300000
    rl=int(z['r2_first_taker_ms'])-start
    cl=int(z['cap_first_taker_ms'])-start
    data.append({'marketId':int(z['market_id']), 'r2FirstTakerLatencyMs':rl, 'capFirstTakerLatencyMs':cl,
                 'deltaLatencyMs':cl-rl, 'deltaPnl':float(z['cap_pnl'])-float(z['r2_pnl'])})

x=[d['deltaLatencyMs'] for d in data]; y=[d['deltaPnl'] for d in data]
rho=corr(ranks(x), ranks(y))
later=[d['deltaPnl'] for d in data if d['deltaLatencyMs']>0]
earlier=[d['deltaPnl'] for d in data if d['deltaLatencyMs']<0]
equal=[d['deltaPnl'] for d in data if d['deltaLatencyMs']==0]
non_equal=len(later)+len(earlier)
med_later=statistics.median(later) if later else None
med_earlier=statistics.median(earlier) if earlier else None
med_equal=statistics.median(equal) if equal else None
if non_equal < 40 or not math.isfinite(rho):
    status='TESTED_INCONCLUSIVE'
elif rho >= 0.20 and med_later is not None and med_earlier is not None and med_later > med_earlier:
    status='TESTED_KEEP_SIGNAL'
else:
    status='TESTED_REJECTED'
rep={
  'testId':'HFT_FIRST_TAKER_INTERVENTION_LATENCY_V1',
  'axis':'FIRST_CONFIRMED_TAKER_INTERVENTION_LATENCY',
  'matchedSettledMarketsBothWithConfirmedTaker':len(data),
  'nonEqualMarkets':non_equal,
  'primary':{
    'spearmanDeltaFirstTakerLatencyVsDeltaPnl':rho,
    'capLaterFirstTakerMarkets':len(later),
    'capEarlierFirstTakerMarkets':len(earlier),
    'equalFirstTakerMarkets':len(equal),
    'medianDeltaPnlCapLaterFirstTaker':med_later,
    'medianDeltaPnlCapEarlierFirstTaker':med_earlier,
    'medianDeltaPnlEqualFirstTaker':med_equal,
  },
  'status':status,
  'interpretation':('First confirmed Taker intervention latency meets the preregistered signal rule; preserve for independent validation only.' if status=='TESTED_KEEP_SIGNAL' else 'First confirmed Taker intervention latency does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Not enough markets where both strategies have confirmed Taker fills to decide this timing hypothesis.'),
  'execution':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP',
  'dreamFillUsed':False,
  'rows':data,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:rep[k] for k in ['testId','matchedSettledMarketsBothWithConfirmedTaker','nonEqualMarkets','primary','status','interpretation']}, ensure_ascii=False, indent=2))
