from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v41=sib('eth_v41_for_v42','run_eth_repair_v41_live_parent_parallel_expand_recoverability_shadow.py');v36=v41.v36;v1=v41.v1;EPS=1e-9

class V42ContinuousExpand(v41.V41Shadow):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v42Keys={};self.v42SubmitCount=0;self.v42FillQty=0.0;self.v42FillEvents=0;self.v42StaleParentFillQty=0.0;self.v42CompletionDeferrals=0;self.v42Events=[];self.v42EligibleAttempts=0;self.v42BlockedHardActive=0
 def _parallel_unresolved(self,pid=None):
  out=[]
  for key,a in self.v42Keys.items():
   if pid is not None and int(a['parentId'])!=int(pid):continue
   e=self.carrierLedger.get(key,{});rem=max(0.0,float(e.get('submittedQty') or a['qty'])-float(e.get('actualFilled') or 0.0))
   o=self.orders.get(key);live=False
   try:live=bool(o and v1.live(self.snap(o).get('status')))
   except Exception:pass
   if rem>EPS and live:out.append((key,a,rem))
  return out
 def _complete_parent_if_structural(self,t,ai):
  if self.repairParent is not None:
   pid=int(self.repairParent.get('id'))
   if self._parallel_unresolved(pid):self.v42CompletionDeferrals+=1;return
  return super()._complete_parent_if_structural(t,ai)
 def _candidate(self,t):
  rp=self.repairParent;th=self.thesis
  if rp is None or th is None or int(self.capEnd)-int(t)<=180000:return None
  pid=int(rp.get('id'))
  if pid in self.hardConfirmed or pid in self.activeByParent:self.v42BlockedHardActive+=1;return None
  if self._live_expand_child() or self._parallel_unresolved(pid):return None
  side=th.get('side');repair='DOWN' if side=='UP' else 'UP' if side=='DOWN' else None
  if repair is None or rp.get('side')!=repair:return None
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('bid') is None or qv.get(repair,{}).get('bid') is None:return None
  ep=float(qv[side]['bid']);rbid=float(qv[repair]['bid'])
  if not(EPS<ep<1-EPS and EPS<rbid<1-EPS):return None
  floor,u,d,cost=self._raw_floor();curStrong=float(u) if side=='UP' else float(d);curWeak=float(d) if side=='UP' else float(u)
  if curStrong<=curWeak+EPS:return None
  eq=1.0/ep
  if eq>12.0+EPS:return None
  hu=float(u)+(eq if side=='UP' else 0.0);hd=float(d)+(eq if side=='DOWN' else 0.0);hc=float(cost)+eq*ep;hfloor=min(hu,hd)-hc
  weakQty=hd if repair=='DOWN' else hu;strongQty=hu if repair=='DOWN' else hd;hgap=max(0.0,strongQty-weakQty)
  owned=0.0;reservedGain=0.0
  for key,e,rem in self.lane_unresolved('REPAIR'):
   if e.get('side')!=repair:continue
   q=float(rem);owned+=q;op=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0)
   if EPS<op<1-EPS:reservedGain+=q*(1.0-op)
  proj=hfloor+reservedGain;room=max(0.0,hgap-owned);ceiling=(strongQty-hc)/hgap if hgap>EPS else None
  rpprice=min(rbid,float(ceiling)) if ceiling is not None else None;need=legal=req=None
  if proj>=-EPS:feasible=True;reason='OWNED_REPAIR_COVERS'
  elif rpprice is None or not(EPS<rpprice<1-EPS):feasible=False;reason='NO_REPAIR_PRICE'
  else:
   need=max(0.0,-proj)/(1.0-rpprice);legal=1.0/rpprice;req=max(need,legal);feasible=req<=room+EPS;reason='PASS' if feasible else 'NO_ROOM'
  if not feasible:return None
  return {'t':int(t),'parentId':pid,'side':side,'price':ep,'qty':eq,'repairSide':repair,'repairBid':rbid,'floorBefore':float(floor),'hypFloor':hfloor,'ownedRepairQty':owned,'ownedRepairGain':reservedGain,'projectedFloorAfterOwnedRepair':proj,'repairRoom':room,'repairCeiling':ceiling,'futureRepairPrice':rpprice,'futureNeedQty':need,'futureLegalQty':legal,'futureRequiredQty':req,'reason':reason}
 def _maybe_expand(self,t):
  c=self._candidate(t)
  if c is None:return False
  self.v42EligibleAttempts+=1;pid=int(c['parentId']);side=c['side'];n0=self.n;oid=self._new_objective('EXPAND',side)['id']
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='PARALLEL_EXPAND_RECOVERABLE'
  ok=self.submit(t,side,float(c['price']),float(c['qty']))
  if not ok:return False
  key=f'{side}_{n0}'
  if key in self.carrierLedger:
   self.carrierLedger[key]['parentId']=pid;self.carrierLedger[key]['lane']='PARALLEL_EXPAND_RECOVERABLE';self.carrierLedger[key]['objectiveRole']='EXPAND';self.carrierLedger[key]['objectiveId']=oid
  self.v42Keys[key]={'key':key,'parentId':pid,'qty':float(c['qty']),'price':float(c['price']),'fillSeen':0.0,'submitAt':int(t)};self.v42SubmitCount+=1
  self.v42Events.append({'event':'V42_PARALLEL_EXPAND_SUBMIT',**c});return True
 def _reconcile_v42(self,t):
  curpid=int(self.repairParent.get('id')) if self.repairParent is not None else None
  for key,a in self.v42Keys.items():
   e=self.carrierLedger.get(key,{});af=float(e.get('actualFilled') or 0.0);old=float(a.get('fillSeen') or 0.0)
   if af>old+EPS:
    inc=af-old;a['fillSeen']=af;self.v42FillQty+=inc;self.v42FillEvents+=1
    stale=curpid is None or int(a['parentId'])!=curpid
    if stale:self.v42StaleParentFillQty+=inc
    self.v42Events.append({'event':'V42_PARALLEL_EXPAND_FILL','t':int(t),'parentId':int(a['parentId']),'qty':inc,'cumQty':af,'price':float(a['price']),'parentStillCurrent':not stale,'floorAfter':float(self._raw_floor()[0])})
 def process(self,t):
  super().process(t);self._reconcile_v42(t);self._maybe_expand(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._reconcile_v42(t);self._maybe_expand(t)
 def run_exam_v42(self,models,winner):
  r=super().run_exam_v41(models,winner);self._reconcile_v42(int(self.meta['lastReceivedMs']));r.update({'v42SubmitCount':self.v42SubmitCount,'v42FillQty':self.v42FillQty,'v42FillEvents':self.v42FillEvents,'v42StaleParentFillQty':self.v42StaleParentFillQty,'v42CompletionDeferrals':self.v42CompletionDeferrals,'v42EligibleAttempts':self.v42EligibleAttempts,'v42BlockedHardActive':self.v42BlockedHardActive,'v42Events':self.v42Events[:300]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v42_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V42_RUN','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V42_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v36.v34.v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v36.V36EventConfirmedActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v36(models,cr['winner'])
   finally:b.close()
   s=V42ContinuousExpand(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:ar=s.run_exam_v42(models,cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':ar});print(json.dumps({'marketId':mid,'submits':ar['v42SubmitCount'],'fillQty':ar['v42FillQty'],'fillEvents':ar['v42FillEvents'],'baseFloor':br['floor'],'candFloor':ar['floor'],'baseAbsNet':br['absNet'],'candAbsNet':ar['absNet'],'baseTerminal':br['v34ParentTerminalUnresolved'],'candTerminal':ar['v34ParentTerminalUnresolved'],'stale':ar['v42StaleParentFillQty']},ensure_ascii=False),flush=True)
  def sm(side,key):return sum(float(x[side].get(key) or 0.0) for x in rows)
  fillMarkets=sum(1 for x in rows if float(x['candidate'].get('v42FillQty') or 0)>EPS)
  agg={'markets':len(rows),'fillMarkets':fillMarkets,'parallelSubmits':int(sm('candidate','v42SubmitCount')),'parallelFillQty':sm('candidate','v42FillQty'),'parallelFillEvents':int(sm('candidate','v42FillEvents')),'staleParentFillQty':sm('candidate','v42StaleParentFillQty'),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'sharedOverfill':sm('candidate','v36SharedRealizedOverfill'),'baselineTerminalParents':int(sm('baseline','v34ParentTerminalUnresolved')),'candidateTerminalParents':int(sm('candidate','v34ParentTerminalUnresolved')),'baselineFloorSum':sm('baseline','floor'),'candidateFloorSum':sm('candidate','floor'),'baselineAbsNetSum':sm('baseline','absNet'),'candidateAbsNetSum':sm('candidate','absNet')}
  gates={'materializedIn3Markets':fillMarkets>=3,'zeroStaleParentFill':agg['staleParentFillQty']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroSharedOverfill':agg['sharedOverfill']<=EPS,'terminalParentsNotWorse':agg['candidateTerminalParents']<=agg['baselineTerminalParents'],'aggregateFloorNotWorse':agg['candidateFloorSum']>=agg['baselineFloorSum']-1e-7}
  out={'version':'ETH_REPAIR_V42_CONTINUOUS_RECOVERABLE_PARALLEL_EXPAND','researchOnly':True,'behaviorChange':True,'aggregate':agg,'gates':gates,'stageAPass':all(gates.values()),'rows':rows,'boundary':['one live parallel EXPAND child at a time','each child venue-minimum and individually V41-recoverable','no fixed cycle count/cooldown','block if V36 hard-active parent','defer parent completion while child can materialize','no winner/PnL gate','realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
