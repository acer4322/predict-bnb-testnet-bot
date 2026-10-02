from __future__ import annotations
import bisect,json,sqlite3
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

def classify(pre,side,shares):
 post=pre+(shares if side=='UP' else -shares)
 if abs(pre)<=1.0:return 'BUILD_ADD'
 return 'REPAIR' if abs(post)<abs(pre)-1e-9 else 'BUILD_ADD'

def add_purpose(d):
 bc=sqlite3.connect(BOOK);blocks=[]
 for mid,x in d.groupby('market_id',sort=False):
  x=x.sort_values('checkpoint_ms').copy();times=x.checkpoint_ms.astype('int64').tolist();nets=pd.to_numeric(x.maker_net,errors='coerce').fillna(0.).tolist();events=[]
  for t,side,sh in bc.execute("select placement_first_ms,target_side,expected_parent_shares from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by placement_first_ms",(int(mid),)):
   t=int(t);j=bisect.bisect_left(times,t)-1;pre=float(nets[j]) if j>=0 else 0.;events.append((t,classify(pre,str(side),float(sh))))
  et=[e[0] for e in events];rows=[]
  for r in x.itertuples(index=False):
   t=int(r.checkpoint_ms);j=bisect.bisect_right(et,t)-1;last=events[j][1] if j>=0 else 'NONE';age=(t-events[j][0])/1000 if j>=0 else 999.;lo15=bisect.bisect_left(et,t-15000);lo60=bisect.bisect_left(et,t-60000);w15=events[lo15:j+1] if j>=0 else [];w60=events[lo60:j+1] if j>=0 else []
   rows.append((1 if last=='REPAIR' else 0,1 if last=='BUILD_ADD' else 0,age,sum(k=='REPAIR' for _,k in w15),sum(k=='BUILD_ADD' for _,k in w15),sum(k=='REPAIR' for _,k in w60),sum(k=='BUILD_ADD' for _,k in w60)))
  cols=['last_obj_repair','last_obj_build_add','last_obj_age_s','repair_count_15s','build_add_count_15s','repair_count_60s','build_add_count_60s']
  for i,c in enumerate(cols):x[c]=[z[i] for z in rows]
  blocks.append(x)
 bc.close();return pd.concat(blocks,ignore_index=True),cols

def fit_eval(tr,te,features):
 valid=[c for c in features if pd.to_numeric(tr[c],errors='coerce').dropna().nunique()>=2];cnt=tr[LABEL].value_counts();w=tr[LABEL].map({c:len(tr)/(len(cnt)*n) for c,n in cnt.items()}).astype(float);m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=20260829).fit(tr[valid],tr[LABEL],sample_weight=w);return score(te[LABEL],m.predict(te[valid])),len(valid)

def main():
 d=pd.read_csv(ROWS);d,pf=add_purpose(d);base=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','split',LABEL} and c not in pf];tr=d[d.split.eq('ADAPT')];te=d[d.split.eq('VALIDATION')];s0,n0=fit_eval(tr,te,base);s1,n1=fit_eval(tr,te,base+pf);rep={'version':'R4_MANAGEMENT_OBJECTIVE_PURPOSE_MEMORY_V1','researchOnly':True,'promotionEligible':False,'strictPastTeacherActionMemory':True,'runtimeAnalogue':'own Objective/Responsibility Ledger current purpose and recent objective history','baseline':s0,'plusPurposeMemory':s1,'deltaBA':s1['balancedAccuracy']-s0['balancedAccuracy'],'featuresAdded':pf,'featureCounts':{'baseline':n0,'withPurpose':n1}};(P/'r4_management_objective_purpose_memory_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
