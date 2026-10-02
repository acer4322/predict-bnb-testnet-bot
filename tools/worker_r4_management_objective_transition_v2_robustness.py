from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
SRC=ST/'r4_p0b_target_objective_topology_rows_v2.csv'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
DELTA=['floor','upside','absNet','coverage','risk_deficit','current_commitment']

def build():
 d=pd.read_csv(SRC).sort_values(['market_id','t','parent_id']).copy()
 # Strictly-earlier timestamp family occupancy; same-t rows cannot predict one another.
 prev_pb=np.zeros(len(d),dtype=float); prev_ss=np.zeros(len(d),dtype=float); labels=np.empty(len(d),dtype=object)
 valid=np.zeros(len(d),dtype=bool)
 last_state={}
 # retain strict-past last numeric state per market at previous timestamp for deltas
 prev_vals={}
 for mid,ix in d.groupby('market_id',sort=False).groups.items():
  sub=d.loc[ix]; families=set(); last_t=None; last_num=None
  for t,g in sub.groupby('t',sort=True):
   inds=list(g.index)
   if last_t is not None:
    for j in inds:
     prev_pb[d.index.get_loc(j)]=float('PAIR_BALANCE' in families)
     prev_ss[d.index.get_loc(j)]=float('STATE_SHAPING' in families)
     fam=str(d.at[j,'objective_family'])
     labels[d.index.get_loc(j)]='CONTINUE' if fam in families else 'SWITCH_OR_OPEN'
     valid[d.index.get_loc(j)]=True
     if last_num is not None:
      for c in DELTA: d.at[j,f'{c}_delta_strict1']=pd.to_numeric(d.at[j,c],errors='coerce')-last_num[c]
   # family-set represents most recent strictly-earlier timestamp, not cumulative history
   families=set(map(str,g.objective_family))
   last_num={c:pd.to_numeric(g[c],errors='coerce').mean() for c in DELTA}
   last_t=t
 d['prev_pb_set']=prev_pb; d['prev_ss_set']=prev_ss; d['transition']=labels
 return d.loc[valid].copy()

def weights(y):
 s=pd.Series(y); c=s.value_counts(); return np.asarray([len(s)/(len(c)*c[v]) for v in s],dtype=float)

def score(y,p,prob):
 yy=np.asarray(y); sw=yy=='SWITCH_OR_OPEN'
 return {'ba':float(balanced_accuracy_score(yy,p)),'switchRecall':float(np.mean(np.asarray(p)[sw]=='SWITCH_OR_OPEN')),'continueRecall':float(np.mean(np.asarray(p)[~sw]=='CONTINUE')),'auc':float(roc_auc_score(sw.astype(int),prob)),'ap':float(average_precision_score(sw.astype(int),prob))}

def fit_predict(name,xtr,ytr,xte,w,seed):
 if name=='HGB':
  m=HistGradientBoostingClassifier(learning_rate=.06,max_iter=160,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed); m.fit(xtr,ytr,sample_weight=w)
 elif name=='LOGIT':
  m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(max_iter=1000,class_weight='balanced',C=1.0,random_state=seed)); m.fit(xtr,ytr)
 elif name=='EXTRATREES':
  m=make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=25,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed)); m.fit(xtr,ytr)
 p=m.predict(xte); cls=list(m.classes_ if hasattr(m,'classes_') else m[-1].classes_); k=cls.index('SWITCH_OR_OPEN'); prob=m.predict_proba(xte)[:,k]
 return p,prob

def main():
 d=build(); mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min())
 groups={
  'ECON_LIFECYCLE':BASE,
  'PLUS_FAMILY_SET':BASE+['prev_pb_set','prev_ss_set'],
  'FULL_STRICT':BASE+['prev_pb_set','prev_ss_set']+[f'{c}_delta_strict1' for c in DELTA]
 }
 out={'version':'R4_MANAGEMENT_OBJECTIVE_TRANSITION_V2_WORKER_ROBUSTNESS','researchOnly':True,'strictPastSameTimestampGuard':True,'rows':len(d),'markets':len(mids),'results':{}}
 for gn,fs in groups.items():
  out['results'][gn]={}
  for mn in ['HGB','LOGIT','EXTRATREES']:
   folds=[]
   for i,cut in enumerate((.60,.70,.80)):
    k=int(len(mids)*cut); v=int(len(mids)*.10); tr=d[d.market_id.isin(mids[:k])]; te=d[d.market_id.isin(mids[k:k+v])]
    p,prob=fit_predict(mn,tr[fs],tr.transition,te[fs],weights(tr.transition),9900+i)
    folds.append(score(te.transition,p,prob))
   out['results'][gn][mn]={'folds':folds,'meanBA':float(np.mean([z['ba'] for z in folds])),'worstBA':float(np.min([z['ba'] for z in folds])),'meanAUC':float(np.mean([z['auc'] for z in folds])),'meanSwitchRecall':float(np.mean([z['switchRecall'] for z in folds]))}
 (OUT/'robustness.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
