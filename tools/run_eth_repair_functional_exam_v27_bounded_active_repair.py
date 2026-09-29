from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
except ImportError:v23=sib('v23active','run_eth_repair_functional_exam_v23_queue_progress_lease.py')
try:
 from tools import bridge_eth_v23_repair_taker_hazard_v1 as br
except ImportError:br=sib('bridgeactive','bridge_eth_v23_repair_taker_hazard_v1.py')
try:
 from tools import run_eth_repair_functional_exam_v1 as ex1
except ImportError:ex1=sib('ex1active','run_eth_repair_functional_exam_v1.py')
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9;ACTIVE_TAKE_WINDOW_MS=500;HAZARD_EVAL_MIN_GAP_MS=1000

class BoundedActiveRepairSim(v23.QueueProgressLeaseSim):
 def __init__(self,*a,hazard_art=None,sizing_art=None,**kw):
  super().__init__(*a,**kw)
  self.hazardArt=hazard_art;self.sizingArt=sizing_art;self.hazardModel=hazard_art['model'];self.sizingModel=sizing_art['model']
  assert list(hazard_art['features'])==list(br.FEATURES);assert list(sizing_art['features'])==list(br.FEATURES)
  self.activeArm=None;self.activeUsed=False;self.activeOrderKey=None;self.activeSubmitAt=None;self.activeCancelAt=None;self.activeGapAtSubmit=0.0;self.activeSubmitQty=0.0;self.activeFillQty=0.0;self.activeFillPrice=None;self.activeFillSeen=0.0
  self.activeHazardScores=[];self.activeSizingScores=[];self.activeHandoffCancelRequests=0;self.activeHandoffWaitTicks=0;self.activeSubmitCount=0;self.activeFillEvents=0;self.activeTruthRoleBlocks=0;self.activePassiveOwnedAtSubmit=[];self.activeTrace=[];self.lastActiveHazardEvalAt=None
 def process(self,t):
  super().process(t)
  if self.activeOrderKey is not None:
   e=self.carrierLedger.get(self.activeOrderKey);cur=float(e.get('actualFilled',0.0)) if e else 0.0;inc=max(0.0,cur-self.activeFillSeen)
   if inc>EPS:
    o=self.orders.get(self.activeOrderKey);s=self.snap(o) if o else {};px=v1.fill_price(o['side'],s,o['price']) if o else None;self.activeFillQty+=inc;self.activeFillSeen=cur;self.activeFillEvents+=1
    if px is not None:self.activeFillPrice=float(px)
    self.activeTrace.append({'event':'ACTIVE_FILL','t':int(t),'qty':float(inc),'cumQty':float(cur),'price':None if px is None else float(px)})
 def cancel_expired(self,t):
  super().cancel_expired(t)
  if self.activeOrderKey is None or self.activeSubmitAt is None:return
  o=self.orders.get(self.activeOrderKey)
  if o is None:return
  try:s=self.snap(o)
  except Exception:return
  if v1.live(s.get('status')) and int(t)-int(self.activeSubmitAt)>=ACTIVE_TAKE_WINDOW_MS and self.activeOrderKey not in self.cancelRequestedAt:
   if self._cancel_key(t,self.activeOrderKey):self.activeCancelAt=int(t);self.activeTrace.append({'event':'ACTIVE_CANCEL_REMAINDER','t':int(t),'ageMs':int(t)-int(self.activeSubmitAt)})
 def _runtime_vector(self,t,end):
  # Before the first active submit every fill is Maker, so the frozen bridge reconstruction is exact for this first-active-intervention smoke.
  pseudo={'causal':{'submitTrace':self.submitTrace,'fillTrace':self.fillTrace}};events=br.annotate_fills(pseudo);inv=br.make_inventory(events,t);ph=br.parent_history(events,t);st=(int(t),dict(self.book.get('bids',{})),dict(self.book.get('asks',{})))
  z=br.feature_row(inv,ph,int(t),int(end),st);return z
 def _score(self,t,end):
  z=self._runtime_vector(t,end)
  if z is None:return None
  x,vals=z;h=float(self.hazardModel.predict_proba(x.reshape(1,-1))[0,1]);sf=float(np.clip(self.sizingModel.predict(x.reshape(1,-1))[0],0,1));return h,sf,vals
 def _weak(self):
  ai=self.auth_inv();return ('UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None),ai
 def _maybe_arm(self,t,qv,end):
  if self.activeUsed or self.activeArm is not None:return False
  rb=self.reserveBuilder
  if rb is None or rb.get('firstFillAt') is None or int(t)<=int(rb['firstFillAt']) or self.repairParent is None:return False
  if self.lastActiveHazardEvalAt is not None and int(t)-int(self.lastActiveHazardEvalAt)<HAZARD_EVAL_MIN_GAP_MS:return False
  self.lastActiveHazardEvalAt=int(t);z=self._score(t,end)
  if z is None:return False
  h,sf,_=z;self.activeHazardScores.append(h)
  if h<.5:return False
  weak,ai=self._weak()
  if weak is None or self.repairParent.get('side')!=weak:return False
  self.activeArm={'armedAt':int(t),'parentId':int(self.repairParent['id']),'side':weak,'hazardAtArm':h,'sizeAtArm':sf,'firstFillAt':int(rb['firstFillAt'])}
  cancelled=0
  for key,e,rem in list(self.lane_unresolved('REPAIR')):
   if self._cancel_key(t,key):cancelled+=1
  self.activeHandoffCancelRequests+=cancelled;self.activeTrace.append({'event':'ACTIVE_ARM','t':int(t),'side':weak,'hazard':h,'cancelRequests':cancelled})
  return True
 def _passive_repair_owned(self):return sum(float(rem) for _,_,rem in self.lane_unresolved('REPAIR'))
 def _active_live(self):
  if self.activeOrderKey is None:return False
  o=self.orders.get(self.activeOrderKey)
  if o is None:return False
  try:return v1.live(self.snap(o).get('status'))
  except Exception:return False
 def _submit_active_order(self,t,side,p,q,oid,pid,gap,hazard,sizefrac):
  if q<=EPS:return False
  passive=self._passive_repair_owned();self.activePassiveOwnedAtSubmit.append(float(passive))
  if passive>EPS:return False
  observed_role=ex1.role_from_inv(self.inv,side);truth_role=ex1.role_from_inv(self.truthInv,side)
  if truth_role!='REPAIR':self.activeTruthRoleBlocks+=1;return False
  if self.reserved_authoritative(side)>EPS:return False
  n=self.n;self.n+=1;native_side,native_price=v1.ex.native_order(side,float(p))
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
  except Exception:return False
  key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':float(p),'qty':float(q),'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'REPAIR','objective_id':oid,'execution_role':'TAKER_ACTIVE'};self.placeHist.append((int(t),side,float(q),float(p)));self.submits+=1;self.localPending[side][key]={'remaining':float(q),'submitted':int(t)}
  self.submitRoleObserved[key]=observed_role;self.submitRoleTruth[key]=truth_role;self.submitRoleAuthorized[key]='REPAIR';
  if observed_role!=truth_role:self.acceptedSubmitWithObservedTruthRoleMismatch+=1
  self.carrierLedger[key]={'key':key,'side':side,'objectiveId':oid,'objectiveRole':'REPAIR','submittedQty':float(q),'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':pid,'lane':'ACTIVE_REPAIR'}
  self.submitTrace.append({'t':int(t),'side':side,'qty':float(q),'price':float(p),'pendingRole':'REPAIR','parentId':pid,'lane':'ACTIVE_REPAIR'})
  self.activeUsed=True;self.activeOrderKey=key;self.activeSubmitAt=int(t);self.activeGapAtSubmit=float(gap);self.activeSubmitQty=float(q);self.activeSubmitCount+=1;self.activeTrace.append({'event':'ACTIVE_SUBMIT','t':int(t),'side':side,'price':float(p),'qty':float(q),'gap':float(gap),'hazard':float(hazard),'sizeFraction':float(sizefrac),'submitRc':rc,'passiveOwnedAtSubmit':float(passive)})
  return True
 def _try_submit_active(self,t,qv,end):
  a=self.activeArm
  if a is None or self.activeUsed:return False
  if self.repairParent is None or int(self.repairParent['id'])!=int(a['parentId']):self.activeTrace.append({'event':'ACTIVE_ARM_ABORT_PARENT','t':int(t)});self.activeArm=None;return False
  weak,ai=self._weak()
  if weak is None or weak!=a['side']:
   self.activeTrace.append({'event':'ACTIVE_ARM_ABORT_ROLE','t':int(t)});self.activeArm=None;return False
  passive=self._passive_repair_owned()
  if passive>EPS:self.activeHandoffWaitTicks+=1;return False
  z=self._score(t,end)
  if z is None:return False
  h,sf,_=z;self.activeHazardScores.append(h);self.activeSizingScores.append(sf)
  if h<.5:
   self.activeTrace.append({'event':'ACTIVE_ARM_ABORT_HAZARD','t':int(t),'hazard':h});self.activeArm=None;return False
  gap=max(0.0,float(ai['DOWN']-ai['UP']) if weak=='UP' else float(ai['UP']-ai['DOWN']))
  if gap<=EPS:self.activeArm=None;return False
  ask=float(qv[weak]['ask']);depth=float(qv['ad'] if weak=='UP' else qv['bd']);q=min(float(sf)*gap,gap,depth)
  oid=self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None;pid=int(self.repairParent['id'])
  ok=self._submit_active_order(t,weak,ask,q,oid,pid,gap,h,sf)
  if ok:self.activeArm=None
  return ok
 def run_exam_v27(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1]);global_active=pa>=models['actionTh'];qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));qty=min(qty,12.)
   self.repairSupervisorTicks+=1;self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
   ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;cs=self.capState.snapshot(t,end)
   if weak is not None and self.repairParent is None:
    precap=self.capability.predict(cs);b0=self.repairParentBirths;self._maybe_birth_parent(t,weak,precap)
    if self.repairParentBirths>b0 and not global_active:self.repairParentBirthsBelowActionGate+=1
   if self.repairParent is None or weak is None:self._clear_anchor()
   # First active-intervention layer: score only while deterministic Reserve Repair responsibility exists.
   if weak is not None and self.repairParent is not None and self.reserveBuilder is not None and self.reserveBuilder.get('firstFillAt') is not None and not self.activeUsed:
    self._maybe_arm(t,qv,end)
    if self.activeArm is not None:self._try_submit_active(t,qv,end)
   if self.activeArm is not None or self._active_live():
    # During Maker->Taker handoff or active ownership, do not create another Repair carrier.
    continue
   roles=set();submitted=0
   if weak is not None and self.repairParent is not None:
    if self.lane_unresolved('REPAIR'):self._clear_anchor()
    else:
     due=self._ensure_anchor(t,self.capState.snapshot(t,end))
     if due is not None and t<due:self.timingWaitTicks+=1;self.timingParentPersistsDuringWaitTicks+=1
     else:
      if self.anchorWasWait:self.timingDeadlineFires+=1;self.timingWaitToNowTransitions+=1;self.anchorWasWait=False
      self.repairProposalTicks+=1;p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));ok=self._submit_authorized(t,qv,z,roles);submitted+=int(ok)
      if ok:self.timingRepairSubmits+=1;self._clear_anchor()
   elif global_active and self.reserveBuilder is None and self.outstanding_total()<=EPS:self._start_reserve_builder(t,qv)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self.process(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());floor=min(self.inv.values())-self.cost
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'floor':floor,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'unresolvedCarrierQty':self.outstanding_total(),'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'reserveFirstLegSubmit':self.reserveFirstLegSubmit,'reserveFirstLegActualFill':self.reserveFirstLegActualFill,'reserveCycleCompletion':self.reserveCycleCompletion,'reserveCycleFloorGainTotal':sum(self.reserveCycleFloorGain),'reserveCyclePairSumMax':max(self.reserveCyclePairSums,default=0.),'ceilingRepairSubmits':self.ceilingRepairSubmits,'deterministicReserveObligationActivations':self.deterministicReserveObligationActivations,'queueLeaseTracked':self.queueLeaseTracked,'queueProgressEvents':self.queueProgressEvents,'queueLeaseExtensions':self.queueLeaseExtensions,'activeSubmitCount':self.activeSubmitCount,'activeFillEvents':self.activeFillEvents,'activeFillQty':self.activeFillQty,'activeFillPrice':self.activeFillPrice,'activeSubmitQty':self.activeSubmitQty,'activeGapAtSubmit':self.activeGapAtSubmit,'activeCancelAt':self.activeCancelAt,'activeHandoffCancelRequests':self.activeHandoffCancelRequests,'activeHandoffWaitTicks':self.activeHandoffWaitTicks,'activeTruthRoleBlocks':self.activeTruthRoleBlocks,'activePassiveOwnedAtSubmitMax':max(self.activePassiveOwnedAtSubmit,default=0.0),'activeHazardMax':max(self.activeHazardScores,default=0.0),'activeSizingAtSubmit':next((x.get('sizeFraction') for x in self.activeTrace if x.get('event')=='ACTIVE_SUBMIT'),None),'activeTrace':self.activeTrace[:40]}

