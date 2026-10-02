from __future__ import annotations
import os,json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path.cwd();F=ROOT/'.lan_worker_v1/staging/r4_management_fresh_adaptation_v1_rows.csv';OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR']);LABEL='option_mode_v2'

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);o={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{}}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;o['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return o

def main():
 d=pd.read_csv(F);tr=d[d.split.eq('ADAPT')].copy();te=d[d.split.eq('VALIDATION')].copy();features=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','split',LABEL}]
 features=[c for c in features if pd.to_numeric(tr[c],errors='coerce').dropna().nunique()>=2]
 cnt=tr[LABEL].value_counts();w=tr[LABEL].map({c:len(tr)/(len(cnt)*n) for c,n in cnt.items()}).astype(float)
 m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=20260829).fit(tr[features],tr[LABEL],sample_weight=w)
 rep={'version':'R4_MANAGEMENT_FRESH_ONLY_V1_DIAGNOSTIC','researchOnly':True,'promotionEligible':False,'trainMarkets':int(tr.market_id.nunique()),'testMarkets':int(te.market_id.nunique()),'trainRows':len(tr),'testRows':len(te),'score':score(te[LABEL],m.predict(te[features]))};(OUT/'fresh_only_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
