from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
SRC=ST/'r4_p0b_target_objective_topology_rows_v2.csv'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s','prev_pb_set','prev_ss_set']
RAW_DELTA=['floor','upside','absNet','coverage','risk_deficit','current_commitment']
GROUPS={
 'D_FLOOR':['floor_delta_strict1'],
 'D_UPSIDE':['upside_delta_strict1'],
 'D_ABSNET':['absNet_delta_strict1'],
 'D_COVERAGE':['coverage_delta_strict1'],
 'D_RISK_DEFICIT':['risk_deficit_delta_strict1'],
 'D_COMMITMENT':['current_commitment_delta_strict1'],
 'D_SAFETY':['floor_delta_strict1','coverage_delta_strict1','risk_deficit_delta_strict1'],
 'D_EXPOSURE':['absNet_delta_strict1','current_commitment_delta_strict1','upside_delta_strict1'],
 'D_ALL':[f'{c}_delta_strict1' for c in RAW_DELTA],
}

def build():
 d=pd.read_csv(SRC).sort_values(['market_id','t','parent_id']).copy(); pos={idx:i for i,idx in enumerate(d.index)}
 pp=np.zeros(len(d)); ps=np.zeros(len(d)); lab=np.empty(len(d),object); valid=np.zeros(len(d),bool)
 for mid,ix in d.groupby('market_id',sort=False).groups.items():
  sub=d.loc[ix]; fams=set(); last=None
  for t,g in sub.groupby('t',sort=True):
   inds=list(g.index)
   if last is not None:
    for j in inds:
     k=pos[j]; pp[k]=float('PAIR_BALANCE' in fams); ps[k]=float('STATE_SHAPING' in fams); f=str(d.at[j,'objective_family']); lab[k]='CONTINUE' if f in fams else 'SWITCH_OR_OPEN'; valid[k]=True
     for c in RAW_DELTA: d.at[j,f'{c}_delta_strict1']=pd.to_numeric(d.at[j,c],errors='coerce')-last[c]
   fams=set(map(str,g.objective_family)); last={c:pd.to_numeric(g[c],errors='coerce').mean() for c in RAW_DELTA}
 d['prev_pb_set']=pp; d['prev_ss_set']=ps; d['transition']=lab
 return d.loc[valid].copy()

def weights(y):
 s=pd.Series(y); c=s.value_counts(); return np.array([len(s)/(len(c)*c[v]) for v in s])
def metrics(y,p,prob):
 y=np.asarray(y); sw=y=='SWITCH_OR_OPEN'; return {'ba':float(balanced_accuracy_score(y,p)),'switchRecall':float(np.mean(np.asarray(p)[sw]=='SWITCH_OR_OPEN')),'continueRecall':float(np.mean(np.asarray(p)[~sw]=='CONTINUE')),'auc':float(roc_auc_score(sw.astype(int),prob)),'ap':float(average_precision_score(sw.astype(int),prob))}
def fit(name,tr,te,fs,seed):
 if name=='HGB': m=HistGradientBoostingClassifier(learning_rate=.06,max_iter=160,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed); m.fit(tr[fs],tr.transition,sample_weight=weights(tr.transition))
 else: m=make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=25,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed)); m.fit(tr[fs],tr.transition)
 p=m.predict(te[fs]); cls=list(m.classes_ if hasattr(m,'classes_') else m[-1].classes_); prob=m.predict_proba(te[fs])[:,cls.index('SWITCH_OR_OPEN')]; return metrics(te.transition,p,prob)
def main():
 d=build(); mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min()); configs={'BASE_OCCUPANCY':BASE}; configs.update({k:BASE+v for k,v in GROUPS.items()})
 out={'version':'R4_MANAGEMENT_OBJECTIVE_TRANSITION_V4_DELTA_ABLATION','researchOnly':True,'strictPast':True,'rows':len(d),'markets':len(mids),'models':{}}
 for mn in ['HGB','EXTRATREES']:
  out['models'][mn]={}
  for name,fs in configs.items():
   folds=[]
   for i,cut in enumerate((.60,.70,.80)):
    k=int(len(mids)*cut); v=int(len(mids)*.10); tr=d[d.market_id.isin(mids[:k])]; te=d[d.market_id.isin(mids[k:k+v])]; folds.append(fit(mn,tr,te,fs,12000+i))
   out['models'][mn][name]={'folds':folds,'meanBA':float(np.mean([x['ba'] for x in folds])),'worstBA':float(np.min([x['ba'] for x in folds])),'meanAUC':float(np.mean([x['auc'] for x in folds])),'meanSwitchRecall':float(np.mean([x['switchRecall'] for x in folds]))}
 (OUT/'delta_ablation.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
