from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
OLD=ST/'r4_p0b_target_objective_topology_rows_v2.csv'
DEV=ST/'r4_management_postfresh80_objective_transition_rows_v1.csv'
PROMO=ST/'r4_management_promotion80_objective_transition_rows_v1.csv'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
FS=BASE+['prev_pb_set','prev_ss_set','risk_deficit_delta_strict1']
SEED=71001

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
     z['risk_deficit_delta_strict1']=float(pd.to_numeric(r.risk_deficit,errors='coerce')-last_num)
     rows.append(z)
   prev_f=set(map(str,h.objective_family)); last_num=float(pd.to_numeric(h.risk_deficit,errors='coerce').mean())
 return pd.DataFrame(rows)

def balanced_weights(y):
 s=pd.Series(y); c=s.value_counts(); return np.asarray([len(s)/(len(c)*c[v]) for v in s],float)

def score(y,p,prob):
 yy=np.asarray(y); sw=yy=='SWITCH_OR_OPEN'
 return {'n':int(len(yy)),'switchRate':float(sw.mean()),'balancedAccuracy':float(balanced_accuracy_score(yy,p)),'auc':float(roc_auc_score(sw.astype(int),prob)),'ap':float(average_precision_score(sw.astype(int),prob)),'switchRecall':float(np.mean(np.asarray(p)[sw]=='SWITCH_OR_OPEN')),'continueRecall':float(np.mean(np.asarray(p)[~sw]=='CONTINUE'))}

def model_score(m,z,X=None):
 xx=z[FS] if X is None else X; p=m.predict(xx); cls=list(m.classes_); prob=m.predict_proba(xx)[:,cls.index('SWITCH_OR_OPEN')]; return score(z.transition,p,prob)

def main():
 old=build(OLD); dev=build(DEV); promo=build(PROMO)
 mids=[int(x) for x in sorted(promo.market_id.unique(),key=lambda m:promo.loc[promo.market_id==m,'t'].min())]
 train=pd.concat([old,dev],ignore_index=True)
 recency=np.concatenate([np.ones(len(old)),np.full(len(dev),8.0)])
 sw=balanced_weights(train.transition)*recency
 hgb=HistGradientBoostingClassifier(learning_rate=.06,max_iter=160,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=SEED)
 hgb.fit(train[FS],train.transition,sample_weight=sw)
 primary={'overall':model_score(hgb,promo),'blocks':[]}
 for i in range(0,80,20):
  z=promo[promo.market_id.isin(mids[i:i+20])]; primary['blocks'].append({'block':i//20+1,'marketIds':mids[i:i+20],**model_score(hgb,z)})
 # Frozen diagnostic monitor only: ExtraTrees trained on full development80 only.
 imp=SimpleImputer(strategy='median'); Xd=imp.fit_transform(dev[FS]); Xp=imp.transform(promo[FS])
 et=ExtraTreesClassifier(n_estimators=400,min_samples_leaf=25,max_features=.75,n_jobs=4,random_state=SEED)
 et.fit(Xd,dev.transition,sample_weight=balanced_weights(dev.transition))
 diag={'overall':model_score(et,promo,Xp),'blocks':[]}
 for i in range(0,80,20):
  mask=promo.market_id.isin(mids[i:i+20]).to_numpy(); z=promo.loc[mask]; diag['blocks'].append({'block':i//20+1,'marketIds':mids[i:i+20],**model_score(et,z,Xp[mask])})
 min_block=min(b['balancedAccuracy'] for b in primary['blocks'])
 gate={'overallBalancedAccuracyMin':0.60,'overallAucMin':0.63,'minimum20MarketBlockBalancedAccuracyMin':0.55}
 passed=primary['overall']['balancedAccuracy']>=gate['overallBalancedAccuracyMin'] and primary['overall']['auc']>=gate['overallAucMin'] and min_block>=gate['minimum20MarketBlockBalancedAccuracyMin']
 out={'version':'R4_MANAGEMENT_OBJECTIVE_FIRST_STUDENT_V1_PROMOTION_SCORE','researchOnly':True,'oneShot':True,'promotionSetConsumed':True,'seed':SEED,'trainMarketsOld':int(old.market_id.nunique()),'trainMarketsDevelopment':int(dev.market_id.nunique()),'promotionMarkets':int(promo.market_id.nunique()),'promotionRows':int(len(promo)),'featureContract':FS,'primaryModel':'HGB old299 + development80 weighted 1:8','primary':primary,'robustnessMonitorExtraTreesDev80Only':diag,'promotionGate':gate,'minimumPrimaryBlockBA':float(min_block),'promotionPassed':bool(passed),'noRetuningAfterThisScore':True}
 (OUT/'promotion_score.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
