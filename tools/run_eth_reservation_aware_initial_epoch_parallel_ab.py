from __future__ import annotations
import argparse, importlib.util, json, os, shutil, sys, tempfile, threading, time, zipfile, math
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))
EPS=1e-9

def load_sibling(name,filename):
    p=Path(__file__).with_name(filename); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

x2=load_sibling("reservation_aware_initial_epoch_dep","run_eth_initial_epoch_x_parallel_repair_2x2.py")
try:
    from tools.eth_repair_modular.parallel_repair_execution_budget import ParallelRepairExecutionContext, SameParentAggregateParallelRepairPolicyV1
except ImportError:
    _p=Path(__file__).with_name("parallel_repair_execution_budget.py")
    _s=importlib.util.spec_from_file_location("parallel_repair_execution_budget_staged",_p)
    _m=importlib.util.module_from_spec(_s); sys.modules[_s.name]=_m; _s.loader.exec_module(_m)
    ParallelRepairExecutionContext=_m.ParallelRepairExecutionContext
    SameParentAggregateParallelRepairPolicyV1=_m.SameParentAggregateParallelRepairPolicyV1
old=x2.old; base=x2.base; v38=x2.v38; v80=x2.v80

class ReservationAwareInitialEpochParallelHFT(x2.InitialEpochParallelHFT):
    def __init__(self,*a,**kw):
        self.reservationAwareChecks=0; self.reservationAwareEligible=0; self.reservationAwareBlocks=0
        self.reservationAwareEvents=[]; self.reservationSnapshots=[]
        self.sharedBudgetPolicy=SameParentAggregateParallelRepairPolicyV1()
        super().__init__(*a,**kw)

    def _carrier_unresolved_reservation(self,key,e):
        if bool(e.get('terminalConfirmed')):
            return 0.0
        o=getattr(self,'orders',{}).get(key,{})
        submitted=float(e.get('submittedQty') or o.get('qty') or 0.0)
        filled=float(e.get('actualFilled') or 0.0)
        return max(0.0,submitted-filled)

    def _maybe_hard_active(self,t):
        self._ensure_initial_repair_epoch(int(t))
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict): return False
        pid=int(rp.get('id')); side=str(rp.get('side'))
        st=getattr(self,'generationEpochByParent',{}).get(pid)
        if st is None or not getattr(st,'armed',False): return False

        self.reservationAwareChecks+=1; self.parallelRepairChecks+=1
        parent_fill_now=float(self._parent_actual_fill(pid)); churn_now=int(self._parent_churn_count(pid)); paid_now=float(self._paid_total(pid))
        obs=st.observe(parent_fill_now=parent_fill_now,churn_now=churn_now,paid_total_now=paid_now)
        pay=self._current_payoffs(); seconds_left=(int(self.capEnd)-int(t))/1000.0
        qv=old.v1.quotes(self.book); ask=None; legal=None
        if qv and side in qv and qv[side].get('ask') is not None:
            ask=float(qv[side]['ask']); legal=(1.0/ask) if ask>EPS else math.inf
        debt=float(self._manager_debt_for_parent(pid,float(pay['gap'])))
        active_owned=pid in getattr(self,'activeByParent',{}) or bool(getattr(st,'active_owned',False)); hard_confirmed=pid in getattr(self,'hardConfirmed',set())
        psv,passive_live=self._find_parent_repair_anchor(pid)
        passive_key=None; passive_reserved=0.0; passive_entry=None
        if psv is not None:
            passive_key,passive_entry,rem,o=psv
            passive_reserved=self._carrier_unresolved_reservation(str(passive_key),passive_entry)
        other_reserved=0.0; other_reservations=[]
        for key,e in getattr(self,'carrierLedger',{}).items():
            try:
                if int(e.get('parentId') or -1)!=pid: continue
            except Exception: continue
            if str(e.get('objectiveRole') or '').upper()!='REPAIR': continue
            if passive_key is not None and str(key)==str(passive_key): continue
            lane=str(e.get('lane') or '').upper()
            if lane.startswith('ACTIVE_') or 'ACTIVE_REPAIR' in lane: continue
            q=self._carrier_unresolved_reservation(str(key),e)
            if q>EPS:
                other_reserved+=q; other_reservations.append({'key':str(key),'reservedQty':q})

        floor_ref=None; epoch_slice=0.0
        if ask is not None and legal is not None and math.isfinite(legal) and side in ('UP','DOWN'):
            epoch_slice=min(max(0.0,float(getattr(st,'attached_debt',0.0))),float(legal))
            weak=float(pay['up'] if side=='UP' else pay['down']); other=float(pay['down'] if side=='UP' else pay['up'])
            floor_ref=min(weak+(1.0-ask)*epoch_slice,other-ask*epoch_slice)
        ctx=ParallelRepairExecutionContext(t=int(t),seconds_left=seconds_left,parent_id=pid,parent_side=side,same_parent_debt=debt,epoch_attached_debt=epoch_slice,live_ask=ask,economic_ceiling=old.ECON_CEILING,floor_before=float(pay['floor']),floor_after_epoch_slice_at_live_ask=floor_ref,passive_live=bool(passive_live),payment_progress_since_epoch=bool(obs.get('paymentProgress')),active_already_owned=active_owned,hard_confirmed=hard_confirmed,passive_reserved_qty=passive_reserved,other_same_parent_reserved_qty=other_reserved)
        d=self.sharedBudgetPolicy.evaluate(ctx)
        ev={'t':int(t),'event':'RESERVATION_AWARE_SAME_PARENT_EVALUATION','parentId':pid,'side':side,'epoch':int(st.epoch),'floor':float(pay['floor']),'managerDebt':debt,'liveAsk':ask,'legalPhysicalQty':legal,'passiveKey':str(passive_key) if passive_key is not None else None,'passiveLive':bool(passive_live),'passiveReservedQty':passive_reserved,'otherReservedQty':other_reserved,'otherReservations':other_reservations,'reservedQty':float(d.reserved_qty),'unreservedRepairCapacity':float(d.unreserved_repair_capacity),'paymentProgress':bool(obs.get('paymentProgress')),'reason':d.reason,'allow':bool(d.allow_active_parallel_child)}
        self.reservationAwareEvents.append(ev); self.parallelRepairEvents.append(dict(ev))
        if not d.allow_active_parallel_child:
            self.reservationAwareBlocks+=1
            return False
        if psv is None or passive_key is None or passive_entry is None:
            self.reservationAwareBlocks+=1
            ev['postDecisionBlock']='NO_PARENT_REPAIR_LINEAGE_ANCHOR'
            return False

        self.reservationAwareEligible+=1; self.parallelRepairEligible+=1
        oid=passive_entry.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
        q=float(d.physical_qty)
        if hasattr(self,'hardConfirmed'): self.hardConfirmed.add(pid)
        if hasattr(self,'hardEventConfirmedCount'): self.hardEventConfirmedCount+=1
        st.active_owned=True
        ok=self._submit_active(int(t),pid,str(passive_key),side,float(ask),q,oid)
        if ok:
            self.parallelRepairSubmits+=1; self.generationEpochActiveSubmits+=1
            active=getattr(self,'activeByParent',{}).get(pid,{}); ak=active.get('key')
            if ak:
                self.parallelRepairActiveKeys.add(str(ak)); self.generationEpochActiveFillKeys.add(str(ak))
                if hasattr(self,'v84Composite'):
                    self.v84Composite[str(ak)]={'key':str(ak),'side':side,'parentId':pid,'price':float(ask),'submittedQty':q,'gapAtSubmit':debt,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'RESERVATION_AWARE_SAME_PARENT_ACTIVE_REPAIR'}
                    if hasattr(self,'v84CompositeSubmits'): self.v84CompositeSubmits+=1
            self.reservationSnapshots.append({'t':int(t),'parentId':pid,'activeKey':str(ak) if ak else None,'activeQty':q,'passiveKey':str(passive_key),'passiveBaseActual':float(passive_entry.get('actualFilled') or 0.0),'passiveReservedQty':passive_reserved,'otherReservedQty':other_reserved,'managerDebt':debt})
            self.reservationAwareEvents.append({'t':int(t),'event':'RESERVATION_AWARE_ACTIVE_REPAIR_SUBMIT','parentId':pid,'activeKey':ak,'passiveKey':str(passive_key),'qty':q,'managerDebt':debt,'reservedQty':float(d.reserved_qty),'unreservedRepairCapacity':float(d.unreserved_repair_capacity)})
        else:
            st.active_owned=False
            if hasattr(self,'hardConfirmed'): self.hardConfirmed.discard(pid)
        return bool(ok)

    def run_reservation(self,models,winner):
        r=super().run_parallel(models,winner)
        r.update({'reservationAwareChecks':self.reservationAwareChecks,'reservationAwareEligible':self.reservationAwareEligible,'reservationAwareBlocks':self.reservationAwareBlocks,'reservationAwareEvents':self.reservationAwareEvents[:500],'reservationSnapshots':self.reservationSnapshots[:80]})
        return r

