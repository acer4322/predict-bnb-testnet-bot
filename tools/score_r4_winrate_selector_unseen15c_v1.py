from __future__ import annotations
import json,math,joblib
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research/lan_worker_returns';P=ROOT/'data/research/r4_v0/p0_provenance_v1'
ART=joblib.load(P/'r4_winrate_selector_target_prior_v1_frozen.joblib')
FILES=[R/f'r4-winrate-unseen15c-l{i}-v1/combined.json' for i in range(1,5)]
PRIOR={tuple(k.split('|')):v for k,v in (json.loads((P/'r4_winrate_selector_target_prior_v1_frozen.json').read_text())['priorLookup']).items()}
def fv(v):
 try:
  x=float(v);return x if math.isfinite(x) else math.nan
 except:return math.nan
def phase(sec):
 if not math.isfinite(sec):return 'UNKNOWN'
 return 'FORMATION' if sec>180 else 'MANAGEMENT' if sec>60 else 'PROTECTION'
def feat(r):
 c=r.get('candidate') or {};p=c.get('portfolio') or {};m=c.get('models') or {};u=c.get('public') or {};f=((r.get('counterfactualCompact') or {}).get('forced') or []);s=((f[0].get('activeOrderPathState') or {}).get('summary') or {}) if f else {}
 net=fv(p.get('maker_net'));ds=fv(u.get('directionScore'));sec=fv(u.get('secondsLeft'));fl=fv(p.get('worst_case_floor'));dom=fv(s.get('dominantSideActiveCount'));rep=fv(s.get('repairSideActiveCount'))
 own='DOMINANT_ACTIVE' if math.isfinite(dom) and dom>0 else 'WEAK_ACTIVE' if math.isfinite(rep) and rep>0 else 'NO_ACTIVE_ROOT';floor='POSITIVE' if math.isfinite(fl) and fl>=0 else 'NEGATIVE'
 v={'makerAbsNet':fv(p.get('maker_abs_net')),'makerCoverage':fv(p.get('maker_paired_coverage')),'preFloor':fl,'preUpside':fv(p.get('best_case_pnl')),'secondsLeft':sec,'directionScore':ds,'netDirectionInteraction':net*ds if math.isfinite(net) and math.isfinite(ds) else math.nan,'pMakerUp':fv(m.get('pMakerUp')),'pMakerDown':fv(m.get('pMakerDown')),'pResidualWake':fv(m.get('pResidualWake')),'activeOrderCount':fv(s.get('activeOrderCount')),'repairSideActiveCount':rep,'dominantSideActiveCount':dom,'repairSideRemainingQty':fv(s.get('repairSideRemainingQty')),'dominantSideRemainingQty':fv(s.get('dominantSideRemainingQty')),'repairBestQuoteOffsetTicks':fv(s.get('repairBestQuoteOffsetTicks')),'dominantBestQuoteOffsetTicks':fv(s.get('dominantBestQuoteOffsetTicks')),'repairMeanOrderAgeS':fv(s.get('repairMeanOrderAgeMs'))/1000,'dominantMeanOrderAgeS':fv(s.get('dominantMeanOrderAgeMs'))/1000,'repairMeanDepletionRatio':fv(s.get('repairMeanDepletionRatio')),'dominantMeanDepletionRatio':fv(s.get('dominantMeanDepletionRatio'))}
 v['targetRepairPrior']=PRIOR.get((phase(sec),floor,own),0.0);v['targetPhase']=phase(sec);v['targetOwnershipProxy']=own;return v
def main():
 rows=[]
 for p in FILES: rows.extend(json.loads(p.read_text(encoding='utf-8')).get('rows',[]))
 rows=[r for r in rows if r.get('exactBranchApplied') and not r.get('error') and r.get('counterfactualScore')]
 out={'version':'R4_WINRATE_SELECTOR_UNSEEN15C_V1','researchOnly':True,'actionAuthority':False,'cohortMarkets':[r['marketId'] for r in rows],'models':{}}
 for name,b in ART['models'].items():
  fs=b['features'];mdl=b['model'];X=np.asarray([[feat(r)[f] for f in fs] for r in rows],float);pr=mdl.predict_proba(X)[:,1];approve=pr>=.5;yt=np.asarray([int(float(r['counterfactualScore']['pnlUsdt'])>0) for r in rows])
  details=[];basewins=[];cfwins=[];polwins=[]
  for r,pv,a in zip(rows,pr,approve):
   bw=float(r['baselineScore']['pnlUsdt'])>0;cw=float(r['counterfactualScore']['pnlUsdt'])>0;pw=cw if a else bw;v=feat(r);basewins.append(bw);cfwins.append(cw);polwins.append(pw);details.append({'marketId':r['marketId'],'pRepairWin':float(pv),'action':'REPAIR' if a else 'WAIT','baselineWin':bw,'repairWin':cw,'policyWin':pw,'conversion':r.get('conversion'),'baselinePnl':r['baselineScore']['pnlUsdt'],'repairPnl':r['counterfactualScore']['pnlUsdt'],'targetRepairPrior':v['targetRepairPrior'],'targetPhase':v['targetPhase'],'targetOwnershipProxy':v['targetOwnershipProxy']})
  out['models'][name]={'testN':len(rows),'aucRepairTerminalWin':float(roc_auc_score(yt,pr)) if len(set(yt))>1 else None,'repairRate':float(np.mean(approve)),'baselineWins':int(sum(basewins)),'forcedRepairWins':int(sum(cfwins)),'policyWins':int(sum(polwins)),'baselineWinRate':float(np.mean(basewins)),'forcedRepairWinRate':float(np.mean(cfwins)),'policyWinRate':float(np.mean(polwins)),'lossToWinCaptured':int(sum(a and r.get('conversion')=='LOSS->WIN' for r,a in zip(rows,approve))),'winnerDestroyed':int(sum(a and r.get('conversion')=='WIN->LOSS' for r,a in zip(rows,approve))),'details':details}
 P.joinpath('r4_winrate_selector_unseen15c_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
