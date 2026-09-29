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
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import load_teacher,load_path,enriched_values,BASE_FEATURES,fnum
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES=[ROOT/'data/research/lan_worker_returns/r4-repair-quote-challenge10-b1-v1/teacher.json',ROOT/'data/research/lan_worker_returns/r4-repair-quote-challenge10-b2-v1/teacher.json']
OUT=P/'r4_repair_exact_quote_representation_challenge10_score_v1.json'

def train_rows():
 t=load_teacher();p=load_path();out=[]
 for mid in sorted(set(t)&set(p)):
  c=str(t[mid].get('branchClass'))
  if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}:continue
  out.append((mid,c,enriched_values(t[mid],p[mid],'EXACT_STATE_PATH')))
 return out

def challenge_rows():
 out=[]
 for fp in FILES:
  o=json.loads(fp.read_text(encoding='utf-8'))
  for r in o.get('rows',[]):
   if not isinstance(r,dict) or r.get('branchClass')=='NO_CANDIDATE':
    out.append({'marketId':r.get('marketId'),'class':r.get('branchClass'),'features':None,'quotePath':None});continue
   c=r.get('candidate') or {}; forced=((r.get('counterfactual') or {}).get('forced') or [])
   ps=((forced[0].get('activeOrderPathState') or {}) if forced else {})
   s=ps.get('summary') or {}
   # construct exact-state + quote from the candidate using same semantics as development helper
   pseudo={'candidate':c}
   # enriched_values needs a path row shape
   prow={'pathState':{'summary':s}}
   v=enriched_values(pseudo,prow,'EXACT_STATE_PATH')
   out.append({'marketId':int(r['marketId']),'class':str(r['branchClass']),'features':v,'quotePath':s})
 return out

def matrix(rows,features):
 return np.asarray([[r[2].get(f,math.nan) for f in features] for r in rows],float)
def fit_apply(tr,chall,features):
 X=matrix(tr,features);y=np.asarray([1 if r[1]=='PARETO_BENEFICIAL' else 0 for r in tr],int)
 med=np.nanmedian(X,axis=0);med=np.where(np.isfinite(med),med,0.0);X=np.where(np.isfinite(X),X,med)
 test=[]
 for r in chall:
  if r['features'] is None:test.append([math.nan]*len(features))
  else:test.append([r['features'].get(f,math.nan) for f in features])
 T=np.asarray(test,float);T=np.where(np.isfinite(T),T,med)
 m=make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830));m.fit(X,y)
 return m.predict_proba(T)[:,1],{features[i]:float(med[i]) for i in range(len(features))}
def metrics(chall,p):
 valid=np.asarray([r['class']!='NO_CANDIDATE' for r in chall]);bh=np.asarray([r['class'] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} for r in chall]);y=np.asarray([1 if r['class']=='PARETO_BENEFICIAL' else 0 for r in chall],int);pred=(p>=.5).astype(int)
 app=(p>=.65)&valid;veto=(p<=.35)&valid;benef=np.asarray([r['class']=='PARETO_BENEFICIAL' for r in chall]);harm=np.asarray([r['class']=='PARETO_HARMFUL' for r in chall]);other=valid&~bh
 def rate(n,d):return float(n/d) if d else math.nan
 return {'exactCandidateCount':int(valid.sum()),'beneficialHarmfulCount':int(bh.sum()),'auc':float(roc_auc_score(y[bh],p[bh])),'balancedAccuracyAt050':float(balanced_accuracy_score(y[bh],pred[bh])),'beneficialRecallAt050':float(recall_score(y[bh],pred[bh],pos_label=1)),'harmfulRecallAt050':float(recall_score(y[bh],pred[bh],pos_label=0)),'decisionCoverage':rate(int((app|veto).sum()),int(valid.sum())),'beneficialOpportunityRetention':rate(int((app&benef).sum()),int(benef.sum())),'harmfulVetoRecall':rate(int((veto&harm).sum()),int(harm.sum())),'approvePrecisionBeneficial':rate(int((app&benef).sum()),int(app.sum())),'approveHarmfulRate':rate(int((app&harm).sum()),int(app.sum())),'vetoPrecisionHarmful':rate(int((veto&harm).sum()),int(veto.sum())),'vetoBeneficialRate':rate(int((veto&benef).sum()),int(veto.sum())),'harmfulFalseApproveCount':int((app&harm).sum()),'beneficialFalseVetoCount':int((veto&benef).sum()),'otherExtremeDecisionRate':rate(int(((app|veto)&other).sum()),int(other.sum()))}
def main():
 tr=train_rows();ch=challenge_rows();sets={'EXACT_STATE':BASE_FEATURES,'EXACT_STATE_PLUS_QUOTE':BASE_FEATURES+['quoteOffsetGapTicks']};rep={'version':'R4_REPAIR_EXACT_QUOTE_REPRESENTATION_CHALLENGE10_SCORE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'trainMarkets':len(tr),'trainClassCounts':dict(Counter(x[1] for x in tr)),'challengeClassCounts':dict(Counter(x['class'] for x in ch)),'challengeMarketIds':[x['marketId'] for x in ch],'representations':{},'thresholds':{'approveMin':.65,'vetoMax':.35}}
 for name,features in sets.items():
  p,med=fit_apply(tr,ch,features);rows=[]
  for i,r in enumerate(ch):
   s=r.get('quotePath') or {};q=None
   a=s.get('repairBestQuoteOffsetTicks');b=s.get('dominantBestQuoteOffsetTicks')
   try:q=float(a)-float(b) if math.isfinite(float(a)) and math.isfinite(float(b)) else None
   except Exception:q=None
   rows.append({'marketId':r['marketId'],'actualClass':r['class'],'pBeneficial':float(p[i]),'triage':'APPROVE' if p[i]>=.65 and r['class']!='NO_CANDIDATE' else 'VETO' if p[i]<=.35 and r['class']!='NO_CANDIDATE' else 'ABSTAIN','exactActiveOrderCount':s.get('activeOrderCount'),'quoteOffsetGapTicks':q})
  rep['representations'][name]={'featureNames':features,'metrics':metrics(ch,p),'trainImputationMedian':med,'rows':rows}
 OUT.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'challengeClassCounts':rep['challengeClassCounts'],'metrics':{k:v['metrics'] for k,v in rep['representations'].items()},'quoteRows':rep['representations']['EXACT_STATE_PLUS_QUOTE']['rows']},indent=2,allow_nan=True))
if __name__=='__main__':main()