def make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47):
    return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())

def safety(r): return old.birth.base.front.safety(r)

def alloc(sim,r):
    cons=abs(float(r.get('v84CompositeFillQty') or 0)-float(r.get('v84RepairAllocatedQty') or 0)-float(r.get('v84OverflowAllocatedQty') or 0))<=1e-7
    parents=r.get('allocationV2Parents') or {}
    if not parents and hasattr(sim,'allocationLedgerV2'): parents={str(pid):sim.allocationLedgerV2.describe_parent(pid) for pid in getattr(sim.allocationLedgerV2,'parents',{})}
    bounded=all(float(p.get('repairPaid') or 0)<=float(p.get('initialDebt') or 0)+1e-7 and float(p.get('remainingDebt') or 0)>=-EPS for p in parents.values() if p)
    return cons,bounded,parents

def slim(r,extra=None):
    out={'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0),'overflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0),'overflowPaidQty':float(r.get('v84OverflowPaidQty') or 0)}
    if extra: out.update(extra)
    return out

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); mid=int(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output); outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=f'reservation_parallel_ab_{mid}_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'RESERVATION_AWARE_PARALLEL_AB','market':mid,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'RESERVATION_AWARE_PARALLEL_AB_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{mid}.json.xz'
        c=make(x2.InitialEpochParallelHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: crun=c.run_parallel(models,cr['winner']); ccons,cbound,cparents=alloc(c,crun)
        finally: c.close()
        n=make(ReservationAwareInitialEpochParallelHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: nr=n.run_reservation(models,cr['winner']); ncons,nbound,nparents=alloc(n,nr)
        finally: n.close()
        cs=safety(crun); ns=safety(nr); csz=all(float(v or 0)<=EPS for v in cs.values()); nsz=all(float(v or 0)<=EPS for v in ns.values())
        floorok=float(nr.get('floor') or 0)>=float(crun.get('floor') or 0)-1e-7
        removed_unsafe=(float(cs.get('responsibilityOverfill') or 0)>EPS or float(cs.get('sharedOverfill') or 0)>EPS) and float(ns.get('responsibilityOverfill') or 0)<=EPS and float(ns.get('sharedOverfill') or 0)<=EPS
        physical=float(nr.get('parallelRepairActiveFillQty') or 0)>EPS
        if not ncons or not nbound or not nsz: decision='REJECT_RESERVATION_AWARE_STILL_UNSAFE'
        elif not removed_unsafe: decision='RESERVATION_POLICY_SAFE_BUT_UNSAFE_CONTROL_NOT_DIFFERENTIATED'
        elif not floorok: decision='REJECT_RESERVATION_AWARE_FLOOR_REGRESSION'
        elif physical: decision='RESERVATION_AWARE_SAFE_PHYSICAL_PASS'
        else: decision='RESERVATION_AWARE_SAFETY_PASS_NO_ACTIVE_REACHABILITY'
        out={'version':'ETH_RESERVATION_AWARE_INITIAL_EPOCH_PARALLEL_AB_V1','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':{'unsafeControlReproduced':not csz,'candidateSafetyZero':nsz,'candidateAllocationConservation':ncons,'candidateSharedParentDebtBounded':nbound,'unsafeOverfillRemoved':removed_unsafe,'floorNonWorse':floorok,'candidatePhysicalActiveFill':physical},'control':slim(crun),'candidate':slim(nr,{'reservationAwareChecks':int(nr.get('reservationAwareChecks') or 0),'reservationAwareEligible':int(nr.get('reservationAwareEligible') or 0),'reservationAwareBlocks':int(nr.get('reservationAwareBlocks') or 0)}),'controlSafety':cs,'candidateSafety':ns,'candidateAllocationParents':nparents,'reservationEvents':nr.get('reservationAwareEvents',[])[:500],'reservationSnapshots':nr.get('reservationSnapshots',[])[:80],'boundary':['same initial-epoch architecture; only same-parent Active admission gains prospective sibling reservation accounting','authoritative parent debt minus unresolved sibling reservations defines Active capacity','no automatic cancellation/resize introduced','AllocationLedger V2 frozen Repair-first/overflow-second','economic ceiling/legal-min/payment-progress/ownership/<=180 fences frozen','no threshold/qty/price/delay tuning','winner post-hoc only; no Target runtime input; no dream fill; no 8781']}
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'gates':out['gates'],'control':out['control'],'candidate':out['candidate'],'controlSafety':cs,'candidateSafety':ns,'reasonCounts':__import__('collections').Counter(e.get('reason') for e in out['reservationEvents'] if e.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION')},ensure_ascii=False,default=str),flush=True)
    finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
