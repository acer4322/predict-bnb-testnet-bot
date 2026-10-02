import json,glob,sys
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import evaluate_r4_github_repair_regime_economic_v3 as v3
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_repair_economic_critic_v1_score.json'
def load_train():
 r=json.loads(v3.TRAIN0.read_text())['queryRows']
 for p in sorted(glob.glob(v3.TRAIN_GLOB)):r+=json.loads(Path(p).read_text())['queryRows']
 return r
def load_test():
 r=[]
 for p in v3.TESTS:r+=json.loads(Path(p).read_text())['queryRows']
 return r
def eligible(r):return v3.regime(r)=='MGMT_NEG_FLOOR' and r.get('repairGain5s') is not None and bool(r.get('repairEconomicEligible'))
def feat(r):
 gap=max(float(r.get('absGap') or 0),18.);floor=float(r.get('floor') or 0);up=float(r.get('upside') or 0)
 return [float(r['repairBase']),float(r.get('secondsLeft') or 0)/300.,floor/18.,float(r.get('absGap') or 0)/36.,up/36.,max(0.,-floor)/gap,floor/gap,up/gap]
def conc(p,g):
 s=n=0
 for i in range(len(g)):
  for j in range(i+1,len(g)):
   if abs(g[i]-g[j])<1e-12:continue
   n+=1;d=p[i]-p[j];truth=g[i]>g[j];s+=.5 if abs(d)<1e-12 else float((d>0)==truth)
 return s/n if n else None
def market_metrics(rows,p):
 by=defaultdict(list)
 for i,r in enumerate(rows):by[int(r['marketId'])].append(i)
 cs=[];aps=[];det=[]
 for mid,ix in by.items():
  g=np.array([rows[i]['repairGain5s'] for i in ix],float);q=np.asarray(p)[ix];y=(g>1e-12).astype(int);c=conc(q,g);ap=float(average_precision_score(y,q)) if len(np.unique(y))>1 else None
  if c is not None:cs.append(c)
  if ap is not None:aps.append(ap)
  det.append({'marketId':mid,'n':len(ix),'positiveGain':int(y.sum()),'concordance':c,'ap':ap})
 return {'markets':len(by),'concMarkets':len(cs),'meanConcordance':float(np.mean(cs)) if cs else None,'medianConcordance':float(np.median(cs)) if cs else None,'apMarkets':len(aps),'meanAP':float(np.mean(aps)) if aps else None,'medianAP':float(np.median(aps)) if aps else None,'details':det}
def fit(rows):
 X=np.array([feat(r) for r in rows]);y=np.array([int(float(r['repairGain5s'])>1e-12) for r in rows]);return make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830)).fit(X,y)
def main():
 tr=[r for r in load_train() if eligible(r)];te=[r for r in load_test() if eligible(r)];m=fit(tr);p=m.predict_proba(np.array([feat(r) for r in te]))[:,1];b=np.array([r['repairBase'] for r in te]);bm=market_metrics(te,b);cm=market_metrics(te,p);g={'meanAPImproves':cm['meanAP']>bm['meanAP'],'meanConcordanceImproves':cm['meanConcordance']>bm['meanConcordance']};rep={'version':'R4_REPAIR_ECONOMIC_CRITIC_V1_SCORE','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'trainMarkets':len(set(r['marketId'] for r in tr)),'trainRows':len(tr),'testMarkets':len(set(r['marketId'] for r in te)),'testRows':len(te),'positiveTrain':sum(float(r['repairGain5s'])>1e-12 for r in tr),'positiveTest':sum(float(r['repairGain5s'])>1e-12 for r in te),'baseline':bm,'critic':cm,'gates':g,'developmentKeep':all(g.values()),'decision':'KEEP_CRITIC_FOR_VETO_PILOT' if all(g.values()) else 'REJECT_CRITIC_FORM','contract':'r4_repair_economic_critic_v1_contract.json'};OUT.write_text(json.dumps(rep,indent=2));print(json.dumps({k:rep[k] for k in ['trainRows','testRows','positiveTrain','positiveTest','baseline','critic','gates','decision']},indent=2))
if __name__=='__main__':main()
