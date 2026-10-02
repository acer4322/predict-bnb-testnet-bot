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

def add_size_context(d):
 bc=sqlite3.connect(BOOK);out=[]
 for mid,x in d.groupby('market_id',sort=False):
  ps=[(int(t),float(s)) for t,s in bc.execute("select placement_first_ms,expected_parent_shares from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by placement_first_ms",(int(mid),))]
  times=[z[0] for z in ps];sizes=[z[1] for z in ps]
  for r in x.itertuples(index=False):
   t=int(r.checkpoint_ms);j=bisect.bisect_right(times,t)-1
   last=sizes[j] if j>=0 else np.nan;age=(t-times[j])/1000 if j>=0 else 999.;vals30=[sizes[k] for k in range(max(0,bisect.bisect_left(times,t-30000)),j+1)] if j>=0 else [];vals60=[sizes[k] for k in range(max(0,bisect.bisect_left(times,t-60000)),j+1)] if j>=0 else []
   out.append((last,age,float(np.median(vals30)) if vals30 else np.nan,float(np.median(vals60)) if vals60 else np.nan,len(vals30),len(vals60)))
 bc.close();z=d.copy();cols=['last_parent_size','last_parent_size_age_s','median_parent_size_30s','median_parent_size_60s','parent_count_30s','parent_count_60s']
 for i,c in enumerate(cols):z[c]=[r[i] for r in out]
 return z,cols

def fit_eval(tr,te,features):
 valid=[c for c in features if pd.to_numeric(tr[c],errors='coerce').dropna().nunique()>=2];cnt=tr[LABEL].value_counts();w=tr[LABEL].map({c:len(tr)/(len(cnt)*n) for c,n in cnt.items()}).astype(float);m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=20260829).fit(tr[valid],tr[LABEL],sample_weight=w);return score(te[LABEL],m.predict(te[valid])),len(valid)

def main():
 d=pd.read_csv(ROWS);d,sz=add_size_context(d);base=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','split',LABEL} and c not in sz];tr=d[d.split.eq('ADAPT')];te=d[d.split.eq('VALIDATION')];s0,n0=fit_eval(tr,te,base);s1,n1=fit_eval(tr,te,base+sz);rep={'version':'R4_MANAGEMENT_SIZE_CONTEXT_V1_DIAGNOSTIC','researchOnly':True,'promotionEligible':False,'trainMarkets':int(tr.market_id.nunique()),'testMarkets':int(te.market_id.nunique()),'baseline':s0,'plusStrictPastSizeContext':s1,'deltaBA':s1['balancedAccuracy']-s0['balancedAccuracy'],'featuresAdded':sz,'featureCounts':{'baseline':n0,'size':n1}};(P/'r4_management_size_context_v1_diagnostic.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
