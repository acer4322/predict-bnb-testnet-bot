from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_target_objective_topology_future_rows_v2.csv'
GEOM=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross']
MEM=['seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s']
OWNER=['weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s','current_commitment']
BASE=GEOM+MEM+OWNER

def clf(seed): return HistGradientBoostingClassifier(max_iter=120,max_depth=4,learning_rate=.06,l2_regularization=1.,random_state=seed)
def auc(y,p):
 return float(roc_auc_score(y,p)) if len(set(map(int,y)))>1 else None
def x(df,cols): return df[cols].fillna(0).to_numpy(float)
def purpose_oof(train,seed):
 mids=sorted(train.market_id.unique(), key=lambda m: train.loc[train.market_id==m,'t'].min()); chunks=np.array_split(mids,4); pred=np.full(len(train),np.nan)
 for k,ch in enumerate(chunks):
  te=train.market_id.isin(ch).to_numpy(); tr=~te
  if tr.sum()<100 or te.sum()==0: continue
  y=(train.objective_family=='STATE_SHAPING').astype(int).to_numpy(); m=clf(seed+k).fit(x(train.loc[tr],BASE),y[tr]);pred[te]=m.predict_proba(x(train.loc[te],BASE))[:,1]
 miss=np.isnan(pred)
 if miss.any():
  y=(train.objective_family=='STATE_SHAPING').astype(int).to_numpy();m=clf(seed+20).fit(x(train.loc[~miss],BASE),y[~miss]);pred[miss]=m.predict_proba(x(train.loc[miss],BASE))[:,1]
 return pred

def main():
 d=pd.read_csv(SRC).sort_values(['t','market_id']).reset_index(drop=True); mids=sorted(d.market_id.unique(), key=lambda m:d.loc[d.market_id==m,'t'].min()); blocks=np.array_split(mids,5); rows=[]
 for k in range(1,5):
  train=d[d.market_id.isin(np.concatenate(blocks[:k]))].copy(); test=d[d.market_id.isin(blocks[k])].copy()
  if len(train)<1000 or len(test)<100: continue
  yp=(train.objective_family=='STATE_SHAPING').astype(int).to_numpy(); ypt=(test.objective_family=='STATE_SHAPING').astype(int).to_numpy(); pm=clf(100+k).fit(x(train,BASE),yp); pt=pm.predict_proba(x(test,BASE))[:,1]; purpose_auc=auc(ypt,pt)
  oof=purpose_oof(train,200+k); y=train.future_different_objective_5s.astype(int).to_numpy(); yt=test.future_different_objective_5s.astype(int).to_numpy()
  base=clf(300+k).fit(x(train,BASE),y); pb=base.predict_proba(x(test,BASE))[:,1]
  tr_or=np.column_stack([x(train,BASE),(train.objective_family=='STATE_SHAPING').astype(float).to_numpy()]);te_or=np.column_stack([x(test,BASE),(test.objective_family=='STATE_SHAPING').astype(float).to_numpy()]); om=clf(400+k).fit(tr_or,y);po=om.predict_proba(te_or)[:,1]
  tr_cf=np.column_stack([x(train,BASE),oof]);te_cf=np.column_stack([x(test,BASE),pt]); cm=clf(500+k).fit(tr_cf,y);pc=cm.predict_proba(te_cf)[:,1]
  rows.append({'block':k,'trainRows':len(train),'testRows':len(test),'testMarkets':test.market_id.nunique(),'purposeTeacherAuc':purpose_auc,'baseFutureAuc':auc(yt,pb),'oraclePurposeFutureAuc':auc(yt,po),'crossfitPurposeFutureAuc':auc(yt,pc)})
 def mean(key):return float(np.mean([r[key] for r in rows if r[key] is not None]))
 def worst(key):return float(np.min([r[key] for r in rows if r[key] is not None]))
 s={'purposeTeacherMeanAuc':mean('purposeTeacherAuc'),'purposeTeacherWorstAuc':worst('purposeTeacherAuc'),'baseMeanAuc':mean('baseFutureAuc'),'baseWorstAuc':worst('baseFutureAuc'),'oraclePurposeMeanAuc':mean('oraclePurposeFutureAuc'),'crossfitPurposeMeanAuc':mean('crossfitPurposeFutureAuc'),'crossfitPurposeWorstAuc':worst('crossfitPurposeFutureAuc')};s['crossfitDeltaMean']=s['crossfitPurposeMeanAuc']-s['baseMeanAuc'];s['crossfitWorstDelta']=s['crossfitPurposeWorstAuc']-s['baseWorstAuc'];s['oracleDeltaMean']=s['oraclePurposeMeanAuc']-s['baseMeanAuc'];checks={'purposeTeacher':s['purposeTeacherMeanAuc']>=.70,'crossfitMeanDelta':s['crossfitDeltaMean']>=.02,'worstNotWorse':s['crossfitWorstDelta']>=-.01};rep={'version':'R4_P0B_TARGET_OBJECTIVE_PURPOSE_BELIEF_V1','researchOnly':True,'rows':rows,'summary':s,'checks':checks,'gatePass':all(checks.values()),'interpretation':'Forward chronological purpose-belief portability audit. Runtime purpose feature is inferred from geometry/memory/owner only; mode/current family flags are excluded.'};(P/'r4_p0b_target_objective_purpose_belief_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
