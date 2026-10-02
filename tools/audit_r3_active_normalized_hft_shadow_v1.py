from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'
ST=joblib.load(D/'r3_active_our_state_student_v2_pilot600.joblib'); NM=joblib.load(D/'r3_execution_actionable_normalizer_hgb_v2_runtime.joblib')
F=ST['features']; nf=NM['features']; nm=NM['model']
arts=sorted(D.glob('r3_hft_context_control_*_market*_v1.json'))[:]
vals=[]
def sf(r,k):
 p=r.get('portfolio') or {}; return float(p.get(k) or 0.0)
def student_x(r):
 p=r.get('portfolio') or {}; direction=r.get('direction') or {}; models=r.get('models') or {}
 # same schema as distillation pilot
 m={
 'combined_gross':sf(r,'combined_gross'),'combined_net':sf(r,'combined_net'),'combined_abs_net':sf(r,'combined_abs_net'),'combined_imbalance_ratio':sf(r,'combined_imbalance_ratio'),'combined_paired_coverage':sf(r,'combined_paired_coverage'),'worst_case_floor':sf(r,'worst_case_floor'),'best_case_pnl':sf(r,'best_case_pnl'),'abs_payoff_gap':sf(r,'abs_payoff_gap'),'last_maker_age_ms':sf(r,'last_maker_age_ms'),'last_taker_age_ms':sf(r,'last_taker_age_ms'),'maker_fills_5s':sf(r,'maker_fills_5s'),'maker_fills_10s':sf(r,'maker_fills_10s'),'taker_fills_5s':sf(r,'taker_fills_5s'),'taker_fills_10s':sf(r,'taker_fills_10s'),'maker_shares_5s':sf(r,'maker_shares_5s'),'maker_shares_10s':sf(r,'maker_shares_10s'),'taker_shares_5s':sf(r,'taker_shares_5s'),'taker_shares_10s':sf(r,'taker_shares_10s'),'combined_absnet_change_10s':sf(r,'combined_absnet_change_10s'),'event_index_norm':0.0,'last_price':0.0,'last_shares':0.0,'last_role_taker':float(sf(r,'last_taker_age_ms')<sf(r,'last_maker_age_ms') if sf(r,'last_taker_age_ms') and sf(r,'last_maker_age_ms') else 0.0)}
 return np.asarray([[float(m.get(k,0.0)) for k in F]],float)
def q(a):
 a=np.asarray(a,float); return {'n':int(len(a)),'p10':float(np.quantile(a,.1)),'median':float(np.median(a)),'p90':float(np.quantile(a,.9)),'p99':float(np.quantile(a,.99)),'mean':float(a.mean()),'max':float(a.max())}
seen=set()
for ap in arts:
 d=json.loads(ap.read_text(encoding='utf-8'))
 for arm in ['A','B']:
  if arm not in d: continue
  obj=d[arm]; mid=int(obj.get('marketId') or d.get('summary',{}).get('marketId') or 0)
  # one row per decisionId; use decisionRows, align active POST rows
  by={}
  for o in obj.get('orderStateRows') or []:
   if o.get('context')=='POST_DECISION_ACTIVE': by.setdefault(str(o.get('decisionId')),[]).append(o)
  for idx,r in enumerate(obj.get('decisionRows') or []):
   if idx % 8 != 0: continue
   did=str(r.get('decisionId')); key=(mid,did)
   if key in seen: continue
   seen.add(key)
   x=student_x(r)
   pw=float(np.clip(ST['whenModel'].predict(x)[0],0,1)); pc=float(np.clip(ST['channelModel'].predict(x)[0],0,1))
   is_candidate = (str(r.get('desiredPortfolioAction'))=='ACTIVE_INTERVENTION_REQUIRED' or str(r.get('executionChoice'))=='TAKER' or bool(r.get('readiness')) or (pw>=0.45 and pc>=0.45))
   if not is_candidate: continue
   rep=float(np.clip(ST['repairQtyModel'].predict(x)[0],0,3)); add=float(np.clip(ST['addQtyModel'].predict(x)[0],0,3))
   raw=sf(r,'combined_abs_net')
   ors=by.get(did,[])
   active=[o for o in ors if str(o.get('hftStatus')) in {'NONE','NEW','PARTIALLY_FILLED','PENDING_NEW','CANCEL_PENDING','UNKNOWN'} and float(o.get('remainingQty') or 0)>1e-9]
   live=sum(float(o.get('remainingQty') or 0) for o in active)
   ac=len(active); partial=max([float(o.get('partialFillRatio') or 0) for o in active] or [0.0]); age=max([float(o.get('orderAgeMs') or 0) for o in active] or [0.0])
   uncertain=float(any(str(o.get('hftStatus')) in {'CANCEL_PENDING','UNKNOWN'} for o in active)); terminal=float(ac==0)
   m={'raw_gap':raw,'live_remaining':live,'active_count':float(ac),'partial_ratio':partial,'max_age_ms':age,'uncertain':uncertain,'terminal_no_owner':terminal,'log_raw_gap':math.log1p(max(raw,0)),'log_live_remaining':math.log1p(max(live,0)),'live_remaining_over_gap':live/max(raw,1e-9) if raw>0 else 0.0}
   z=np.asarray([[m[k] for k in nf]],float); ar=float(np.clip(nm.predict(z)[0],0,1))
   vals.append((rep,add,ar,rep*ar,add*ar,raw,live,ac))
A=np.asarray(vals,float)
rep={'version':'R3_ACTIVE_NORMALIZED_HFT_SHADOW_V1','researchOnly':True,'markets':len(set(int(json.loads(p.read_text(encoding="utf-8")).get('summary',{}).get('marketId') or 0) for p in arts)),'rows':len(A),'rawRepairFraction':q(A[:,0]),'rawAddFraction':q(A[:,1]),'actionableRatio':q(A[:,2]),'effectiveRepairFraction':q(A[:,3]),'effectiveAddFraction':q(A[:,4]),'rates':{'effectiveRepairGt05':float(np.mean(A[:,3]>.5)),'effectiveAddGt1':float(np.mean(A[:,4]>1)),'actionableZeroish':float(np.mean(A[:,2]<.05))},'boundary':'Shadow-only. Normalizer uses orderStateRows lifecycle facts; no action authority; no future-fill labels.'}
(D/'r3_active_normalized_hft_shadow_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
