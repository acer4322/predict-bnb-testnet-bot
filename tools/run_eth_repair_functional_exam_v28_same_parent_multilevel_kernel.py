from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
try:
 from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
except ImportError:
 import importlib.util
 p=Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v23_queue_progress_lease.py');s=importlib.util.spec_from_file_location('v23for28',p);v23=importlib.util.module_from_spec(s);s.loader.exec_module(v23)
EPS=1e-9

class SameParentMultiLevelKernelSim(v23.QueueProgressLeaseSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.optionKernelAttempts=0;self.optionDualSubmitEvents=0;self.optionChildSubmits=0;self.optionKeys=[]
  self.optionFallbackSingle=0;self.optionInsufficientBudget=0;self.optionInsufficientLevels=0
  self.maxSimultaneousOptionChildren=0;self.maxSimultaneousOptionDistinctPrices=0;self.multiOptionTicks=0
  self.maxSameParentRepairOwnedOverGap=0.0;self.optionFillChildren=0;self.optionKernelExercised=False
  self.optionTrace=[]
 def _outcome_levels(self,side,rb,qv):
  ceiling=1.0-float(rb['firstPrice'])-0.01;ask=float(qv[side]['ask'])
  if side=='UP': raw=[float(p) for p in self.book.get('bids',{}).keys()]
  else: raw=[1.0-float(a) for a in self.book.get('asks',{}).keys()]
  vals=[]
  for p in raw:
   p=round(float(p),12)
   if p<=EPS or p>=ask-EPS: continue
   if p>ceiling+EPS: continue
   if float(rb['firstPrice'])+p>=1.0-EPS: continue
   vals.append(p)
  return sorted(set(vals),reverse=True)
 def _submit_one_option(self,t,side,p,q,role,oid,rb):
  self._pendingAuthorizedRole=role;self._pendingAuthorizedObjectiveId=oid
  self._pendingParentId=self.repairParent['id'] if self.repairParent else None;self._pendingLane='REPAIR'
  n0=self.n;old_allow=bool(getattr(self,'_allowSameSideParallelReservation',False));self._allowSameSideParallelReservation=True
  try:ok=self.submit(t,side,p,q)
  finally:self._allowSameSideParallelReservation=old_allow
  if ok:
   key=f'{side}_{n0}';self.optionKeys.append(key);self.optionChildSubmits+=1;rb.setdefault('repairKeys',[]).append(key)
   self.optionTrace.append({'event':'OPTION_SUBMIT','t':int(t),'key':key,'side':side,'price':float(p),'qty':float(q),'parentId':self.repairParent['id'] if self.repairParent else None})
   if rb.get('firstCeilingSubmitAt') is None:
    rb['firstCeilingSubmitAt']=int(t);self.ceilingSubmitLags.append(int(t)-int(rb['firstFillAt']))
   self.ceilingRepairSubmits+=1;self.ceilingPrices.append(float(p))
  return bool(ok)
 def _submit_package_at_price(self,t,qv,z,roles_this_tick,p):
  if z is None:return False
  side,qty,_,role,oid=z;rb=self.reserveBuilder
  if role!='REPAIR' or role in roles_this_tick or rb is None:return False
  # Only the first eligible package attempt is converted into a bounded two-level capability probe.
  if self.optionKernelExercised:return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  self.optionKernelAttempts+=1;levels=self._outcome_levels(side,rb,qv)
  if len(levels)<2:
   self.optionInsufficientLevels+=1;self.optionFallbackSingle+=1
   return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  p1,p2=levels[0],levels[1];q1=1.0/p1;q2=1.0/p2;budget=float(qty)
  if q1+q2>budget+EPS:
   self.optionInsufficientBudget+=1;self.optionFallbackSingle+=1
   return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  # Shared responsibility conservation: each child owns only its legal-minimum tranche; sum <= already-authorized budget.
  ok1=self._submit_one_option(t,side,p1,q1,role,oid,rb);ok2=False
  if ok1:
   # Recompute current ownership before child 2; PersistentCarrierSim submit guard remains authoritative.
   ai=self.auth_inv();opp='DOWN' if side=='UP' else 'UP';gap=max(0.0,float(ai[opp])-float(ai[side]))
   owned=sum(rem for _,_,rem in self.lane_unresolved('REPAIR'))
   if owned+q2<=gap+1e-7:ok2=self._submit_one_option(t,side,p2,q2,role,oid,rb)
  if ok1 and ok2:
   self.optionDualSubmitEvents+=1;self.optionKernelExercised=True;roles_this_tick.add(role)
   self.optionTrace.append({'event':'OPTION_DUAL_COMMIT','t':int(t),'side':side,'prices':[float(p1),float(p2)],'qtys':[float(q1),float(q2)],'budget':budget})
   return True
  if ok1:
   roles_this_tick.add(role);return True
  self.optionFallbackSingle+=1
  return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
 def process(self,t):
  super().process(t);self._refresh_carrier_ledger(t)
  active=[]
  for key in self.optionKeys:
   e=self.carrierLedger.get(key)
   if not e:continue
   rem=self._ledger_remaining(e)
   if rem>EPS:active.append((key,e,rem))
  if active:
   self.maxSimultaneousOptionChildren=max(self.maxSimultaneousOptionChildren,len(active))
   self.maxSimultaneousOptionDistinctPrices=max(self.maxSimultaneousOptionDistinctPrices,len(set(round(float(self.orders[k]['price']),12) for k,_,_ in active if k in self.orders)))
   if len(active)>=2:self.multiOptionTicks+=1
  if self.repairParent:
   ai=self.auth_inv();side=self.repairParent['side'];opp='DOWN' if side=='UP' else 'UP';gap=max(0.0,float(ai[opp])-float(ai[side]));owned=sum(rem for _,_,rem in self.lane_unresolved('REPAIR'))
   self.maxSameParentRepairOwnedOverGap=max(self.maxSameParentRepairOwnedOverGap,max(0.0,owned-gap))
  self.optionFillChildren=sum(1 for k in self.optionKeys if float(self.carrierLedger.get(k,{}).get('actualFilled',0.0))>EPS)
 def run_exam_v28(self,models,winner):
  r=super().run_exam_v23(models,winner)
  r.update({'optionKernelAttempts':self.optionKernelAttempts,'optionDualSubmitEvents':self.optionDualSubmitEvents,'optionChildSubmits':self.optionChildSubmits,'optionFallbackSingle':self.optionFallbackSingle,'optionInsufficientBudget':self.optionInsufficientBudget,'optionInsufficientLevels':self.optionInsufficientLevels,'maxSimultaneousOptionChildren':self.maxSimultaneousOptionChildren,'maxSimultaneousOptionDistinctPrices':self.maxSimultaneousOptionDistinctPrices,'multiOptionTicks':self.multiOptionTicks,'maxSameParentRepairOwnedOverGap':self.maxSameParentRepairOwnedOverGap,'optionFillChildren':self.optionFillChildren,'optionKernelExercised':self.optionKernelExercised,'optionTrace':self.optionTrace[:30]})
  return r

def main():
 ap=argparse.ArgumentParser();
 for k in ['bundle','lifecycle_model','capability_model','dagger_cache','timing_model','economic_model','price_model','surplus_model']:
  ap.add_argument('--'+k.replace('_','-'),required=True)
 ap.add_argument('--market-ids',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v28_multilevel_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v23.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=SameParentMultiLevelKernelSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v28(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   local={'zeroOverOwnedSubmit':int(r.get('overOwnedSubmitViolations') or 0)==0,'ownedNeverAboveGap':float(r.get('maxSameParentRepairOwnedOverGap') or 0)<=1e-7,'zeroTruthMismatch':int(r.get('authorizedSubmitWithTruthRoleMismatch') or 0)==0,'zeroRepairDrift':int(r.get('repairToExpandAtFirstFill') or 0)==0,'zeroUnresolvedAtTerminal':abs(float(r.get('unresolvedCarrierQty') or 0))<=1e-9}
   rows.append({'marketId':mid,'functional':r,'causal':c,'localSafety':local});print(json.dumps({'marketId':mid,'dual':r['optionDualSubmitEvents'],'children':r['optionChildSubmits'],'maxConcurrent':r['maxSimultaneousOptionChildren'],'distinct':r['maxSimultaneousOptionDistinctPrices'],'optionFillChildren':r['optionFillChildren'],'cycles':r['reserveCycleCompletion'],'overOwned':r['overOwnedSubmitViolations'],'maxOwnedOverGap':r['maxSameParentRepairOwnedOverGap'],'unresolved':r['unresolvedCarrierQty'],'trace':r['optionTrace'][:4]},ensure_ascii=False),flush=True)
  dual=sum(int(x['functional']['optionDualSubmitEvents']) for x in rows);multi=sum(int(x['functional']['multiOptionTicks']) for x in rows);fills=sum(int(x['functional']['optionFillChildren']) for x in rows)
  safety={k:all(x['localSafety'][k] for x in rows) for k in rows[0]['localSafety']} if rows else {}
  gates={**safety,'dualSameParentDistinctPriceExercised':dual>0 and max((int(x['functional']['maxSimultaneousOptionDistinctPrices']) for x in rows),default=0)>=2,'simultaneousOwnershipObserved':multi>0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V28_SAME_PARENT_MULTI_LEVEL_MAKER_KERNEL','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':{'dualSubmitEvents':dual,'multiOptionTicks':multi,'optionFillChildren':fills,'maxConcurrentChildren':max((int(x['functional']['maxSimultaneousOptionChildren']) for x in rows),default=0),'maxDistinctPrices':max((int(x['functional']['maxSimultaneousOptionDistinctPrices']) for x in rows),default=0)},'gates':gates,'kernelPass':bool(rows) and all(gates.values()),'rows':rows,'boundary':['kernel capability only; not ETH policy promotion','actual consumed HftBacktest passive orders only','first two distinct currently available passive outcome-bid levels; no fixed tick transfer','each option child uses legal-minimum tranche and total reservation remains within one authoritative Repair residual','no Taker','no PnL tuning']}
  op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'kernelPass':out['kernelPass'],'aggregate':out['aggregate'],'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
