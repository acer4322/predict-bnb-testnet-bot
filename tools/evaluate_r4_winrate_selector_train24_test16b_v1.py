from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_winrate_selector_train24_test16b_v1.json'
TRAIN_CONV=[R/'r4-winrate-conversion-recent8-v2/conversion.json',R/'r4-winrate-recent16-a-v1/conversion.json',R/'r4-winrate-recent16-b-v1/conversion.json']
TRAIN_TEACH=[R/'r4-winrate-recent8-causal-v1/teacher.json']+[R/f'r4-winrate-path16a-l{i}-v1/teacher.json' for i in range(1,5)]
TEST_CONV=[R/f'r4-winrate-recent16b-l{i}-v1/conversion.json' for i in range(1,5)]
TEST_TEACH=[R/f'r4-winrate-path16b-l{i}-v1/teacher.json' for i in range(1,5)]
FEATURES=['makerAbsNet','makerCoverage','preFloor','preUpside','secondsLeft','directionScore','netDirectionInteraction','pMakerUp','pMakerDown','pResidualWake','activeOrderCount','repairSideActiveCount','dominantSideActiveCount','repairSideRemainingQty','dominantSideRemainingQty','repairBestQuoteOffsetTicks','dominantBestQuoteOffsetTicks','repairMeanOrderAgeS','dominantMeanOrderAgeS','repairMeanDepletionRatio','dominantMeanDepletionRatio']
def load(files,key='rows'):
 d={}
 for p in files:
  o=json.loads(p.read_text(encoding='utf-8'))
  for x in o.get(key,[]): d[int(x['marketId'])]=x
 return d
def fv(v):
 try:
  x=float(v);return x if math.isfinite(x) else math.nan
 except:return math.nan
def feat(t):
 c=t.get('candidate') or {};p=c.get('portfolio') or {};m=c.get('models') or {};u=c.get('public') or {};f=((t.get('counterfactual') or {}).get('forced') or []);s=((f[0].get('activeOrderPathState') or {}).get('summary') or {}) if f else {}
 net=fv(p.get('maker_net'));ds=fv(u.get('directionScore'))
 return {'makerAbsNet':fv(p.get('maker_abs_net')),'makerCoverage':fv(p.get('maker_paired_coverage')),'preFloor':fv(p.get('worst_case_floor')),'preUpside':fv(p.get('best_case_pnl')),'secondsLeft':fv(u.get('secondsLeft')),'directionScore':ds,'netDirectionInteraction':net*ds if math.isfinite(net) and math.isfinite(ds) else math.nan,'pMakerUp':fv(m.get('pMakerUp')),'pMakerDown':fv(m.get('pMakerDown')),'pResidualWake':fv(m.get('pResidualWake')),'activeOrderCount':fv(s.get('activeOrderCount')),'repairSideActiveCount':fv(s.get('repairSideActiveCount')),'dominantSideActiveCount':fv(s.get('dominantSideActiveCount')),'repairSideRemainingQty':fv(s.get('repairSideRemainingQty')),'dominantSideRemainingQty':fv(s.get('dominantSideRemainingQty')),'repairBestQuoteOffsetTicks':fv(s.get('repairBestQuoteOffsetTicks')),'dominantBestQuoteOffsetTicks':fv(s.get('dominantBestQuoteOffsetTicks')),'repairMeanOrderAgeS':fv(s.get('repairMeanOrderAgeMs'))/1000,'dominantMeanOrderAgeS':fv(s.get('dominantMeanOrderAgeMs'))/1000,'repairMeanDepletionRatio':fv(s.get('repairMeanDepletionRatio')),'dominantMeanDepletionRatio':fv(s.get('dominantMeanDepletionRatio'))}
def make(conv,teach):
 z=[]
 for mid,c in conv.items():
  t=teach.get(mid)
  if not t or not t.get('exactBranchApplied'):continue
  b=c.get('baseline') or {};cf=c.get('counterfactual') or {}
  if 'pnlUsdt' not in cf:continue
  z.append({'marketId':mid,'v':feat(t),'baselinePnl':float(b.get('pnlUsdt') or 0),'cfPnl':float(cf.get('pnlUsdt') or 0),'baselineWin':float(b.get('pnlUsdt') or 0)>0,'cfWin':float(cf.get('pnlUsdt') or 0)>0,'conversion':c.get('conversion')})
 return z
def eval_model(name,model,tr,te):
 Xtr=np.array([[r['v'][f] for f in FEATURES] for r in tr],float); y=np.array([int(r['cfWin']) for r in tr]); Xte=np.array([[r['v'][f] for f in FEATURES] for r in te],float); yt=np.array([int(r['cfWin']) for r in te])
 model.fit(Xtr,y);pr=model.predict_proba(Xte)[:,1];approve=pr>=.5
 policy=[r['cfWin'] if a else r['baselineWin'] for r,a in zip(te,approve)]
 rows=[]
 for r,p,a,w in zip(te,pr,approve,policy):rows.append({'marketId':r['marketId'],'pRepairWin':float(p),'action':'REPAIR' if a else 'WAIT','baselineWin':r['baselineWin'],'repairWin':r['cfWin'],'policyWin':bool(w),'conversion':r['conversion'],'baselinePnl':r['baselinePnl'],'repairPnl':r['cfPnl']})
 return {'model':name,'trainN':len(tr),'testN':len(te),'testAucRepairTerminalWin':float(roc_auc_score(yt,pr)) if len(set(yt))>1 else None,'repairRate':float(np.mean(approve)),'baselineWins':int(sum(r['baselineWin'] for r in te)),'forcedRepairWins':int(sum(r['cfWin'] for r in te)),'policyWins':int(sum(policy)),'baselineWinRate':sum(r['baselineWin'] for r in te)/len(te),'forcedRepairWinRate':sum(r['cfWin'] for r in te)/len(te),'policyWinRate':sum(policy)/len(te),'lossToWinCaptured':int(sum(a and r['conversion']=='LOSS->WIN' for r,a in zip(te,approve))),'winnerDestroyed':int(sum(a and r['conversion']=='WIN->LOSS' for r,a in zip(te,approve))),'rows':rows}
def main():
 tr=make(load(TRAIN_CONV),load(TRAIN_TEACH));te=make(load(TEST_CONV),load(TEST_TEACH))
 models=[('LOGISTIC_BALANCED',make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830))),('TREE_D2',make_pipeline(SimpleImputer(strategy='median',add_indicator=True),DecisionTreeClassifier(max_depth=2,min_samples_leaf=3,class_weight='balanced',random_state=20260830)))]
 rep={'version':'R4_WINRATE_SELECTOR_TRAIN24_TEST16B_V1','researchOnly':True,'actionAuthority':False,'objective':'maximize terminal positive-market rate by choosing REPAIR vs WAIT','features':FEATURES,'trainMarkets':[r['marketId'] for r in tr],'testMarkets':[r['marketId'] for r in te],'results':[eval_model(n,m,tr,te) for n,m in models]}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
