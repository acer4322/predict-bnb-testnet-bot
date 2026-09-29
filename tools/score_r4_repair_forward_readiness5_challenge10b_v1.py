from __future__ import annotations
import json,math,sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_forward_readiness_v1 import rows as dev_rows,readiness,R5
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import enriched_values,BASE_FEATURES,impute
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_repair_forward_readiness5_challenge10b_score_v1.json'
CH=[ROOT/'data/research/lan_worker_returns/r4-repair-readiness5-challenge10b-b1-v2/teacher.json',ROOT/'data/research/lan_worker_returns/r4-repair-readiness5-challenge10b-b2-v2/teacher.json']
FEATURES=BASE_FEATURES+R5

def challenge_rows():
 out=[]
 for fp in CH:
  o=json.loads(fp.read_text(encoding='utf-8'))
  for r in o.get('rows',[]):
   c=str(r.get('branchClass'))
   if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT','NO_CANDIDATE'}: continue
   cand=r.get('candidate') or {}; forced=((r.get('counterfactual') or {}).get('forced') or []); path=(forced[0].get('activeOrderPathState') or {}) if forced else {}
   if cand and path:
    base=enriched_values({'candidate':cand},{'pathState':path},'EXACT_STATE'); v={**base,**readiness(path,cand.get('portfolio') or {})}
   else:v=None
   out.append({'marketId':int(r['marketId']),'class':c,'v':v,'delta':r.get('delta')})
 return out

def main():
 D=[r for r in dev_rows() if r['class'] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}]
 C=challenge_rows(); CB=[r for r in C if r['class'] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} and isinstance(r.get('v'),dict)]
 X=np.asarray([[r['v'].get(f,math.nan) for f in FEATURES] for r in D],float); y=np.asarray([1 if r['class']=='PARETO_BENEFICIAL' else 0 for r in D],int)
 T=np.asarray([[r['v'].get(f,math.nan) for f in FEATURES] for r in CB],float); X2,T2=impute(X,T)
 m=make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830));m.fit(X2,y);p=m.predict_proba(T2)[:,1];yt=np.asarray([1 if r['class']=='PARETO_BENEFICIAL' else 0 for r in CB],int)
 pred=(p>=.5).astype(int);app=p>=.65;veto=p<=.35;benef=yt==1;harm=yt==0
 def rate(n,d):return float(n/d) if d else math.nan
 metrics={'bhN':len(CB),'auc':float(roc_auc_score(yt,p)) if len(set(yt))==2 else math.nan,'balancedAccuracyAt050':float(balanced_accuracy_score(yt,pred)) if len(set(yt))==2 else math.nan,'beneficialRecallAt050':float(recall_score(yt,pred,pos_label=1)) if int(benef.sum()) else math.nan,'harmfulRecallAt050':float(recall_score(yt,pred,pos_label=0)) if int(harm.sum()) else math.nan,'beneficialOpportunityRetention':rate(int((app&benef).sum()),int(benef.sum())),'harmfulVetoRecall':rate(int((veto&harm).sum()),int(harm.sum())),'approvePrecisionBeneficial':rate(int((app&benef).sum()),int(app.sum())),'approveHarmfulRate':rate(int((app&harm).sum()),int(app.sum())),'vetoPrecisionHarmful':rate(int((veto&harm).sum()),int(veto.sum())),'vetoBeneficialRate':rate(int((veto&benef).sum()),int(veto.sum())),'harmfulFalseApproveCount':int((app&harm).sum()),'beneficialFalseVetoCount':int((veto&benef).sum())}
 rows=[]
 pi=0
 for r in C:
  z={'marketId':r['marketId'],'actualClass':r['class'],'delta':r.get('delta')}
  if r in CB:
   z['pBeneficial']=float(p[pi]);z['triage']='APPROVE' if p[pi]>=.65 else 'VETO' if p[pi]<=.35 else 'ABSTAIN';pi+=1
  rows.append(z)
 rep={'version':'R4_REPAIR_FORWARD_READINESS5_CHALLENGE10B_SCORE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'frozenContract':'r4_repair_forward_readiness5_challenge10b_contract_v1.json','developmentTrainMarkets':len(D),'developmentClassCounts':dict(Counter(r['class'] for r in D)),'challengeClassCounts':dict(Counter(r['class'] for r in C)),'metrics':metrics,'rows':rows,'decision':'KEEP_DIRECTION_FOR_INTEGRATED_HFT' if metrics['auc']>=0.60 and metrics['approveHarmfulRate']<=0.25 and metrics['beneficialOpportunityRetention']>=0.35 else 'FAIL_FROZEN_CHALLENGE'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
