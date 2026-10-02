from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v38=sib('eth_v38_for_v39','run_eth_repair_v38_incremental_repair_parallel_surplus_causal.py')
v1=v38.v37.v1; EPS=1e-9

class V39PersistentThesisRecycle(v38.V38IncrementalRecycle):
 def _maybe_surplus_recycle(self,t):
  if self._live_surplus_exists():return False
  if self.repairParent is None or self.thesis is None:return False
  if int(self.capEnd)-int(t)<=180000:return False
  pid=int(self.repairParent.get('id'));side=self.thesis.get('side')
  if side not in ('UP','DOWN') or self._floor()>=-EPS:return False
  qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('bid') is None:return False
  eprice=float(qv[side]['bid'])
  if eprice<=EPS or eprice>=1.0-EPS:return False
  eligible=[];total=0.0
  for tr in self.creditTranches:
   if int(tr['parentId'])!=pid:continue
   rem=float(tr.get('remaining') or 0.0)
   if rem<=EPS:continue
   if float(tr['repairPrice'])+eprice<1.0-EPS:eligible.append(tr);total+=rem
  if total<=EPS:
   if any(int(z['parentId'])==pid and float(z.get('remaining') or 0.0)>EPS for z in self.creditTranches):self.creditHeldEconomicallyIneligible+=1
   return False
  legal=1.0/eprice;q=min(total,12.0)
  if q+EPS<legal:self.creditHeldBelowLegal+=1;return False
  allocations=[];left=q
  for tr in eligible:
   take=min(float(tr['remaining']),left)
   if take<=EPS:continue
   tr['remaining']-=take;allocations.append({'trancheId':int(tr['id']),'qty':take,'consumed':0.0,'repairPrice':float(tr['repairPrice'])});left-=take
   if left<=EPS:break
  reserved=sum(float(x['qty']) for x in allocations)
  if reserved<=EPS:return False
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='SURPLUS_RECYCLE_PERSISTENT_THESIS'
  ok=self.submit(t,side,eprice,reserved)
  if not ok:
   for al in allocations:
    tr=self._tranche(al['trancheId'])
    if tr:tr['remaining']+=float(al['qty'])
   return False
  key=f'{side}_{n0}'
  if key in self.carrierLedger:
   self.carrierLedger[key]['parentId']=pid;self.carrierLedger[key]['lane']='SURPLUS_RECYCLE_PERSISTENT_THESIS';self.carrierLedger[key]['objectiveRole']='EXPAND';self.carrierLedger[key]['objectiveId']=oid
  self.surplusOrders[key]={'key':key,'parentId':pid,'side':side,'submitAt':int(t),'price':eprice,'qty':reserved,'allocations':allocations,'fillSeen':0.0}
  self.creditReservedQty+=reserved;self.surplusSubmitCount+=1
  self.v37Events.append({'event':'V39_SURPLUS_RECYCLE_SUBMIT','t':int(t),'parentId':pid,'side':side,'price':eprice,'qty':reserved,'legalMin':legal,'eligibleCredit':total,'instantSignal':self._signal_side(qv),'thesisSide':side,'allocations':[{'trancheId':x['trancheId'],'qty':x['qty'],'repairPrice':x['repairPrice'],'pairSumAtSubmit':x['repairPrice']+eprice} for x in allocations],'floorBeforeSubmit':self._floor()})
  return True
 def run_exam_v39(self,models,winner):
  r=super().run_exam_v38b(models,winner);r['v39SignalVetoRemoved']=True;return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v39_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V39_RUN','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V39_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort}
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v38.V38IncrementalRecycle(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v38b(models,cr['winner'])
   finally:b.close()
   s=V39PersistentThesisRecycle(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:ar=s.run_exam_v39(models,cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'v38':br,'v39':ar})
   print(json.dumps({'marketId':mid,'mint':ar['v37CreditMintedQty'],'submits':ar['v37SurplusSubmitCount'],'fills':ar['v37SurplusFillQty'],'gain':ar['v37SurplusMatchedGain'],'floor38':br['floor'],'floor39':ar['floor'],'absNet38':br['absNet'],'absNet39':ar['absNet'],'econHold':ar['v37CreditHeldEconomicallyIneligible'],'legalHold':ar['v37CreditHeldBelowLegal']},ensure_ascii=False),flush=True)
  def sm(side,key):return sum(float(x[side].get(key) or 0) for x in rows)
  agg={'markets':len(rows),'creditMintedQty':sm('v39','v37CreditMintedQty'),'surplusSubmits':int(sm('v39','v37SurplusSubmitCount')),'surplusFillQty':sm('v39','v37SurplusFillQty'),'surplusMatchedGain':sm('v39','v37SurplusMatchedGain'),'nonPositivePairFillQty':sm('v39','v37NonPositivePairFillQty'),'creditOverspend':sm('v39','v37CreditOverspend'),'crossParentCreditLeak':int(sm('v39','v37CrossParentCreditLeak')),'lateSurplusFillAfterParentCompletion':sm('v39','v37LateSurplusFillAfterParentCompletion'),'truthMismatch':sm('v39','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('v39','overOwnedSubmitViolations'),'repairDrift':sm('v39','repairToExpandAtFirstFill'),'v38FloorSum':sm('v38','floor'),'v39FloorSum':sm('v39','floor'),'v38AbsNetSum':sm('v38','absNet'),'v39AbsNetSum':sm('v39','absNet'),'partialProgressEvents':int(sm('v39','v38PartialProgressEvents'))}
  gates={'cycleExercised':agg['surplusFillQty']>EPS,'positiveMatchedEconomics':agg['nonPositivePairFillQty']<=EPS and agg['surplusMatchedGain']>EPS,'zeroCreditOverspend':agg['creditOverspend']<=1e-7,'zeroCrossParentLeak':agg['crossParentCreditLeak']==0,'zeroLateFill':agg['lateSurplusFillAfterParentCompletion']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'floorNotWorseVsV38':agg['v39FloorSum']>=agg['v38FloorSum']-1e-7}
  out={'version':'ETH_REPAIR_V39_PERSISTENT_THESIS_SURPLUS_RECYCLE','researchOnly':True,'aggregate':agg,'gates':gates,'stageAPass':all(gates.values()),'rows':rows,'boundary':['single change vs V38 recycle: no per-receipt BOOK_IMBALANCE sign veto after thesis materialization','persistent V30 thesis supplies recycle side','repairPrice+reexpandPrice<1 unchanged','realized same-parent credit only','venue legal min unchanged','no winner/PnL gate','realistic HFT only','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
