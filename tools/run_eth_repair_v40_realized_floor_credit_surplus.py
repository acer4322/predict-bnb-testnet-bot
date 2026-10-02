from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v38=sib('eth_v38_for_v40','run_eth_repair_v38_incremental_repair_parallel_surplus_causal.py')
v1=v38.v37.v1;EPS=1e-9;TERMINAL={'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}

class V40FloorCredit(v38.V38IncrementalRecycle):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v40RepairSeen={};self.v40Credit={};self.v40Anchor={};self.v40Live={};self.v40Events=[]
  self.v40Minted=0.0;self.v40Reserved=0.0;self.v40Spent=0.0;self.v40Returned=0.0;self.v40Expired=0.0
  self.v40Submits=0;self.v40FillQty=0.0;self.v40Overspend=0.0;self.v40CrossParent=0;self.v40LateFill=0.0;self.v40UnsafeSubmit=0;self.v40UnsafeFill=0
  self.v40HeldCredit=0;self.v40HeldProjection=0;self.v40HeldLegal=0
 # Disable V37 equal-share recycle machinery; V38 partial-progress instrumentation remains active.
 def _scan_passive_repair_credit(self,t):return None
 def _maybe_surplus_recycle(self,t):return False
 def _reconcile_surplus_orders(self,t):return None
 def _expire_stale_credit(self,t):return None
 def _live_surplus_exists(self):return bool(self.v40Live)
 def _floor(self):return float(self._raw_floor()[0])
 def _status(self,key):
  o=self.orders.get(key)
  if not o:return None
  try:return self.snap(o).get('status')
  except Exception:return None
 def _fill_price(self,key,e):
  o=self.orders.get(key)
  if not o:return float(e.get('price') or 0.0)
  try:return float(v1.fill_price(o['side'],self.snap(o),float(o.get('price') or 0.0)))
  except Exception:return float(o.get('price') or e.get('price') or 0.0)
 def _scan_floor_credit(self,t):
  cur=int(self.repairParent.get('id')) if self.repairParent is not None else None
  for key,e in list(getattr(self,'carrierLedger',{}).items()):
   if e.get('objectiveRole')!='REPAIR' or str(e.get('lane') or '').startswith('ACTIVE_'):continue
   pid=e.get('parentId')
   if pid is None:continue
   try:pid=int(pid)
   except Exception:continue
   now=float(e.get('actualFilled') or 0.0);old=float(self.v40RepairSeen.get(key,0.0));self.v40RepairSeen[key]=max(old,now)
   if now<=old+EPS or cur is None or pid!=cur:continue
   inc=now-old;px=self._fill_price(key,e)
   if not (0.0<px<1.0):continue
   gain=inc*(1.0-px);floor_after=self._floor();anchor=floor_after-gain
   if pid not in self.v40Anchor:self.v40Anchor[pid]=anchor
   self.v40Credit[pid]=float(self.v40Credit.get(pid,0.0))+gain;self.v40Minted+=gain
   self.v40Events.append({'event':'V40_FLOOR_CREDIT_MINT','t':int(t),'parentId':pid,'repairKey':key,'repairQty':inc,'repairPrice':px,'creditValue':gain,'floorAfter':floor_after,'parentAnchorFloor':self.v40Anchor[pid]})
 def _expire_credit(self,t):
  cur=int(self.repairParent.get('id')) if self.repairParent is not None else None
  for pid in list(self.v40Credit):
   if pid==cur:continue
   val=float(self.v40Credit.get(pid,0.0))
   if val>EPS:self.v40Expired+=val;self.v40Events.append({'event':'V40_CREDIT_EXPIRE_PARENT_CHANGE','t':int(t),'parentId':pid,'value':val,'currentParentId':cur})
   self.v40Credit[pid]=0.0
 def _submit_surplus(self,t,pid,side,price,qty,reserve,anchor,projected):
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='SURPLUS_FLOOR_CREDIT'
  ok=self.submit(t,side,price,qty)
  if not ok:return False
  key=f'{side}_{n0}'
  if key in self.carrierLedger:
   self.carrierLedger[key]['parentId']=pid;self.carrierLedger[key]['lane']='SURPLUS_FLOOR_CREDIT';self.carrierLedger[key]['objectiveRole']='EXPAND';self.carrierLedger[key]['objectiveId']=oid
  self.v40Credit[pid]=max(0.0,float(self.v40Credit.get(pid,0.0))-reserve);self.v40Reserved+=reserve;self.v40Submits+=1
  self.v40Live[key]={'key':key,'parentId':pid,'side':side,'submitAt':int(t),'limitPrice':price,'qty':qty,'reservedValue':reserve,'spentValue':0.0,'fillSeen':0.0,'anchorFloor':anchor}
  self.v40Events.append({'event':'V40_SURPLUS_SUBMIT','t':int(t),'parentId':pid,'side':side,'price':price,'qty':qty,'reservedFloorCredit':reserve,'availableAfterReserve':self.v40Credit[pid],'anchorFloor':anchor,'projectedFloorAfterFullFill':projected})
  return True
 def _maybe_floor_surplus(self,t):
  if self.v40Live or self.repairParent is None or self.thesis is None:return False
  if int(self.capEnd)-int(t)<=180000:return False
  pid=int(self.repairParent.get('id'));side=self.thesis.get('side')
  if side not in ('UP','DOWN') or self._floor()>=-EPS:return False
  qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('bid') is None:return False
  e=float(qv[side]['bid'])
  if not (EPS<e<1.0-EPS):return False
  legal=1.0/e
  if legal>12.0+EPS:self.v40HeldLegal+=1;return False
  spend=legal*e;avail=float(self.v40Credit.get(pid,0.0));anchor=float(self.v40Anchor.get(pid,self._floor()))
  if avail<=spend+EPS:self.v40HeldCredit+=1;return False
  projected=self._floor()-spend
  if projected<=anchor+EPS:self.v40HeldProjection+=1;return False
  if projected<=anchor+EPS:self.v40UnsafeSubmit+=1
  return self._submit_surplus(t,pid,side,e,legal,spend,anchor,projected)
 def _reconcile_v40(self,t):
  cur=int(self.repairParent.get('id')) if self.repairParent is not None else None
  for key,a in list(self.v40Live.items()):
   e=self.carrierLedger.get(key,{});af=float(e.get('actualFilled') or 0.0);old=float(a.get('fillSeen') or 0.0)
   if af>old+EPS:
    inc=af-old;a['fillSeen']=af;px=self._fill_price(key,e);cost=inc*px;a['spentValue']+=cost;self.v40Spent+=cost;self.v40FillQty+=inc
    floor_now=self._floor();late=(cur is None or int(a['parentId'])!=cur)
    if late:self.v40LateFill+=inc
    if floor_now<=float(a['anchorFloor'])+EPS:self.v40UnsafeFill+=1
    self.v40Events.append({'event':'V40_SURPLUS_FILL','t':int(t),'parentId':int(a['parentId']),'incQty':inc,'fillPrice':px,'spentFloorCredit':cost,'floorAfter':floor_now,'anchorFloor':a['anchorFloor'],'parentStillCurrent':not late})
   st=self._status(key);terminal=st is not None and str(st).upper() in TERMINAL;stale=(cur is None or int(a['parentId'])!=cur)
   if stale and not terminal and key not in self.cancelRequestedAt:
    if self._cancel_key(t,key):self.v40Events.append({'event':'V40_CANCEL_STALE_SURPLUS','t':int(t),'parentId':int(a['parentId']),'currentParentId':cur})
   if terminal:
    unused=max(0.0,float(a['reservedValue'])-float(a['spentValue']))
    if unused>EPS:
     if cur is not None and int(a['parentId'])==cur:self.v40Credit[cur]=float(self.v40Credit.get(cur,0.0))+unused;self.v40Returned+=unused
     else:self.v40Expired+=unused
    self.v40Live.pop(key,None)
 def process(self,t):
  super().process(t);self._scan_floor_credit(t);self._reconcile_v40(t);self._expire_credit(t);self._maybe_floor_surplus(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._reconcile_v40(t);self._expire_credit(t);self._maybe_floor_surplus(t)
 def run_exam_v40(self,models,winner):
  r=super().run_exam_v38b(models,winner);self._reconcile_v40(int(self.meta['lastReceivedMs']));self._expire_credit(int(self.meta['lastReceivedMs']))
  live_reserved=sum(max(0.0,float(a['reservedValue'])-float(a['spentValue'])) for a in self.v40Live.values());remaining=sum(max(0.0,float(v)) for v in self.v40Credit.values())
  self.v40Overspend=max(0.0,self.v40Spent+self.v40Expired+remaining+live_reserved-self.v40Minted)
  r.update({'v40CreditMintedValue':self.v40Minted,'v40CreditSpentValue':self.v40Spent,'v40CreditReturnedValue':self.v40Returned,'v40CreditExpiredValue':self.v40Expired,'v40CreditRemainingValue':remaining,'v40CreditOverspend':self.v40Overspend,'v40SurplusSubmits':self.v40Submits,'v40SurplusFillQty':self.v40FillQty,'v40UnsafeSubmit':self.v40UnsafeSubmit,'v40UnsafeFill':self.v40UnsafeFill,'v40LateFillAfterParentCompletion':self.v40LateFill,'v40HeldInsufficientCredit':self.v40HeldCredit,'v40HeldProjection':self.v40HeldProjection,'v40HeldLegal':self.v40HeldLegal,'v40Events':self.v40Events[:300]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v40_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V40_RUN','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V40_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v38.V38IncrementalOnly(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v38a(models,cr['winner'])
   finally:b.close()
   s=V40FloorCredit(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:ar=s.run_exam_v40(models,cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':ar});print(json.dumps({'marketId':mid,'partial':ar['v38PartialProgressEvents'],'mintValue':ar['v40CreditMintedValue'],'submits':ar['v40SurplusSubmits'],'fillQty':ar['v40SurplusFillQty'],'floor':[br['floor'],ar['floor']],'absNet':[br['absNet'],ar['absNet']],'holds':[ar['v40HeldInsufficientCredit'],ar['v40HeldProjection'],ar['v40HeldLegal']],'unsafe':[ar['v40UnsafeSubmit'],ar['v40UnsafeFill']]}),flush=True)
  def sm(side,key):return sum(float(x[side].get(key) or 0.0) for x in rows)
  agg={'markets':len(rows),'partialProgressEvents':int(sm('candidate','v38PartialProgressEvents')),'creditMintedValue':sm('candidate','v40CreditMintedValue'),'creditSpentValue':sm('candidate','v40CreditSpentValue'),'surplusSubmits':int(sm('candidate','v40SurplusSubmits')),'surplusFillQty':sm('candidate','v40SurplusFillQty'),'unsafeSubmit':int(sm('candidate','v40UnsafeSubmit')),'unsafeFill':int(sm('candidate','v40UnsafeFill')),'creditOverspend':sm('candidate','v40CreditOverspend'),'lateFill':sm('candidate','v40LateFillAfterParentCompletion'),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baseline','floor'),'candidateFloorSum':sm('candidate','floor'),'baselineAbsNetSum':sm('baseline','absNet'),'candidateAbsNetSum':sm('candidate','absNet')}
  gates={'partialProgressExercised':agg['partialProgressEvents']>0,'cycleExercised':agg['surplusFillQty']>EPS,'zeroUnsafeSubmit':agg['unsafeSubmit']==0,'zeroUnsafeFill':agg['unsafeFill']==0,'zeroCreditOverspend':agg['creditOverspend']<=1e-7,'zeroLateFill':agg['lateFill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V40_REALIZED_FLOOR_CREDIT_SURPLUS','researchOnly':True,'aggregate':agg,'gates':gates,'stageAPass':all(gates.values()),'rows':rows,'boundary':['V38 venue-minimum incremental passive Repair unchanged','passive Repair fill mints q*(1-r) floor-improvement credit','same live Repair parent only','persistent thesis supplies Surplus side','one venue-minimum passive Surplus child only if accumulated floor credit strictly exceeds its full floor cost and projected floor remains above pre-Repair anchor','no fitted fraction/time/tick/pairSum threshold','winner/PnL excluded','realistic HFT only','no dream fill','no 8781','<=180s no-new-exposure inherited']}
  Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates}),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
