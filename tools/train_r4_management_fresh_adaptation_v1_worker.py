from __future__ import annotations
import os,json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
ROOT=Path.cwd()
OLD=ROOT/'.lan_worker_v1/staging/r4_management_old699_compact.csv.gz'
FRESH=ROOT/'.lan_worker_v1/staging/r4_management_fresh_adaptation_v1_rows.csv'
OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
LABEL='option_mode_v2'
EXCLUDE={'market_id','market_end_ms','checkpoint_ms','split',LABEL,'gate_act','taker_next3s_raw'}

def domain_class_weights(y:pd.Series)->np.ndarray:
 y=y.astype(str); counts=y.value_counts(); k=len(counts); n=len(y)
 return y.map({c:n/(k*v) for c,v in counts.items()}).astype(float).to_numpy()

def main():
 old=pd.read_csv(OLD); fresh=pd.read_csv(FRESH); fresh=fresh[fresh['split'].eq('ADAPT')].copy()
 feats=[c for c in fresh.columns if c not in EXCLUDE]
 old=old.dropna(subset=[LABEL]);fresh=fresh.dropna(subset=[LABEL])
 old=old[[LABEL]+feats].copy(); fresh=fresh[[LABEL]+feats].copy()
 wo=domain_class_weights(old[LABEL]); wf=domain_class_weights(fresh[LABEL])
 wo=wo*(0.5/wo.sum()); wf=wf*(0.5/wf.sum()); X=pd.concat([old[feats],fresh[feats]],ignore_index=True); y=pd.concat([old[LABEL],fresh[LABEL]],ignore_index=True); w=np.concatenate([wo,wf])
 m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=20260829).fit(X,y,sample_weight=w)
 art={'version':'R4_MANAGEMENT_FRESH_ADAPTATION_V1','researchOnly':True,'actionAuthority':False,'features':feats,'model':m,'training':{'oldRows':len(old),'freshRows':len(fresh),'oldMarkets':699,'freshMarkets':int(pd.read_csv(FRESH,usecols=['market_id','split']).query("split=='ADAPT'").market_id.nunique()),'weighting':'equal domain total; equal class total within domain'}}
 joblib.dump(art,OUT/'r4_management_fresh_adaptation_v1.joblib')
 rep={'version':art['version'],'oldRows':len(old),'freshRows':len(fresh),'features':len(feats),'classes':[str(x) for x in m.classes_],'weightSums':{'old':float(wo.sum()),'fresh':float(wf.sum())},'status':'MODEL_FROZEN_BEFORE_UNTOUCHED_VALIDATION'}
 (OUT/'train_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
