from __future__ import annotations

import argparse, csv, json, math, statistics, sys, warnings
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_cap100_closed_loop_v0 as cl

CSV=ROOT/'data'/'research'/'8784_r2_vs_8786_cap100_fresh_v1_markets.csv'
OUT=ROOT/'data'/'research'/'hftbacktest_execution_shift_v0'


def ids(start:int,n:int)->list[int]:
    with CSV.open(encoding='utf-8-sig',newline='') as f: xs=[int(r['marketId']) for r in csv.DictReader(f)][:126]
    return xs[start:start+n]


def first_candidate(decisions:list[dict[str,Any]], age_ms:int)->dict[str,Any]|None:
    risk_since=None
    for d in decisions:
        p=d.get('portfolio') or {}; t=int(d['decisionMs'])
        try: absnet=float(p.get('maker_abs_net') or 0); pc=float(p.get('maker_paired_coverage') or 0)
        except Exception: absnet=0; pc=1
        risky=(d.get('episode') is None and absnet>=18-1e-9 and pc<0.80)
        if not risky:
            risk_since=None; continue
        if risk_since is None: risk_since=t
        if t-risk_since>=age_ms:
            return {'decisionMs':t,'riskSinceMs':risk_since,'riskAgeMs':t-risk_since,'phase':d.get('phase'),'portfolio':p,'models':d.get('models') or {},'direction':d.get('direction') or {},'capital':d.get('capital') or {},'executionChoice':d.get('executionChoice'),'desiredPortfolioAction':d.get('desiredPortfolioAction')}
    return None


def run_one(mid:int, age_ms:int)->dict[str,Any]:
    base=cl.run_market(mid,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_mode='hft')
    cand=first_candidate(base['decisionRows'],age_ms)
    base_pnl=float(base['closedLoop']['ledger']['realizedPnlUsdt'])
    if cand is None:
        return {'marketId':mid,'candidate':None,'basePnlUsdt':base_pnl,'cfPnlUsdt':None,'deltaUsdt':None,'label':'NO_CANDIDATE','forcedWakeApplied':False}
    cf=cl.run_market(mid,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_mode='hft',forced_repair_wake_ms=int(cand['decisionMs']))
    cf_pnl=float(cf['closedLoop']['ledger']['realizedPnlUsdt']); delta=cf_pnl-base_pnl
    applied=bool(cf.get('forcedRepairWakes'))
    label='BENEFICIAL' if delta>1.0 else 'HARMFUL' if delta<-1.0 else 'NEUTRAL'
    if not applied: label='INVALID_NO_WAKE'
    return {'marketId':mid,'candidate':cand,'basePnlUsdt':base_pnl,'cfPnlUsdt':cf_pnl,'deltaUsdt':delta,'label':label,'forcedWakeApplied':applied,'forcedWake':(cf.get('forcedRepairWakes') or [None])[0],'baseTakers':base['closedLoop']['takerFills'],'cfTakers':cf['closedLoop']['takerFills']}


def summarize(rows):
    valid=[r for r in rows if r.get('deltaUsdt') is not None and r.get('forcedWakeApplied')]
    labs={k:sum(r['label']==k for r in rows) for k in ['BENEFICIAL','HARMFUL','NEUTRAL','NO_CANDIDATE','INVALID_NO_WAKE']}
    ds=[float(r['deltaUsdt']) for r in valid]
    return {'markets':len(rows),'validCounterfactuals':len(valid),**labs,'sumDeltaUsdt':sum(ds),'meanDeltaUsdt':statistics.mean(ds) if ds else None,'medianDeltaUsdt':statistics.median(ds) if ds else None,'beneficialSumUsdt':sum(float(r['deltaUsdt']) for r in valid if r['label']=='BENEFICIAL'),'harmfulSumUsdt':sum(float(r['deltaUsdt']) for r in valid if r['label']=='HARMFUL')}


def main():
    p=argparse.ArgumentParser(); p.add_argument('--start-index',type=int,default=0); p.add_argument('--markets',type=int,default=5); p.add_argument('--candidate-age-ms',type=int,default=5000); a=p.parse_args(); warnings.filterwarnings('ignore')
    rows=[]; errs=[]
    for mid in ids(a.start_index,a.markets):
        try: rows.append(run_one(mid,a.candidate_age_ms))
        except Exception as e: errs.append({'marketId':mid,'error':f'{type(e).__name__}: {e}'})
    report={'version':'HFTBACKTEST_EXECUTION_AWARE_REPAIR_WAKE_TEACHER_V0','config':vars(a),'summary':summarize(rows),'errors':errs,'rows':rows,'boundary':'Candidate checkpoint is the first realistic no-episode state with maker_abs_net>=18 and paired_coverage<0.80 persisting for candidate_age_ms. Label is end-of-market PnL benefit of one forced RESIDUAL wake at that checkpoint. This is counterfactual research teacher data, not a live policy.'}
    OUT.mkdir(parents=True,exist_ok=True); path=OUT/f'repair_wake_teacher_i{a.start_index}_n{a.markets}_age{a.candidate_age_ms}_v0.json'; path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'path':str(path),'summary':report['summary'],'errors':errs[:3]},ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
