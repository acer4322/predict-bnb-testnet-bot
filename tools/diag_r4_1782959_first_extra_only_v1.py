from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from src.predict_bot import r3_dual_paper_shadow_v2 as r3
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features,summary
from tools.compare_r3_vs_r4_formation_path_scale_v5 import path_features,scale_pf
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib');TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib');PATH=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_formation_path_scale_invariant_v2.joblib')
M1=STACK['M1_model'];CLASSES=list(STACK['classes']);FULL=list(STACK['features']['full']);TM=TRANS['model'];TFEATS=list(TRANS['features']);PM=PATH['models']['HGB'];PFEATS=list(PATH['features'])
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_bounded_one_parent_guard_v1.json';EV=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_bounded_one_parent_guard_v1_events.csv'

def run_overlay(market_id:int,enabled:bool,event_rows:list[dict]):
 orig_new=base.new_controller;control_events=[];rng=np.random.default_rng(930000+int(market_id));stats={'riskCandidates':0,'positiveFloorPreserves':0,'oneParentGapPreserves':0,'pathEntries':0,'pathPreserves':0,'crossOverrides':0}
 def custom_new(a):
  c=orig_new(a);orig_add=c._add_order;state={'name':'ALLOW_ASYMMETRY','last':-10**18,'eval_t':None,'intent':None,'path_latch':False,'mgmt_consumed_t':None,'diag_mgmt_requests':0};parents=[]
  def eval_state(now:int):
   if state['eval_t']==now and state['intent'] is not None:return state['intent']
   f=r3ctl.current_features(c,now)
   if f is None:
    intent={'state':'ALLOW_ASYMMETRY','repairMode':'NORMAL_FORMATION','weakSide':None,'weakSideMakerScale':1.,'strongSideMakerScale':1.,'pauseStrongSide':False,'pb':0.,'pc':0.,'unc':0.,'contextType':'NO_INVENTORY','mgmtReason':'','pFormationPath':0.};state['intent']=intent;state['eval_t']=now;return intent
   X=np.asarray([[float(f[k]) for k in r3.FEATS]],float);pb=float(r3.ARB.predict_proba(X)[0,1]);pc=float(r3.CROSS.predict_proba(X)[0,1]);p=r3ctl.packet_from_active(c,a,now,f)
   mode=r3.cooperation(state['name'],pb,pc,f,p);unc=r3.uncertainty_from_packet(p,mode);bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc
   if mode=='PRESERVE_CURRENT_REGIME' and pc>=.35:con=.35
   can=(now-state['last'])>=1000;ns=state['name']
   if can and pc>=con:ns='CROSSING_PROTECTION'
   elif ns=='CROSSING_PROTECTION':
    if can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
   elif ns=='BUILD_WEAK_SIDE':
    if can and pb<boff:ns='ALLOW_ASYMMETRY'
   elif can and pb>=bon:ns='BUILD_WEAK_SIDE'
   mgmt_reason='';ppath=0.;risk=trans=0.;mgmt_override=False
   if enabled:
    try:
     mf,weakm=mgmt_features(c,a,now,f);pm=M1.predict_proba(np.asarray([[mf[k] for k in FULL]]))[0];cm={str(x):float(pm[i]) for i,x in enumerate(CLASSES)};risk=1-cm.get('CONTINUE_WEAK',0.);pt=TM.predict_proba(np.asarray([[mf[k] for k in TFEATS]]))[0];trans=float(pt[1] if len(pt)>1 else pt[0]);pf,weakp=path_features(c,now,parents);candidate=bool(60<=mf['seconds_left']<180 and risk>=.5 and trans>=.5 and (weakp!='FLAT' or weakm is not None))
     if candidate:
      stats['riskCandidates']+=1
      if float(mf.get('abs_gap',0.0)) <= 18.0 + 1e-9:
       state['path_latch']=False;mgmt_reason='ONE_PARENT_GAP_GUARD';stats['oneParentGapPreserves']+=1
      elif pf['pre_floor']>0:
       state['path_latch']=False;mgmt_reason='POSITIVE_FLOOR';stats['positiveFloorPreserves']+=1
      else:
       sp=scale_pf(pf);ppath=float(PM.predict_proba(pd.DataFrame([[sp[k] for k in PFEATS]],columns=PFEATS))[0,1])
       if (not state['path_latch']) and ppath>=.5:state['path_latch']=True;stats['pathEntries']+=1
       if state['path_latch']:mgmt_reason='FORMATION_PATH_LATCH';stats['pathPreserves']+=1
      if not mgmt_reason and ns!='CROSSING_PROTECTION':
       if state['diag_mgmt_requests']>=1: mgmt_reason='DIAG_SUPPRESS_AFTER_FIRST'
       else: mgmt_override=True;state['diag_mgmt_requests']+=1;stats['crossOverrides']+=1
      event_rows.append({'marketId':market_id,'atMs':now,'risk':risk,'transition':trans,'pFormationPath':ppath,'floor':pf['pre_floor'],'surplus':pf['pre_surplus'],'mgmtReason':mgmt_reason,'forcedCross':not bool(mgmt_reason),**{('mf_'+k):v for k,v in mf.items()},**{('pf_'+k):v for k,v in pf.items() if isinstance(v,(int,float,np.integer,np.floating))}})
    except Exception as ex:event_rows.append({'marketId':market_id,'atMs':now,'error':str(ex)})
   if ns!=state['name']:state['name']=ns;state['last']=now
   ci=r3.control_intent(ns,f);intent={'state':ns,**ci,'pb':pb,'pc':pc,'unc':unc,'contextType':p['contextType'],'cooperationMode':mode,'buildOn':bon,'crossOn':con,'mgmtReason':mgmt_reason,'pFormationPath':ppath,'mgmtOverride':mgmt_override};state['intent']=intent;state['eval_t']=now;return intent
  def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
   it=eval_state(now);weak=it.get('weakSide');strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;action='PASS'
   if bool(it.get('mgmtOverride')) and strong and side==strong and state.get('mgmt_consumed_t')!=now:
    state['mgmt_consumed_t']=now;action='MGMT_ONE_SHOT_WEAK_PRIORITY';made=False
    if weak:made=orig_add(weak,now,snapshot_ns,decision_id,'R4_BOUNDED_MGMT_WEAK_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
    if made:parents.append({'t':now,'side':weak,'role':'MAKER','shares':18.0})
    control_events.append({'atMs':now,'attemptSide':side,'action':action,'substituteWeak':weak,'made':bool(made),**it});return bool(made)
   if it['state']=='CROSSING_PROTECTION' and strong and side==strong:
    action='VETO_STRONG_CROSS';made=False
    if weak:made=orig_add(weak,now,snapshot_ns,decision_id,'R3_CROSSING_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
    if made:parents.append({'t':now,'side':weak,'role':'MAKER','shares':18.0})
    control_events.append({'atMs':now,'attemptSide':side,'action':action,'substituteWeak':weak,'made':bool(made),**it});return bool(made)
   if it['state']=='BUILD_WEAK_SIDE' and strong and side==strong and rng.random()>float(it['strongSideMakerScale']):
    action='VETO_STRONG_BUILD';made=False
    if weak:made=orig_add(weak,now,snapshot_ns,decision_id,'R3_BUILD_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
    if made:parents.append({'t':now,'side':weak,'role':'MAKER','shares':18.0})
    control_events.append({'atMs':now,'attemptSide':side,'action':action,'substituteWeak':weak,'made':bool(made),**it});return bool(made)
   made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
   if made:parents.append({'t':now,'side':side,'role':'MAKER','shares':18.0})
   control_events.append({'atMs':now,'attemptSide':side,'action':action,'made':bool(made),**it});return made
  c._add_order=add;return c
 base.new_controller=custom_new
 try:rep=base.run_market(int(market_id),entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200)
 finally:base.new_controller=orig_new
 rep['mgmtStats']=stats;return rep

def main():
 mids=[int(x) for x in sys.argv[1:]] if len(sys.argv)>1 else [1736560];rows=[];events=[]
 for mid in mids:
  r3rep=r3ctl.run_market(mid,True);off=run_overlay(mid,False,[]);A=summary(r3rep);O=summary(off);equiv=A==O
  row={'marketId':mid,'R3':A,'overlayOff':O,'seedEquivalent':equiv}
  if equiv:
   on=run_overlay(mid,True,events);B=summary(on);row.update({'R4_EXACT':B,'deltaFloor':B['worstCaseFloor']-A['worstCaseFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],**on['mgmtStats']})
  rows.append(row);print(json.dumps(row),flush=True)
 pd.DataFrame(events).to_csv(EV,index=False);out={'version':'R4_BOUNDED_ONE_PARENT_GUARD_V1','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','rule':'Exact R3 context-control copy + management overlay only after native state decision. Management OFF must be seed-equivalent before ON is scored. Scale-invariant Formation Path p>=0.5 enters latch until floor>=0; positive floor blocks added forced crossing; native R3 state transitions remain authoritative. No threshold sweep.','markets':rows,'allSeedEquivalent':all(x['seedEquivalent'] for x in rows)};OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'allSeedEquivalent':out['allSeedEquivalent'],'scored':sum('R4_EXACT' in x for x in rows)},indent=2))
if __name__=='__main__':main()
