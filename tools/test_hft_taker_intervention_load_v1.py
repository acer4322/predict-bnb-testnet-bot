from __future__ import annotations

import json, math, sqlite3, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_taker_intervention_load_v1_report.json'


def ranks(vals):
    order=sorted(range(len(vals)), key=lambda i: vals[i])
    r=[0.0]*len(vals); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and vals[order[j]]==vals[order[i]]: j+=1
        rr=(i+j-1)/2+1
        for k in range(i,j): r[order[k]]=rr
        i=j
    return r


def corr(x,y):
    if len(x)<2:return math.nan
    mx=sum(x)/len(x); my=sum(y)/len(y)
    sx=sum((a-mx)**2 for a in x); sy=sum((b-my)**2 for b in y)
    if sx<=0 or sy<=0:return math.nan
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sx*sy)

con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True,timeout=20)
con.row_factory=sqlite3.Row
rows=con.execute('''
SELECT r.market_id,
       r.realized_pnl_usdt r2_pnl, c.realized_pnl_usdt cap_pnl,
       r.maker_filled_shares r2_maker, r.taker_filled_shares r2_taker,
       c.maker_filled_shares cap_maker, c.taker_filled_shares cap_taker
FROM hft_forward_runs_v1 r JOIN hft_forward_runs_v1 c ON c.market_id=r.market_id
WHERE r.strategy_key='R2' AND c.strategy_key='CAP100'
  AND r.status='COMPLETE' AND c.status='COMPLETE'
  AND r.winner IS NOT NULL AND c.winner IS NOT NULL
  AND r.realized_pnl_usdt IS NOT NULL AND c.realized_pnl_usdt IS NOT NULL
ORDER BY r.window_end_ms,r.market_id
''').fetchall(); con.close()

data=[]
for z in rows:
    rm=float(z['r2_maker'] or 0); rt=float(z['r2_taker'] or 0)
    cm=float(z['cap_maker'] or 0); ct=float(z['cap_taker'] or 0)
    if rm+rt<=0 or cm+ct<=0: continue
    rs=rt/(rm+rt); cs=ct/(cm+ct)
    data.append({'marketId':int(z['market_id']),'r2Share':rs,'capShare':cs,'deltaShare':cs-rs,'deltaPnl':float(z['cap_pnl'])-float(z['r2_pnl'])})

x=[d['deltaShare'] for d in data]; y=[d['deltaPnl'] for d in data]
rho=corr(ranks(x),ranks(y))
lower=[d['deltaPnl'] for d in data if d['deltaShare'] < -1e-12]
higher=[d['deltaPnl'] for d in data if d['deltaShare'] > 1e-12]
equal=[d['deltaPnl'] for d in data if abs(d['deltaShare']) <= 1e-12]
non_equal=len(lower)+len(higher)
med_lower=statistics.median(lower) if lower else None
med_higher=statistics.median(higher) if higher else None
med_equal=statistics.median(equal) if equal else None
if non_equal < 40 or not math.isfinite(rho):
    status='TESTED_INCONCLUSIVE'
elif rho <= -0.20 and med_lower is not None and med_higher is not None and med_lower > med_higher:
    status='TESTED_KEEP_SIGNAL'
else:
    status='TESTED_REJECTED'
rep={
 'testId':'HFT_TAKER_INTERVENTION_LOAD_V1','axis':'TAKER_INTERVENTION_LOAD',
 'matchedSettledMarkets':len(rows),'qualifyingMarkets':len(data),'nonEqualMarkets':non_equal,
 'primary':{
   'spearmanDeltaTakerInterventionShareVsDeltaPnl':rho,
   'capLowerInterventionShareMarkets':len(lower),'capHigherInterventionShareMarkets':len(higher),'equalShareMarkets':len(equal),
   'medianDeltaPnlCapLowerInterventionShare':med_lower,'medianDeltaPnlCapHigherInterventionShare':med_higher,
   'medianDeltaPnlEqualInterventionShare':med_equal,
 },
 'status':status,
 'interpretation':('Confirmed Taker intervention load meets the preregistered signal rule; preserve as a research signal for independent validation, not tuning.' if status=='TESTED_KEEP_SIGNAL' else 'Confirmed Taker intervention load does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Insufficient independent/non-equal evidence to decide the intervention-load hypothesis.'),
 'execution':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP','dreamFillUsed':False,'rows':data
}
OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:rep[k] for k in ['testId','matchedSettledMarkets','qualifyingMarkets','nonEqualMarkets','primary','status','interpretation']},ensure_ascii=False,indent=2))
