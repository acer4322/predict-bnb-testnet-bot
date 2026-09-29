from __future__ import annotations
import json, math, warnings
from pathlib import Path
from typing import Any
import joblib, numpy as np, pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'open_order_fill_lifecycle_v0_dataset.csv'
OPEN_ART=D/'open_order_fill_lifecycle_v0.joblib'
OUT=D/'r2_fill_quality_teacher_v0_report.json'
ART=D/'r2_fill_quality_teacher_v0.joblib'
DATA=D/'r2_fill_quality_teacher_v0_dataset.csv'
SEED=20260822

# Strict-past state only. No Target, winner, settlement, future-fill labels, or PnL as features.
EXTRA=['seconds_left_proxy','microprice_bias_ticks','book_pressure_proxy']

def metric(y,p):
 y=np.asarray(y,dtype=int); p=np.clip(np.asarray(p,dtype=float),1e-7,1-1e-7)
 return {'n':len(y),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 warnings.filterwarnings('ignore')
 base=pd.read_csv(SRC,low_memory=False)
 bundle=joblib.load(OPEN_ART); feats=list(bundle['features'])
 # Quality label uses already-existing HFT future fill physics: a fill opportunity is considered toxic when
 # the order is likely to fill soon while public depletion/quote state indicates one-sided selection pressure.
 # V0 intentionally avoids winner/PnL and is only a coarse teacher; next version will use explicit post-fill markout counterfactuals.
 d=base.copy()
 d['seconds_left_proxy']=np.nan
 # normalized local pressure proxies from current quote/depletion, all observable at checkpoint
 d['microprice_bias_ticks']=(pd.to_numeric(d['current_ask'],errors='coerce')-pd.to_numeric(d['current_bid'],errors='coerce'))
 d['book_pressure_proxy']=pd.to_numeric(d['public_depletion_ratio'],errors='coerce')*pd.to_numeric(d['remaining_ratio'],errors='coerce')
 # physics-only weak label: imminent fill plus strong depletion while quote is at/inside best is SELECTIVE_RISK.
 # This is deliberately preregistered and not PnL tuned.
 fill3=pd.to_numeric(d['label_fill_3s'],errors='coerce').fillna(0).astype(int)
 dep=pd.to_numeric(d['public_depletion_ratio'],errors='coerce')
 off=pd.to_numeric(d['quote_offset_ticks'],errors='coerce')
 d['label_selective_risk']=((fill3==1)&(dep>=0.50)&(off<=0.5)).astype(int)
 d=d[pd.to_numeric(d['censored_3s'],errors='coerce').fillna(1).astype(int)==0].copy()
 d.to_csv(DATA,index=False)
 ids=(d.groupby('market_id')['checkpoint_ms'].max().sort_values().index.astype(int).tolist())
 a=int(len(ids)*.70); b=int(len(ids)*.85); splits={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}
 features=feats+EXTRA
 tr=d[d.market_id.astype(int).isin(splits['train'])]
 m=ExplainableBoostingClassifier(feature_names=features,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=800,early_stopping_rounds=50,min_samples_leaf=16,n_jobs=-2,random_state=SEED)
 m.fit(tr[features].apply(pd.to_numeric,errors='coerce'),tr.label_selective_risk.astype(int))
 mets={}
 for k,s in splits.items():
  x=d[d.market_id.astype(int).isin(s)]; p=m.predict_proba(x[features].apply(pd.to_numeric,errors='coerce'))[:,1]
  mets[k]=metric(x.label_selective_risk,p)
 imp=list(m.term_importances()); names=list(m.term_names_); top=sorted([{'term':str(names[i]),'importance':float(imp[i])} for i in range(len(imp))],key=lambda z:z['importance'],reverse=True)[:20]
 joblib.dump({'version':'R2_FILL_QUALITY_TEACHER_V0','model':m,'features':features,'trainingMarkets':sorted(splits['train']),'runtimeTargetDataAllowed':False,'dreamFillAllowed':False},ART)
 rep={'version':'R2_FILL_QUALITY_TEACHER_V0','researchOnly':True,'liveTradingChanges':False,'status':'COARSE_PHYSICS_TEACHER_NOT_ACTION_AUTHORITY','source':str(SRC),'rows':len(d),'markets':len(ids),'splitMarkets':{k:len(v) for k,v in splits.items()},'label':'SELECTIVE_RISK = HFT any-fill within 3s AND public depletion ratio >=0.50 AND quote offset <=0.5 ticks; fixed coarse physics label, not PnL tuned','metrics':mets,'topTerms':top,'artifact':str(ART),'guardrails':['No dream fill.','No Target/winner/settlement/PnL runtime feature.','Chronological market split.','No threshold sweep.','V0 is diagnostic teacher only; it may not override R2 actions.','Explicit post-fill markout counterfactual teacher is required before promotion.']}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'report':str(OUT),'rows':len(d),'markets':len(ids),'metrics':mets,'topTerms':top[:8]},ensure_ascii=False))
if __name__=='__main__': main()
