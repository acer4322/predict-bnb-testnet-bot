from __future__ import annotations

import json, math, sqlite3, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'hft_forward_paper_v1.db'
OUT = ROOT / 'data' / 'research' / 'hourly_novel_tests' / 'hft_fill_fragmentation_v1_report.json'


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    j = 0
    while j < len(order):
        k = j + 1
        while k < len(order) and xs[order[k]] == xs[order[j]]:
            k += 1
        r = (j + 1 + k) / 2.0
        for z in range(j, k): out[order[z]] = r
        j = k
    return out


def pearson(a,b):
    if len(a) < 3: return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    xa=[x-ma for x in a]; xb=[x-mb for x in b]
    den=math.sqrt(sum(x*x for x in xa)*sum(y*y for y in xb))
    return None if den <= 0 else sum(x*y for x,y in zip(xa,xb))/den


def spearman(a,b):
    return pearson(rank(a), rank(b))

con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
runs=con.execute('''
SELECT strategy_key,market_id,realized_pnl_usdt,maker_filled_shares,maker_fill_events,total_cost_usdt,final_abs_net
FROM hft_forward_runs_v1
WHERE status='COMPLETE' AND winner IS NOT NULL AND realized_pnl_usdt IS NOT NULL
ORDER BY market_id,strategy_key
''').fetchall()
fills=con.execute('''
SELECT strategy_key,market_id,shares FROM hft_forward_fills_v1
WHERE channel='MAKER'
ORDER BY market_id,strategy_key,fill_seq
''').fetchall()
con.close()

fm={}
for r in fills:
    fm.setdefault((str(r['strategy_key']),int(r['market_id'])),[]).append(float(r['shares']))

by={}
for r in runs:
    key=(str(r['strategy_key']),int(r['market_id']))
    fs=fm.get(key,[])
    total=float(r['maker_filled_shares'] or 0.0)
    n=len(fs)
    by[key]={
        'pnl':float(r['realized_pnl_usdt']),
        'makerFilledShares':total,
        'makerFillEvents':n,
        'avgFillSize':(total/n if n else None),
        'fragmentationPer18':(n*18.0/total if total>0 and n else None),
        'partialEventFraction':(sum(1 for x in fs if x < 17.999)/n if n else None),
        'partialVolumeFraction':(sum(x for x in fs if x < 17.999)/total if total>0 else None),
        'capital':float(r['total_cost_usdt'] or 0.0),
        'finalAbsNet':float(r['final_abs_net'] or 0.0),
    }

mids=sorted(set(mid for sk,mid in by if sk=='R2') & set(mid for sk,mid in by if sk=='CAP100'))
pairs=[]
for mid in mids:
    a=by.get(('R2',mid)); b=by.get(('CAP100',mid))
    if not a or not b or a['fragmentationPer18'] is None or b['fragmentationPer18'] is None: continue
    pairs.append({
        'marketId':mid,
        'r2Frag':a['fragmentationPer18'], 'capFrag':b['fragmentationPer18'],
        'deltaFragCapMinusR2':b['fragmentationPer18']-a['fragmentationPer18'],
        'r2Pnl':a['pnl'],'capPnl':b['pnl'],'deltaPnlCapMinusR2':b['pnl']-a['pnl'],
        'r2PartialEventFraction':a['partialEventFraction'],'capPartialEventFraction':b['partialEventFraction'],
    })

df=[x['deltaFragCapMinusR2'] for x in pairs]
dp=[x['deltaPnlCapMinusR2'] for x in pairs]
rho=spearman(df,dp) if pairs else None
lower=[x['deltaPnlCapMinusR2'] for x in pairs if x['deltaFragCapMinusR2']<0]
higher=[x['deltaPnlCapMinusR2'] for x in pairs if x['deltaFragCapMinusR2']>0]
equal=[x['deltaPnlCapMinusR2'] for x in pairs if x['deltaFragCapMinusR2']==0]

def strat_summary(sk):
    rows=[v for (s,_),v in by.items() if s==sk and v['fragmentationPer18'] is not None]
    fr=[x['fragmentationPer18'] for x in rows]
    pp=[x['partialEventFraction'] for x in rows if x['partialEventFraction'] is not None]
    pnl=[x['pnl'] for x in rows]
    return {
        'markets':len(rows),
        'medianFragmentationPer18':statistics.median(fr) if fr else None,
        'meanFragmentationPer18':statistics.fmean(fr) if fr else None,
        'medianPartialEventFraction':statistics.median(pp) if pp else None,
        'spearmanFragmentationVsPnl':spearman(fr,pnl) if len(fr)>=3 else None,
    }

signal = bool(rho is not None and rho <= -0.25 and lower and higher and statistics.median(lower) > statistics.median(higher))
status = 'TESTED_KEEP_SIGNAL' if signal else ('TESTED_REJECTED' if len(pairs)>=20 else 'TESTED_INCONCLUSIVE')
report={
    'testId':'HFT_FILL_FRAGMENTATION_V1',
    'novelAxis':'PARTIAL_FILL_FRAGMENTATION',
    'preregisteredHypothesis':'CAP100 relative PnL advantage should increase when CAP100 realizes the same Maker volume with less partial-fill fragmentation than R2.',
    'primaryMetric':'Spearman(delta fragmentation per 18 shares, CAP100-R2 realized PnL). Expected direction: negative.',
    'fixedSignalRule':'KEEP_SIGNAL only if rho<=-0.25 and median CAP100-R2 PnL is higher in markets where CAP100 fragmentation<R2 than where CAP100 fragmentation>R2. No threshold sweep.',
    'executionEvidence':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP',
    'dreamFillUsed':False,
    'matchedMarkets':len(pairs),
    'primary':{
        'spearmanDeltaFragmentationVsDeltaPnl':rho,
        'capLessFragmentedMarkets':len(lower),
        'capMoreFragmentedMarkets':len(higher),
        'equalFragmentationMarkets':len(equal),
        'medianDeltaPnlWhenCapLessFragmented':statistics.median(lower) if lower else None,
        'medianDeltaPnlWhenCapMoreFragmented':statistics.median(higher) if higher else None,
        'medianDeltaPnlWhenEqualFragmentation':statistics.median(equal) if equal else None,
    },
    'strategySummary':{'R2':strat_summary('R2'),'CAP100':strat_summary('CAP100')},
    'status':status,
    'interpretation':('Partial-fill fragmentation is a retained explanatory signal for relative HFT PnL.' if signal else 'Partial-fill fragmentation alone does not meet the predeclared explanatory-signal rule; do not tune execution policy around it.'),
    'pairedRows':pairs,
}
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ['testId','matchedMarkets','primary','status','interpretation']},ensure_ascii=False,indent=2))
