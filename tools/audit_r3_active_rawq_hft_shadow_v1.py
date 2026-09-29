from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'
ST=joblib.load(D/'r3_active_our_state_student_v2_pilot600.joblib'); RQ=joblib.load(D/'r3_active_rawq_our_student_pilot300_v2_batch.joblib')
F=ST['features']; RF=RQ['features']
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

vals=[]; seen=set()
for ap in arts:
 d=json.loads(ap.read_text(encoding='utf-8'))
 for arm in ['A','B']:
  if arm not in d: continue
  obj=d[arm]; mid=int(obj.get('marketId') or d.get('summary',{}).get('marketId') or 0)
  for idx,r in enumerate(obj.get('decisionRows') or []):
   if idx%8: continue
   did=str(r.get('decisionId')); key=(mid,did)
   if key in seen: continue
   seen.add(key); x=student_x(r)
   pw=float(np.clip(ST['whenModel'].predict(x)[0],0,1)); pc=float(np.clip(ST['channelModel'].predict(x)[0],0,1))
   cand=(str(r.get('desiredPortfolioAction'))=='ACTIVE_INTERVENTION_REQUIRED' or str(r.get('executionChoice'))=='TAKER' or bool(r.get('readiness')) or (pw>=.45 and pc>=.45))
   if not cand: continue
   fracR=float(np.clip(ST['repairQtyModel'].predict(x)[0],0,3)); fracA=float(np.clip(ST['addQtyModel'].predict(x)[0],0,3)); gap=sf(r,'combined_abs_net')
   rawR=float(max(0,np.expm1(RQ['repairModel'].predict(x)[0]))); rawA=float(max(0,np.expm1(RQ['addModel'].predict(x)[0])))
   vals.append((gap,fracR*gap,fracA*gap,rawR,rawA,pw,pc))
A=np.asarray(vals,float)
def qs(a): return {'n':int(len(a)),'median':float(np.median(a)),'p90':float(np.quantile(a,.9)),'p99':float(np.quantile(a,.99)),'mean':float(a.mean()),'max':float(a.max())}
rep={'version':'R3_ACTIVE_RAWQ_HFT_SHADOW_V1','researchOnly':True,'rows':len(A),'markets':len(arts),'gapShares':qs(A[:,0]),'fractionRepairShares':qs(A[:,1]),'fractionAddShares':qs(A[:,2]),'rawQRepairShares':qs(A[:,3]),'rawQAddShares':qs(A[:,4]),'rates':{'fractionRepairGt36':float(np.mean(A[:,1]>36)),'rawQRepairGt36':float(np.mean(A[:,3]>36)),'fractionAddGt36':float(np.mean(A[:,2]>36)),'rawQAddGt36':float(np.mean(A[:,4]>36))},'boundary':'Same HFT candidate states; raw-share student predicts absolute Target-like Taker shares and has no fixed 18 cap.'}
(D/'r3_active_rawq_hft_shadow_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
