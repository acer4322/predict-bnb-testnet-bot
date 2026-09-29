from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np,joblib
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,precision_recall_fscore_support,brier_score_loss

ROLES=['PROBE_CORE','ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND']
FEATURES=['price','rank','logDepth','bestPrice','logBestDepth','distanceTicks','pairCompatible','pairSumMinus1','floor','upsideGap','scopeDebtQty','reservedRepairQuota','availableExpandRiskCredit','liveSlots','repairQtyAuthorized','overflowQtyAuthorized','sideUp']+[f'role_{r}' for r in ROLES]

def load_rows(path):
 d=json.load(open(path,encoding='utf-8'));out=[]
 for r in d['rows']:
  mid=int(r['marketId'])
  for x in r['executionRows']:out.append((mid,x))
 return out

def vec(x):
 depth=float(x.get('depth') or 0.0);bd=float(x.get('bestDepth') or 0.0);ps=x.get('pairSum')
 vals=[float(x.get('price') or 0),float(x.get('rank') or 99),math.log1p(max(0.,depth)),float(x.get('bestPrice') or 0),math.log1p(max(0.,bd)),float(x.get('distanceTicks') or 0),1. if x.get('pairCompatible') else 0.,(float(ps)-1.0) if ps is not None else 0.,float(x.get('floor') or 0),float(x.get('upsideGap') or 0),float(x.get('scopeDebtQty') or 0),float(x.get('reservedRepairQuota') or 0),float(x.get('availableExpandRiskCredit') or 0),float(x.get('liveSlots') or 0),float(x.get('repairQtyAuthorized') or 0),float(x.get('overflowQtyAuthorized') or 0),1. if str(x.get('side')).upper()=='UP' else 0.]
 role=str(x.get('role') or '');vals.extend(1. if role==r else 0. for r in ROLES);return vals

def mats(rows,markets):
 z=[x for m,x in rows if m in markets];X=np.asarray([vec(x) for x in z],float);y=np.asarray([int(bool(x.get('filledWithin5s'))) for x in z],int);return X,y,z

def metric(model,X,y):
 p=model.predict_proba(X)[:,1];pred=(p>=.5).astype(int);pr,rc,f1,_=precision_recall_fscore_support(y,pred,average='binary',zero_division=0)
 return {'n':int(len(y)),'positiveRate':float(np.mean(y)),'predictedRate':float(np.mean(pred)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(np.unique(y))>1 else None,'brier':float(brier_score_loss(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'precision':float(pr),'recall':float(rc),'f1':float(f1),'scoreMean':float(np.mean(p)),'scoreP10':float(np.quantile(p,.1)),'scoreMedian':float(np.quantile(p,.5)),'scoreP90':float(np.quantile(p,.9))}

def build_models():
 return {
  'LOGISTIC':Pipeline([('scale',StandardScaler()),('model',LogisticRegression(max_iter=1500,class_weight='balanced',C=1.0,random_state=20260905))]),
  'HGB':HistGradientBoostingClassifier(max_iter=160,learning_rate=.045,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=4.0,class_weight='balanced',random_state=20260905)
 }

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dev',required=True);ap.add_argument('--holdout',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();dev=load_rows(a.dev);hold=load_rows(a.holdout);mids=sorted({m for m,_ in dev});train=set(mids[:16]);val=set(mids[16:]);Xm,ym,_=mats(dev,train);Xv,yv,_=mats(dev,val);allm=set(mids);Xa,ya,_=mats(dev,allm);holdm=set(m for m,_ in hold);Xh,yh,hrows=mats(hold,holdm)
 cand={};models=build_models()
 for name,m in models.items():m.fit(Xm,ym);cand[name]={'train':metric(m,Xm,ym),'validation':metric(m,Xv,yv)}
 winner=max(cand,key=lambda n:(cand[n]['validation']['auc'] if cand[n]['validation']['auc'] is not None else -1));final=build_models()[winner];final.fit(Xa,ya);fresh=metric(final,Xh,yh)
 # diagnostics by rank on holdout, without changing winner/model.
 p=final.predict_proba(Xh)[:,1];rankdiag={}
 for bucket,fn in [('R1',lambda r:(r.get('rank')==1)),('R2',lambda r:(r.get('rank')==2)),('R3PLUS',lambda r:((r.get('rank') or 99)>=3))]:
  idx=[i for i,r in enumerate(hrows) if fn(r)]
  if idx:rankdiag[bucket]={'n':len(idx),'positiveRate':float(np.mean(yh[idx])),'scoreMean':float(np.mean(p[idx]))}
 outdir=Path(a.output);outdir.mkdir(parents=True,exist_ok=True);joblib.dump({'model':final,'features':FEATURES,'version':'MS4_R2_OUR_EXECUTION_FILLABILITY_V1','winnerModel':winner,'researchOnly':True,'label':'filledWithin5s','runtimeAuthority':False},outdir/'ms4_r2_our_execution_fillability_v1.joblib')
 rep={'version':'MS4_R2_OUR_EXECUTION_FILLABILITY_V1','researchOnly':True,'runtimeAuthority':False,'features':FEATURES,'devMarkets':mids,'trainMarkets':sorted(train),'validationMarkets':sorted(val),'holdoutMarkets':sorted(holdm),'devOrders':len(dev),'holdoutOrders':len(hold),'candidates':cand,'selected':winner,'selectedRefitDev24':metric(final,Xa,ya),'fresh8Holdout':fresh,'fresh8RankDiagnostic':rankdiag,'boundary':['Model selection uses dev24 validation only','Fresh8 evaluated once after model selection','No winner/settlement/Target runtime inputs','Execution realization only; no responsibility/risk/quantity authority']};(outdir/'result.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__':main()
