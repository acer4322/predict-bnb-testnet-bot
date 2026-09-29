from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score
import joblib
sys.path.insert(0,str(Path('tools').resolve()))
import train_r3_formation_sequence_v2 as src

OUT=Path('data/research/r3_v0'); OUT.mkdir(parents=True,exist_ok=True)
BASE_FEATURES=list(src.FEATURES)
R21_FIELDS=['r21_ownership','r21_terminal','r21_situation','r21_remaining_frac','r21_target_revision','r21_state_conf']
# information-only situation encodings; no action output.
SITUATIONS={
 'NORMAL':(0,0,0,0.0,0,1.0),
 'LIVE_NO_FILL_DELAY':(1,0,1,1.0,0,0.9),
 'LIVE_PARTIAL':(1,0,2,0.5,0,0.95),
 'LATE_FILL':(1,0,3,0.25,0,0.95),
 'CANCEL_PENDING':(2,0,4,1.0,0,0.85),
 'TARGET_REVISION':(1,0,5,0.5,1,0.9),
 'TERMINAL_PARTIAL':(3,1,6,0.5,0,1.0),
 'SUBMIT_REJECT':(3,1,7,1.0,0,1.0),
 'DUPLICATE_EVENT':(0,0,8,0.0,0,0.7),
 'OUT_OF_ORDER_EVENT':(0,0,9,0.0,0,0.7),
}
OBS_ONLY=['NORMAL','LIVE_NO_FILL_DELAY','LIVE_PARTIAL','LATE_FILL','CANCEL_PENDING','TARGET_REVISION','DUPLICATE_EVENT','OUT_OF_ORDER_EVENT']

def rows_df():
 import sqlite3
 c=sqlite3.connect(src.DB)
 mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
 rows=[]
 for mid in mids: rows.extend(src.build_market(c,mid))
 c.close()
 return pd.DataFrame([{'market_id':mid,'ts_ms':t,**{k:v for k,v in zip(src.FEATURES,x)},'label':y} for mid,t,x,y in rows])

def add_r21(d,name):
 vals=SITUATIONS[name]
 x=d.copy()
 for c,v in zip(R21_FIELDS,vals): x[c]=v
 return x

def train_model(train,features):
 m=HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=15,learning_rate=.07,l2_regularization=2.0,random_state=42)
 m.fit(train[features].replace([np.inf,-np.inf],np.nan).fillna(0),train.label)
 return m

def score(m,d,features):
 y=d.label.to_numpy(); p=m.predict(d[features].replace([np.inf,-np.inf],np.nan).fillna(0))
 return {'n':len(d),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p))}

def main():
 df=rows_df()
 mids=list(dict.fromkeys(df.market_id.tolist())); n=len(mids); a=int(n*.6); b=int(n*.8)
 tr=df[df.market_id.isin(mids[:a])].copy(); va=df[df.market_id.isin(mids[a:b])].copy(); te=df[df.market_id.isin(mids[b:])].copy()
 # A: no R2.1
 A=train_model(tr,BASE_FEATURES)
 # B: naive attachment: normal-only R2.1 during training
 Btr=add_r21(tr,'NORMAL'); B=train_model(Btr,BASE_FEATURES+R21_FIELDS)
 # C: cooperation curriculum: observation-only situations must preserve Target Formation label.
 parts=[]
 rng=np.random.RandomState(42)
 # keep original normal plus one sampled observer context per training row to keep size bounded
 parts.append(add_r21(tr,'NORMAL'))
 names=np.array(OBS_ONLY[1:])
 sampled=names[rng.randint(0,len(names),size=len(tr))]
 for nm in OBS_ONLY[1:]:
  mask=sampled==nm
  if mask.any(): parts.append(add_r21(tr.loc[mask],nm))
 Ctr=pd.concat(parts,ignore_index=True)
 C=train_model(Ctr,BASE_FEATURES+R21_FIELDS)
 rep={'version':'R3_R21_COOPERATION_AB_V0','markets':n,'rows':len(df),'arms':{},'authority':{'r21ActionAuthority':False,'r3DecisionOwner':True}}
 rep['arms']['A_normal']=score(A,te,BASE_FEATURES)
 rep['arms']['B_normal']=score(B,add_r21(te,'NORMAL'),BASE_FEATURES+R21_FIELDS)
 rep['arms']['C_normal']=score(C,add_r21(te,'NORMAL'),BASE_FEATURES+R21_FIELDS)
 pert={}
 for nm in OBS_ONLY[1:]:
  d=add_r21(te,nm)
  pert[nm]={
   'B':score(B,d,BASE_FEATURES+R21_FIELDS),
   'C':score(C,d,BASE_FEATURES+R21_FIELDS),
  }
 rep['perturbations']=pert
 # prediction invariance relative to normal prediction
 Xn=add_r21(te,'NORMAL')
 pb=B.predict(Xn[BASE_FEATURES+R21_FIELDS].fillna(0)); pc=C.predict(Xn[BASE_FEATURES+R21_FIELDS].fillna(0))
 inv={}
 for nm in OBS_ONLY[1:]:
  d=add_r21(te,nm)
  qb=B.predict(d[BASE_FEATURES+R21_FIELDS].fillna(0)); qc=C.predict(d[BASE_FEATURES+R21_FIELDS].fillna(0))
  inv[nm]={'B_sameAsNormal':float(np.mean(qb==pb)),'C_sameAsNormal':float(np.mean(qc==pc))}
 rep['observerInvariance']=inv
 joblib.dump({'model':B,'features':BASE_FEATURES+R21_FIELDS},OUT/'r3_r21_naive_attachment_hgb_v0.joblib')
 joblib.dump({'model':C,'features':BASE_FEATURES+R21_FIELDS},OUT/'r3_r21_cooperation_hgb_v0.joblib')
 (OUT/'r3_r21_cooperation_ab_v0_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
