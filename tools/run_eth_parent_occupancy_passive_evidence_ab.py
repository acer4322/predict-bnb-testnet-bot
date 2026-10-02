from __future__ import annotations
import argparse, collections, json, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))
EPS=1e-9

import tools.run_eth_parent_occupancy_anchorless_parallel_ab as v1
prev=v1.prev; old=v1.old; v38=v1.v38; v80=v1.v80

class PassiveEvidenceParentOccupancyHFT(v1.ParentOccupancyAnchorlessParallelHFT):
    def __init__(self,*a,**kw):
        self.passiveMaterializedEpochs=set()
        self.passiveEvidenceEvents=[]
        self.anchorlessWaitPassiveEvidence=0
        super().__init__(*a,**kw)

    def submit(self,t,side,p,q):
        role=str(getattr(self,'_pendingAuthorizedRole',None) or '').upper()
        pid=getattr(self,'_pendingParentId',None)
        ok=super().submit(t,side,p,q)
        if ok and role=='REPAIR' and pid is not None:
            st=getattr(self,'generationEpochByParent',{}).get(int(pid))
            if st is not None:
                token=(int(pid),int(getattr(st,'epoch',0)))
                if token not in self.passiveMaterializedEpochs:
                    self.passiveMaterializedEpochs.add(token)
                    self.passiveEvidenceEvents.append({'t':int(t),'event':'PASSIVE_ROUTE_MATERIALIZED_FOR_EPOCH','parentId':int(pid),'epoch':token[1],'side':side})
        return ok

    def _anchorless_execution_evidence(self,pid,st):
        token=(int(pid),int(getattr(st,'epoch',0)))
        if token in self.passiveMaterializedEpochs:
            return True
        try:
            obs=st.observe(parent_fill_now=float(self._parent_actual_fill(int(pid))),churn_now=int(self._parent_churn_count(int(pid))),paid_total_now=float(self._paid_total(int(pid))))
            if int(obs.get('postEpochChurn') or 0)>0:
                return True
        except Exception:
            pass
        self.anchorlessWaitPassiveEvidence+=1
        return False

    def run_passive_evidence(self,models,winner):
        r=super().run_parent_occupancy(models,winner)
        r.update({'passiveEvidenceEpochCount':len(self.passiveMaterializedEpochs),'passiveEvidenceEvents':self.passiveEvidenceEvents[:160],'anchorlessWaitPassiveEvidence':self.anchorlessWaitPassiveEvidence})
        return r

def make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47):
    return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())

def safety(r): return old.birth.base.front.safety(r)
def alloc(sim,r): return prev.alloc(sim,r)
def slim(r):
    return {'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0),'anchorlessActiveSubmits':int(r.get('anchorlessActiveSubmits') or 0),'passiveEvidenceEpochCount':int(r.get('passiveEvidenceEpochCount') or 0),'anchorlessWaitPassiveEvidence':int(r.get('anchorlessWaitPassiveEvidence') or 0),'overflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0),'overflowPaidQty':float(r.get('v84OverflowPaidQty') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); mid=int(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output); outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=f'passive_evidence_parent_occupancy_{mid}_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'PASSIVE_EVIDENCE_PARENT_OCCUPANCY_AB','market':mid,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'PASSIVE_EVIDENCE_PARENT_OCCUPANCY_AB_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{mid}.json.xz'
        b=make(v1.ParentOccupancyAnchorlessParallelHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: br=b.run_parent_occupancy(models,cr['winner']); bcons,bbound,bparents=alloc(b,br)
        finally: b.close()
        c=make(PassiveEvidenceParentOccupancyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: rr=c.run_passive_evidence(models,cr['winner']); cons,bound,parents=alloc(c,rr)
        finally: c.close()
        bs=safety(br); ss=safety(rr); safe=all(float(v or 0)<=EPS for v in ss.values())
        occupancy_bound=all(float(p.get('overReservedQty') or 0)<=EPS for p in (rr.get('occupancyParents') or {}).values())
        floorok=float(rr.get('floor') or 0)>=float(br.get('floor') or 0)-1e-7
        control_harm=float(br.get('floor') or 0)<-EPS
        recovers_control_harm=(not control_harm) or float(rr.get('floor') or 0)>float(br.get('floor') or 0)+EPS
        gates={'candidateSafetyZero':safe,'allocationConservation':cons,'allocationParentDebtBounded':bound,'executionOccupancyBounded':occupancy_bound,'passiveEvidenceGateExercised':int(rr.get('anchorlessWaitPassiveEvidence') or 0)>0,'passiveRouteMaterialized':int(rr.get('passiveEvidenceEpochCount') or 0)>0,'floorNonWorseThanUnsafeAnchorlessControl':floorok,'recoversUnsafeAnchorlessEconomicHarm':recovers_control_harm}
        if not safe or not cons or not bound or not occupancy_bound: decision='REJECT_PASSIVE_EVIDENCE_OCCUPANCY_SAFETY'
        elif not floorok: decision='REJECT_PASSIVE_EVIDENCE_OCCUPANCY_FLOOR_WORSE'
        elif int(rr.get('parallelRepairActiveFillQty') or 0)>EPS: decision='FUNCTIONAL_PASS_PASSIVE_EVIDENCE_WITH_PHYSICAL_ACTIVE'
        else: decision='PASS_NONREGRESSION_PASSIVE_FIRST_NO_ACTIVE_ON_THIS_MARKET'
        out={'version':'ETH_PARENT_OCCUPANCY_PASSIVE_EXECUTION_EVIDENCE_AB_V1','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'controlUnsafeAnchorless':slim(br),'candidatePassiveEvidence':slim(rr),'controlSafety':bs,'candidateSafety':ss,'passiveEvidenceEvents':rr.get('passiveEvidenceEvents',[])[:160],'occupancyEvents':rr.get('occupancyEvents',[])[:400],'reservationEvents':rr.get('reservationAwareEvents',[])[:400],'occupancyParents':rr.get('occupancyParents'),'allocationParents':parents,'reasonCounts':dict(collections.Counter(e.get('reason') for e in rr.get('reservationAwareEvents',[]) if e.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION')),'boundary':['Active cannot be the first execution route of a fresh Repair epoch','event evidence only: a Passive Repair carrier materialized in the same parent+epoch, or post-epoch disconnect evidence exists','no fixed millisecond/tick delay added','parent-scoped Passive+Active occupancy retained','anchorless Active remains allowed after evidence when no Passive sibling is currently live','AllocationLedger V2 Repair-first/overflow-second retained','economic/legal/payment/<=180 fences retained','no Target runtime input; no dream fill; no 8781']}
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'gates':gates,'control':out['controlUnsafeAnchorless'],'candidate':out['candidatePassiveEvidence'],'candidateSafety':ss,'reasonCounts':out['reasonCounts']},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
