from __future__ import annotations
import argparse, collections, json, math, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))
EPS=1e-9

try:
    import tools.run_eth_reservation_aware_initial_epoch_parallel_ab as prev
except ImportError:
    import importlib.util as _prev_iu
    _prev_p=Path(__file__).with_name("run_eth_reservation_aware_initial_epoch_parallel_ab.py")
    _prev_s=_prev_iu.spec_from_file_location("reservation_aware_initial_epoch_parallel_staged",_prev_p)
    _prev_m=_prev_iu.module_from_spec(_prev_s); sys.modules[_prev_s.name]=_prev_m; _prev_s.loader.exec_module(_prev_m)
    prev=_prev_m
try:
    from tools.eth_repair_modular.parent_execution_occupancy import ParentRepairExecutionOccupancyV1
except ImportError:
    import importlib.util as _iu
    _p=Path(__file__).with_name("parent_execution_occupancy.py")
    _s=_iu.spec_from_file_location("parent_execution_occupancy_staged",_p)
    _m=_iu.module_from_spec(_s); sys.modules[_s.name]=_m; _s.loader.exec_module(_m)
    ParentRepairExecutionOccupancyV1=_m.ParentRepairExecutionOccupancyV1

old=prev.old; x2=prev.x2; v38=prev.v38; v80=prev.v80

