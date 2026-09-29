from __future__ import annotations
import argparse,glob,json
from pathlib import Path
import joblib,numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score,balanced_accuracy_score,roc_auc_score,average_precision_score,brier_score_loss,confusion_matrix

STAGEA=[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768]

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);both=len(set(y.tolist()))>1
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'accuracy':float(accuracy_score(y,pred)) if len(y) else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if both else None,'auc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'confusion':confusion_matrix(y,pred,labels=[0,1]).tolist() if len(y) else None}

def make_model():
 return Pipeline([('imputer',SimpleImputer(strategy='median')),('scale',StandardScaler()),('model',LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=6703))])

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model',required=True);a=ap.parse_args()
 files=sorted(glob.glob(a.pattern));rows=[];summaries=[];seen=set()
 for p in files:
  d=json.load(open(p,encoding='utf-8'));summaries.append({'path':p,'aggregate':d.get('aggregate'),'functionalPass':d.get('functionalPass')})
  for m in d.get('rows',[]):seen.add(int(m['marketId']))
  rows.extend(d.get('experience') or [])
 missing=[m for m in STAGEA if m not in seen];extra=sorted(seen-set(STAGEA))
 features=sorted({k for r in rows for k,v in (r.get('state') or {}).items() if v is None or isinstance(v,(bool,int,float))})
 for k in ('pV44','pV47'):
  if k not in features:features.append(k)
 def vector(r):
  s=r.get('state') or {};return [np.nan if (s.get(k) if k in s else r.get(k)) is None else float(s.get(k) if k in s else r.get(k)) for k in features]
 X=np.asarray([vector(r) for r in rows],np.float64) if rows else np.empty((0,len(features)));y=np.asarray([int(r['labelOpenOrExtend']) for r in rows],int);g=np.asarray([int(r['marketId']) for r in rows],int)
 out={'version':'TARGET_ETH_V67_STAGEA16_COUNTERFACTUAL_EXPERIENCE_ROUTER','researchOnly':True,'actionAuthority':False,'scope':'V65 HOLD-state residual OPEN_OR_EXTEND teacher','sources':summaries,'coverage':{'files':len(files),'markets':sorted(seen),'missingStageA':missing,'extraMarkets':extra,'samples':len(rows),'positives':int(y.sum()) if len(y) else 0,'negatives':int(len(y)-y.sum()) if len(y) else 0},'features':features,'label':'OPEN_OR_EXTEND succeeds in OUR realistic-HFT branch: actual Expand fill, subsequent Repair, non-decreasing rounds, zero safety violation','split':{'trainMarkets':STAGEA[:12],'validationMarkets':STAGEA[12:]},'metrics':{},'experience':rows,'boundary':['Target lifecycle deficit is analysis/curriculum only and excluded from model features.','Only OUR strict-past state and existing frozen model scores are features.','One fixed regularized logistic model; no sweep or threshold fitting.','Research model only; no runtime action authority/no 8781.']}
 enough=(not missing and not extra and len(rows)>=12 and len(set(y.tolist()))==2)
 out['trainingEligible']=bool(enough)
 if enough:
  tr=np.isin(g,STAGEA[:12]);va=np.isin(g,STAGEA[12:])
  if tr.sum() and len(set(y[tr].tolist()))==2:
   mdl=make_model().fit(X[tr],y[tr]);out['metrics']['train']=met(y[tr],mdl.predict_proba(X[tr])[:,1])
   if va.sum():out['metrics']['validation']=met(y[va],mdl.predict_proba(X[va])[:,1])
  else:out['metrics']['fixedSplitError']='training split lacks both classes'
  loo=np.zeros(len(y),float)
  for mid in sorted(set(g.tolist())):
   te=g==mid;tt=~te
   if len(set(y[tt].tolist()))<2:loo[te]=float(y[tt].mean())
   else:loo[te]=make_model().fit(X[tt],y[tt]).predict_proba(X[te])[:,1]
  out['metrics']['leaveOneMarketOut']=met(y,loo)
  out['metrics']['alwaysContinue']=met(y,np.zeros(len(y),float))
  final=make_model().fit(X,y);Path(a.model).parent.mkdir(parents=True,exist_ok=True);joblib.dump({'version':out['version'],'features':features,'model':final,'scope':out['scope'],'actionAuthority':False,'trainingMarkets':STAGEA,'label':out['label']},a.model);out['model']=a.model
 else:out['trainingBlockedReason']='Need complete Stage-A 16 coverage, at least 12 causal samples, and both outcome classes.'
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'trainingEligible':out['trainingEligible'],'coverage':out['coverage'],'metrics':out['metrics'],'model':out.get('model')},ensure_ascii=False))
if __name__=='__main__':main()
