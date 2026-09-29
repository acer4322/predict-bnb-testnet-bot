from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data'/'research'/'r3_v0'
HZ=joblib.load(D/'r3_target_active_hazard_5s_hgb_v4.joblib'); CH=joblib.load(D/'r3_target_active_channel_hgb_v3.joblib'); RQ=joblib.load(D/'r3_target_active_repair_effect_fraction_hgb_v2_big.joblib'); AQ=joblib.load(D/'r3_target_active_add_effect_fraction_hgb_v2_big.joblib')
arts=sorted(D.glob('r3_hft_context_control_*market*_v1.json'))+sorted(D.glob('r3_hft_context_control_smalltest_market*_v0.json'))

def n(v,d=0.):
 try:
  x=float(v); return x if math.isfinite(x) else d
 except: return d

def vals(row):
 p=row.get('portfolio') or {}; dr=row.get('direction') or {}; gross=n(p.get('combined_gross')); net=n(p.get('combined_net')); ss=abs(net); base=max(0.,(gross-ss)/2); floor=n(p.get('worst_case_floor')); upside=n(p.get('best_case_pnl'))
 lm=n(p.get('last_maker_age_ms'),1e9); lt=n(p.get('last_taker_age_ms'),1e9)
 v={'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':max(0.,(gross-min(floor,0))/gross) if gross else 0.,'last_price':n(((dr.get('components') or {}).get('predict') or {}).get('value'),.5),'last_shares':0.,'last_role_taker':1. if lt<=lm else 0.,'age_since_last_ms':min(lm,lt),'events_5s':n(p.get('maker_fills_5s'))+n(p.get('taker_fills_5s')),'events_15s':n(p.get('maker_fills_10s'))+n(p.get('taker_fills_10s')),'maker_events_15s':n(p.get('maker_fills_10s')),'taker_events_15s':n(p.get('taker_fills_10s')),'same_side_events_15s':0.,'opp_side_events_15s':0.,'same_side_shares_15s':0.,'opp_side_shares_15s':0.,'surplus_change_5s':n(p.get('combined_absnet_change_10s')),'floor_change_5s':0.,'upside_change_5s':0.,'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,'event_index_norm':0.5,'action_same_as_surplus':0.,'action_side_up':1. if (dr.get('side')=='UP') else 0.}
 return v
rows=[]; seen=set()
for a in arts:
 try:d=json.loads(a.read_text(encoding='utf-8'))
 except:continue
 if not isinstance(d,dict) or 'A' not in d: continue
 for arm in ['A','B']:
  x=d.get(arm) or {}; mid=x.get('marketId'); key=(mid,arm)
  if key in seen: continue
  seen.add(key); rr=x.get('decisionRows') or []
  if len(rr)>120:
   idx=np.linspace(0,len(rr)-1,120,dtype=int); rr=[rr[i] for i in idx]
  for r in rr: rows.append((mid,arm,vals(r)))
def X(bundle): return np.asarray([[v.get(k,0.) for k in bundle['features']] for _,_,v in rows],float)
ph=HZ['model'].predict_proba(X(HZ))[:,1]; pc=CH['model'].predict_proba(X(CH))[:,1]; fr=np.clip(RQ['model'].predict(X(RQ)),0,2); fa=np.clip(AQ['model'].predict(X(AQ)),0,3)
def q(a): return {'n':int(len(a)),'p10':float(np.quantile(a,.1)),'median':float(np.quantile(a,.5)),'p90':float(np.quantile(a,.9)),'p99':float(np.quantile(a,.99)),'mean':float(np.mean(a)),'max':float(np.max(a))}
rep={'version':'R3_ACTIVE_TEACHER_ON_OUR_STATE_FAST_V2','researchOnly':True,'markets':len(set(m for m,_,_ in rows)),'rows':len(rows),'summary':{'pWhen5s':q(ph),'pChannelTaker':q(pc),'repairFraction':q(fr),'addFraction':q(fa)},'rates':{'whenGe05':float(np.mean(ph>=.5)),'channelGe05':float(np.mean(pc>=.5)),'repairFracGt05':float(np.mean(fr>.5)),'addFracGt1':float(np.mean(fa>1))},'boundary':'Shadow-only approximate bridge; side-specific 15s flow and exact floor/upside deltas unavailable in current HFT decision rows and zero-filled. No action authority.'}
(D/'r3_active_teacher_on_our_state_fast_v2.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
