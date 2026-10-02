from __future__ import annotations
import json, math, sys
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_forward_readiness_v1 import readiness
T=ROOT/'data/research/lan_worker_returns/r4-winrate-recent8-causal-v1/teacher.json'
C=ROOT/'data/research/lan_worker_returns/r4-winrate-conversion-recent8-v2/conversion.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_adaptive_winrate_matched_anatomy_v1.json'
def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception:return None
def main():
    t={int(r['marketId']):r for r in json.loads(T.read_text(encoding='utf-8'))['rows']}
    conv={int(r['marketId']):r for r in json.loads(C.read_text(encoding='utf-8'))['rows']}
    rows=[]
    for mid,r in t.items():
        c=r.get('candidate') or {}; p=c.get('portfolio') or {}; m=c.get('models') or {}; u=c.get('public') or {}
        forced=((r.get('counterfactual') or {}).get('forced') or []); ps=(forced[0].get('activeOrderPathState') or {}) if forced else {}; s=ps.get('summary') or {}
        rd=readiness(ps,p)
        net=f(p.get('maker_net')); ds=f(u.get('directionScore'))
        repair_side=s.get('repairSide'); dom_side=s.get('dominantSide')
        vals={
          'marketId':mid,'conversion':conv[mid]['conversion'],'baselinePnl':conv[mid]['baseline']['pnlUsdt'],'counterfactualPnl':conv[mid]['counterfactual']['pnlUsdt'],'deltaPnl':conv[mid]['deltaPnl'],
          'secondsLeft':f(u.get('secondsLeft')),'riskAgeS':f(c.get('riskAgeMs'))/1000 if f(c.get('riskAgeMs')) is not None else None,'makerNet':net,'makerAbsNet':f(p.get('maker_abs_net')),'makerCoverage':f(p.get('maker_paired_coverage')),
          'preFloor':f(p.get('worst_case_floor')),'preUpside':f(p.get('best_case_pnl')),'directionScore':ds,'netDirectionInteraction':net*ds if net is not None and ds is not None else None,
          'pMakerUp':f(m.get('pMakerUp')),'pMakerDown':f(m.get('pMakerDown')),'pResidualWake':f(m.get('pResidualWake')),
          'activeOrderCount':s.get('activeOrderCount'),'repairSideActiveCount':s.get('repairSideActiveCount'),'dominantSideActiveCount':s.get('dominantSideActiveCount'),
          'repairBestQuoteOffsetTicks':f(s.get('repairBestQuoteOffsetTicks')),'dominantBestQuoteOffsetTicks':f(s.get('dominantBestQuoteOffsetTicks')),
          'repairMeanOrderAgeS':f(s.get('repairMeanOrderAgeMs'))/1000 if f(s.get('repairMeanOrderAgeMs')) is not None else None,'dominantMeanOrderAgeS':f(s.get('dominantMeanOrderAgeMs'))/1000 if f(s.get('dominantMeanOrderAgeMs')) is not None else None,
          **rd
        }
        rows.append(vals)
    # simple contrasts: convertible vs winner-preserve failures
    key=[r for r in rows if r['conversion'] in {'LOSS->WIN','WIN->LOSS'}]
    rep={'version':'R4_ADAPTIVE_WINRATE_MATCHED_ANATOMY_V1','researchOnly':True,'actionAuthority':False,'rows':rows,'keyContrast':key,
         'interpretationBoundary':'Descriptive strict-past anatomy only. No selector thresholds are fit from recent8.'}
    OUT.write_text(json.dumps(rep,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'keyContrast':key},indent=2,allow_nan=False))
if __name__=='__main__':main()
