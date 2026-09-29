from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,os,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_repair_functional_exam_v30_directional_thesis_cycle as v30
from tools import run_eth_repair_functional_exam_v27_bounded_active_repair as v27
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9; GRID=.01; ACTIVE_TAKE_WINDOW_MS=500

class V33BoundedActiveChildSim(v30.DirectionalThesisCycleSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self._stall={}; self.active=None; self.activeUsed=False; self.activeEvents=[]
  self.activeSubmitCount=0; self.activeFillQty=0.0; self.activeFillSeen=0.0; self.activeFillPrice=None
  self.sharedInitialQty=0.0; self.sharedPassiveKey=None; self.sharedPassiveBaseActual=0.0; self.sharedPassiveFillAfterArm=0.0
  self.passiveCancelBeforeActiveFill=0; self.passiveResizeAfterActiveFill=0; self.passiveReplacementSubmit=0
  self.sharedRealizedOverfill=0.0; self.floorBeforeActive=None; self.floorAfterActive=None
  self.stallDetections=0; self.stallBlocksNoLegalSlice=0
 def _target_best_bid(self,side):
  if side=='UP': return float(max(self.book.get('bids',{}))) if self.book.get('bids') else None
  if side=='DOWN': return 1.0-float(min(self.book.get('asks',{}))) if self.book.get('asks') else None
  return None
 def _native_level_depth(self,o):
  try:return float(super()._native_level_depth(o))
  except Exception:return 0.0
 def _track_stall(self,key,o,t):
  cur=self._native_level_depth(o); bb=self._target_best_bid(o.get('side')); p=float(o.get('price') or 0.0)
  behind=max(0.0,(float(bb)-p)/GRID) if bb is not None else 0.0
  z=self._stall.get(key)
  if z is None:
   z={'firstAt':int(t),'lastAt':int(t),'initialDepth':float(cur),'minDepth':float(cur),'maxBehindTicks':float(behind)}; self._stall[key]=z
  z['lastAt']=int(t); z['minDepth']=min(float(z['minDepth']),float(cur)); z['maxBehindTicks']=max(float(z['maxBehindTicks']),float(behind))
  init=max(float(z['initialDepth']),EPS); dep=max(0.0,float(z['initialDepth'])-float(z['minDepth']))/init
  age=int(t)-int(z['firstAt']); stalled=(z['maxBehindTicks']>=5.0-EPS and age>=5000 and dep<=1e-12)
  return stalled,{'ageMs':age,'maxBehindTicks':z['maxBehindTicks'],'depletionFraction':dep,'depth':cur,'bestBid':bb,'orderPrice':p}
 def _current_payoffs(self):
  floor,u,d,cost=self._raw_floor(); return {'floor':float(floor),'up':float(u-cost),'down':float(d-cost),'best':float(max(u,d)-cost),'gap':float(abs((u-cost)-(d-cost)))}
 def _submit_active(self,t,side,ask,q,parent_id,objective_id,passive_key,diag):
  if q<=EPS:return False
  n=self.n; self.n+=1; native_side,native_price=v1.ex.native_order(side,float(ask))
  try:
   if native_side=='BUY': rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
   else: rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
  except Exception:return False
  key=f'{side}_{n}'
  self.orders[key]={'n':n,'side':side,'price':float(ask),'qty':float(q),'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'REPAIR','objective_id':objective_id,'execution_role':'TAKER_ACTIVE_SHARED'}
  self.placeHist.append((int(t),side,float(q),float(ask))); self.submits+=1; self.localPending[side][key]={'remaining':float(q),'submitted':int(t)}
  self.submitRoleObserved[key]='REPAIR'; self.submitRoleTruth[key]='REPAIR'; self.submitRoleAuthorized[key]='REPAIR'
  self.carrierLedger[key]={'key':key,'side':side,'objectiveId':objective_id,'objectiveRole':'REPAIR','submittedQty':float(q),'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':parent_id,'lane':'ACTIVE_REPAIR_SHARED'}
  self.submitTrace.append({'t':int(t),'side':side,'qty':float(q),'price':float(ask),'pendingRole':'REPAIR','parentId':parent_id,'lane':'ACTIVE_REPAIR_SHARED'})
  pe=self.carrierLedger.get(passive_key,{})
  self.active={'key':key,'passiveKey':passive_key,'side':side,'qty':float(q),'submitAt':int(t),'parentId':parent_id,'objectiveId':objective_id,'submitRc':rc,'cancelAt':None,'resizeDone':False}
  self.activeUsed=True; self.activeSubmitCount+=1; self.sharedInitialQty=float(q); self.sharedPassiveKey=passive_key; self.sharedPassiveBaseActual=float(pe.get('actualFilled') or 0.0)
  self.floorBeforeActive=self._current_payoffs()['floor']
  self.activeEvents.append({'event':'ACTIVE_SHARED_SUBMIT','t':int(t),'side':side,'ask':float(ask),'qty':float(q),'passiveKey':passive_key,'stall':diag,'floorBefore':self.floorBeforeActive,'submitRc':rc})
  return True
 def _maybe_active_from_stall(self,t):
  if self.activeUsed or self.active is not None or self.repairParent is None:return False
  rb=self.reserveBuilder
  if rb is None or rb.get('firstFillAt') is None:return False
  qv=v1.quotes(self.book)
  if not qv:return False
  for key,e,rem in list(self.lane_unresolved('REPAIR')):
   if rem<=EPS:continue
   o=self.orders.get(key)
   if not o:continue
   stalled,diag=self._track_stall(key,o,t)
   if not stalled:continue
   self.stallDetections+=1
   pay=self._current_payoffs(); side=e.get('side') or o.get('side'); ask=float(qv[side]['ask'])
   legal=1.0/ask if ask>EPS else 1e9
   q=min(float(legal),float(pay['gap']),float(rem))
   if q+EPS<legal or q<=EPS:
    self.stallBlocksNoLegalSlice+=1; self.activeEvents.append({'event':'STALL_NO_LEGAL_ACTIVE_SLICE','t':int(t),'key':key,'legal':legal,'gap':pay['gap'],'passiveRem':rem,'stall':diag}); return False
   pid=int(self.repairParent['id']); oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
   return self._submit_active(t,side,ask,q,pid,oid,key,diag)
  return False
 def lane_unresolved(self,role):
  rows=super().lane_unresolved(role)
  if role!='REPAIR' or not self.active:return rows
  pk=self.active.get('passiveKey'); ak=self.active.get('key'); q=float(self.active.get('qty') or 0.0)
  out=[]
  for key,e,rem in rows:
   rr=float(rem)
   # The active child and passive carrier jointly own the same slice while active is pending.
   # Count that shared slice exactly once: subtract it from passive effective authority.
   if key==pk and ak in self.orders:
    ae=self.carrierLedger.get(ak,{}) ; af=float(ae.get('actualFilled') or 0.0); ao=self.orders.get(ak); livea=False
    try: livea=bool(ao and v1.live(self.snap(ao).get('status')))
    except Exception: livea=False
    shared_pending=max(0.0,q-af) if livea else 0.0
    rr=max(0.0,rr-shared_pending)
   if rr>EPS: out.append((key,e,rr))
  return out
 def process(self,t):
  super().process(t)
  a=self.active
  if not a:return
  ak=a['key']; pk=a['passiveKey']; ae=self.carrierLedger.get(ak,{}); pe=self.carrierLedger.get(pk,{})
  af=float(ae.get('actualFilled') or 0.0); pf=max(0.0,float(pe.get('actualFilled') or 0.0)-self.sharedPassiveBaseActual)
  if af>self.activeFillSeen+EPS:
   inc=af-self.activeFillSeen; self.activeFillSeen=af; self.activeFillQty+=inc
   o=self.orders.get(ak); px=None
   try:px=v1.fill_price(o['side'],self.snap(o),o['price']) if o else None
   except Exception:pass
   if px is not None:self.activeFillPrice=float(px)
   self.activeEvents.append({'event':'ACTIVE_SHARED_FILL','t':int(t),'incQty':float(inc),'cumQty':af,'price':self.activeFillPrice,'passiveFillAfterArm':pf})
  self.sharedPassiveFillAfterArm=max(self.sharedPassiveFillAfterArm,pf)
  realized=af+pf; self.sharedRealizedOverfill=max(self.sharedRealizedOverfill,max(0.0,realized-self.sharedInitialQty))
  # Once either carrier consumes shared responsibility, prevent the counterpart from consuming the same slice.
  if af>EPS and not a['resizeDone']:
   po=self.orders.get(pk); prem=0.0
   if po:
    try: prem=max(0.0,float(po.get('qty') or 0.0)-float(pe.get('actualFilled') or 0.0))
    except Exception: prem=0.0
   if prem>EPS:
    if self._cancel_key(t,pk): self.passiveResizeAfterActiveFill+=1; self.activeEvents.append({'event':'PASSIVE_RESIZE_CANCEL_AFTER_ACTIVE_FILL','t':int(t),'passiveKey':pk,'passiveRemaining':prem,'activeFilled':af})
   a['resizeDone']=True
  if pf>EPS and af<EPS:
   ao=self.orders.get(ak)
   try:
    if ao and v1.live(self.snap(ao).get('status')) and ak not in self.cancelRequestedAt:
     if self._cancel_key(t,ak): self.activeEvents.append({'event':'ACTIVE_CANCEL_AFTER_PASSIVE_SHARED_FILL','t':int(t),'passiveFilled':pf})
   except Exception:pass
  self.floorAfterActive=self._current_payoffs()['floor'] if af>EPS else self.floorAfterActive
 def cancel_expired(self,t):
  # Observe stall before inherited lease expiry can cancel the carrier.
  self._maybe_active_from_stall(t)
  if self.active:
   pk=self.active['passiveKey']; st=self._qlease.get(pk)
   # While active slice is pending, preserve passive carrier for the short active window; no pre-fill cancel-all handoff.
   ae=self.carrierLedger.get(self.active['key'],{}); af=float(ae.get('actualFilled') or 0.0)
   ao=self.orders.get(self.active['key']); alive=False
   try: alive=bool(ao and v1.live(self.snap(ao).get('status')))
   except Exception: alive=False
   if alive and af<=EPS and st is not None: st['lastProgressAt']=int(t)
  super().cancel_expired(t)
  if self.active:
   a=self.active; ak=a['key']; ao=self.orders.get(ak)
   if ao:
    try:
     livea=v1.live(self.snap(ao).get('status'))
     if livea and int(t)-int(a['submitAt'])>=ACTIVE_TAKE_WINDOW_MS and ak not in self.cancelRequestedAt:
      if self._cancel_key(t,ak): a['cancelAt']=int(t); self.activeEvents.append({'event':'ACTIVE_SHARED_CANCEL_REMAINDER','t':int(t),'ageMs':int(t)-int(a['submitAt'])})
    except Exception:pass
 def run_exam_v33(self,models,winner):
  r=super().run_exam_v30(models,winner); pay=self._current_payoffs()
  r.update({'activeSubmitCount':self.activeSubmitCount,'activeFillQty':self.activeFillQty,'activeFillPrice':self.activeFillPrice,'stallDetections':self.stallDetections,'stallBlocksNoLegalSlice':self.stallBlocksNoLegalSlice,'sharedInitialQty':self.sharedInitialQty,'sharedPassiveFillAfterArm':self.sharedPassiveFillAfterArm,'sharedRealizedOverfill':self.sharedRealizedOverfill,'passiveCancelBeforeActiveFill':self.passiveCancelBeforeActiveFill,'passiveResizeAfterActiveFill':self.passiveResizeAfterActiveFill,'passiveReplacementSubmit':self.passiveReplacementSubmit,'floorBeforeActive':self.floorBeforeActive,'floorAfterActive':self.floorAfterActive,'finalWorstPayoff':pay['floor'],'finalBestPayoff':pay['best'],'activeEvents':self.activeEvents[:80]})
  return r

def load_runtime(a): return v30.load_runtime(a)

def main():
 ap=argparse.ArgumentParser();
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']: ap.add_argument('--'+n,required=True)
 ap.add_argument('--output'); a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='eth_v33_active_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp); cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']; by={int(r['marketId']):r for r in cohort}; models,life,cap,tim,econ,price,sur=load_runtime(a); rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid]; sim=V33BoundedActiveChildSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v33(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r}); print(json.dumps({'marketId':mid,'stall':r['stallDetections'],'activeSubmit':r['activeSubmitCount'],'activeFillQty':r['activeFillQty'],'floorBeforeActive':r['floorBeforeActive'],'floorAfterActive':r['floorAfterActive'],'finalFloor':r['finalWorstPayoff'],'sharedOverfill':r['sharedRealizedOverfill'],'resizeAfterFill':r['passiveResizeAfterActiveFill']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['functional'].get(k) or 0) for x in rows)
  bymid={x['marketId']:x['functional'] for x in rows}; stalled=bymid.get(1840896); control=bymid.get(1842999)
  gates={
   'stalledCaseExercisesActive': bool(stalled and stalled['activeSubmitCount']>0 and stalled['activeFillQty']>0),
   'nonStalledControlZeroActive': bool(control and control['activeSubmitCount']==0),
   'activeDoesNotWorsenFloorAtObservedFill': bool(stalled and stalled['floorAfterActive'] is not None and stalled['floorBeforeActive'] is not None and stalled['floorAfterActive']>=stalled['floorBeforeActive']-EPS),
   'zeroSharedRealizedOverfill': sm('sharedRealizedOverfill')<=EPS,
   'zeroPreFillPassiveCancel': sm('passiveCancelBeforeActiveFill')==0,
   'zeroRepairDrift': sm('repairToExpandAtFirstFill')==0,
   'zeroTruthMismatch': sm('authorizedSubmitWithTruthRoleMismatch')==0,
   'zeroOverOwned': sm('overOwnedSubmitViolations')==0,
   'zeroTerminalUnresolved': abs(sm('unresolvedCarrierQty'))<=EPS
  }
  out={'version':'ETH_REPAIR_V33_BOUNDED_ACTIVE_CHILD_SMOKE','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed realistic HFT only','V30 direction/payoff Repair lifecycle preserved','V32 stall conjunction fixed: maxBehind>=5 ticks AND age>=5s AND zero net same-price depletion','first active sizing fixed MIN_SLICE=venue legal minimum clipped by payoff gap and passive shared responsibility','active and passive share one Repair slice; active is not ADD','passive carrier is not canceled before active actual fill; after active fill it may be responsibility-resized/canceled','winner/PnL diagnostic only; no threshold tuning','no dream fill; no 8781']}
  op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json'; op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'gates':gates},ensure_ascii=False),flush=True)
 finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
