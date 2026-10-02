from __future__ import annotations
import json, math, sqlite3
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_taker_corrective_alignment_v1_report.json'
TEST_ID='HFT_TAKER_CORRECTIVE_ALIGNMENT_V1'

def rankdata(xs):
    order=sorted(range(len(xs)), key=lambda i: xs[i])
    ranks=[0.0]*len(xs); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and xs[order[j]]==xs[order[i]]: j+=1
        r=(i+j-1)/2+1
        for k in range(i,j): ranks[order[k]]=r
        i=j
    return ranks

def pearson(a,b):
    if len(a)<2:return float('nan')
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    da=[x-ma for x in a]; db=[x-mb for x in b]
    va=sum(x*x for x in da); vb=sum(x*x for x in db)
    if va<=0 or vb<=0:return float('nan')
    return sum(x*y for x,y in zip(da,db))/math.sqrt(va*vb)

def spearman(a,b): return pearson(rankdata(a),rankdata(b))

def metric(con,strategy,mid):
    rows=con.execute("SELECT channel,side,shares,fill_ms,fill_seq FROM hft_forward_fills_v1 WHERE strategy_key=? AND market_id=? ORDER BY fill_ms,fill_seq",(strategy,mid)).fetchall()
    maker_net=0.0; corr=0.0; aggrav=0.0; neutral=0.0; taker=0.0
    for r in rows:
        ch=str(r['channel']); side=str(r['side']); q=float(r['shares'] or 0)
        if ch=='MAKER': maker_net += q if side=='UP' else -q
        elif ch=='TAKER':
            taker += q
            if abs(maker_net)<1e-9: neutral += q
            elif (maker_net>0 and side=='DOWN') or (maker_net<0 and side=='UP'): corr += q
            else: aggrav += q
    denom=corr+aggrav
    return {'correctiveShare': corr/denom if denom>0 else None,'correctiveShares':corr,'aggravatingShares':aggrav,'neutralShares':neutral,'takerShares':taker}

def main():
    con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
    mids=[int(r[0]) for r in con.execute("SELECT market_id FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL GROUP BY market_id HAVING COUNT(DISTINCT CASE WHEN strategy_key IN ('R2','CAP100') THEN strategy_key END)=2 ORDER BY market_id")]
    rows=[]
    for mid in mids:
        rr=con.execute("SELECT strategy_key,realized_pnl_usdt FROM hft_forward_runs_v1 WHERE market_id=? AND strategy_key IN ('R2','CAP100') AND status='COMPLETE' AND winner IS NOT NULL",(mid,)).fetchall()
        pnl={str(r['strategy_key']):float(r['realized_pnl_usdt']) for r in rr}
        if len(pnl)!=2: continue
        a=metric(con,'R2',mid); b=metric(con,'CAP100',mid)
        if a['correctiveShare'] is None or b['correctiveShare'] is None: continue
        rows.append({'marketId':mid,'r2':a,'cap100':b,'deltaCorrectiveShare':b['correctiveShare']-a['correctiveShare'],'deltaPnl':pnl['CAP100']-pnl['R2']})
    unequal=[r for r in rows if abs(r['deltaCorrectiveShare'])>1e-12]
    rho=spearman([r['deltaCorrectiveShare'] for r in unequal],[r['deltaPnl'] for r in unequal]) if len(unequal)>=2 else float('nan')
    higher=[r['deltaPnl'] for r in unequal if r['deltaCorrectiveShare']>0]
    lower=[r['deltaPnl'] for r in unequal if r['deltaCorrectiveShare']<0]
    equal=[r['deltaPnl'] for r in rows if abs(r['deltaCorrectiveShare'])<=1e-12]
    keep=(len(unequal)>=30 and math.isfinite(rho) and rho>=0.20 and higher and lower and median(higher)>median(lower))
    status='TESTED_INCONCLUSIVE' if len(unequal)<30 else ('TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED')
    report={
      'testId':TEST_ID,'axis':'TAKER_CORRECTIVE_ALIGNMENT_TO_PRE_TAKER_MAKER_NET',
      'cohort':{'matchedSettledMarkets':len(mids),'qualifyingMarkets':len(rows),'nonEqualMarkets':len(unequal),'source':str(DB),'execution':'HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP'},
      'primary':{'spearmanDeltaCorrectiveShareVsDeltaPnl':rho,'capHigherCorrectiveShareMarkets':len(higher),'capLowerCorrectiveShareMarkets':len(lower),'equalCorrectiveShareMarkets':len(equal),'medianDeltaPnlCapHigherCorrectiveShare':median(higher) if higher else None,'medianDeltaPnlCapLowerCorrectiveShare':median(lower) if lower else None,'medianDeltaPnlEqualCorrectiveShare':median(equal) if equal else None},
      'status':status,
      'interpretation':('Corrective alignment meets preregistered signal gate; preserve as research signal only, no frozen-policy change.' if keep else ('Insufficient non-equal qualifying sample; no inference.' if status=='TESTED_INCONCLUSIVE' else 'Taker corrective alignment fails the preregistered explanatory-signal rule; do not tune frozen policies around it.')),
      'rows':rows
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['testId','cohort','primary','status','interpretation']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
