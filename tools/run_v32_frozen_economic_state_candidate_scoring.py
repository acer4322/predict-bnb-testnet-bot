"""V32 freeze economic-state representation and score previously un-forked V22 states.
Models are market-disjoint: candidates in market M are scored by fixed-L2 logistic heads
fit only on the original 29 exact-fork rows from the other two markets.
No candidate outcome exists/used at scoring time.
"""
from __future__ import annotations
import json,math,os
from pathlib import Path
import numpy as np
TRAIN=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V30_ROUTE_LOGIC_ENRICHED_DATASET_20260912.json')
CAND=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V22_ROUTE_SHADOW_SCORED_20260912.json')
MARKETS=(2022527,2022538,2022602);L2=1.0
FEATURES=['wall_phase','log1p_own_gross','floor_per_gross','best_per_gross','side_aligned_own_net','side_aligned_thesis','atomic_need_per_qty','repair_qty','fresh_qty','live_side_per_qty','live_opp_per_qty','deficit_per_qty','frontier_REPAIR','material_BIRTH','material_COMPOSITE']
def train_feat(r):
 f=dict(r['base_features']);f['log1p_own_gross']=math.log1p(max(0,float(f['own_gross'])));f['frontier_REPAIR']=float(r['frontier_kind']=='REPAIR');f['material_BIRTH']=float(r['materialization']=='BIRTH_ONLY');f['material_COMPOSITE']=float(r['materialization']=='COMPOSITE');return f
def cand_feat(r):
 q=max(float(r['qty']),1e-12);need=float(r['atomic_repair_need']);fresh=float(r['fresh_gap']);repair=float(r['repair_gap']);mat='BIRTH_ONLY' if need<=1e-12 else ('PAY_ONLY' if need>=q-1e-12 else 'COMPOSITE')
 return {'wall_phase':float(r['wall_phase']),'log1p_own_gross':math.log1p(max(0,float(r['own_gross']))),'floor_per_gross':float(r['floor'])/max(float(r['own_gross']),1.0),'best_per_gross':float(r['best'])/max(float(r['own_gross']),1.0),'side_aligned_own_net':(1.0 if r['side']=='UP' else -1.0)*float(r['own_net']),'side_aligned_thesis':(1.0 if r['side']=='UP' else -1.0)*(1.2*(2*float(r['mid'])-1)+.3*float(r['depth_imbalance'])),'atomic_need_per_qty':need/q,'repair_qty':repair/q,'fresh_qty':fresh/q,'live_side_per_qty':float(r['live_unfilled_side'])/q,'live_opp_per_qty':float(r['live_unfilled_opp'])/q,'deficit_per_qty':float(r['controller_deficit'])/q,'frontier_REPAIR':float(r['kind']=='REPAIR'),'material_BIRTH':float(mat=='BIRTH_ONLY'),'material_COMPOSITE':float(mat=='COMPOSITE')},mat
def sig(z):return 1/(1+np.exp(-np.clip(z,-30,30)))
def fit(X,y):
 mu=X.mean(0);sd=X.std(0);sd=np.where(sd<1e-9,1.,sd);A=np.c_[np.ones(len(X)),(X-mu)/sd];w=np.zeros(A.shape[1]);p0=min(.99,max(.01,float(y.mean())));w[0]=math.log(p0/(1-p0));reg=np.eye(A.shape[1])*L2;reg[0,0]=0
 for _ in range(60):
  p=sig(A@w);v=np.maximum(p*(1-p),1e-5);g=A.T@(p-y)+reg@w;H=(A.T*v)@A+reg
  try:step=np.linalg.solve(H,g)
  except np.linalg.LinAlgError:step=np.linalg.pinv(H)@g
  w-=step
  if np.max(abs(step))<1e-7:break
 return mu,sd,w
def pred(x,m):mu,sd,w=m;return float(sig(np.r_[1.,(x-mu)/sd]@w))
def main():
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);tr=json.loads(TRAIN.read_text(encoding='utf-8'))['rows'];ca=json.loads(CAND.read_text(encoding='utf-8'))['rows'];used={(r['market_id'],int(r['t']),r['side'],r['frontier_kind']) for r in tr};rows=[];models={}
 for m in MARKETS:
  fitrows=[r for r in tr if r['market_id']!=m];X=np.asarray([[train_feat(r)[k] for k in FEATURES] for r in fitrows],float);yf=np.asarray([r['outcomes']['floor_positive'] for r in fitrows],float);yb=np.asarray([r['outcomes']['best_positive'] for r in fitrows],float);mf=fit(X,yf);mb=fit(X,yb);models[str(m)]={'train_markets':[x for x in MARKETS if x!=m],'train_rows':len(fitrows),'floor_prevalence':float(yf.mean()),'best_prevalence':float(yb.mean())}
  for r in ca:
   if r['market_id']!=m:continue
   key=(m,int(r['t']),r['side'],r['kind'])
   if key in used:continue
   f,mat=cand_feat(r);x=np.asarray([f[k] for k in FEATURES],float);pf=pred(x,mf);pb=pred(x,mb);rows.append({'market_id':m,'t':int(r['t']),'side':r['side'],'frontier_kind':r['kind'],'materialization':mat,'qty':float(r['qty']),'passive_price':float(r['passive_price']),'active_ask':float(r['active_ask']),'premium':float(r['premium']),'p_fill_passive':float(r['p_fill_passive']),'wall_phase':float(r['wall_phase']),'pending_count':int(r['pending_count']),'atomic_repair_need':float(r['atomic_repair_need']),'repair_gap':float(r['repair_gap']),'fresh_gap':float(r['fresh_gap']),'floor':float(r['floor']),'best':float(r['best']),'p_floor_positive':pf,'p_best_positive':pb,'joint_min':min(pf,pb),'joint_product':pf*pb,'predicted_active':bool(pf>=.5 and pb>=.5),'features':f})
 res={'version':'V32_FROZEN_ECONOMIC_STATE_CANDIDATE_SCORING','features':FEATURES,'l2':L2,'models':models,'candidate_rows':len(rows),'used_exact_rows_excluded':len(used),'rows':rows,'boundary':['No candidate exact-fork outcome was read or available during scoring.','Each market is scored by heads fit only on the other two markets original exact-fork states.','Representation and L2 are frozen from V30B/V31; no refit after candidate outcomes.','Predictions are research-only and do not authorize runtime routing.']};(out/'result.json').write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps({'candidate_rows':len(rows),'by_market':{str(m):sum(r['market_id']==m for r in rows) for m in MARKETS},'pred_active':{str(m):sum(r['market_id']==m and r['predicted_active'] for r in rows) for m in MARKETS}},indent=2),flush=True)
if __name__=='__main__':main()
