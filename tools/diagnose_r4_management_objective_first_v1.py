from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_target_objective_topology_rows_v2.csv';OUT=P/'r4_management_objective_first_v1_diagnostic.json'
LABELS={'PAIR_BALANCE':'PAIR_BALANCE_WEAK','STATE_SHAPING':'STATE_SHAPING_DOMINANT'}
# Exclude weak/dominant unresolved/progress/current-family fields because those are partly constructed from the same objective assignment and make the teacher tautological.
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);cs=sorted(set(y));return {'balancedAccuracy':float(balanced_accuracy_score(y,p)),'counts':pd.Series(y).value_counts().to_dict(),'perClass':{c:{'n':int((y==c).sum()),'recall':float(np.mean(p[y==c]==c))} for c in cs}}
def model(seed):return HistGradientBoostingClassifier(learning_rate=.06,max_iter=140,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed)
def add_memory(d):
 d=d.sort_values(['market_id','t']).copy();g=d.groupby('market_id',sort=False);mem={}
 for lag in (1,2,3):mem[f'prev_family_{lag}']=g.objective_family.shift(lag).map({'PAIR_BALANCE':1.0,'STATE_SHAPING':0.0})
 for c in ['floor','upside','absNet','coverage','risk_deficit','current_commitment']:
  for lag in (1,2,3):mem[f'{c}_delta_parent{lag}']=pd.to_numeric(d[c],errors='coerce')-pd.to_numeric(g[c].shift(lag),errors='coerce')
 return pd.concat([d,pd.DataFrame(mem,index=d.index)],axis=1),list(mem)
def main():
 d=pd.read_csv(SRC);d=d[d.objective_family.isin(LABELS)].copy();d['label']=d.objective_family.map(LABELS);d,mem=add_memory(d)
 mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min());cuts=[.60,.70,.80];folds=[]
 for i,cut in enumerate(cuts):
  k=int(len(mids)*cut);v=int(len(mids)*.10);trm=set(mids[:k]);tem=set(mids[k:min(k+v,len(mids))]);tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)]
  row={'trainMarkets':len(trm),'testMarkets':len(tem)}
  for name,fs in [('BASE',BASE),('OBJECTIVE_MEMORY',BASE+mem)]:
   m=model(9000+i).fit(tr[fs],tr.label);row[name]=score(te.label,m.predict(te[fs]))
  folds.append(row)
 def mean(key):return float(np.mean([x[key]['balancedAccuracy'] for x in folds]))
 rep={'version':'R4_MANAGEMENT_OBJECTIVE_FIRST_V1_DIAGNOSTIC','researchOnly':True,'promotionEligible':False,'antiTautology':'weak/dominant unresolved/progress/current-family features excluded; objective memory is strict-past only','markets':len(mids),'rows':len(d),'features':{'base':BASE,'memory':mem},'folds':folds,'summary':{'baseMeanBA':mean('BASE'),'objectiveMemoryMeanBA':mean('OBJECTIVE_MEMORY'),'delta':mean('OBJECTIVE_MEMORY')-mean('BASE')},'next':'Build objective-first lifecycle target only if non-tautological chronological performance remains useful; fresh promotion requires a newly preregistered post-fresh100 cohort.'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'markets':rep['markets'],'rows':rep['rows'],'summary':rep['summary'],'folds':[{'BASE':x['BASE']['balancedAccuracy'],'MEM':x['OBJECTIVE_MEMORY']['balancedAccuracy']} for x in folds]},indent=2))
if __name__=='__main__':main()
