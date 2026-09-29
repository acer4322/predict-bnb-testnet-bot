from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_formation_belief_bridge_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_formation_belief_bridge_v1_rows.csv'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
FORM_RAW=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
ROUTED=PORT+RESP+MEM
RAW=ROUTED+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def met(y,p):
 return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if np.sum(y)>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def summ(blocks,key):
 x=[b[key] for b in blocks];return {'meanAuc':float(np.mean([z['auc'] for z in x])),'worstAuc':float(np.min([z['auc'] for z in x])),'stdAuc':float(np.std([z['auc'] for z in x])),'meanAp':float(np.mean([z['ap'] for z in x])),'worstAp':float(np.min([z['ap'] for z in x])),'meanLogLoss':float(np.mean([z['logLoss'] for z in x])),'worstLogLoss':float(np.max([z['logLoss'] for z in x]))}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan)
 need=list(dict.fromkeys(RAW+['market_id','t','build_now','continue_weak_5s']))
 d=d.dropna(subset=need).copy();d=d[(d.seconds_left>=60)&(d.seconds_left<=300)&(d.build_now==1)].sort_values(['market_id','t'])
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=18;rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3): sizes[i]+=1
 cur=initial;blocks=[];outs=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)].copy();te=d[d.market_id.isin(tem)].copy()
  # Formation head learns current BUILD/weak responsibility from semantic Formation context. It is separate from continuation target.
  # train it on all management rows in train markets, not only current-build rows, to avoid constant label.
  all_d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);all_d=all_d.dropna(subset=FORM_RAW+['market_id','build_now']).copy();alltr=all_d[all_d.market_id.isin(trm)]
  fm=hgb(1000+bi).fit(alltr[FORM_RAW],alltr.build_now.astype(int));tr['formation_build_confidence']=fm.predict_proba(tr[FORM_RAW])[:,1];te['formation_build_confidence']=fm.predict_proba(te[FORM_RAW])[:,1]
  m0=hgb(2000+bi).fit(tr[ROUTED],tr.continue_weak_5s);m1=hgb(3000+bi).fit(tr[ROUTED+['formation_build_confidence']],tr.continue_weak_5s);m2=hgb(4000+bi).fit(tr[RAW],tr.continue_weak_5s)
  te['p_routed']=m0.predict_proba(te[ROUTED])[:,1];te['p_bridge']=m1.predict_proba(te[ROUTED+['formation_build_confidence']])[:,1];te['p_raw']=m2.predict_proba(te[RAW])[:,1]
  b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'ROUTED':met(te.continue_weak_5s,te.p_routed),'FORMATION_BRIDGE':met(te.continue_weak_5s,te.p_bridge),'RAW_CONTEXT':met(te.continue_weak_5s,te.p_raw)};blocks.append(b);outs.append(te)
 o=pd.concat(outs,ignore_index=True);o.to_csv(ROWS,index=False)
 s={k:summ(blocks,k) for k in ['ROUTED','FORMATION_BRIDGE','RAW_CONTEXT']}
 s['bridgeVsRouted']={'meanAuc':s['FORMATION_BRIDGE']['meanAuc']-s['ROUTED']['meanAuc'],'worstAuc':s['FORMATION_BRIDGE']['worstAuc']-s['ROUTED']['worstAuc'],'meanAp':s['FORMATION_BRIDGE']['meanAp']-s['ROUTED']['meanAp'],'logLossImprovement':s['ROUTED']['meanLogLoss']-s['FORMATION_BRIDGE']['meanLogLoss']}
 s['remainingGapToRaw']={'meanAuc':s['RAW_CONTEXT']['meanAuc']-s['FORMATION_BRIDGE']['meanAuc'],'worstAuc':s['RAW_CONTEXT']['worstAuc']-s['FORMATION_BRIDGE']['worstAuc']}
 art={'version':'R4_MANAGEMENT_FORMATION_BELIEF_BRIDGE_V1','researchOnly':True,'actionAuthority':False,'question':'Can a semantic Formation BUILD confidence bridge raw Predict/strike context into M0 responsibility continuation without exposing raw market features to the manager?','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'phase':'60-300s current BUILD only'},'features':{'managerRouted':ROUTED,'formationHead':FORM_RAW,'bridge':'formation_build_confidence','rawDiagnostic':RAW},'summary':s,'blocks':blocks,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['Formation confidence is generated out-of-block chronologically from a separate current-BUILD head.','Manager continuation target is future weak responsibility, distinct from formation-head current-mode label.','Raw Predict/strike remains diagnostic-only for the manager.','No action authority, no threshold sweep, no winner/settlement.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':s,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
