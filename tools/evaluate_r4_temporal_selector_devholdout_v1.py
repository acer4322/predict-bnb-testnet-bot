from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research/lan_worker_returns'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_temporal_selector_devholdout_v1.json'
TRAIN=[R/'r4-temporal-recent8-v3/temporal.json',R/'r4-temporal-recent16a-v3/temporal.json',R/'r4-temporal-recent16b-v3/temporal.json']
TEST=[R/'r4-temporal-unseen15c-v3/temporal.json']
STATIC=['makerAbsNet','makerCoverage','preFloor','preUpside','secondsLeft','directionScore','netDirectionInteraction','pMakerUp','pMakerDown','pResidualWake','activeOrderCount','repairSideActiveCount','dominantSideActiveCount','repairSideRemainingQty','dominantSideRemainingQty','repairBestQuoteOffsetTicks','dominantBestQuoteOffsetTicks','repairMeanOrderAgeS','dominantMeanOrderAgeS','repairMeanDepletionRatio','dominantMeanDepletionRatio']
TEMP=[
'delta_maker_abs_net_3s','delta_maker_abs_net_10s','delta_maker_abs_net_15s','delta_maker_paired_coverage_3s','delta_maker_paired_coverage_10s','delta_maker_paired_coverage_15s','delta_worst_case_floor_3s','delta_worst_case_floor_10s','delta_worst_case_floor_15s','delta_best_case_pnl_10s','delta_combined_abs_net_10s','delta_combined_paired_coverage_10s',
'makerFilledShares_3s','makerFilledShares_10s','makerFilledShares_15s','takerFilledShares_3s','takerFilledShares_10s','takerFilledShares_15s','executionTransitions_10s','executionTransitions_15s','waitDecisions_10s','makerDecisions_10s','takerDecisions_10s','delta_pResidualWake_3s','delta_pResidualWake_10s','delta_pResidualWake_15s']

def fv(v):
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except:return math.nan

def static_feat(r):
 c=r.get('candidate') or {};p=c.get('portfolio') or {};m=c.get('models') or {};u=c.get('public') or {}; f=((r.get('counterfactualCompact') or {}).get('forced') or []); s=((f[0].get('activeOrderPathState') or {}).get('summary') or {}) if f else {}; net=fv(p.get('maker_net')); ds=fv(u.get('directionScore'))
 return {'makerAbsNet':fv(p.get('maker_abs_net')),'makerCoverage':fv(p.get('maker_paired_coverage')),'preFloor':fv(p.get('worst_case_floor')),'preUpside':fv(p.get('best_case_pnl')),'secondsLeft':fv(u.get('secondsLeft')),'directionScore':ds,'netDirectionInteraction':net*ds if np.isfinite(net) and np.isfinite(ds) else math.nan,'pMakerUp':fv(m.get('pMakerUp')),'pMakerDown':fv(m.get('pMakerDown')),'pResidualWake':fv(m.get('pResidualWake')),'activeOrderCount':fv(s.get('activeOrderCount')),'repairSideActiveCount':fv(s.get('repairSideActiveCount')),'dominantSideActiveCount':fv(s.get('dominantSideActiveCount')),'repairSideRemainingQty':fv(s.get('repairSideRemainingQty')),'dominantSideRemainingQty':fv(s.get('dominantSideRemainingQty')),'repairBestQuoteOffsetTicks':fv(s.get('repairBestQuoteOffsetTicks')),'dominantBestQuoteOffsetTicks':fv(s.get('dominantBestQuoteOffsetTicks')),'repairMeanOrderAgeS':fv(s.get('repairMeanOrderAgeMs'))/1000,'dominantMeanOrderAgeS':fv(s.get('dominantMeanOrderAgeMs'))/1000,'repairMeanDepletionRatio':fv(s.get('repairMeanDepletionRatio')),'dominantMeanDepletionRatio':fv(s.get('dominantMeanDepletionRatio'))}

def load(paths):
 out=[]
 for p in paths:
  o=json.loads(p.read_text(encoding='utf-8'))
  for r in o.get('rows',[]):
   if not r.get('exactBranchApplied'): continue
   b=float((r.get('baselineScore') or {}).get('pnlUsdt') or 0); c=float((r.get('counterfactualScore') or {}).get('pnlUsdt') or 0)
   v=static_feat(r); v.update({k:fv((r.get('temporalStrictPast') or {}).get(k)) for k in TEMP})
   out.append({'mid':int(r['marketId']),'v':v,'baselineWin':b>0,'repairWin':c>0,'conversion':r.get('conversion'),'b':b,'c':c})
 return out

def evaluate(name,features,tr,te,C=.5):
 X=np.array([[z['v'][f] for f in features] for z in tr],float); y=np.array([int(z['repairWin']) for z in tr]); Xt=np.array([[z['v'][f] for f in features] for z in te],float); yt=np.array([int(z['repairWin']) for z in te])
 model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(C=C,class_weight='balanced',max_iter=3000,random_state=20260830)); model.fit(X,y); pr=model.predict_proba(Xt)[:,1]; act=pr>=.5; policy=[z['repairWin'] if a else z['baselineWin'] for z,a in zip(te,act)]
 return {'model':name,'features':len(features),'trainN':len(tr),'testN':len(te),'auc':float(roc_auc_score(yt,pr)) if len(set(yt))>1 else None,'repairRate':float(np.mean(act)),'baselineWins':int(sum(z['baselineWin'] for z in te)),'forcedRepairWins':int(sum(z['repairWin'] for z in te)),'policyWins':int(sum(policy)),'baselineWinRate':sum(z['baselineWin'] for z in te)/len(te),'policyWinRate':sum(policy)/len(te),'lossToWinCaptured':int(sum(a and z['conversion']=='LOSS->WIN' for z,a in zip(te,act))),'winnerDestroyed':int(sum(a and z['conversion']=='WIN->LOSS' for z,a in zip(te,act))),'rows':[{'marketId':z['mid'],'p':float(p),'action':'REPAIR' if a else 'WAIT','conversion':z['conversion']} for z,p,a in zip(te,pr,act)]}

def main():
 tr=load(TRAIN);te=load(TEST); results=[evaluate('STATIC_C05',STATIC,tr,te,.5),evaluate('STATIC_TEMPORAL_C05',STATIC+TEMP,tr,te,.5),evaluate('STATIC_TEMPORAL_C02',STATIC+TEMP,tr,te,.2)]
 rep={'version':'R4_TEMPORAL_SELECTOR_DEVHOLDOUT_V1','researchOnly':True,'promotionEvidence':False,'note':'unseen15c labels were already inspected before this comparison; development evidence only','results':results}; OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