class ParentOccupancyAnchorlessParallelHFT(prev.ReservationAwareInitialEpochParallelHFT):
    def __init__(self,*a,**kw):
        self.parentExecutionOccupancy=ParentRepairExecutionOccupancyV1()
        self.occupancyEvents=[]
        self.occupancyPassiveBlocks=0
        self.occupancyPassiveResizes=0
        self.anchorlessActiveAllows=0
        self.anchorlessActiveSubmits=0
        self.anchorlessActiveKeys=set()
        self.occupancyBoundPassiveCarriers=0
        super().__init__(*a,**kw)

    def _sync_parent_occupancy(self):
        for key,e in list(getattr(self,'carrierLedger',{}).items()):
            try: pid=e.get('parentId')
            except Exception: pid=None
            if pid is None or str(e.get('objectiveRole') or '').upper()!='REPAIR':
                continue
            self.parentExecutionOccupancy.sync_from_carrier(str(key),e,parent_id=int(pid),route=str(e.get('lane') or 'REPAIR'))

    def _parent_debt_now(self,pid:int)->float:
        pay=self._current_payoffs()
        return max(0.0,float(self._manager_debt_for_parent(int(pid),float(pay['gap']))))

    def submit(self,t,side,p,q):
        role=str(getattr(self,'_pendingAuthorizedRole',None) or '').upper()
        pid=getattr(self,'_pendingParentId',None)
        lane=getattr(self,'_pendingLane',None)
        n0=getattr(self,'n',0)
        q0=float(q)
        debt_at_submit=None
        if role=='REPAIR' and pid is not None:
            self._sync_parent_occupancy()
            debt_at_submit=self._parent_debt_now(int(pid))
            active_reserved=sum(c.unresolved_qty for c in self.parentExecutionOccupancy.carriers.values() if c.parent_id==int(pid) and 'ACTIVE' in str(c.route).upper())
            available=self.parentExecutionOccupancy.available(int(pid),debt_at_submit)
            legal=(1.0/float(p)) if float(p)>EPS else math.inf
            if active_reserved>EPS and available+EPS < legal:
                self.occupancyPassiveBlocks+=1
                self.occupancyEvents.append({'t':int(t),'event':'PARENT_OCCUPANCY_PASSIVE_BLOCK','parentId':int(pid),'side':side,'requestedQty':q0,'availableQty':available,'legalQty':legal,'debt':debt_at_submit,'activeReservedQty':active_reserved,'lane':lane})
                self._pendingParentId=None; self._pendingLane=None
                return False
            if active_reserved>EPS and q0>available+EPS:
                q=float(available); self.occupancyPassiveResizes+=1
                self.occupancyEvents.append({'t':int(t),'event':'PARENT_OCCUPANCY_PASSIVE_RESIZE','parentId':int(pid),'side':side,'requestedQty':q0,'admittedQty':float(q),'availableQty':available,'legalQty':legal,'debt':debt_at_submit,'activeReservedQty':active_reserved,'lane':lane})
        ok=super().submit(t,side,p,q)
        if ok and role=='REPAIR' and pid is not None:
            key=f'{side}_{n0}'
            e=getattr(self,'carrierLedger',{}).get(key)
            if e is not None:
                e['parentDebtAtSubmit']=float(debt_at_submit if debt_at_submit is not None else self._parent_debt_now(int(pid)))
                self.parentExecutionOccupancy.sync_from_carrier(key,e,parent_id=int(pid),route=str(e.get('lane') or lane or 'PASSIVE_REPAIR'))
                self.occupancyBoundPassiveCarriers+=1
        return ok

    def _cancel_key(self,t,key):
        ok=super()._cancel_key(t,key)
        if ok:
            self.parentExecutionOccupancy.mark_cancel_pending(str(key))
        return ok

    def _bind_all_repair_carriers_to_v84(self):
        self._sync_parent_occupancy()
        if not hasattr(self,'v84Composite'):
            return
        pay=self._current_payoffs()
        for key,e in list(getattr(self,'carrierLedger',{}).items()):
            try: pid=e.get('parentId')
            except Exception: pid=None
            if pid is None or str(e.get('objectiveRole') or '').upper()!='REPAIR' or str(key) in self.v84Composite:
                continue
            q=float(e.get('submittedQty') or 0.0)
            if q<=EPS: continue
            o=getattr(self,'orders',{}).get(key,{})
            px=float(o.get('price') or 0.0)
            debt=float(e.get('parentDebtAtSubmit') or self._manager_debt_for_parent(int(pid),float(pay['gap'])))
            self.v84Composite[str(key)]={'key':str(key),'side':str(e.get('side')),'parentId':int(pid),'price':px,'submittedQty':q,'gapAtSubmit':debt,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'PARENT_OCCUPANCY_BOUND_REPAIR_CARRIER'}
            self.occupancyEvents.append({'event':'PARENT_OCCUPANCY_BIND_ALLOCATION_V2','key':str(key),'parentId':int(pid),'submittedQty':q,'debtAtSubmit':debt})

    def _scan_v84(self,t):
        self._bind_all_repair_carriers_to_v84()
        return super()._scan_v84(t)

    def _anchorless_execution_evidence(self,pid,st):
        return True

    def _maybe_hard_active(self,t):
        before=int(getattr(self,'parallelRepairSubmits',0))
        ok=super()._maybe_hard_active(t)
        if ok or int(getattr(self,'parallelRepairSubmits',0))>before:
            return bool(ok)
        ev=None
        for row in reversed(getattr(self,'reservationAwareEvents',[])):
            if int(row.get('t') or -1)!=int(t):
                break
            if row.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION':
                ev=row; break
        if not ev or not bool(ev.get('allow')) or ev.get('postDecisionBlock')!='NO_PARENT_REPAIR_LINEAGE_ANCHOR':
            return False

        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict): return False
        pid=int(rp.get('id')); side=str(rp.get('side'))
        st=getattr(self,'generationEpochByParent',{}).get(pid)
        if st is None or not getattr(st,'armed',False) or getattr(st,'active_owned',False): return False
        if not self._anchorless_execution_evidence(pid,st):
            self.occupancyEvents.append({'t':int(t),'event':'ANCHORLESS_ACTIVE_WAIT_PASSIVE_EVIDENCE','parentId':pid,'epoch':int(getattr(st,'epoch',0))})
            return False
        ask=ev.get('liveAsk'); q=ev.get('legalPhysicalQty'); debt=float(ev.get('managerDebt') or 0.0)
        if ask is None or q is None or float(q)<=EPS: return False
        self._sync_parent_occupancy()
        avail=self.parentExecutionOccupancy.available(pid,debt)
        if float(q)>avail+EPS:
            self.occupancyEvents.append({'t':int(t),'event':'ANCHORLESS_ACTIVE_OCCUPANCY_BLOCK','parentId':pid,'qty':float(q),'availableQty':avail,'debt':debt})
            return False

        oid=(self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
        synthetic_anchor=f'PARENT_{pid}_RESPONSIBILITY_ANCHOR'
        self.anchorlessActiveAllows+=1
        if hasattr(self,'hardConfirmed'): self.hardConfirmed.add(pid)
        if hasattr(self,'hardEventConfirmedCount'): self.hardEventConfirmedCount+=1
        st.active_owned=True
        submit_ok=self._submit_active(int(t),pid,synthetic_anchor,side,float(ask),float(q),oid)
        if not submit_ok:
            st.active_owned=False
            if hasattr(self,'hardConfirmed'): self.hardConfirmed.discard(pid)
            return False
        self.anchorlessActiveSubmits+=1
        self.parallelRepairSubmits+=1; self.parallelRepairEligible+=1; self.generationEpochActiveSubmits+=1
        if self.reservationAwareBlocks>0: self.reservationAwareBlocks-=1
        self.reservationAwareEligible+=1
        active=getattr(self,'activeByParent',{}).get(pid,{}); ak=active.get('key')
        if ak:
            self.anchorlessActiveKeys.add(str(ak)); self.parallelRepairActiveKeys.add(str(ak)); self.generationEpochActiveFillKeys.add(str(ak))
            ae=getattr(self,'carrierLedger',{}).get(str(ak),{})
            ae['parentDebtAtSubmit']=debt
            self.parentExecutionOccupancy.reserve(key=str(ak),parent_id=pid,route='ACTIVE_REPAIR',qty=float(q))
            if hasattr(self,'v84Composite'):
                self.v84Composite[str(ak)]={'key':str(ak),'side':side,'parentId':pid,'price':float(ask),'submittedQty':float(q),'gapAtSubmit':debt,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'PARENT_OCCUPANCY_ANCHORLESS_ACTIVE_REPAIR'}
                if hasattr(self,'v84CompositeSubmits'): self.v84CompositeSubmits+=1
        self.occupancyEvents.append({'t':int(t),'event':'ANCHORLESS_PARENT_AUTHORIZED_ACTIVE_SUBMIT','parentId':pid,'side':side,'activeKey':ak,'syntheticAnchor':synthetic_anchor,'qty':float(q),'ask':float(ask),'debt':debt,'availableBefore':avail})
        return True

    def run_parent_occupancy(self,models,winner):
        r=super().run_reservation(models,winner)
        self._sync_parent_occupancy()
        parents={}
        for c in self.parentExecutionOccupancy.carriers.values():
            if str(c.parent_id) not in parents:
                try: debt=self._parent_debt_now(c.parent_id)
                except Exception: debt=0.0
                parents[str(c.parent_id)]=self.parentExecutionOccupancy.describe_parent(c.parent_id,debt)
        r.update({'parentExecutionOccupancy':self.parentExecutionOccupancy.name,'occupancyPassiveBlocks':self.occupancyPassiveBlocks,'occupancyPassiveResizes':self.occupancyPassiveResizes,'occupancyBoundPassiveCarriers':self.occupancyBoundPassiveCarriers,'anchorlessActiveAllows':self.anchorlessActiveAllows,'anchorlessActiveSubmits':self.anchorlessActiveSubmits,'anchorlessActiveKeys':sorted(self.anchorlessActiveKeys),'occupancyParents':parents,'occupancyEvents':self.occupancyEvents[:600]})
        return r

def make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47):
    return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())

