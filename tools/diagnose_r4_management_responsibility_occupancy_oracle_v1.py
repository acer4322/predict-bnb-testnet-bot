from __future__ import annotations
import json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';ROWS=P/'r4_management_fresh_adaptation_v1_rows.csv';BOOK=ROOT/'data/wallet_maker_book_inference.db';LABEL='option_mode_v2'

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);o={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{}}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;o['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return o

def add_occ(d):
 bc=sqlite3.connect(BOOK);blocks=[]
 for mid,x in d.groupby('market_id',sort=False):
  ps=[dict(zip(['start','end','expected','placed'],r)) for r in bc.execute("select placement_first_ms,last_target_ms,expected_parent_shares,placement_allocated_shares from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and last_target_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by placement_first_ms",(int(mid),))]
  vals=[]
  for r in x.itertuples(index=False):
   t=int(r.checkpoint_ms);a=[p for p in ps if int(p['start'])<=t<int(p['end'])]
   if a:
    ages=[(t-int(p['start']))/1000 for p in a];vals.append((len(a),float(sum(float(p['expected']) for p in a)),float(sum(float(p['placed']) for p in a)),float(max(ages)),float(min(ages))))
   else: vals.append((0,0.,0.,0.,0.))
  z=x.copy();cols=['oracle_active_responsibilities','oracle_active_expected_shares','oracle_active_placed_shares','oracle_oldest_active_age_s','oracle_newest_active_age_s']
  for i,c in enumerate(cols):z[c]=[v[i] for v in vals]
  blocks.append(z)
 bc.close();return pd.concat(blocks,ignore_index=True),cols

def fit_eval(tr,te,features):
 valid=[c for c in features if pd.to_numeric(tr[c],errors='coerce').dropna().nunique()>=2];cnt=tr[LABEL].value_counts();w=tr[LABEL].map({c:len(tr)/(len(cnt)*n) for c,n in cnt.items()}).astype(float);m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=20260829).fit(tr[valid],tr[LABEL],sample_weight=w);return score(te[LABEL],m.predict(te[valid])),len(valid)

def main():
 d=pd.read_csv(ROWS);d,occ=add_occ(d);base=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','split',LABEL} and c not in occ];tr=d[d.split.eq('ADAPT')];te=d[d.split.eq('VALIDATION')];s0,n0=fit_eval(tr,te,base);s1,n1=fit_eval(tr,te,base+occ);rep={'version':'R4_MANAGEMENT_RESPONSIBILITY_OCCUPANCY_ORACLE_V1','researchOnly':True,'promotionEligible':False,'teacherDerivedOracleContext':True,'runtimeAnalogue':'own Responsibility Ledger knows live roots/commitment exactly','trainMarkets':int(tr.market_id.nunique()),'testMarkets':int(te.market_id.nunique()),'baseline':s0,'plusOracleOccupancy':s1,'deltaBA':s1['balancedAccuracy']-s0['balancedAccuracy'],'featuresAdded':occ,'featureCounts':{'baseline':n0,'withOccupancy':n1}};(P/'r4_management_responsibility_occupancy_oracle_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
