from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUT=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_taker_fill_side_reversal_v1_report.json'
PRE=ROOT/'data'/'research'/'hourly_novel_tests'/'hft_taker_fill_side_reversal_v1_preregistered.json'

def ranks(vals):
    order=sorted(range(len(vals)), key=lambda i: vals[i]); r=[0.0]*len(vals); j=0
    while j<len(order):
        k=j
        while k+1<len(order) and vals[order[k+1]]==vals[order[j]]: k+=1
        avg=(j+k+2)/2.0
        for z in range(j,k+1): r[order[z]]=avg
        j=k+1
    return r

def corr(a,b):
    if len(a)<2:return float('nan')
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    da=[x-ma for x in a]; db=[x-mb for x in b]
    den=math.sqrt(sum(x*x for x in da)*sum(x*x for x in db))
    return sum(x*y for x,y in zip(da,db))/den if den>0 else float('nan')

def spearman(a,b): return corr(ranks(a),ranks(b))

def alt_rate(sides):
    if len(sides)<2:return 0.0
    return sum(1 for a,b in zip(sides,sides[1:]) if a!=b)/(len(sides)-1)

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8'))
    con=sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro",uri=True); con.row_factory=sqlite3.Row
    runs={}
    for r in con.execute("select strategy_key,market_id,realized_pnl_usdt from hft_forward_runs_v1 where status='COMPLETE' and winner is not null and strategy_key in ('R2','CAP100')"):
        runs.setdefault(int(r['market_id']),{})[str(r['strategy_key'])]=float(r['realized_pnl_usdt'])
    rows=[]
    for mid,p in sorted(runs.items()):
        if set(p)!={'R2','CAP100'}: continue
        rates={}; counts={}; seqs={}
        for key in ('R2','CAP100'):
            sides=[str(x[0]) for x in con.execute("select side from hft_forward_fills_v1 where strategy_key=? and market_id=? and channel='TAKER' order by fill_ms,fill_seq",(key,mid)).fetchall()]
            rates[key]=alt_rate(sides); counts[key]=len(sides); seqs[key]=sides
        if counts['R2']<2 or counts['CAP100']<2: continue
        rows.append({'marketId':mid,'r2TakerReversalRate':rates['R2'],'capTakerReversalRate':rates['CAP100'],'deltaTakerReversalRate':rates['CAP100']-rates['R2'],'r2Pnl':p['R2'],'capPnl':p['CAP100'],'deltaPnl':p['CAP100']-p['R2'],'r2TakerFills':counts['R2'],'capTakerFills':counts['CAP100'],'r2Sides':seqs['R2'],'capSides':seqs['CAP100']})
    d=[x['deltaTakerReversalRate'] for x in rows]; pnl=[x['deltaPnl'] for x in rows]
    rho=spearman(d,pnl) if rows else float('nan')
    lower=[x['deltaPnl'] for x in rows if x['deltaTakerReversalRate']<-1e-12]
    higher=[x['deltaPnl'] for x in rows if x['deltaTakerReversalRate']>1e-12]
    equal=[x['deltaPnl'] for x in rows if abs(x['deltaTakerReversalRate'])<=1e-12]
    usable=len(rows)
    if usable<30: status='TESTED_INCONCLUSIVE'
    elif math.isfinite(rho) and rho<=-0.20 and lower and higher and median(lower)>median(higher): status='TESTED_KEEP_SIGNAL'
    else: status='TESTED_REJECTED'
    report={**pre,'status':status,'cohort':{**pre['cohort'],'matchedQualifyingMarkets':usable},'primaryResult':{'spearmanDeltaTakerReversalVsDeltaPnl':rho,'capLowerReversalMarkets':len(lower),'capHigherReversalMarkets':len(higher),'equalReversalMarkets':len(equal),'medianDeltaPnlCapLowerReversal':median(lower) if lower else None,'medianDeltaPnlCapHigherReversal':median(higher) if higher else None,'medianDeltaPnlEqualReversal':median(equal) if equal else None},'interpretation':('Lower confirmed Taker side reversal meets the preregistered signal rule; preserve only as a diagnostic signal pending independent validation.' if status=='TESTED_KEEP_SIGNAL' else 'Confirmed Taker side reversal does not meet the preregistered explanatory-signal rule; do not tune frozen policies around it.' if status=='TESTED_REJECTED' else 'Too few markets have >=2 confirmed Taker fills in both strategies; record as inconclusive and do not infer direction.'),'rows':rows}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'testId':report['testId'],'usableMatchedMarkets':usable,'primaryResult':report['primaryResult'],'status':status,'artifact':str(OUT.relative_to(ROOT))},ensure_ascii=False,indent=2,allow_nan=True))
if __name__=='__main__': main()
