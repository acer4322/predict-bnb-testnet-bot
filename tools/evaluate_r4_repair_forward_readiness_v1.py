from __future__ import annotations
import json,math,sys
from pathlib import Path
from collections import Counter
import numpy as np
import joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import load_teacher,load_path,enriched_values,BASE_FEATURES,impute
from tools.hftbacktest_r2_execution_school_v0 import _pending_skill_frame
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_repair_forward_readiness_v1.json'
FART=joblib.load(ROOT/'data/research/execution_aware_fill_lifecycle_v0/open_order_fill_lifecycle_v0.joblib')
PART=joblib.load(ROOT/'data/research/execution_aware_fill_lifecycle_v0/execution_aware_pending_management_v0.joblib')
CH=[ROOT/'data/research/lan_worker_returns/r4-repair-quote-challenge10-b1-v1/teacher.json',ROOT/'data/research/lan_worker_returns/r4-repair-quote-challenge10-b2-v1/teacher.json']
R5=['repairAnyFill5','dominantAnyFill5','repairExpectedFill5Units','dominantExpectedFill5Units','repairMaxContinue','dominantMaxContinue']
RM=['repairAnyFill1','dominantAnyFill1','repairAnyFill3','dominantAnyFill3']

def prob_order(order,orders,portfolio):
 row=dict(order);row['portfolio']=portfolio;row['activeSameCount']=sum(x.get('side')==order.get('side') for x in orders);row['activeOppCount']=len(orders)-row['activeSameCount']
 xf=_pending_skill_frame(row,list(FART['features']));xp=_pending_skill_frame(row,list(PART['features']))
 return {'p1':float(FART['models']['fill_1s'].predict_proba(xf)[0,1]),'p3':float(FART['models']['fill_3s'].predict_proba(xf)[0,1]),'p5':float(FART['models']['fill_5s'].predict_proba(xf)[0,1]),'pc':float(PART['model'].predict_proba(xp)[0,1]),'rem':float(order.get('remainingQty') or 0.0),'side':str(order.get('side') or '')}
def agg(side,pp):
 z=[x for x in pp if x['side']==side]
 if not z:return {'any1':0.0,'any3':0.0,'any5':0.0,'exp5':0.0,'maxc':0.0}
 def anyp(k):
  q=1.0
  for x in z:q*=max(0.0,min(1.0,1.0-x[k]))
  return 1.0-q
 return {'any1':anyp('p1'),'any3':anyp('p3'),'any5':anyp('p5'),'exp5':sum((x['rem']/18.0)*x['p5'] for x in z),'maxc':max(x['pc'] for x in z)}
def readiness(path,portfolio):
 s=(path or {}).get('summary') or {};orders=(path or {}).get('orders') or [];pp=[prob_order(o,orders,portfolio) for o in orders];r=agg(str(s.get('repairSide') or ''),pp);d=agg(str(s.get('dominantSide') or ''),pp)
 return {'repairAnyFill1':r['any1'],'dominantAnyFill1':d['any1'],'repairAnyFill3':r['any3'],'dominantAnyFill3':d['any3'],'repairAnyFill5':r['any5'],'dominantAnyFill5':d['any5'],'repairExpectedFill5Units':r['exp5'],'dominantExpectedFill5Units':d['exp5'],'repairMaxContinue':r['maxc'],'dominantMaxContinue':d['maxc']}
def rows():
 out=[];t=load_teacher();p=load_path()
 for mid in sorted(set(t)&set(p)):
  c=str(t[mid].get('branchClass'))
  if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT'}:continue
  cand=t[mid].get('candidate') or {};path=p[mid].get('pathState') or {};base=enriched_values(t[mid],p[mid],'EXACT_STATE');v={**base,**readiness(path,cand.get('portfolio') or {})};out.append({'marketId':mid,'class':c,'v':v,'source':'DEV39'})
 for fp in CH:
  o=json.loads(fp.read_text(encoding='utf-8'))
  for r in o.get('rows',[]):
   c=str(r.get('branchClass'))
   if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT'}:continue
   forced=((r.get('counterfactual') or {}).get('forced') or []);path=(forced[0].get('activeOrderPathState') or {}) if forced else {};cand=r.get('candidate') or {};base=enriched_values({'candidate':cand},{'pathState':path},'EXACT_STATE');v={**base,**readiness(path,cand.get('portfolio') or {})};out.append({'marketId':int(r['marketId']),'class':c,'v':v,'source':'CHALLENGE10_CONSUMED'})
 return out

def loo(R,features):
 X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in R],float);classes=[r['class'] for r in R];y=np.asarray([1 if c=='PARETO_BENEFICIAL' else 0 for c in classes],int);pr=np.full(len(R),np.nan)
 for i in range(len(R)):
  tridx=[j for j,c in enumerate(classes) if j!=i and c in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}] if classes[i] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} else [j for j,c in enumerate(classes) if c in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}]
  tr,te=impute(X[tridx],X[i:i+1]);m=make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830));m.fit(tr,y[tridx]);pr[i]=m.predict_proba(te)[0,1]
 return y,classes,pr

def met(y,c,p):
 bh=np.asarray([x in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} for x in c]);pred=(p>=.5).astype(int);app=p>=.65;veto=p<=.35;benef=np.asarray([x=='PARETO_BENEFICIAL' for x in c]);harm=np.asarray([x=='PARETO_HARMFUL' for x in c]);other=~bh
 def rate(n,d):return float(n/d) if d else math.nan
 return {'bhN':int(bh.sum()),'auc':float(roc_auc_score(y[bh],p[bh])),'balancedAccuracyAt050':float(balanced_accuracy_score(y[bh],pred[bh])),'beneficialRecallAt050':float(recall_score(y[bh],pred[bh],pos_label=1)),'harmfulRecallAt050':float(recall_score(y[bh],pred[bh],pos_label=0)),'beneficialOpportunityRetention':rate(int((app&benef).sum()),int(benef.sum())),'harmfulVetoRecall':rate(int((veto&harm).sum()),int(harm.sum())),'approveHarmfulRate':rate(int((app&harm).sum()),int(app.sum())),'harmfulFalseApproveCount':int((app&harm).sum()),'beneficialFalseVetoCount':int((veto&benef).sum()),'otherExtremeDecisionRate':rate(int(((app|veto)&other).sum()),int(other.sum()))}
def main():
 R=rows();sets={'EXACT_STATE':BASE_FEATURES,'EXACT_STATE_PLUS_READINESS5':BASE_FEATURES+R5,'EXACT_STATE_PLUS_READINESS_MULTI':BASE_FEATURES+R5+RM,'READINESS_ONLY':R5+RM};res={}
 for name,f in sets.items():
  y,c,p=loo(R,f);res[name]={'featureNames':f,'metrics':met(y,c,p)}
 rep={'version':'R4_REPAIR_FORWARD_READINESS_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'marketCount':len(R),'classCounts':dict(Counter(r['class'] for r in R)),'sourceCounts':dict(Counter(r['source'] for r in R)),'expertProvenance':{'fillArtifact':'open_order_fill_lifecycle_v0.joblib','pendingArtifact':'execution_aware_pending_management_v0.joblib','overlapWith48ExactMarkets':0},'representations':res,'interpretationBoundary':'All readiness features are predictions from market-disjoint pre-existing execution experts applied to exact strict-past seam state. Consumed-development only; no threshold tuning or authority.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'marketCount':rep['marketCount'],'classCounts':rep['classCounts'],'representations':res},indent=2))
if __name__=='__main__':main()
