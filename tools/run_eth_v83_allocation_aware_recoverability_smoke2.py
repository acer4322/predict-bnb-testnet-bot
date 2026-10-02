from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,joblib,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_allocaware_rec',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
g=front.g;v38=front.v38;v80=front.v80;EPS=1e-9;FIXED=[1916847,1916869]

class AllocationAwareRecoverabilityCandidate(front.ResponsibilityTransitionCandidate):
 def __init__(self,*a,**kw):
  self.allocAwareRecoverabilityChecks=0;self.allocAwareRecoverabilityOverrides=0;self.allocAwareRecoverabilityEvents=[];super().__init__(*a,**kw)
 def _v75_recoverability(self,t,side,qv):
  z=super()._v75_recoverability(t,side,qv);self.allocAwareRecoverabilityChecks+=1;legacy=dict(z);changed=False
  if not bool(z.get('recoverable')) and str(z.get('reason'))=='FUTURE_REPAIR_QTY_EXCEEDS_ROOM':
   need=z.get('futureNeedQty');room=z.get('repairRoomAfterOwned');req=z.get('futureRequiredQty')
   if need is not None and room is not None and req is not None and float(need)<=float(room)+EPS:
    physical=float(req);repair_alloc=min(float(room),physical);overflow=max(0.0,physical-repair_alloc)
    z=dict(z);z.update({'recoverable':True,'reason':'PASS_ALLOCATION_AWARE_COMPOSITE_OVERFLOW','legacyRecoverable':False,'legacyReason':'FUTURE_REPAIR_QTY_EXCEEDS_ROOM','managerRepairSolvable':True,'physicalCarrierQty':physical,'prospectiveRepairAllocationQty':repair_alloc,'prospectiveOverflowQty':overflow,'prospectiveOverflowSide':z.get('repairSide')});changed=True;self.allocAwareRecoverabilityOverrides+=1
  ev={'t':int(t),'side':side,'changed':changed,'legacy':legacy,'candidate':dict(z)};self.allocAwareRecoverabilityEvents.append(ev);return z
 def run_candidate(self,models,winner):
  r=self.run_transition(models,winner);r.update({'allocationAwareRecoverabilityChecks':self.allocAwareRecoverabilityChecks,'allocationAwareRecoverabilityOverrides':self.allocAwareRecoverabilityOverrides,'allocationAwareRecoverabilityEvents':self.allocAwareRecoverabilityEvents[:160]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(mids)
 tmp=Path(tempfile.mkdtemp(prefix='allocaware_rec_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'ALLOC_AWARE_RECOVERABILITY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ALLOC_AWARE_RECOVERABILITY_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for i,mid in enumerate(mids,1):
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=AllocationAwareRecoverabilityCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:r=c.run_candidate(models,cr['winner'])
   finally:c.close()
   ss=front.safety(r);cons=abs(float(r.get('v84CompositeFillQty') or 0)-float(r.get('v84RepairAllocatedQty') or 0)-float(r.get('v84OverflowAllocatedQty') or 0))<=1e-7
   row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'checks':r.get('allocationAwareRecoverabilityChecks'),'overrides':r.get('allocationAwareRecoverabilityOverrides'),'v83Checks':r.get('v83AdmissionChecks'),'v83Allows':r.get('v83AdmissionAllows'),'v83Blocks':r.get('v83AdmissionBlocks'),'v75Births':r.get('v75PreSafeThesisBirths'),'fills':r.get('actualFillEvents'),'rounds':r.get('v70dSemanticRounds'),'floor':r.get('floor'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'transitionBlocks':r.get('transitionBlocks'),'activeExpandFillQty':r.get('v64FillQty'),'overflowPaidQty':r.get('v84OverflowPaidQty'),'overflowRemainingQty':r.get('v84OverflowRemainingQty'),'safety':ss,'allocationConservation':cons,'overrideEvents':r.get('allocationAwareRecoverabilityEvents',[]),'v83Admissions':r.get('v83Admissions',[])};rows.append(row);print(json.dumps({'idx':i,'marketId':mid,'overrides':row['overrides'],'v83Allows':row['v83Allows'],'fills':row['fills'],'rounds':row['rounds'],'floor':row['floor'],'pnl':row['pnlDiagnosticOnly'],'safety':ss},ensure_ascii=False),flush=True)
  keys=list(rows[0]['safety']);safe_sum={k:sum(float(r['safety'][k]) for r in rows) for k in keys};gates={'overrideExercised':sum(int(r['overrides'] or 0) for r in rows)>0,'previouslyBlockedPathReachable':sum(int(r['v83Allows'] or 0) for r in rows)>0 or sum(int(r['v75Births'] or 0) for r in rows)>0,'allocationConservation':all(r['allocationConservation'] for r in rows),'zeroTruthMismatch':safe_sum['truthMismatch']==0,'zeroUnexplainedOverOwned':safe_sum['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':safe_sum['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':safe_sum['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':safe_sum['preBirthLeak']<=EPS,'zeroDuplicateDebt':safe_sum['duplicateDebt']<=EPS,'zeroSharedOverfill':safe_sum['sharedOverfill']<=EPS}
  out={'version':'ETH_V83_ALLOCATION_AWARE_RECOVERABILITY_V1_SMOKE2','date':'2026-09-04','researchOnly':True,'promotionEvidence':False,'fixedMarkets':mids,'decision':'KEEP_FOR_UNSEEN_FUNCTIONAL_REPLICATION' if all(gates.values()) else 'DIAGNOSE_BEFORE_MORE_MARKETS','gates':gates,'safetyAggregate':safe_sum,'aggregate':{'overrides':sum(int(r['overrides'] or 0) for r in rows),'v83Allows':sum(int(r['v83Allows'] or 0) for r in rows),'v75Births':sum(int(r['v75Births'] or 0) for r in rows),'fills':sum(int(r['fills'] or 0) for r in rows),'rounds':sum(float(r['rounds'] or 0) for r in rows),'pnlDiagnosticOnly':sum(float(r['pnlDiagnosticOnly'] or 0) for r in rows),'floorSumDiagnosticOnly':sum(float(r['floor'] or 0) for r in rows)},'rows':rows,'boundary':['only recoverability interpretation changed','Manager need must fit Repair room','venue-min physical overflow delegated to frozen AllocationLedger V2','ResponsibilityTransition frozen','no pExpand/qty/price/delay tuning','winner post-hoc only','no 8781','realistic HFT only']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'gates':gates,'aggregate':out['aggregate'],'safety':safe_sum},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