def safety(r): return old.birth.base.front.safety(r)
def alloc(sim,r): return prev.alloc(sim,r)
def slim(r):
    return {'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0.0),'anchorlessActiveSubmits':int(r.get('anchorlessActiveSubmits') or 0),'overflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0.0),'overflowPaidQty':float(r.get('v84OverflowPaidQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); mid=int(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output); outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=f'parent_occupancy_anchorless_{mid}_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'PARENT_OCCUPANCY_ANCHORLESS_AB','market':mid,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'PARENT_OCCUPANCY_ANCHORLESS_AB_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{mid}.json.xz'
        b=make(prev.ReservationAwareInitialEpochParallelHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: br=b.run_reservation(models,cr['winner']); bcons,bbound,bparents=alloc(b,br)
        finally: b.close()
        c=make(ParentOccupancyAnchorlessParallelHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: rr=c.run_parent_occupancy(models,cr['winner']); cons,bound,parents=alloc(c,rr)
        finally: c.close()
        bs=safety(br); ss=safety(rr); safe=all(float(v or 0.0)<=EPS for v in ss.values())
        occupancy_bound=all(float(p.get('overReservedQty') or 0.0)<=EPS for p in (rr.get('occupancyParents') or {}).values())
        physical=float(rr.get('parallelRepairActiveFillQty') or 0.0)>EPS
        floorok=float(rr.get('floor') or 0.0)>=float(br.get('floor') or 0.0)-1e-7
        gates={'controlSafe':all(float(v or 0.0)<=EPS for v in bs.values()),'parentOccupancyModuleActive':rr.get('parentExecutionOccupancy')=='parent_scoped_repair_execution_occupancy_v1','anchorlessActiveMaterialized':int(rr.get('anchorlessActiveSubmits') or 0)>0,'physicalActiveFill':physical,'candidateSafetyZero':safe,'allocationConservation':cons,'allocationParentDebtBounded':bound,'executionOccupancyBounded':occupancy_bound,'terminalFloorNonWorse':floorok}
        if not safe or not cons or not bound or not occupancy_bound or not floorok: decision='REJECT_PARENT_OCCUPANCY_ANCHORLESS_SAFETY_OR_FLOOR'
        elif not gates['anchorlessActiveMaterialized']: decision='PARENT_OCCUPANCY_SAFE_BUT_ANCHORLESS_NOT_MATERIALIZED'
        elif not physical: decision='PARENT_OCCUPANCY_ANCHORLESS_SUBMIT_NO_PHYSICAL_FILL'
        else: decision='FUNCTIONAL_PASS_PARENT_OCCUPANCY_ANCHORLESS_ACTIVE'
        out={'version':'ETH_PARENT_SCOPED_EXECUTION_OCCUPANCY_ANCHORLESS_PARALLEL_AB_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'control':slim(br),'candidate':slim(rr),'controlSafety':bs,'candidateSafety':ss,'occupancyParents':rr.get('occupancyParents'),'allocationParents':parents,'occupancyEvents':rr.get('occupancyEvents',[])[:600],'reservationEvents':rr.get('reservationAwareEvents',[])[:500],'reasonCounts':dict(collections.Counter(e.get('reason') for e in rr.get('reservationAwareEvents',[]) if e.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION')),'boundary':['single fresh market first','Repair parent/responsibility supplies Active lineage; passiveKey is optional sibling reference only','parent-scoped occupancy binds Passive + Active prospective unresolved quantity','cancel-pending remains reserved until terminal confirmation','all Repair carrier confirmed fills bound into AllocationLedger V2','Repair-first/overflow-second accounting frozen','economic ceiling/legal min/payment progress/<=180 fences frozen','no threshold/price/delay tuning','winner post-hoc only; no Target runtime input; no dream fill; no 8781']}
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'gates':gates,'control':out['control'],'candidate':out['candidate'],'candidateSafety':ss,'reasonCounts':out['reasonCounts']},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
