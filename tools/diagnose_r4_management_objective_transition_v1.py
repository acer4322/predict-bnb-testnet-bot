from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=P/'r4_p0b_target_objective_topology_rows_v2.csv';OUT=P/'r4_management_objective_transition_v1_diagnostic.json'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);return {'balancedAccuracy':float(balanced_accuracy_score(y,p)),'counts':pd.Series(y).value_counts().to_dict(),'perClass':{c:float(np.mean(p[y==c]==c)) for c in sorted(set(y))}}
def model(seed):return HistGradientBoostingClassifier(learning_rate=.06,max_iter=140,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed)
def main():
 d=pd.read_csv(SRC).sort_values(['market_id','t']).copy();g=d.groupby('market_id',sort=False);d['prev_family']=g.objective_family.shift(1);d=d[d.prev_family.notna()].copy();d['transition']=np.where(d.objective_family.eq(d.prev_family),'CONTINUE','SWITCH');d['prev_pair_balance']=d.prev_family.eq('PAIR_BALANCE').astype(float)
 for c in ['floor','upside','absNet','coverage','risk_deficit','current_commitment']:
  d[f'{c}_delta_parent1']=pd.to_numeric(d[c],errors='coerce')-pd.to_numeric(g[c].shift(1),errors='coerce')
 fs=BASE+['prev_pair_balance']+[f'{c}_delta_parent1' for c in ['floor','upside','absNet','coverage','risk_deficit','current_commitment']]
 mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min());folds=[]
 for i,cut in enumerate((.60,.70,.80)):
  k=int(len(mids)*cut);v=int(len(mids)*.10);tr=d[d.market_id.isin(mids[:k])];te=d[d.market_id.isin(mids[k:k+v])];m=model(9100+i).fit(tr[fs],tr.transition);pred=m.predict(te[fs]);base=np.repeat('CONTINUE',len(te));folds.append({'trainMarkets':tr.market_id.nunique(),'testMarkets':te.market_id.nunique(),'alwaysContinue':score(te.transition,base),'model':score(te.transition,pred)})
 rep={'version':'R4_MANAGEMENT_OBJECTIVE_TRANSITION_V1_DIAGNOSTIC','researchOnly':True,'promotionEligible':False,'target':'CONTINUE current objective vs SWITCH objective family at next Target responsibility','strictPast':True,'features':fs,'folds':folds,'summary':{'modelMeanBA':float(np.mean([x['model']['balancedAccuracy'] for x in folds])),'alwaysContinueMeanBA':float(np.mean([x['alwaysContinue']['balancedAccuracy'] for x in folds]))},'interpretation':'Objective family persistence is useful memory only if transition itself is predictable above the trivial persistence policy.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'summary':rep['summary'],'folds':[x['model'] for x in folds]},indent=2))
if __name__=='__main__':main()
