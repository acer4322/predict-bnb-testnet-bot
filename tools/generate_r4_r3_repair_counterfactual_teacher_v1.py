from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.validate_r4_r3_repair_counterfactual_teacher_v1 import run_exact,compact,classify
EPS=1e-9

def candidate(decisions,snapshots,age_ms=5000):
    sm={int(s.get('sampledAtMs') or 0):s for s in snapshots};risk_since=None
    for d in decisions:
        p=d.get('portfolio') or {};t=int(d.get('decisionMs') or 0);absn=float(p.get('maker_abs_net') or 0);pc=float(p.get('maker_paired_coverage') or 0);risky=(d.get('episode') is None and absn>=18-EPS and pc<.80)
        if not risky:risk_since=None;continue
        if risk_since is None:risk_since=t
        if t-risk_since<age_ms:continue
        s=sm.get(t) or {}
        keep_port=['maker_net','maker_abs_net','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl','maker_avg_pair_edge','combined_avg_pair_edge','maker_gross','combined_gross']
        keep_models=['pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown','pTaker1s','pTaker3s','pResidualWake','pPassiveRepair']
        pub_keys=['secondsLeft','directionScore','spotQueueImbalance','spotTakerImbalance1s','spotReturn1sBps','spotReturn3sBps','futuresQueueImbalance','futuresTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','predictUpBid','predictUpAsk','predictDownBid','predictDownAsk']
        return {'decisionMs':t,'riskSinceMs':risk_since,'riskAgeMs':t-risk_since,'phase':d.get('phase'),'desiredPortfolioAction':d.get('desiredPortfolioAction'),'executionChoice':d.get('executionChoice'),'activeMakerOrders':d.get('activeMakerOrders'),'bookAgeMs':d.get('bookAgeMs'),'portfolio':{k:p.get(k) for k in keep_port},'models':{k:(d.get('models') or {}).get(k) for k in keep_models},'public':{k:s.get(k) for k in pub_keys}}
    return None

def one(mid):
    b=r3ctl.run_market(mid,True); cand=candidate(b.get('decisionRows') or [],base.load_public_snapshots(mid)); bc=compact(b)
    out={'marketId':mid,'candidate':cand,'baseline':bc,'counterfactual':None,'branchClass':'NO_CANDIDATE','exactBranchApplied':False}
    if cand:
        c=run_exact(mid,cand['decisionMs']);cc=compact(c);out.update({'counterfactual':cc,'exactBranchApplied':bool(cc['forced'] and int(cc['forced'][0]['atMs'])==int(cand['decisionMs'])),'branchClass':classify(bc,cc),'delta':{'finalFloor':cc['finalFloor']-bc['finalFloor'],'finalAbsNet':cc['finalAbsNet']-bc['finalAbsNet'],'finalCoverage':cc['finalCoverage']-bc['finalCoverage'],'makerFilledShares':cc['makerFilledShares']-bc['makerFilledShares'],'takerFilledShares':cc['takerFilledShares']-bc['takerFilledShares'],'makerCostUsdt':cc['makerCostUsdt']-bc['makerCostUsdt'],'takerCostUsdt':cc['takerCostUsdt']-bc['takerCostUsdt'],'takerFeesUsdt':cc['takerFeesUsdt']-bc['takerFeesUsdt']}})
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();ids=[int(x) for x in a.ids.split(',') if x.strip()];rows=[]
    for mid in ids:
        try:r=one(mid)
        except Exception as e:r={'marketId':mid,'error':f'{type(e).__name__}:{e}','branchClass':'ERROR'}
        rows.append(r);print(json.dumps({'marketId':mid,'class':r.get('branchClass'),'delta':r.get('delta'),'exact':r.get('exactBranchApplied'),'error':r.get('error')},ensure_ascii=False),flush=True)
    counts={k:sum(r.get('branchClass')==k for r in rows) for k in ['PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT','NO_CANDIDATE','ERROR']};rep={'version':'R4_R3_REPAIR_COUNTERFACTUAL_TEACHER_V1_DATASET_CHUNK','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'marketCount':len(ids),'counts':counts,'rows':rows,'contract':'r4_r3_repair_counterfactual_teacher_v1_contract.json','guards':['current R3 realistic-HFT','exact post-fill/pre-decision branch','strict-past candidate features','no fresh13 teacher development','main host only']};Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'marketCount':len(ids),'counts':counts},ensure_ascii=False))
if __name__=='__main__':main()
