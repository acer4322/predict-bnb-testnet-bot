from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v38=sib('eth_v38_for_v43','run_eth_repair_v38_incremental_repair_parallel_surplus_causal.py');v36=v38.v36;v1=v36.v1;EPS=1e-9
TERMINAL={'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}

class V43FloorCredit(v38.V38IncrementalOnly):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.credit={};self.creditOrders={};self.v43Events=[]
  self.v43MintUsd=0.0;self.v43ReservedUsd=0.0;self.v43ConsumedUsd=0.0;self.v43ReturnedUsd=0.0;self.v43ExpiredUsd=0.0;self.v43OverspendUsd=0.0
  self.v43SubmitCount=0;self.v43FillQty=0.0;self.v43FillEvents=0;self.v43FillMarketsFlag=False;self.v43StaleParentFillUsd=0.0;self.v43CompletionDeferrals=0;self.v43BelowLegalCreditHolds=0
 def _floor(self):return float(self._raw_floor()[0])
 def _pool(self,pid):
  return self.credit.setdefault(int(pid),{'available':0.0,'minted':0.0,'consumed':0.0,'returned':0.0,'expired':0.0})
 def _passive_repair_snapshot(self,pid):
  out={}
  for key,e in getattr(self,'carrierLedger',{}).items():
   try:epid=int(e.get('parentId'))
   except Exception:continue
   if epid!=int(pid) or e.get('objectiveRole')!='REPAIR' or str(e.get('lane') or '').startswith('ACTIVE_'):continue
   out[key]=float(e.get('actualFilled') or 0.0)
  return out
 def _fill_price(self,key,e):
  o=self.orders.get(key)
  if not o:return float(e.get('price') or 0.0)
  try:return float(v1.fill_price(o['side'],self.snap(o),float(o.get('price') or 0.0)))
  except Exception:return float(o.get('price') or e.get('price') or 0.0)
 def _mint_from_process(self,t,pid,pre_floor,before):
  cur=int(self.repairParent.get('id')) if self.repairParent is not None else None
  if cur is None or cur!=int(pid):return
  max_gain=0.0;fill_rows=[]
  for key,e in getattr(self,'carrierLedger',{}).items():
   try:epid=int(e.get('parentId'))
   except Exception:continue
   if epid!=int(pid) or e.get('objectiveRole')!='REPAIR' or str(e.get('lane') or '').startswith('ACTIVE_'):continue
   now=float(e.get('actualFilled') or 0.0);old=float(before.get(key,0.0));inc=max(0.0,now-old)
   if inc<=EPS:continue
   px=self._fill_price(key,e)
   if EPS<px<1-EPS:
    g=inc*(1.0-px);max_gain+=g;fill_rows.append({'key':key,'qty':inc,'price':px,'maxFloorGain':g})
  if max_gain<=EPS:return
  post=self._floor();realized=max(0.0,post-float(pre_floor));mint=min(realized,max_gain)
  if mint<=EPS:return
  p=self._pool(pid);p['available']+=mint;p['minted']+=mint;self.v43MintUsd+=mint
  self.v43Events.append({'event':'V43_FLOOR_CREDIT_MINT','t':int(t),'parentId':int(pid),'preFloor':float(pre_floor),'postFloor':post,'realizedFloorDelta':realized,'passiveRepairMaxGain':max_gain,'mintUsd':mint,'availableAfter':p['available'],'fills':fill_rows})
 def _live_credit_order(self,pid=None):
  for key,a in self.creditOrders.items():
   if a.get('terminal'):continue
   if pid is not None and int(a['parentId'])!=int(pid):continue
   o=self.orders.get(key);live=False
   try:live=bool(o and v1.live(self.snap(o).get('status')))
   except Exception:pass
   if live:return True
  return False
 def _complete_parent_if_structural(self,t,ai):
  if self.repairParent is not None:
   pid=int(self.repairParent.get('id'))
   if self._live_credit_order(pid):self.v43CompletionDeferrals+=1;return
  return super()._complete_parent_if_structural(t,ai)
 def _reconcile_credit_orders(self,t):
  cur=int(self.repairParent.get('id')) if self.repairParent is not None else None
  for key,a in list(self.creditOrders.items()):
   if a.get('terminal'):continue
   e=self.carrierLedger.get(key,{});af=float(e.get('actualFilled') or 0.0);old=float(a.get('fillSeen') or 0.0)
   if af>old+EPS:
    inc=af-old;a['fillSeen']=af;px=self._fill_price(key,e);actual=inc*px;reserved_unit=float(a['submitPrice']);released=max(0.0,inc*(reserved_unit-px));consume=min(float(a['reservedRemaining']),inc*reserved_unit)
    a['reservedRemaining']=max(0.0,float(a['reservedRemaining'])-consume)
    pid=int(a['parentId']);p=self._pool(pid);p['consumed']+=actual;self.v43ConsumedUsd+=actual
    if released>EPS and cur==pid:p['available']+=released;p['returned']+=released;self.v43ReturnedUsd+=released
    elif released>EPS:p['expired']+=released;self.v43ExpiredUsd+=released
    if actual>consume+1e-7:self.v43OverspendUsd+=actual-consume
    stale=cur is None or cur!=pid
    if stale:self.v43StaleParentFillUsd+=actual
    self.v43FillQty+=inc;self.v43FillEvents+=1;self.v43FillMarketsFlag=True
    self.v43Events.append({'event':'V43_CREDIT_EXPAND_FILL','t':int(t),'parentId':pid,'qty':inc,'fillPrice':px,'actualCostUsd':actual,'reservedCostReleasedOnQty':consume,'priceImprovementReturnedUsd':released,'parentStillCurrent':not stale,'floorAfter':self._floor(),'availableCredit':p['available']})
   o=self.orders.get(key);st=None
   try:st=self.snap(o).get('status') if o else None
   except Exception:pass
   terminal=st is not None and str(st).upper() in TERMINAL
   stale=cur is None or int(a['parentId'])!=cur
   if stale and not terminal and o and key not in self.cancelRequestedAt:
    if self._cancel_key(t,key):self.v43Events.append({'event':'V43_CANCEL_STALE_EXPAND','t':int(t),'key':key,'parentId':int(a['parentId']),'currentParentId':cur})
   if terminal:
    rem=float(a.get('reservedRemaining') or 0.0);pid=int(a['parentId']);p=self._pool(pid)
    if rem>EPS:
     if cur==pid:p['available']+=rem;p['returned']+=rem;self.v43ReturnedUsd+=rem
     else:p['expired']+=rem;self.v43ExpiredUsd+=rem
    a['reservedRemaining']=0.0;a['terminal']=True;a['terminalStatus']=str(st).upper();a['terminalAt']=int(t)
 def _expire_stale_pool(self,t):
  cur=int(self.repairParent.get('id')) if self.repairParent is not None else None
  for pid,p in self.credit.items():
   if cur==int(pid):continue
   av=float(p.get('available') or 0.0)
   if av>EPS:p['available']=0.0;p['expired']+=av;self.v43ExpiredUsd+=av;self.v43Events.append({'event':'V43_CREDIT_EXPIRE_PARENT_CHANGE','t':int(t),'parentId':int(pid),'qtyUsd':av,'currentParentId':cur})
 def _maybe_credit_expand(self,t):
  rp=self.repairParent;th=self.thesis
  if rp is None or th is None or int(self.capEnd)-int(t)<=180000:return False
  pid=int(rp.get('id'));side=th.get('side')
  if side not in ('UP','DOWN') or pid in self.hardConfirmed or pid in self.activeByParent:return False
  if self._live_credit_order(pid):return False
  ai=self.auth_inv();opp='DOWN' if side=='UP' else 'UP'
  if float(ai[side])<=float(ai[opp])+EPS:return False
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('bid') is None:return False
  p=float(qv[side]['bid'])
  if not(EPS<p<1-EPS):return False
  q=1.0/p
  if q>12.0+EPS:return False
  reserve=q*p;pool=self._pool(pid)
  if float(pool['available'])+EPS<reserve:self.v43BelowLegalCreditHolds+=1;return False
  pool['available']-=reserve;self.v43ReservedUsd+=reserve
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='FLOOR_CREDIT_PARALLEL_EXPAND'
  ok=self.submit(t,side,p,q)
  if not ok:pool['available']+=reserve;return False
  key=f'{side}_{n0}'
  if key in self.carrierLedger:
   self.carrierLedger[key]['parentId']=pid;self.carrierLedger[key]['lane']='FLOOR_CREDIT_PARALLEL_EXPAND';self.carrierLedger[key]['objectiveRole']='EXPAND';self.carrierLedger[key]['objectiveId']=oid
  self.creditOrders[key]={'key':key,'parentId':pid,'qty':q,'submitPrice':p,'reservedRemaining':reserve,'fillSeen':0.0,'submitAt':int(t),'terminal':False}
  self.v43SubmitCount+=1;self.v43Events.append({'event':'V43_CREDIT_EXPAND_SUBMIT','t':int(t),'parentId':pid,'side':side,'price':p,'qty':q,'reservedUsd':reserve,'availableAfterReserve':pool['available'],'floorBefore':self._floor()});return True
 def process(self,t):
  pid=int(self.repairParent.get('id')) if self.repairParent is not None else None;pre=self._floor();before=self._passive_repair_snapshot(pid) if pid is not None else {}
  super().process(t)
  if pid is not None:self._mint_from_process(t,pid,pre,before)
  self._reconcile_credit_orders(t);self._expire_stale_pool(t);self._maybe_credit_expand(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._reconcile_credit_orders(t);self._expire_stale_pool(t);self._maybe_credit_expand(t)
 def run_exam_v43(self,models,winner):
  r=super().run_exam_v38a(models,winner);self._reconcile_credit_orders(int(self.meta['lastReceivedMs']))
  available=sum(float(p.get('available') or 0.0) for p in self.credit.values());reserved=sum(float(a.get('reservedRemaining') or 0.0) for a in self.creditOrders.values() if not a.get('terminal'))
  ledger=self.v43ConsumedUsd+self.v43ExpiredUsd+available+reserved
  self.v43OverspendUsd=max(self.v43OverspendUsd,max(0.0,ledger-self.v43MintUsd))
  r.update({'v43MintUsd':self.v43MintUsd,'v43ReservedUsd':self.v43ReservedUsd,'v43ConsumedUsd':self.v43ConsumedUsd,'v43ReturnedUsd':self.v43ReturnedUsd,'v43ExpiredUsd':self.v43ExpiredUsd,'v43AvailableUsd':available,'v43LiveReservedUsd':reserved,'v43OverspendUsd':self.v43OverspendUsd,'v43SubmitCount':self.v43SubmitCount,'v43FillQty':self.v43FillQty,'v43FillEvents':self.v43FillEvents,'v43StaleParentFillUsd':self.v43StaleParentFillUsd,'v43CompletionDeferrals':self.v43CompletionDeferrals,'v43BelowLegalCreditHolds':self.v43BelowLegalCreditHolds,'v43Events':self.v43Events[:300]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v43_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V43_RUN','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V43_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v36.v34.v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v38.V38IncrementalOnly(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v38a(models,cr['winner'])
   finally:b.close()
   s=V43FloorCredit(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:ar=s.run_exam_v43(models,cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':ar});print(json.dumps({'marketId':mid,'mintUsd':ar['v43MintUsd'],'submits':ar['v43SubmitCount'],'fillQty':ar['v43FillQty'],'fillEvents':ar['v43FillEvents'],'partial':ar['v38PartialProgressEvents'],'baseFloor':br['floor'],'candFloor':ar['floor'],'baseAbs':br['absNet'],'candAbs':ar['absNet'],'baseTerminal':br.get('v34ParentTerminalUnresolved'),'candTerminal':ar.get('v34ParentTerminalUnresolved'),'overspend':ar['v43OverspendUsd']},ensure_ascii=False),flush=True)
  def sm(side,key):return sum(float(x[side].get(key) or 0.0) for x in rows)
  fillMarkets=sum(1 for x in rows if float(x['candidate'].get('v43FillQty') or 0)>EPS)
  agg={'markets':len(rows),'fillMarkets':fillMarkets,'mintUsd':sm('candidate','v43MintUsd'),'consumedUsd':sm('candidate','v43ConsumedUsd'),'parallelSubmits':int(sm('candidate','v43SubmitCount')),'parallelFillQty':sm('candidate','v43FillQty'),'parallelFillEvents':int(sm('candidate','v43FillEvents')),'partialProgressEvents':int(sm('candidate','v38PartialProgressEvents')),'creditOverspendUsd':sm('candidate','v43OverspendUsd'),'staleParentFillUsd':sm('candidate','v43StaleParentFillUsd'),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'sharedOverfill':sm('candidate','v36SharedRealizedOverfill'),'baselineTerminalParents':int(sm('baseline','v34ParentTerminalUnresolved')),'candidateTerminalParents':int(sm('candidate','v34ParentTerminalUnresolved')),'baselineFloorSum':sm('baseline','floor'),'candidateFloorSum':sm('candidate','floor'),'baselineAbsNetSum':sm('baseline','absNet'),'candidateAbsNetSum':sm('candidate','absNet')}
  gates={'materializedIn3Markets':fillMarkets>=3,'partialProgressRetained':agg['partialProgressEvents']>0,'zeroCreditOverspend':agg['creditOverspendUsd']<=1e-7,'zeroStaleParentFill':agg['staleParentFillUsd']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroSharedOverfill':agg['sharedOverfill']<=EPS,'terminalParentsNotWorse':agg['candidateTerminalParents']<=agg['baselineTerminalParents'],'aggregateFloorNotWorse':agg['candidateFloorSum']>=agg['baselineFloorSum']-1e-7,'directionalExposureActuallyIncreases':agg['candidateAbsNetSum']>agg['baselineAbsNetSum']+EPS}
  support='PASS' if all(gates.values()) else ('TESTED_INCONCLUSIVE' if fillMarkets<3 and all(v for k,v in gates.items() if k not in ('materializedIn3Markets','directionalExposureActuallyIncreases')) else 'REJECT')
  out={'version':'ETH_REPAIR_V43_REALIZED_FLOOR_CREDIT_PARALLEL_SURPLUS','researchOnly':True,'behaviorChange':True,'aggregate':agg,'gates':gates,'decision':support,'rows':rows,'boundary':['V38 incremental passive Repair base','credit only from realized same-parent passive Repair floor improvement','no credit from hypothetical room/absNet/V36 active fill','one venue-minimum same-thesis child at a time funded by reserved floor-credit USD','no fixed cycles/time/ticks','no winner/PnL gate','realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':support=='PASS','decision':support,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
