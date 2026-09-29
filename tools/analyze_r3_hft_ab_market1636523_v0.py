from __future__ import annotations
import json, math, sqlite3, joblib, numpy as np
from pathlib import Path
from collections import deque
import sys
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r3_v0'; TRACE=R/'r3_hft_ab_market1636523_base_trace_v0.json'; DB=ROOT/'data/target_wallet_official_v1.db'
sys.path.insert(0,str((ROOT/'tools').resolve()))
import train_r3_formation_sequence_v2 as seq
ARB=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['model']
CROSS=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')['model']
FEATS=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['features']

def teacher_map(mid):
 c=sqlite3.connect(DB); rows=seq.build_market(c,mid); c.close(); out=[]
 for m,t,x,y in rows: out.append((int(t),'CROSSING_PROTECTION' if seq.STATES[y]=='CROSS_SAFE' else seq.STATES[y]))
 return out

def nearest_teacher(tm,t):
 if not tm:return None
 return min(tm,key=lambda z:abs(z[0]-t))[1]

def build_hft_features(r):
 ev=[]
 for f in r['executionLifecycleTrace']['makerFills']:
  ev.append((int(f['observedAtMs']),'MAKER',str(f['side']),float(f['price']),float(f.get('deltaShares',0)),0.0))
 for f in r['executionLifecycleTrace']['takerFills']:
  ev.append((int(f['observedAtMs']),'TAKER',str(f['side']),float(f['price']),float(f.get('shares',0)),float(f.get('feeUsdt',0))))
 ev.sort()
 up=down=cost=fees=0.; hist=deque(); prev=None; rows=[]
 start=ev[0][0] if ev else 0; end=ev[-1][0] if ev else start
 for i,(t,role,side,px,sh,fee) in enumerate(ev):
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh; fees+=fee
  pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; sur='UP' if up>down else 'DOWN' if down>up else 'FLAT'
  hist.append((t,role,side,sh,ss,fl,ups))
  while hist and t-hist[0][0]>15000: hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old=r5[0] if r5 else hist[0]
  f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),'surplus_change_5s':float(ss-old[4]),'floor_change_5s':float(fl-old[5]),'upside_change_5s':float(ups-old[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':(t-start)/max(1,end-start)}
  rows.append((t,f)); prev=t
 return rows

def run(rows,soft=False):
 state='ALLOW_ASYMMETRY'; last=rows[0][0] if rows else 0; pred=[]
 for i,(t,f) in enumerate(rows):
  X=np.asarray([[float(f[k]) for k in FEATS]],float); pb=float(ARB.predict_proba(X)[0,1]); pc=float(CROSS.predict_proba(X)[0,1])
  # HFT context proxy derived only from own-state execution: uncertainty around recent Taker/young fills.
  # V4 stays soft: no veto/lease. This is deliberately bounded to the best dream-AB strength 0.02.
  unc=0.0
  if soft:
   unc=min(1.0, 0.45*float(f['last_role_taker']) + 0.35*(1.0 if f['age_since_last_ms']<1000 else 0.0) + 0.20*min(1.0,f['events_5s']/5.0))
  bon=.48+.02*unc; boff=.38-.01*unc; con=.35+.015*unc
  can=(t-last)>=1000; ns=state
  if can and pc>=con: ns='CROSSING_PROTECTION'
  elif state=='CROSSING_PROTECTION':
   if can and pc<con*.7: ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
  elif state=='BUILD_WEAK_SIDE':
   if can and pb<boff: ns='ALLOW_ASYMMETRY'
  elif can and pb>=bon: ns='BUILD_WEAK_SIDE'
  if ns!=state: state=ns; last=t
  pred.append({'atMs':t,'state':state,'pb':pb,'pc':pc,'uncertainty':unc,'floor':f['floor'],'surplus':f['surplus_shares']})
 return pred

def metrics(pred,tm):
 truth=[nearest_teacher(tm,x['atMs']) for x in pred]; p=[x['state'] for x in pred]; n=len(p)
 sync=sum(a==b for a,b in zip(truth,p))/max(1,n); sw=sum(a!=b for a,b in zip(p,p[1:])); tsw=sum(a!=b for a,b in zip(truth,truth[1:]));
 tch=[(i,truth[i]) for i in range(1,n) if truth[i]!=truth[i-1]]; pch=[(i,p[i]) for i in range(1,n) if p[i]!=p[i-1]]; trans=sum(1 for i,s in tch if any(abs(j-i)<=2 and ps==s for j,ps in pch))/max(1,len(tch))
 return {'checkpoints':n,'checkpointSyncToNearestTargetState':sync,'transitionSync2cp':trans,'r3Switches':sw,'targetSwitchesOnHftCheckpoints':tsw,'stateCounts':{s:p.count(s) for s in ['ALLOW_ASYMMETRY','BUILD_WEAK_SIDE','CROSSING_PROTECTION']}}

def main():
 r=json.load(open(TRACE,encoding='utf-8')); rows=build_hft_features(r); tm=teacher_map(1636523); A=run(rows,False); B=run(rows,True); ma=metrics(A,tm); mb=metrics(B,tm)
 changed=sum(a['state']!=b['state'] for a,b in zip(A,B)); rep={'version':'R3_HFT_AB_MARKET1636523_V0','marketId':1636523,'executionSemantics':r['executionSemantics'],'hftSemanticGate':r['semanticGate'],'hftActualExecution':{'makerFills':r['r2ObjectiveExecution']['makerFills'],'takerFills':r['r2ObjectiveExecution']['takerFills'],'finalPortfolio':r['actualExecution']['finalPortfolio']},'A':'original R3 Formation on HFT actual-fill state','B':'Soft Context V4 bounded modulation on same HFT actual-fill state','A_metrics':ma,'B_metrics':mb,'delta':{'checkpointSync':mb['checkpointSyncToNearestTargetState']-ma['checkpointSyncToNearestTargetState'],'transitionSync2cp':mb['transitionSync2cp']-ma['transitionSync2cp'],'switches':mb['r3Switches']-ma['r3Switches'],'stateDifferentCheckpoints':changed},'note':'R3 is not yet wired to mutate HFT orders; execution/PnL is intentionally identical in A/B. This test compares Formation decisions after realistic HFT fills, not economic performance.','A_trace':A,'B_trace':B}
 (R/'r3_hft_ab_market1636523_v0_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({k:rep[k] for k in ['marketId','A_metrics','B_metrics','delta','note']},indent=2))
if __name__=='__main__':main()
