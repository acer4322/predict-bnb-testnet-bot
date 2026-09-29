from __future__ import annotations
import argparse,json,math,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.residual_multipayment import ResidualCompositeMultipaymentContext,ResidualCompositeMultipaymentRouterPolicyV1
from tools.eth_repair_modular.recoverability import RecursiveCompositeCurrentCoordinateRecoverabilityPolicy,RecursiveCompositeRecoverabilityContext
EPS=1e-9;MID=1946784;pe=base.pe

class ResidualCompositeRearmShadowHFT(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):
  self.residualPolicy=ResidualCompositeMultipaymentRouterPolicyV1();self.recursivePolicy=RecursiveCompositeCurrentCoordinateRecoverabilityPolicy();self.residualShadow=[];self._seen=set()
  super().__init__(*a,**kw)
 def process(self,t):
  super().process(t);self._refresh_carrier_ledger(int(t))
  rp=getattr(self,'repairParent',None)
  if not isinstance(rp,dict):return
  pid=int(rp.get('id'));side=str(rp.get('side') or '').upper()
  if side not in ('UP','DOWN'):return
  debt=float(self._parent_debt_now(pid));qv=v1.quotes(self.book)
  if debt<=EPS or not qv or side not in qv:return
  price=float(qv[side]['bid']);physical=1.0/price if price>EPS else math.inf
  if not math.isfinite(physical) or physical<=debt+EPS or physical>12.0+EPS:return
  self._sync_parent_occupancy();occ=self.parentExecutionOccupancy.describe_parent(pid,debt);live=any(float(x.get('unresolvedQty') or 0)>EPS for x in occ.get('carriers',[]));pending=any(bool(x.get('cancelPending')) for x in occ.get('carriers',[]));st=getattr(self,'generationEpochByParent',{}).get(pid);active_owned=bool(getattr(st,'active_owned',False)) if st is not None else pid in getattr(self,'activeByParent',{})
  pstate=self.allocationLedgerV2.describe_parent(pid) or {};prior_paid=float(pstate.get('repairPaid') or 0.0)
  if prior_paid<=EPS:return
  repair=min(debt,physical);overflow=max(0.0,physical-debt);floor_before=float(self._raw_floor()[0]);u=float(self.auth_inv()['UP']);d=float(self.auth_inv()['DOWN']);cost=float(self.cost);hu=u+(physical if side=='UP' else 0);hd=d+(physical if side=='DOWN' else 0);hf=min(hu,hd)-(cost+physical*price);surplus_side='UP' if hu>hd+EPS else 'DOWN' if hd>hu+EPS else side
  rec=self.recursivePolicy.evaluate(RecursiveCompositeRecoverabilityContext(floor_before,hf,surplus_side,abs(hu-hd),float(qv['UP']['bid']),float(qv['DOWN']['bid']),4,12.0))
  truth=self.auth_inv();opp='DOWN' if side=='UP' else 'UP';truth_role='REPAIR' if float(truth[side])<float(truth[opp])-EPS else 'EXPAND'
  common=dict(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,responsibility_id=pid,responsibility_side=side,authorized_role='REPAIR',physical_truth_role=truth_role,prior_confirmed_payments=1,authoritative_residual_debt=debt,candidate_physical_qty=physical,candidate_repair_allocation=repair,candidate_overflow_allocation=overflow,shared_remaining_budget=12.0,ledger_snapshot_current=True,pending_sibling_reconciliation=pending,same_parent_live_carrier=live,venue_legal=True,recursive_current_coordinate_recoverable=bool(rec.recoverable))
  fd=self.residualPolicy.evaluate(ResidualCompositeMultipaymentContext(active_already_owned=active_owned,**common));cf=self.residualPolicy.evaluate(ResidualCompositeMultipaymentContext(active_already_owned=False,**common))
  sig=(round(debt,9),round(price,4),active_owned,live,pending,fd.reason,cf.reason)
  if sig in self._seen:return
  self._seen.add(sig);self.residualShadow.append({'t':int(t),'parentId':pid,'side':side,'debt':debt,'price':price,'physicalQty':physical,'repairAllocation':repair,'overflowAllocation':overflow,'priorRepairPaid':prior_paid,'activeOwnedFactual':active_owned,'sameParentLiveCarrier':live,'pendingSiblingReconciliation':pending,'floorBefore':floor_before,'floorAfterHypCarrier':hf,'recursive':{'recoverable':rec.recoverable,'reason':rec.reason,'recoveredStep':rec.recovered_step,'terminalFloor':rec.terminal_floor,'terminalDebt':rec.terminal_debt,'path':list(rec.path)},'factual':{'allow':fd.allow,'reason':fd.reason},'counterfactualActiveRearmed':{'allow':cf.allow,'reason':cf.reason}})
 def run_shadow(self,models,winner):
  r=self.run_locked(models,winner);r['residualCompositeRearmShadow']=self.residualShadow[:200];return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='residual_rearm_shadow_1946784_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'RESIDUAL_COMPOSITE_REARM_SHADOW_1946784','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'RESIDUAL_COMPOSITE_REARM_SHADOW_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(ResidualCompositeRearmShadowHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_shadow(models,cr['winner']);cons,bound,parents=pe.alloc(s,r)
  finally:s.close()
  rows=r.get('residualCompositeRearmShadow') or [];switch=[x for x in rows if not x['factual']['allow'] and x['counterfactualActiveRearmed']['allow']]
  out={'version':'ETH_MULTISLOT_RESIDUAL_COMPOSITE_REARM_SHADOW_1946784_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'candidate':base.slim(r),'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'rows':rows,'factualBlockedCounterfactualAllowed':len(switch),'decision':'STALE_ACTIVE_OWNERSHIP_BLOCKS_RECOVERABLE_RESIDUAL_COMPOSITE' if switch else 'ACTIVE_REARM_NOT_SUFFICIENT','boundary':['shadow only; no action change','residual composite router V1 frozen','factual current active ownership vs counterfactual terminal-active rearm only','strict-past current bids only','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'candidate':out['candidate'],'switchCount':len(switch),'rows':rows},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
