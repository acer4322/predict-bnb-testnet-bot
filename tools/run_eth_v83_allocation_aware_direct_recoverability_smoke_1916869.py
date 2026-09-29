from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_allocaware_v1',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
EPS=1e-9;FIXED=1916869;v38=front.v38;v80=front.v80

class AllocationAwareDirectRecoverability(front.ResponsibilityTransitionCandidate):
    def __init__(self,*a,**kw):
        self.allocAwareChecks=0;self.allocAwareDirectPasses=0;self.allocAwareEvents=[]
        super().__init__(*a,**kw)
    def _v75_recoverability(self,t,side,qv):
        z=dict(super()._v75_recoverability(t,side,qv));self.allocAwareChecks+=1
        z['legacyRecoverable']=bool(z.get('recoverable'));z['legacyReason']=z.get('reason')
        if z.get('recoverable') or z.get('reason')!='FUTURE_REPAIR_QTY_EXCEEDS_ROOM':
            return z
        vals=[z.get('futureRequiredQty'),z.get('repairRoomAfterOwned'),z.get('admissibleFutureRepairPrice'),z.get('projectedFloorAfterOwnedRepair')]
        if any(v is None for v in vals):return z
        q=float(vals[0]);room=max(0.0,float(vals[1]));p=float(vals[2]);projected=float(vals[3])
        repair=min(q,room);overflow=max(0.0,q-room);floor_after=projected+repair*(1.0-p)-overflow*p
        ev={'t':int(t),'side':side,'carrierQty':q,'repairRoom':room,'repairPaidQty':repair,'overflowQty':overflow,'admissibleRepairPrice':p,'projectedFloorBeforeCarrier':projected,'floorAfterPhysicalCarrier':floor_after,'legacyReason':z.get('reason')}
        z.update({'allocationAwareRepairPaidQty':repair,'allocationAwareOverflowQty':overflow,'allocationAwareFloorAfterPhysicalCarrier':floor_after})
        if floor_after>=-EPS:
            z['recoverable']=True;z['reason']='ALLOCATION_AWARE_DIRECT_RECOVERABLE';self.allocAwareDirectPasses+=1;ev['reclassified']=True
        else:
            ev['reclassified']=False;ev['reason']='RECURSIVE_OVERFLOW_STILL_REQUIRED'
        self.allocAwareEvents.append(ev)
        return z
    def run_candidate(self,models,winner):
        r=self.run_transition(models,winner);r.update({'allocationAwareRecoverabilityChecks':self.allocAwareChecks,'allocationAwareDirectPasses':self.allocAwareDirectPasses,'allocationAwareRecoverabilityEvents':self.allocAwareEvents[:120]});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='v83_allocaware_1916869_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'V83_ALLOCAWARE_DIRECT_1916869','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_ALLOCAWARE_DIRECT_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(front.ResponsibilityTransitionCandidate)
        try:br=b.run_transition(models,cr['winner'])
        finally:b.close()
        c=mk(AllocationAwareDirectRecoverability)
        try:rr=c.run_candidate(models,cr['winner'])
        finally:c.close()
        bs=front.safety(br);ss=front.safety(rr)
        cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        b_allow=int(br.get('v83AdmissionAllows') or 0);c_allow=int(rr.get('v83AdmissionAllows') or 0)
        b_birth=int(br.get('v75Births') or 0);c_birth=int(rr.get('v75Births') or 0)
        exercised=int(rr.get('allocationAwareDirectPasses') or 0)>0
        progressed=(c_allow>b_allow) or (c_birth>b_birth)
        safety_zero=all(float(v)<=EPS for v in ss.values())
        fills=int(rr.get('actualFillEvents') or 0);new_submit=c_allow>b_allow
        if not safety_zero or not cons:
            decision='REJECT_ALLOCATION_AWARE_DIRECT_RECOVERABILITY'
        elif exercised and progressed and new_submit and fills>int(br.get('actualFillEvents') or 0):
            decision='FUNCTIONAL_PASS_KEEP_FOR_SMALL_FRESH_REPLICATION'
        elif exercised and progressed and new_submit:
            decision='EXECUTION_INCONCLUSIVE_SUBMIT_NO_NEW_FILL'
        elif exercised and progressed:
            decision='OWNERSHIP_PROGRESS_ONLY_DIAGNOSE_NEXT_GATE'
        else:
            decision='NO_FUNCTIONAL_PROGRESS_DIAGNOSE'
        out={'version':'ETH_V83_ALLOCATION_AWARE_DIRECT_RECOVERABILITY_BEHAVIOR_SMOKE_1916869','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':{'reclassificationExercised':exercised,'progressBeyondBaseline':progressed,'allocationConservation':cons,'safetyZero':safety_zero},'baseline':{'fills':br.get('actualFillEvents'),'pnlDiagnosticOnly':br.get('pnlDiagnosticOnly'),'floor':br.get('floor'),'v75Births':br.get('v75Births'),'v83Checks':br.get('v83AdmissionChecks'),'v83Allows':br.get('v83AdmissionAllows'),'v83Blocks':br.get('v83AdmissionBlocks'),'transitionBlocks':br.get('transitionBlocks')},'candidate':{'fills':rr.get('actualFillEvents'),'pnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),'v75Births':rr.get('v75Births'),'v83Checks':rr.get('v83AdmissionChecks'),'v83Allows':rr.get('v83AdmissionAllows'),'v83Blocks':rr.get('v83AdmissionBlocks'),'transitionBlocks':rr.get('transitionBlocks'),'allocationAwareDirectPasses':rr.get('allocationAwareDirectPasses'),'semanticRounds':rr.get('v70dSemanticRounds'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty')},'safety':ss,'allocationAwareEvents':rr.get('allocationAwareRecoverabilityEvents',[]),'v83Admissions':rr.get('v83Admissions',[]),'v75Events':rr.get('v75Events',[]),'allocationEvents':rr.get('allocationV2Events',[]),'boundary':['single deterministic recoverability-kernel replacement only','legacy PASS/non-room rejection unchanged','negative-floor recursive overflow still rejected','ResponsibilityTransition/AllocationLedgerV2/RepairExecutionRouter frozen','pExpand threshold 0.50 frozen','qty/price/delay frozen','winner post-hoc only','realistic HFT only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':out['gates'],'baseline':out['baseline'],'candidate':out['candidate'],'safety':ss,'allocAware':out['allocationAwareEvents'][:10]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