def load_runtime(a):return v23.load_runtime(a)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--hazard-model',required=True);ap.add_argument('--sizing-model',required=True);ap.add_argument('--market-ids',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v27_active_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);haz=joblib.load(a.hazard_model);siz=joblib.load(a.sizing_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];pair={}
   sim0=v23.QueueProgressLeaseSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r0=sim0.run_exam_v23(models,cr['winner']);c0=sim0.causal()
   finally:sim0.close()
   sim=BoundedActiveRepairSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,hazard_art=haz,sizing_art=siz)
   try:r=sim.run_exam_v27(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   pair={'V23':{'functional':r0,'causal':c0},'V27':{'functional':r,'causal':c}};rows.append({'marketId':mid,**pair});print(json.dumps({'marketId':mid,'v23Cycles':r0['reserveCycleCompletion'],'v27Cycles':r['reserveCycleCompletion'],'activeSubmit':r['activeSubmitCount'],'activeFillQty':r['activeFillQty'],'v23AbsNet':r0['absNet'],'v27AbsNet':r['absNet'],'v23Pnl':r0['pnlDiagnosticOnly'],'v27Pnl':r['pnlDiagnosticOnly']},ensure_ascii=False),flush=True)
  def sm(ver,k):return sum(float(x[ver]['functional'].get(k) or 0) for x in rows)
  active_sub=sm('V27','activeSubmitCount');active_fill=sm('V27','activeFillQty');maxpass=max((float(x['V27']['functional'].get('activePassiveOwnedAtSubmitMax') or 0) for x in rows),default=0.);maxqover=max((max(0.0,float(x['V27']['functional'].get('activeSubmitQty') or 0)-float(x['V27']['functional'].get('activeGapAtSubmit') or 0)) for x in rows),default=0.);maxsub=max((int(x['V27']['functional'].get('activeSubmitCount') or 0) for x in rows),default=0)
  active_prefill=sum(1 for x in rows for e in x['V27']['functional'].get('activeTrace',[]) if e.get('event')=='ACTIVE_SUBMIT' and x['V27']['causal'].get('firstActualFill') and int(e['t'])<=int(x['V27']['causal']['firstActualFill']['t']))
  safety={'activeSubmitAfterFirstActualFill':active_prefill==0,'zeroPassiveRepairOwnershipAtActiveSubmit':maxpass<=EPS,'activeQtyNeverExceedsResidualGap':maxqover<=1e-8,'zeroActiveTruthRoleBlocks':sm('V27','activeTruthRoleBlocks')==0,'zeroAuthorizedTruthMismatch':sm('V27','authorizedSubmitWithTruthRoleMismatch')==0,'zeroOverOwnedRepairSubmit':sm('V27','overOwnedSubmitViolations')==0,'zeroUnresolvedCarrierAtTerminal':abs(sm('V27','unresolvedCarrierQty'))<=1e-9,'atMostOneActiveSubmitPerMarket':maxsub<=1}
  behavior={'activeSubmitExercised':active_sub>0,'activeActualFillExercised':active_fill>0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V27_BOUNDED_ACTIVE_REPAIR','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':{'activeSubmits':active_sub,'activeFillQty':active_fill,'v23Cycles':sm('V23','reserveCycleCompletion'),'v27Cycles':sm('V27','reserveCycleCompletion'),'v23FloorGain':sm('V23','reserveCycleFloorGainTotal'),'v27FloorGain':sm('V27','reserveCycleFloorGainTotal'),'v23PnlDiagnostic':sm('V23','pnlDiagnosticOnly'),'v27PnlDiagnostic':sm('V27','pnlDiagnosticOnly'),'v23BuyNotional':sm('V23','buyNotional'),'v27BuyNotional':sm('V27','buyNotional')},'safetyGates':safety,'behaviorGates':behavior,'smokeVerified':all(safety.values()) and all(behavior.values()),'rows':rows,'boundary':['consumed realistic HFT only','V23 Maker primitive unchanged outside Maker->Taker handoff','frozen ETH 1s Repair-Taker hazard p>=0.5, no threshold sweep','frozen ETH bounded Repair sizing model','one active intervention max per market','cancel passive Repair ownership before active submit','marketable GTC limit at contemporaneous best ask; unfilled remainder cancel after fixed 500ms control cycle','active quantity never intentionally exceeds current residual Repair gap','ADD authority absent','PnL diagnostic only']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':out['aggregate'],'safety':safety,'behavior':behavior},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
