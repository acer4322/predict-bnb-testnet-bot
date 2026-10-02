from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
NEW=ST/'r4_management_postfresh80_objective_transition_rows_v1.csv'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
FS=BASE+['prev_pb_set','prev_ss_set','risk_deficit_delta_strict1']

def build(src):
 d=pd.read_csv(src).sort_values(['market_id','t','parent_id']).copy(); rows=[]
 for mid,g in d.groupby('market_id',sort=False):
  prev_f=set(); last_num=None
  for t,h in g.sort_values(['t','parent_id']).groupby('t',sort=True):
   if last_num is not None:
    for _,r in h.iterrows():
     z=r.to_dict(); fam=str(r.objective_family)
     z['prev_pb_set']=float('PAIR_BALANCE' in prev_f); z['prev_ss_set']=float('STATE_SHAPING' in prev_f)
     z['transition']='CONTINUE' if fam in prev_f else 'SWITCH_OR_OPEN'
     z['risk_deficit_delta_strict1']=float(pd.to_numeric(r.risk_deficit,errors='coerce')-last_num); rows.append(z)
   prev_f=set(map(str,h.objective_family)); last_num=float(pd.to_numeric(h.risk_deficit,errors='coerce').mean())
 return pd.DataFrame(rows)

def class_weights(y):
 s=pd.Series(y); c=s.value_counts(); return np.asarray([len(s)/(len(c)*c[v]) for v in s],float)

def score(y,p,prob):
 yy=np.asarray(y); sw=yy=='SWITCH_OR_OPEN'
 return {'n':int(len(yy)),'ba':float(balanced_accuracy_score(yy,p)),'auc':float(roc_auc_score(sw.astype(int),prob)),'ap':float(average_precision_score(sw.astype(int),prob)),'switchRecall':float(np.mean(np.asarray(p)[sw]=='SWITCH_OR_OPEN')),'continueRecall':float(np.mean(np.asarray(p)[~sw]=='CONTINUE'))}

def main():
 new=build(NEW); mids=[int(x) for x in sorted(new.market_id.unique(),key=lambda m:new.loc[new.market_id==m,'t'].min())]
 holdout_mids=mids[60:]; holdout=new[new.market_id.isin(holdout_mids)].copy()
 windows={'RECENT20':mids[40:60],'RECENT40':mids[20:60],'RECENT60':mids[:60]}
 out={'version':'R4_MANAGEMENT_POSTFRESH80_ROLLING_WINDOW_V1','researchOnly':True,'developmentSelectionOnly':True,'untouchedPromotionValidationUsed':False,'holdoutMarkets':holdout_mids,'featureContract':FS,'results':{}}
 for model_name in ['HGB','EXTRATREES']:
  out['results'][model_name]={}
  for wn,wmids in windows.items():
   tr=new[new.market_id.isin(wmids)].copy(); runs=[]
   for seed in [61001,62001,63001]:
    sw=class_weights(tr.transition)
    if model_name=='HGB':
     m=HistGradientBoostingClassifier(learning_rate=.06,max_iter=160,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed)
     m.fit(tr[FS],tr.transition,sample_weight=sw); Xh=holdout[FS]
    else:
     imp=SimpleImputer(strategy='median'); Xtr=imp.fit_transform(tr[FS]); Xh=imp.transform(holdout[FS])
     m=ExtraTreesClassifier(n_estimators=400,min_samples_leaf=25,max_features=.75,n_jobs=4,random_state=seed)
     m.fit(Xtr,tr.transition,sample_weight=sw)
    p=m.predict(Xh); cls=list(m.classes_); prob=m.predict_proba(Xh)[:,cls.index('SWITCH_OR_OPEN')]
    runs.append({'seed':seed,'holdout':score(holdout.transition,p,prob)})
   out['results'][model_name][wn]={'trainMarkets':len(wmids),'meanBA':float(np.mean([r['holdout']['ba'] for r in runs])),'meanAUC':float(np.mean([r['holdout']['auc'] for r in runs])),'minBA':float(min(r['holdout']['ba'] for r in runs)),'meanSwitchRecall':float(np.mean([r['holdout']['switchRecall'] for r in runs])),'meanContinueRecall':float(np.mean([r['holdout']['continueRecall'] for r in runs])),'runs':runs}
 (OUT/'rolling_window.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
