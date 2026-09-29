from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,confusion_matrix
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PRE=P/'r4_management_marginal_objective_value_full_role_trajectory_v2_preregistered.json'
OUT=P/'r4_management_marginal_objective_value_full_role_trajectory_v2_diagnostic.json'
CH=[P/f'r4_management_marginal_objective_value_full_role_trajectory_chunk_{i}_6_v2.json' for i in range(0,48,6)]

def model(seed):
 return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])

def binary_lomo(d,fs,label_col,pos):
 probs=np.full(len(d),np.nan);pred=np.empty(len(d),dtype=object)
 mids=list(dict.fromkeys(int(x) for x in d.marketId.tolist()))
 for i,mid in enumerate(mids):
  te=np.where(d.marketId.to_numpy()==mid)[0];tr=np.where(d.marketId.to_numpy()!=mid)[0]
  m=model(51000+i);m.fit(d.iloc[tr][fs],d.iloc[tr][label_col]);cls=list(m.classes_)
  p=m.predict_proba(d.iloc[te][fs])[:,cls.index(pos)];probs[te]=p
  neg=[c for c in cls if c!=pos][0];pred[te]=np.where(p>=.5,pos,neg)
 y=d[label_col].astype(str).to_numpy();yy=(y==pos).astype(int)
 return probs,pred,{'n':int(len(d)),'markets':int(d.marketId.nunique()),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'auc':float(roc_auc_score(yy,probs)),'ap':float(average_precision_score(yy,probs)),'positiveRecall':float(np.mean(pred[yy==1]==pos)),'negativeRecall':float(np.mean(pred[yy==0]!=pos))}

def main():
 pre=json.loads(PRE.read_text(encoding='utf-8'));rows=[]
 for f in CH: rows += json.loads(f.read_text(encoding='utf-8'))['rows']
 d=pd.DataFrame([r for r in rows if 'error' not in r]).copy()
 role=d.knownRole.astype(str)
 d['gateA']=np.where(role.eq('REJECT_NO_ACTION'),'REJECT','REALIZE')
 d['gateB']=np.where(role.eq('PREPOSITION_REPAIR_SUBSTITUTE'),'SUBSTITUTE',np.where(role.eq('PARALLEL_STATE_SHAPING'),'ADDITIVE',None))
 real=d[d.gateA.eq('REALIZE')].reset_index(drop=True)
 results={}
 for name,fs0 in pre['featureSets'].items():
  fs=list(fs0)
  pa,pra,sa=binary_lomo(d,fs,'gateA','REALIZE')
  pb,prb,sb=binary_lomo(real,fs,'gateB','SUBSTITUTE')
  # Full two-stage market-LOMO: Gate B is evaluated only when Gate A predicts REALIZE.
  full_pred=[];detail=[]
  # map gateB OOF prediction/prob by key from the REALIZE subset; false-positive Gate-A REJECT rows use a separately LOMO-fitted Gate-B model on REALIZE training markets only.
  real_key={(int(r.marketId),str(r.candidateKey)):(float(pb[i]),str(prb[i])) for i,(_,r) in enumerate(real.iterrows())}
  mids=list(dict.fromkeys(int(x) for x in d.marketId.tolist()))
  for mid in mids:
   te=d[d.marketId.eq(mid)]
   tr_real=real[~real.marketId.eq(mid)]
   mb=model(61000+mid%10000);mb.fit(tr_real[fs],tr_real.gateB);cls=list(mb.classes_)
   for idx,r in te.iterrows():
    a=str(pra[idx])
    if a=='REJECT': final='REJECT_NO_ACTION';bp=None
    else:
     key=(int(r.marketId),str(r.candidateKey))
     if key in real_key: bp=real_key[key][0]
     else: bp=float(mb.predict_proba(pd.DataFrame([r[fs].to_dict()]))[0,cls.index('SUBSTITUTE')])
     final='PREPOSITION_REPAIR_SUBSTITUTE' if bp>=.5 else 'PARALLEL_STATE_SHAPING'
    full_pred.append((idx,final));detail.append({'marketId':int(r.marketId),'candidateKey':str(r.candidateKey),'truth':str(r.knownRole),'gateAProbRealize':float(pa[idx]),'gateAPred':a,'gateBProbSubstitute':bp,'fullPred':final})
  full_pred=[x[1] for x in sorted(full_pred)]
  truth=d.sort_index().knownRole.astype(str).tolist()
  classes=['REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING']
  cm=confusion_matrix(truth,full_pred,labels=classes)
  per={classes[i]:float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None for i in range(len(classes))}
  results[name]={'gateA':sa,'gateB':sb,'fullRouter':{'n':int(len(d)),'markets':int(d.marketId.nunique()),'balancedAccuracy':float(np.mean([x for x in per.values() if x is not None])),'accuracy':float(np.mean(np.asarray(truth)==np.asarray(full_pred))),'perRoleRecall':per,'confusionMatrix':cm.tolist(),'labels':classes},'rows':detail}
 out={'version':'R4_MANAGEMENT_MARGINAL_OBJECTIVE_VALUE_FULL_ROLE_TRAJECTORY_V2_DIAGNOSTIC','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'contractStatus':pre['status'],'rows':int(len(d)),'markets':int(d.marketId.nunique()),'roleCounts':d.knownRole.value_counts().to_dict(),'allReplayExact':bool(all(abs(float(x.get('sameResidualReplayError',0)))<1e-9 and abs(float(x.get('parentResidualReplayError',0)))<1e-9 for x in rows if 'error' not in x)),'results':results,'interpretationBoundary':'Consumed Expanded48 development only. Full two-stage router is true leave-one-market-out by market. Future counterfactual branch outcomes remain teacher labels only; runtime X is strict-past pre-open ledger/candidate state. No authority or threshold sweep.'}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in out.items() if k!='results'}|{'results':{n:{'gateA':r['gateA'],'gateB':r['gateB'],'fullRouter':r['fullRouter']} for n,r in results.items()}},indent=2))
if __name__=='__main__': main()
