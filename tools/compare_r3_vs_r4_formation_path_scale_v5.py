from __future__ import annotations
import json,sys,sqlite3
from pathlib import Path
import numpy as np,pandas as pd,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from src.predict_bot import r3_dual_paper_shadow_v2 as r3
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features,summary
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'); TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib'); PATH=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_formation_path_scale_invariant_v2.joblib')
M1=STACK['M1_model'];CLASSES=list(STACK['classes']);FULL=list(STACK['features']['full']);TM=TRANS['model'];TFEATS=list(TRANS['features']);PM=PATH['models']['HGB'];PFEATS=list(PATH['features'])
OUT=ROOT/'data/research/r4_v0/hourly/r3_vs_r4_formation_path_scale_v5.json';EROWS=ROOT/'data/research/r4_v0/hourly/r3_vs_r4_formation_path_scale_v5_events.csv'
def fee(sh,px):return sh*px*(1-px)*.02*4
def econ_series(ev):
 up=dn=cost=fees=0.;out=[]
 for e in sorted(ev,key=lambda x:int(x['event_ms'])):
  sh=float(e.get('shares') or 0);px=float(e.get('price') or 0);side=e.get('side');role=e.get('role')
  if side=='UP':up+=sh
  elif side=='DOWN':dn+=sh
  cost+=sh*px
  if role=='TAKER':fees+=fee(sh,px)
  pu=up-cost-fees;pd=dn-cost-fees;out.append({'t':int(e['event_ms']),'floor':min(pu,pd),'upside':max(pu,pd),'surplus':abs(up-dn),'up':up,'dn':dn,'base':min(up,dn),'price':px,'shares':sh,'role':role,'side':side})
 return out
def path_features(c,now,parent_events):
 ev=list(c.inventory.events);es=econ_series(ev);cur=es[-1] if es else {'t':now,'floor':0.,'upside':0.,'surplus':0.,'up':0.,'dn':0.,'base':0.,'price':0.,'shares':0.,'role':'','side':''};gross=cur['up']+cur['dn'];ss='UP' if cur['up']>cur['dn'] else 'DOWN' if cur['dn']>cur['up'] else 'FLAT';weak='DOWN' if ss=='UP' else 'UP' if ss=='DOWN' else 'FLAT'
 def state_before(ms):
  z=[x for x in es if x['t']>=now-ms];return z[0] if z else cur
 p5,p15=state_before(5000),state_before(15000)
 def pw(ms):return [x for x in parent_events if now-x['t']<=ms]
 w5,w15,w30=pw(5000),pw(15000),pw(30000)
 def shs(w,side,role='MAKER'):return sum(x['shares'] for x in w if side!='FLAT' and x['side']==side and x['role']==role)
 tev=[x for x in ev if now-int(x['event_ms'])<=15000 and x.get('role')=='TAKER']
 def tsh(side):return sum(float(x.get('shares') or 0) for x in tev if side!='FLAT' and x.get('side')==side)
 start=min([x['t'] for x in parent_events]+[x['t'] for x in es] or [now])
 return {'seconds_left':max(0.,300.-(now-start)/1000.),'pre_floor':cur['floor'],'pre_upside':cur['upside'],'pre_surplus':cur['surplus'],'pre_base':cur['base'],'pre_surplus_ratio':cur['surplus']/max(gross,1e-9),'pre_floor_per_base':cur['floor']/max(cur['base'],1e-9),'pre_upside_per_surplus':cur['upside']/max(cur['surplus'],1e-9),'last_price':cur['price'],'last_shares':cur['shares'],'last_role_taker':int(cur['role']=='TAKER'),'events_5s':len(w5),'events_15s':len(w15),'events_30s':len(w30),'maker_events_15s':sum(x['role']=='MAKER' for x in w15),'taker_events_15s':len(tev),'weak_maker_shares_5s':shs(w5,weak),'weak_maker_shares_15s':shs(w15,weak),'weak_maker_shares_30s':shs(w30,weak),'surplus_maker_shares_15s':shs(w15,ss),'surplus_maker_shares_30s':shs(w30,ss),'weak_taker_shares_15s':tsh(weak),'surplus_taker_shares_15s':tsh(ss),'parent_weak_dominance_15s':(shs(w15,weak)+1)/(shs(w15,ss)+1),'parent_weak_dominance_30s':(shs(w30,weak)+1)/(shs(w30,ss)+1),'floor_change_5s':cur['floor']-p5['floor'],'floor_change_15s':cur['floor']-p15['floor'],'upside_change_5s':cur['upside']-p5['upside'],'upside_change_15s':cur['upside']-p15['upside'],'surplus_change_5s':cur['surplus']-p5['surplus'],'surplus_change_15s':cur['surplus']-p15['surplus'],'seconds_from_first_event':(now-start)/1000.},weak

