from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
OLD=ST/'r4_p0b_target_objective_topology_rows_v2.csv'
NEW=ST/'r4_management_postfresh80_objective_transition_rows_v1.csv'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']

def build(src):
 d=pd.read_csv(src).sort_values(['market_id','t','parent_id']).copy(); rows=[]
 for mid,g in d.groupby('market_id',sort=False):
  g=g.sort_values(['t','parent_id']).copy(); prev_f=set(); last_num=None
  for t,h in g.groupby('t',sort=True):
   if last_num is not None:
    for _,r in h.iterrows():
     z=r.to_dict(); fam=str(r.objective_family); z['prev_pb_set']=float('PAIR_BALANCE' in prev_f); z['prev_ss_set']=float('STATE_SHAPING' in prev_f); z['transition']='CONTINUE' if fam in prev_f else 'SWITCH_OR_OPEN'; z['risk_deficit_delta_strict1']=float(pd.to_numeric(r.risk_deficit,errors='coerce')-last_num); rows.append(z)
   prev_f=set(map(str,h.objective_family)); last_num=float(pd.to_numeric(h.risk_deficit,errors='coerce').mean())
 return pd.DataFrame(rows)

def weights(y):
 s=pd.Series(y); c=s.value_counts(); return np.asarray([len(s)/(len(c)*c[v]) for v in s],float)

def score(y,p,prob):
 yy=np.asarray(y); sw=yy=='SWITCH_OR_OPEN'; return {'n':int(len(yy)),'switchRate':float(sw.mean()),'ba':float(balanced_accuracy_score(yy,p)),'switchRecall':float(np.mean(np.asarray(p)[sw]=='SWITCH_OR_OPEN')),'continueRecall':float(np.mean(np.asarray(p)[~sw]=='CONTINUE')),'auc':float(roc_auc_score(sw.astype(int),prob)),'ap':float(average_precision_score(sw.astype(int),prob))}

def fit(name,x,y,w,seed):
 if name=='HGB':
  m=HistGradientBoostingClassifier(learning_rate=.06,max_iter=160,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed); m.fit(x,y,sample_weight=w); return m
 m=make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=400,min_samples_leaf=25,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed)); m.fit(x,y); return m

def pred(m,x):
 p=m.predict(x); cls=list(m.classes_ if hasattr(m,'classes_') else m[-1].classes_); prob=m.predict_proba(x)[:,cls.index('SWITCH_OR_OPEN')]; return p,prob

def main():
 old=build(OLD); new=build(NEW); mids=[int(x) for x in sorted(new.market_id.unique(),key=lambda m:new.loc[new.market_id==m,'t'].min())]
 sets={'BASE_OCCUPANCY':BASE+['prev_pb_set','prev_ss_set'],'BASE_OCCUPANCY_RISK_DELTA':BASE+['prev_pb_set','prev_ss_set','risk_deficit_delta_strict1']}
 out={'version':'R4_MANAGEMENT_POSTFRESH80_TRANSITION_GENERALIZATION_V1','researchOnly':True,'trainMarkets':int(old.market_id.nunique()),'trainRows':int(len(old)),'testMarkets':int(new.market_id.nunique()),'testRows':int(len(new)),'newMarketBlocks':[mids[i:i+20] for i in range(0,len(mids),20)],'results':{}}
 for mn in ['HGB','EXTRATREES']:
  out['results'][mn]={}
  for sn,fs in sets.items():
   runs=[]
   for seed in [21001,22001,23001]:
    m=fit(mn,old[fs],old.transition,weights(old.transition),seed); p,pr=pred(m,new[fs]); overall=score(new.transition,p,pr); blocks=[]
    for bi in range(0,len(mids),20):
     z=new[new.market_id.isin(mids[bi:bi+20])]; bp,bpr=pred(m,z[fs]); blocks.append(score(z.transition,bp,bpr))
    runs.append({'seed':seed,'overall':overall,'blocks':blocks})
   out['results'][mn][sn]={'runs':runs,'meanBA':float(np.mean([r['overall']['ba'] for r in runs])),'meanAUC':float(np.mean([r['overall']['auc'] for r in runs])),'meanSwitchRecall':float(np.mean([r['overall']['switchRecall'] for r in runs])),'minBlockBA':float(min(b['ba'] for r in runs for b in r['blocks'])),'meanBlockBA':float(np.mean([b['ba'] for r in runs for b in r['blocks']]))}
 (OUT/'generalization.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
