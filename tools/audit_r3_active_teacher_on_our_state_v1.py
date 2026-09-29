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

def feat(row, names, action_side=None):
 p=row.get('portfolio') or {}; dr=row.get('direction') or {}; models=row.get('models') or {}
 gross=n(p.get('combined_gross')); net=n(p.get('combined_net')); ss=abs(net); base=max(0.,(gross-ss)/2)
 floor=n(p.get('worst_case_floor')); upside=n(p.get('best_case_pnl')); costpg=max(0., (gross-min(floor,0)+0)/gross) if gross else 0.
 vals={
 'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':costpg,
 'last_price':n((dr.get('components') or {}).get('predict',{}).get('value'),.5),'last_shares':0.,'last_role_taker':1. if n(p.get('last_taker_age_ms'),1e18)<=n(p.get('last_maker_age_ms'),1e18) else 0.,
 'age_since_last_ms':min(n(p.get('last_taker_age_ms'),1e9),n(p.get('last_maker_age_ms'),1e9)),
 'events_5s':n(p.get('maker_fills_5s'))+n(p.get('taker_fills_5s')),'events_15s':n(p.get('maker_fills_10s'))+n(p.get('taker_fills_10s')),
 'maker_events_15s':n(p.get('maker_fills_10s')),'taker_events_15s':n(p.get('taker_fills_10s')),
 'same_side_events_15s':0.,'opp_side_events_15s':0.,'same_side_shares_15s':0.,'opp_side_shares_15s':0.,
 'surplus_change_5s':n(p.get('combined_absnet_change_10s')),'floor_change_5s':0.,'upside_change_5s':0.,
 'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,
 'event_index_norm':0.5,
 'action_same_as_surplus':0., 'action_side_up':1. if action_side=='UP' else 0.,
 }
 return np.asarray([[vals.get(k,0.) for k in names]],float)
rows=[]; seen=set()
for a in arts:
 try:d=json.loads(a.read_text(encoding='utf-8'))
 except:continue
 arms=[]
 if isinstance(d,dict) and 'A' in d: arms=[('A',d['A']),('B',d.get('B') or {})]
 else: continue
 for arm,x in arms:
  mid=x.get('marketId'); key=(mid,arm)
  if key in seen: continue
  seen.add(key)
  for r in x.get('decisionRows') or []:
   phz=float(HZ['model'].predict_proba(feat(r,HZ['features']))[0,1]); pch=float(CH['model'].predict_proba(feat(r,CH['features']))[0,1])
   # quantity scored for both effect hypotheses; action side uses current R2 directional side if available
   side=(r.get('direction') or {}).get('side'); side=side if side in {'UP','DOWN'} else 'UP'
   fr=float(np.clip(RQ['model'].predict(feat(r,RQ['features'],side))[0],0,2)); fa=float(np.clip(AQ['model'].predict(feat(r,AQ['features'],side))[0],0,3))
   rows.append({'marketId':mid,'arm':arm,'pWhen5s':phz,'pChannelTaker':pch,'repairFraction':fr,'addFraction':fa,'absNet':abs(n((r.get('portfolio') or {}).get('combined_net'))),'execution':r.get('executionChoice')})

def q(xs):
 if not xs:return {}
 return {'n':len(xs),'p10':float(np.quantile(xs,.1)),'median':float(np.quantile(xs,.5)),'p90':float(np.quantile(xs,.9)),'p99':float(np.quantile(xs,.99)),'mean':float(np.mean(xs)),'max':float(np.max(xs))}
rep={'version':'R3_ACTIVE_TEACHER_ON_OUR_STATE_AUDIT_V1','researchOnly':True,'artifacts':len(arts),'rows':len(rows),'summary':{k:q([r[k] for r in rows]) for k in ['pWhen5s','pChannelTaker','repairFraction','addFraction','absNet']},'rates':{'whenGe05':sum(r['pWhen5s']>=.5 for r in rows)/len(rows) if rows else 0,'channelGe05':sum(r['pChannelTaker']>=.5 for r in rows)/len(rows) if rows else 0,'repairFracGt05':sum(r['repairFraction']>.5 for r in rows)/len(rows) if rows else 0,'addFracGt1':sum(r['addFraction']>1 for r in rows)/len(rows) if rows else 0},'boundary':'Shadow-only covariate-shift audit; no orders; approximate feature bridge from OUR portfolio. Missing 15s side-flow/floor deltas currently zero-filled and must be improved before authority.'}
(D/'r3_active_teacher_on_our_state_audit_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
