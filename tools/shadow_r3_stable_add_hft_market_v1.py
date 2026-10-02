from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
D=ROOT/'data/research/r3_v0'
SK=joblib.load(D/'r3_stable_add_eligibility_pilot500_v1.joblib'); FEATURES=list(SK['features'])
def fee(sh,px,role): return sh*px*.02 if role=='TAKER' else 0.0
def shadow(mid:int):
 r=run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=D/'r3_post_add_our_state_student_v2_stream.joblib',post_add_lifecycle_control=True)
 ev=[]
 for z in r.get('makerFillEvents',[]): ev.append((int(z['atMs']),'MAKER',str(z['side']),float(z['price']),float(z['deltaShares'])))
 for z in r.get('takerEvents',[]): ev.append((int(z['atMs']),'TAKER',str(z['side']),float(z['price']),float(z['shares'])))
 ev.sort(key=lambda x:x[0]); attempts=sorted(r.get('takerAttempts',[]),key=lambda x:int(x['atMs']))
 first=int(r.get('feed',{}).get('firstReceivedMs') or (ev[0][0] if ev else attempts[0]['atMs'])); last=int(r.get('feed',{}).get('lastReceivedMs') or (ev[-1][0] if ev else attempts[-1]['atMs']))
 scored=[]
 for a in attempts:
  if str(a.get('structuralEffect'))!='ADD_EFFECT': continue
  t=int(a['atMs']); hist=[x for x in ev if x[0]<t]
  if not hist: continue
  up=dn=cost=fees=0.; snaps=[]; prev=None
  for et,role,side,px,sh in hist:
   if side=='UP':up+=sh
   else:dn+=sh
   cost+=px*sh;fees+=fee(sh,px,role);pu,pd=up-cost-fees,dn-cost-fees;fl=min(pu,pd);ups=max(pu,pd);ab=abs(up-dn);snaps.append((et,role,side,px,sh,fl,ups,ab));prev=et
  et,role,side,px,sh,fl,ups,ab=snaps[-1];gross=up+dn;surplus='UP' if up>dn else 'DOWN' if dn>up else 'FLAT';q15=[x for x in snaps if t-x[0]<=15000];q5=[x for x in snaps if t-x[0]<=5000];old=q5[0] if q5 else q15[0] if q15 else snaps[-1]
  vals={'floor':fl,'upside':ups,'abs_net':ab,'gross':gross,'paired_coverage':2*min(up,dn)/gross if gross else 0.,'imbalance_ratio':ab/gross if gross else 0.,'surplus_ratio':ab/gross if gross else 0.,'cost_per_gross':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':float(role=='TAKER'),'age_since_last_ms':float(t-et),'events_5s':len(q5),'events_15s':len(q15),'maker_events_15s':sum(x[1]=='MAKER' for x in q15),'taker_events_15s':sum(x[1]=='TAKER' for x in q15),'same_side_events_15s':sum(surplus!='FLAT' and x[2]==surplus for x in q15),'opp_side_events_15s':sum(surplus!='FLAT' and x[2]!=surplus for x in q15),'same_side_shares_15s':sum(x[4] for x in q15 if surplus!='FLAT' and x[2]==surplus),'opp_side_shares_15s':sum(x[4] for x in q15 if surplus!='FLAT' and x[2]!=surplus),'floor_change_5s':fl-old[5],'upside_change_5s':ups-old[6],'absnet_change_5s':ab-old[7],'event_index_norm':max(0.,min(1.,(t-first)/max(1,last-first)))}
  x=np.asarray([[float(vals.get(f,0.)) for f in FEATURES]],float);p=float(SK['model'].predict_proba(x)[0,1]);scored.append({'atMs':t,'side':a.get('side'),'requestedShares':a.get('requestedShares'),'preCombinedNet':a.get('preCombinedNet'),'pStableAddEligibility':p,'wouldPassP05':bool(p>=.5),'result':a.get('result')})
 s=r['studentRollout'];fp=s['finalPortfolio'];out={'version':'R3_STABLE_ADD_HFT_SHADOW_V1','marketId':mid,'researchOnly':True,'actionAuthority':False,'teacher':SK['version'],'structuralAddAttempts':scored,'summary':{'nStructuralAdd':len(scored),'passP05':sum(x['wouldPassP05'] for x in scored),'finalFloor':fp.get('worst_case_floor'),'finalUpside':fp.get('best_case_pnl'),'finalAbsNet':fp.get('combined_abs_net')}}
 p=D/f'r3_stable_add_hft_shadow_market{mid}_v1.json';p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p),'marketId':mid,'n':len(scored),'pass':out['summary']['passP05']}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);a=ap.parse_args();shadow(a.market_id)