def scale_pf(pf):
 gross=2*float(pf['pre_base'])+float(pf['pre_surplus']);g=max(gross,1e-9)
 x={'seconds_left':pf['seconds_left'],'pre_surplus_ratio':pf['pre_surplus_ratio'],'pre_floor_per_base':pf['pre_floor_per_base'],'pre_upside_per_surplus':pf['pre_upside_per_surplus'],'floor_per_gross':pf['pre_floor']/g,'upside_per_gross':pf['pre_upside']/g,'last_price':pf['last_price'],'last_role_taker':pf['last_role_taker'],'events_5s':pf['events_5s'],'events_15s':pf['events_15s'],'events_30s':pf['events_30s'],'maker_events_15s':pf['maker_events_15s'],'taker_events_15s':pf['taker_events_15s'],'parent_weak_dominance_15s':pf['parent_weak_dominance_15s'],'parent_weak_dominance_30s':pf['parent_weak_dominance_30s'],'seconds_from_first_event':pf['seconds_from_first_event']}
 for c in ['weak_maker_shares_5s','weak_maker_shares_15s','weak_maker_shares_30s','surplus_maker_shares_15s','surplus_maker_shares_30s','weak_taker_shares_15s','surplus_taker_shares_15s','floor_change_5s','floor_change_15s','upside_change_5s','upside_change_15s','surplus_change_5s','surplus_change_15s']: x[c+'_per_gross']=pf[c]/g
 return x

def run_r4(mid,elog):
 orig_new=base.new_controller;stats={'riskCandidates':0,'positiveFloorPreserves':0,'pathPreserves':0,'crossOverrides':0}
 def custom_new(a):
  c=orig_new(a);orig_add=c._add_order;st={'name':'ALLOW_ASYMMETRY','last':-10**18,'t':None,'intent':None};parents=[]
  def evaluate(now):
   if st['t']==now:return st['intent']
   f=r3ctl.current_features(c,now)
   if f is None:it={'state':'ALLOW_ASYMMETRY','weakSide':None};st['t']=now;st['intent']=it;return it
   X=np.asarray([[float(f[k]) for k in r3.FEATS]]);pb=float(r3.ARB.predict_proba(X)[0,1]);pc=float(r3.CROSS.predict_proba(X)[0,1]);p=r3ctl.packet_from_active(c,a,now,f);mode=r3.cooperation(st['name'],pb,pc,f,p);unc=r3.uncertainty_from_packet(p,mode);bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc;can=now-st['last']>=1000;ns=st['name']
   if can and pc>=con:ns='CROSSING_PROTECTION'
   elif ns=='CROSSING_PROTECTION' and can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
   elif ns=='BUILD_WEAK_SIDE' and can and pb<boff:ns='ALLOW_ASYMMETRY'
   elif ns=='ALLOW_ASYMMETRY' and can and pb>=bon:ns='BUILD_WEAK_SIDE'
   mf,weak=mgmt_features(c,a,now,f);risk=trans=ppath=0.;reason='';candidate=False
   try:
    pm=M1.predict_proba(np.asarray([[mf[k] for k in FULL]]))[0];cm={str(x):float(pm[i]) for i,x in enumerate(CLASSES)};risk=1-cm.get('CONTINUE_WEAK',0.);pt=TM.predict_proba(np.asarray([[mf[k] for k in TFEATS]]))[0];trans=float(pt[1] if len(pt)>1 else pt[0]);pf,weak2=path_features(c,now,parents);weak=weak2 if weak2!='FLAT' else weak;candidate=bool(60<=mf['seconds_left']<180 and risk>=.5 and trans>=.5 and weak is not None)
    if candidate:
     stats['riskCandidates']+=1
     if pf['pre_floor']>0:reason='POSITIVE_FLOOR';stats['positiveFloorPreserves']+=1
     else:
      sp=scale_pf(pf);row=pd.DataFrame([[sp[k] for k in PFEATS]],columns=PFEATS);ppath=float(PM.predict_proba(row)[0,1])
      if ppath>=.5:reason='FORMATION_PATH_VALUE';stats['pathPreserves']+=1
     elog.append({'marketId':mid,'t':now,'secondsLeft':mf['seconds_left'],'risk':risk,'transition':trans,'floor':pf['pre_floor'],'surplus':pf['pre_surplus'],'pFormationPath':ppath,'preserveReason':reason,**{('feat_'+k):pf[k] for k in PFEATS}})
   except Exception:pass
   if candidate and not reason:ns='CROSSING_PROTECTION';stats['crossOverrides']+=1
   if ns!=st['name']:st['name']=ns;st['last']=now
   it={'state':ns,**r3.control_intent(ns,f),'weakSide':weak};st['t']=now;st['intent']=it;return it
  def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
   it=evaluate(now);weak=it.get('weakSide');strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
   if it['state']=='CROSSING_PROTECTION' and strong and side==strong:made=bool(orig_add(weak,now,snapshot_ns,decision_id,'R4_PATH_WEAK_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)) if weak else False
   else:made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
   if made:parents.append({'t':now,'side':side,'role':'MAKER','shares':18.0})
   return made
  c._add_order=add;return c
 base.new_controller=custom_new
 try:r=base.run_market(mid,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200)
 finally:base.new_controller=orig_new
 r['stats']=stats;return r

def main():
 mids=[int(x) for x in sys.argv[1:]] if len(sys.argv)>1 else [1736560];rows=[];elog=[]
 for mid in mids:
  A=summary(r3ctl.run_market(mid,True));b=run_r4(mid,elog);B=summary(b);rows.append({'marketId':mid,'R3':A,'R4_PATH':B,'deltaFloor':B['worstCaseFloor']-A['worstCaseFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],**b['stats']});print(json.dumps(rows[-1]),flush=True)
 pd.DataFrame(elog).to_csv(EROWS,index=False);OUT.write_text(json.dumps({'version':'R3_VS_R4_FORMATION_PATH_SCALE_V5','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','rule':'Existing risk+transition candidate; positive floor or EBM Formation Path Value p>=0.5 vetoes added forced crossing. No threshold sweep.','markets':rows},indent=2));print(pd.DataFrame(elog).to_string(index=False))
if __name__=='__main__':main()

