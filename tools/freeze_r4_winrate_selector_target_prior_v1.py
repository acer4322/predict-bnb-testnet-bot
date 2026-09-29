from __future__ import annotations
import json,math,joblib
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research/lan_worker_returns';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1'
TRAIN_CONV=[R/'r4-winrate-conversion-recent8-v2/conversion.json',R/'r4-winrate-recent16-a-v1/conversion.json',R/'r4-winrate-recent16-b-v1/conversion.json']
TRAIN_TEACH=[R/'r4-winrate-recent8-causal-v1/teacher.json']+[R/f'r4-winrate-path16a-l{i}-v1/teacher.json' for i in range(1,5)]
BASE=['makerAbsNet','makerCoverage','preFloor','preUpside','secondsLeft','directionScore','netDirectionInteraction','pMakerUp','pMakerDown','pResidualWake','activeOrderCount','repairSideActiveCount','dominantSideActiveCount','repairSideRemainingQty','dominantSideRemainingQty','repairBestQuoteOffsetTicks','dominantBestQuoteOffsetTicks','repairMeanOrderAgeS','dominantMeanOrderAgeS','repairMeanDepletionRatio','dominantMeanDepletionRatio']
# Only robust recurring Target REPAIR contexts. Direction-reversal contexts deliberately map to zero.
PRIOR={
 ('FORMATION','NEGATIVE','DOMINANT_ACTIVE'):0.0389269775672647,
 ('FORMATION','NEGATIVE','NO_ACTIVE_ROOT'):0.032397888288386904,
 ('MANAGEMENT','NEGATIVE','DOMINANT_ACTIVE'):0.0369214319934447,
 ('MANAGEMENT','NEGATIVE','NO_ACTIVE_ROOT'):0.0247393133481429,
 ('PROTECTION','NEGATIVE','NO_ACTIVE_ROOT'):0.01813874780668685,
 ('PROTECTION','POSITIVE','NO_ACTIVE_ROOT'):0.017994582951391374,
}
def load(ps):
 d={}
 for p in ps:
  for x in json.loads(p.read_text(encoding='utf-8')).get('rows',[]):d[int(x['marketId'])]=x
 return d
def fv(v):
 try:
  x=float(v);return x if math.isfinite(x) else math.nan
 except:return math.nan
def phase(sec):
 if not math.isfinite(sec):return 'UNKNOWN'
 return 'FORMATION' if sec>180 else 'MANAGEMENT' if sec>60 else 'PROTECTION'
def feat(t):
 c=t.get('candidate') or {};p=c.get('portfolio') or {};m=c.get('models') or {};u=c.get('public') or {};f=((t.get('counterfactual') or {}).get('forced') or []);s=((f[0].get('activeOrderPathState') or {}).get('summary') or {}) if f else {}
 net=fv(p.get('maker_net'));ds=fv(u.get('directionScore'));sec=fv(u.get('secondsLeft'));fl=fv(p.get('worst_case_floor'))
 dom=fv(s.get('dominantSideActiveCount'));rep=fv(s.get('repairSideActiveCount'))
 own='DOMINANT_ACTIVE' if math.isfinite(dom) and dom>0 else 'WEAK_ACTIVE' if math.isfinite(rep) and rep>0 else 'NO_ACTIVE_ROOT'
 floor='POSITIVE' if math.isfinite(fl) and fl>=0 else 'NEGATIVE'
 v={'makerAbsNet':fv(p.get('maker_abs_net')),'makerCoverage':fv(p.get('maker_paired_coverage')),'preFloor':fl,'preUpside':fv(p.get('best_case_pnl')),'secondsLeft':sec,'directionScore':ds,'netDirectionInteraction':net*ds if math.isfinite(net) and math.isfinite(ds) else math.nan,'pMakerUp':fv(m.get('pMakerUp')),'pMakerDown':fv(m.get('pMakerDown')),'pResidualWake':fv(m.get('pResidualWake')),'activeOrderCount':fv(s.get('activeOrderCount')),'repairSideActiveCount':rep,'dominantSideActiveCount':dom,'repairSideRemainingQty':fv(s.get('repairSideRemainingQty')),'dominantSideRemainingQty':fv(s.get('dominantSideRemainingQty')),'repairBestQuoteOffsetTicks':fv(s.get('repairBestQuoteOffsetTicks')),'dominantBestQuoteOffsetTicks':fv(s.get('dominantBestQuoteOffsetTicks')),'repairMeanOrderAgeS':fv(s.get('repairMeanOrderAgeMs'))/1000,'dominantMeanOrderAgeS':fv(s.get('dominantMeanOrderAgeMs'))/1000,'repairMeanDepletionRatio':fv(s.get('repairMeanDepletionRatio')),'dominantMeanDepletionRatio':fv(s.get('dominantMeanDepletionRatio'))}
 v['targetRepairPrior']=PRIOR.get((phase(sec),floor,own),0.0);return v
def main():
 cv=load(TRAIN_CONV);tt=load(TRAIN_TEACH);rows=[]
 for mid,c in cv.items():
  t=tt.get(mid)
  if not t or not t.get('exactBranchApplied'):continue
  cf=c.get('counterfactual') or {}
  if 'pnlUsdt' not in cf:continue
  rows.append((mid,feat(t),int(float(cf['pnlUsdt'])>0)))
 y=np.array([z[2] for z in rows]);models={}
 for name,fs in [('BASE',BASE),('TARGET_PRIOR',BASE+['targetRepairPrior'])]:
  X=np.asarray([[z[1][f] for f in fs] for z in rows],float);mdl=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830));mdl.fit(X,y);models[name]={'model':mdl,'features':fs}
 art={'version':'R4_WINRATE_SELECTOR_TARGET_PRIOR_V1_FROZEN','frozenBeforeUnseen15cLabels':True,'researchOnly':True,'actionAuthority':False,'trainMarkets':[z[0] for z in rows],'priorSemantics':'Target robust cross-stream REPAIR recurrence only; weak/direction-reversal contexts=0','priorLookup':{'|'.join(k):v for k,v in PRIOR.items()},'models':models}
 joblib.dump(art,OUT/'r4_winrate_selector_target_prior_v1_frozen.joblib');meta={k:v for k,v in art.items() if k!='models'};meta['modelFeatures']={k:v['features'] for k,v in models.items()};(OUT/'r4_winrate_selector_target_prior_v1_frozen.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps({'trainN':len(rows),'trainMarkets':meta['trainMarkets'],'modelFeatures':meta['modelFeatures'],'priorLookup':meta['priorLookup']},indent=2))
if __name__=='__main__':main()
