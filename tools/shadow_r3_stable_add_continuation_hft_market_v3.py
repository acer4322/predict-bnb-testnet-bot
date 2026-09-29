from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
D=ROOT/'data/research/r3_v0';SK=joblib.load(D/'r3_stable_add_eligibility_pilot500_v2_normalized.joblib');F=SK['features'];CONT=joblib.load(D/'r3_formation_continuation_add_pilot500_v2.joblib')
def fee(sh,px,r):return sh*px*.02 if r=='TAKER' else 0.
def shadow(mid):
 r=run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=D/'r3_post_add_our_state_student_v2_stream.joblib',post_add_lifecycle_control=True);ev=[]
 for z in r.get('makerFillEvents',[]):ev.append((int(z['atMs']),'MAKER',str(z['side']),float(z['price']),float(z['deltaShares'])))
 for z in r.get('takerEvents',[]):ev.append((int(z['atMs']),'TAKER',str(z['side']),float(z['price']),float(z['shares'])))
 ev.sort(key=lambda x:x[0]);att=sorted(r.get('takerAttempts',[]),key=lambda x:int(x['atMs']));first=int(r.get('feed',{}).get('firstReceivedMs') or (ev[0][0] if ev else att[0]['atMs']));last=int(r.get('feed',{}).get('lastReceivedMs') or (ev[-1][0] if ev else att[-1]['atMs']));out=[]
 for a in att:
  if a.get('structuralEffect')!='ADD_EFFECT':continue
  t=int(a['atMs']);h=[x for x in ev if x[0]<t]
  if not h:continue
  up=dn=cost=fees=0.;sn=[]
  for et,role,side,px,sh in h:
   if side=='UP':up+=sh
   else:dn+=sh
   cost+=px*sh;fees+=fee(sh,px,role);pu,pd=up-cost-fees,dn-cost-fees;sn.append((et,role,side,px,sh,min(pu,pd),max(pu,pd),abs(up-dn)))
  et,role,side,px,sh,fl,ups,ab=sn[-1];g=up+dn;sur='UP' if up>dn else 'DOWN' if dn>up else 'FLAT';q15=[x for x in sn if t-x[0]<=15000];q5=[x for x in sn if t-x[0]<=5000];old=q5[0] if q5 else q15[0] if q15 else sn[-1];totsh=sum(x[4] for x in q15) or 1.;nev=len(q15) or 1
  v={'paired_coverage':2*min(up,dn)/g if g else 0.,'imbalance_ratio':ab/g if g else 0.,'cost_per_gross':(cost+fees)/g if g else 0.,'floor_per_gross':fl/g if g else 0.,'upside_per_gross':ups/g if g else 0.,'floor_to_upside':fl/ups if abs(ups)>1e-9 else 0.,'last_price':px,'last_role_taker':float(role=='TAKER'),'age_since_last_s':(t-et)/1000.,'events_5s':len(q5),'events_15s':len(q15),'maker_frac_15s':sum(x[1]=='MAKER' for x in q15)/nev,'taker_frac_15s':sum(x[1]=='TAKER' for x in q15)/nev,'same_side_event_frac_15s':sum(sur!='FLAT' and x[2]==sur for x in q15)/nev,'same_side_share_frac_15s':sum(x[4] for x in q15 if sur!='FLAT' and x[2]==sur)/totsh,'opp_side_share_frac_15s':sum(x[4] for x in q15 if sur!='FLAT' and x[2]!=sur)/totsh,'floor_change5_per_gross':(fl-old[5])/g if g else 0.,'upside_change5_per_gross':(ups-old[6])/g if g else 0.,'absnet_change5_per_gross':(ab-old[7])/g if g else 0.,'event_index_norm':max(0.,min(1.,(t-first)/max(1,last-first)))}
  x=np.asarray([[float(v.get(f,0.)) for f in F]],float);p=float(SK['model'].predict_proba(x)[0,1]);pc=float(CONT['model'].predict_proba(x)[0,1]);out.append({'atMs':t,'side':a.get('side'),'requestedShares':a.get('requestedShares'),'pStableAddEligibilityV2':p,'pFormationContinuationV2':pc,'result':a.get('result')})
 fp=r['studentRollout']['finalPortfolio'];rep={'version':'R3_STABLE_ADD_HFT_SHADOW_V2_NORMALIZED','marketId':mid,'structuralAddAttempts':out,'summary':{'n':len(out),'meanP':sum(x['pStableAddEligibilityV2'] for x in out)/len(out) if out else None,'finalFloor':fp.get('worst_case_floor'),'finalUpside':fp.get('best_case_pnl')}};p=D/f'r3_stable_add_continuation_hft_shadow_market{mid}_v3.json';p.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p),'meanP':rep['summary']['meanP']}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);a=ap.parse_args();shadow(a.market_id)
