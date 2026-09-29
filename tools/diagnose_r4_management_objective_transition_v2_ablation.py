from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=P/'r4_p0b_target_objective_topology_rows_v2.csv';OUT=P/'r4_management_objective_transition_v2_ablation.json'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
DELTA=['floor','upside','absNet','coverage','risk_deficit','current_commitment']
def mdl(seed):return HistGradientBoostingClassifier(learning_rate=.06,max_iter=140,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed)
def main():
 d=pd.read_csv(SRC).sort_values(['market_id','t','parent_id']).copy(); fam=d.groupby(['market_id','t'],sort=False).objective_family.agg(lambda x:tuple(sorted(set(map(str,x))))); prev=fam.groupby(level=0).shift(1).to_dict(); d['prevset']=[prev.get((m,t),np.nan) for m,t in zip(d.market_id,d.t)]; d=d[d.prevset.map(lambda x:isinstance(x,tuple))].copy(); d['y']=np.array([str(f) not in set(s) for f,s in zip(d.objective_family,d.prevset)],dtype=int); d['prev_pair']=[float('PAIR_BALANCE' in set(s)) for s in d.prevset];d['prev_shape']=[float('STATE_SHAPING' in set(s)) for s in d.prevset];d['prev_parallel']=[float(len(set(s))>1) for s in d.prevset]
 ts=d.groupby(['market_id','t'],sort=False)[DELTA].first(); pts=ts.groupby(level=0).shift(1)
 for c in DELTA:
  mp=pts[c].to_dict();d[f'{c}_dp']=pd.to_numeric(d[c],errors='coerce')-[mp.get((m,t),np.nan) for m,t in zip(d.market_id,d.t)]
 groups={'ECON_LIFECYCLE_BASE':BASE,'BASE_PLUS_PREV_FAMILY_SET':BASE+['prev_pair','prev_shape','prev_parallel'],'FULL_STRICT_EVENT':BASE+['prev_pair','prev_shape','prev_parallel']+[f'{c}_dp' for c in DELTA]}
 mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min());out={}
 for name,fs in groups.items():
  folds=[]
  for i,cut in enumerate((.60,.70,.80)):
   k=int(len(mids)*cut);v=int(len(mids)*.10);tr=d[d.market_id.isin(mids[:k])];te=d[d.market_id.isin(mids[k:k+v])]; ytr=tr.y.to_numpy();yte=te.y.to_numpy();n1=ytr.sum();n0=len(ytr)-n1;sw=np.where(ytr==1,len(ytr)/(2*n1),len(ytr)/(2*n0));m=mdl(9500+i).fit(tr[fs],ytr,sample_weight=sw);p=m.predict(te[fs]);pr=m.predict_proba(te[fs])[:,1];folds.append({'ba':float(balanced_accuracy_score(yte,p)),'auc':float(roc_auc_score(yte,pr)),'switchRecall':float(np.mean(p[yte==1]==1)),'continueRecall':float(np.mean(p[yte==0]==0))})
  out[name]={'folds':folds,'meanBA':float(np.mean([x['ba'] for x in folds])),'worstBA':float(np.min([x['ba'] for x in folds])),'meanAUC':float(np.mean([x['auc'] for x in folds])),'meanSwitchRecall':float(np.mean([x['switchRecall'] for x in folds]))}
 rep={'version':'R4_MANAGEMENT_OBJECTIVE_TRANSITION_V2_ABLATION','researchOnly':True,'promotionEligible':False,'strictPast':True,'label':'SWITCH_OR_OPEN iff current family absent from strictly-earlier timestamp family set','groups':out,'interpretation':'Tests whether transition learnability exists in non-tautological economic/lifecycle state before adding prior-family occupancy and strict event deltas.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
