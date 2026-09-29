from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,threading,time,zipfile,importlib,importlib.util
from pathlib import Path
import joblib,numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9
MID=1945898

def load_or_staged(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START', flush=True)
    try:
        m=importlib.import_module(fullname);print(f'IMPORT_STAGE {fullname} PASS project', flush=True);return m
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);print(f'IMPORT_STAGE {fullname} PASS staged', flush=True);return m

load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
load_or_staged('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
load_or_staged('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py')
from tools.eth_repair_modular.responsibility_transition import ResponsibilityTransitionContext
# Worker may not yet have the new allocation modules in its checkout; preload staged V2 under the canonical package name.
try:
    import tools.allocation_ledger_v2
except ImportError:
    load_or_staged('tools.allocation_ledger_v2','allocation_ledger_v2.py')
try:
    from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
except ImportError:
    gm=load_or_staged('tools.eth_repair_modular.generation_aware_allocation_ledger','generation_aware_allocation_ledger.py');GenerationAwareSharedParentDebtAllocationLedgerV3=gm.GenerationAwareSharedParentDebtAllocationLedgerV3
try:
    from tools.eth_repair_modular.parallel_cycle_capacity import PhaseAdaptiveParallelCycleCapacityPolicy
except ImportError:
    pm=load_or_staged('tools.eth_repair_modular.parallel_cycle_capacity','parallel_cycle_capacity.py');PhaseAdaptiveParallelCycleCapacityPolicy=pm.PhaseAdaptiveParallelCycleCapacityPolicy

pe=pg.pe
v1=pe.v38.v36.v34.v30.v1

class PhaseNativeTwoSlotHFT(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        # No event has run yet. Replace V2 with generation-aware V3; base Repair-first
        # allocation semantics are identical until a confirmed new generation attaches.
        self.allocationLedgerV2=GenerationAwareSharedParentDebtAllocationLedgerV3()
        self.phaseCapacity=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,4),(0.5,3),(0.9,2)))
        self.twoSlotChecks=0;self.twoSlotEligible=0;self.twoSlotBlocks={};self.twoSlotEvents=[]
        self.secondSlotKey=None;self.secondSlotObjectiveId=None;self.secondSlotSubmittedAt=None;self.secondSlotFillAt=None;self.secondSlotFillQty=0.0
        self._secondSeenFill=0.0;self._secondSlotBusy=False;self._generationDebtHandled=set();self.generationDebtAttachEvents=[]
        self.parentDebtSnapshotAtSecondSubmit=None

    def _block(self,reason,row=None):
        self.twoSlotBlocks[reason]=int(self.twoSlotBlocks.get(reason,0))+1
        if row is not None:
            z=dict(row);z.update({'event':'TWO_SLOT_ADMISSION_BLOCK','reason':reason});self.twoSlotEvents.append(z)
        return False

    def _normalized_phase(self,t):
        start=int(self.capEnd)-300000
        return min(1.0,max(0.0,(int(t)-start)/300000.0))

    def _attach_new_epoch_after_resolution(self,t):
        before=len(getattr(self,'generationEpochEvents',[]))
        out=super()._attach_new_epoch_after_resolution(t)
        for ev in list(getattr(self,'generationEpochEvents',[]))[before:]:
            if ev.get('event')!='EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH':continue
            src=str(ev.get('sourceKey'))
            if self.secondSlotKey is not None and src!=str(self.secondSlotKey):continue
            token=f"{src}:epoch:{int(ev.get('epoch') or 0)}"
            if token in self._generationDebtHandled:continue
            self._generationDebtHandled.add(token)
            pid=int(ev.get('parentId'));add=float(ev.get('addedDebt') or 0.0)
            before_parent=self.allocationLedgerV2.describe_parent(pid)
            ar=self.allocationLedgerV2.attach_generation_debt(pid,token,add)
            after_parent=self.allocationLedgerV2.describe_parent(pid)
            row={'t':int(t),'event':'TWO_SLOT_GENERATION_DEBT_ATTACH','sourceKey':src,'parentId':pid,'epoch':int(ev.get('epoch') or 0),'parentBefore':before_parent,'parentAfter':after_parent,**ar.__dict__}
            self.generationDebtAttachEvents.append(row);self.twoSlotEvents.append(dict(row))
        return out

    def _scan_second_slot_fill(self,t):
        if not self.secondSlotKey:return
        self._refresh_carrier_ledger(int(t))
        e=getattr(self,'carrierLedger',{}).get(str(self.secondSlotKey),{})
        now=float(e.get('actualFilled') or 0.0)
        if now<=self._secondSeenFill+EPS:return
        inc=now-self._secondSeenFill;self._secondSeenFill=now;self.secondSlotFillQty+=inc
        if self.secondSlotFillAt is None:self.secondSlotFillAt=int(t)
        self.twoSlotEvents.append({'t':int(t),'event':'TWO_SLOT_CONFIRMED_EXPAND_FILL','key':self.secondSlotKey,'incQty':inc,'cumQty':now})

    def _maybe_second_slot(self,t):
        if self._secondSlotBusy or self.secondSlotKey is not None:return False
        self._secondSlotBusy=True
        try:
            th=getattr(self,'thesis',None);rp=getattr(self,'repairParent',None)
            if not isinstance(th,dict) or not bool(th.get('materialized')):return self._block('FIRST_THESIS_NOT_MATERIALIZED')
            if not isinstance(rp,dict):return self._block('NO_LIVE_REPAIR_PARENT')
            pid=int(rp.get('id'));parent_side=str(rp.get('side') or '').upper()
            if parent_side not in ('UP','DOWN'):return self._block('INVALID_REPAIR_PARENT_SIDE')
            qv=v1.quotes(self.book)
            if not qv:return self._block('NO_QUOTES')
            side=str(th.get('side') or '').upper();signal=self._signal_side(qv)
            row={'t':int(t),'normalizedPhase':self._normalized_phase(t),'existingThesisId':th.get('id'),'thesisSide':side,'signalSide':signal,'repairParentId':pid,'repairParentSide':parent_side}
            self.twoSlotChecks+=1
            if side not in ('UP','DOWN') or signal!=side:return self._block('SIGNAL_NOT_SAME_AS_THESIS',row)
            if int(self.capEnd)-int(t)<=180000:return self._block('INHERITED_LATE_EXPOSURE_FENCE',row)
            cap=self.phaseCapacity.capacity_at(row['normalizedPhase']);row['developmentCapacity']=cap
            if cap<2:return self._block('PHASE_CAPACITY_LT2',row)
            pay=self._current_payoffs();gap=float(pay.get('gap') or 0.0);row['payoffGap']=gap;row['floorBefore']=float(pay.get('floor') or 0.0)
            debt,debt_rows=self._live_repair_debt_by_side();row['repairDebtBySide']=dict(debt);row['debtRows']=debt_rows[:8]
            if max(float(debt['UP']),float(debt['DOWN']))<=EPS or gap<=EPS:return self._block('NO_POSITIVE_REPAIR_DEBT',row)
            parent_desc=self.allocationLedgerV2.describe_parent(pid);row['allocationParentBefore']=parent_desc
            if parent_desc is None:return self._block('ALLOCATION_PARENT_NOT_REGISTERED',row)
            f=self._coord_feature(int(t));x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);row['pExpand']=pE
            if pE<.5:return self._block('P_EXPAND_BELOW_FROZEN_THRESHOLD',row)
            rec=self._v75_recoverability(int(t),side,qv);row['recoverability']=rec
            if not bool(rec.get('recoverable')):return self._block('UNRECOVERABLE',row)
            td=self.transitionPolicy.evaluate(ResponsibilityTransitionContext(side,float(debt['UP']),float(debt['DOWN'])));row.update({'transitionAllow':bool(td.allow_expand_ownership),'transitionRole':td.bind_role,'transitionReason':td.reason})
            if not td.allow_expand_ownership or td.bind_role!='EXPAND':return self._block('RESPONSIBILITY_TRANSITION_BLOCK',row)
            if hasattr(self,'_expand_occupied') and self._expand_occupied():return self._block('GLOBAL_EXPAND_PHYSICAL_OCCUPANCY',row)
            px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf;row.update({'side':side,'price':px,'qty':qty})
            if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS:return self._block('VENUE_MIN_INFEASIBLE',row)
            oid=self._new_objective('EXPAND',side)['id'];n0=self.n
            self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='PHASE_NATIVE_SECOND_SLOT'
            ok=self.submit(int(t),side,px,qty)
            if not ok:return self._block('SUBMIT_FAILED',row)
            key=f'{side}_{n0}';self.secondSlotKey=key;self.secondSlotObjectiveId=oid;self.secondSlotSubmittedAt=int(t);self.parentDebtSnapshotAtSecondSubmit=parent_desc
            if hasattr(self,'v44Keys'):self.v44Keys.add(key)
            if hasattr(self,'v44Submits'):self.v44Submits+=1
            admission={**row,'submit':True,'reason':'PHASE_NATIVE_TWO_SLOT_ADMISSION','key':key,'objectiveId':oid}
            if hasattr(self,'v83Admissions'):self.v83Admissions.append(dict(admission))
            if hasattr(self,'v83AdmissionAllows'):self.v83AdmissionAllows+=1
            self.twoSlotEligible+=1;self.twoSlotEvents.append({'event':'TWO_SLOT_EXPAND_SUBMIT',**admission})
            return True
        finally:self._secondSlotBusy=False

    def process(self,t):
        out=super().process(t)
        self._scan_second_slot_fill(int(t))
        self._maybe_second_slot(int(t))
        return out

    def run_two_slot(self,models,winner):
        r=self.run_guard(models,winner)
        r.update({'twoSlotChecks':self.twoSlotChecks,'twoSlotEligible':self.twoSlotEligible,'twoSlotBlocks':self.twoSlotBlocks,'twoSlotEvents':self.twoSlotEvents[:500],'secondSlotKey':self.secondSlotKey,'secondSlotObjectiveId':self.secondSlotObjectiveId,'secondSlotSubmittedAt':self.secondSlotSubmittedAt,'secondSlotFillAt':self.secondSlotFillAt,'secondSlotFillQty':self.secondSlotFillQty,'generationAwareAllocationLedger':getattr(self.allocationLedgerV2,'name',None),'generationDebtAttachEvents':self.generationDebtAttachEvents,'generationDebtAttachApplied':sum(bool(x.get('applied')) for x in self.generationDebtAttachEvents),'generationAttachments':self.allocationLedgerV2.describe_generation_attachments()})
        return r

def compact(r):
    return {'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0.0),'v83AdmissionAllows':int(r.get('v83AdmissionAllows') or 0),'v83AdmissionBlocks':int(r.get('v83AdmissionBlocks') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',default='AUTO');a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='two_slot_1945898_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'TWO_SLOT_1945898','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'TWO_SLOT_1945898_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        b=pe.make(pg.ProspectiveGuardParentOccupancyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:br=b.run_guard(models,cr['winner']);bcons,bbound,bparents=pe.alloc(b,br)
        finally:b.close()
        c=pe.make(PhaseNativeTwoSlotHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            rr=c.run_two_slot(models,cr['winner']);cons,bound,parents=pe.alloc(c,rr)
            second_fill_at=c.secondSlotFillAt
            later_passive=[dict(x) for x in getattr(c,'v38PassiveRepairFillEvents',[]) if second_fill_at is not None and int(x.get('t') or -1)>int(second_fill_at) and float(x.get('incQty') or 0)>EPS]
            attach=list(c.generationDebtAttachEvents);second_key=c.secondSlotKey
            final_parent=(parents.get(str(getattr(c,'repairParent',{}).get('id'))) if isinstance(getattr(c,'repairParent',None),dict) else None)
        finally:c.close()
        ss=pe.safety(rr);safety_zero=all(float(v or 0)<=EPS for v in ss.values())
        ev=rr.get('expandFillResponsibilityEvents',[]) or []
        second_sched=[x for x in ev if second_key is not None and str(x.get('sourceKey'))==str(second_key) and x.get('event')=='V83_CONFIRMED_EXPAND_FILL_SCHEDULES_REPAIR_RESPONSIBILITY']
        second_res=[x for x in ev if second_key is not None and str(x.get('sourceKey'))==str(second_key) and x.get('event')=='EXPAND_FILL_REPAIR_RESPONSIBILITY_RESOLUTION']
        same_parent_attach=bool(attach and rr.get('generationEpochEvents'))
        applied=[x for x in attach if x.get('applied')]
        attached_qty=sum(float(x.get('added_debt') or x.get('addedDebt') or 0.0) for x in applied)
        fill_qty=float(rr.get('secondSlotFillQty') or 0.0)
        attach_matches=bool(applied and abs(attached_qty-fill_qty)<=1e-7)
        repair_paid_preserved=all(float(x.get('parentAfter',{}).get('repairPaid') or 0.0)>=float(x.get('parentBefore',{}).get('repairPaid') or 0.0)-1e-9 for x in applied)
        no_hidden_gap=True
        if applied:
            for x in applied:
                pb=x.get('parentBefore') or {};pa=x.get('parentAfter') or {};add=float(x.get('added_debt') or 0.0)
                if abs((float(pa.get('initialDebt') or 0)-float(pb.get('initialDebt') or 0))-add)>1e-7 or abs((float(pa.get('remainingDebt') or 0)-float(pb.get('remainingDebt') or 0))-add)>1e-7:no_hidden_gap=False
        gates={'baselineAllocationSafe':bool(bcons and bbound),'candidateSafetyZero':safety_zero,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'secondSlotCandidateExercised':int(rr.get('twoSlotChecks') or 0)>0,'secondSlotSubmitted':bool(rr.get('secondSlotKey')),'secondSlotActualFill':fill_qty>EPS,'deferredResponsibilityScheduled':len(second_sched)>0,'deferredResponsibilityResolved':len(second_res)>0,'generationDebtAttachedExactlyOnce':len(applied)==1,'attachedDebtMatchesConfirmedSecondSlotFill':attach_matches,'priorRepairPaidNotReset':repair_paid_preserved,'noHiddenDebtDelta':no_hidden_gap,'laterPassiveRepairFillAfterSecondSlot':len(later_passive)>0}
        bcmp=compact(br);ccmp=compact(rr);floor_delta=ccmp['floor']-bcmp['floor'];pnl_delta=ccmp['pnlDiagnosticOnly']-bcmp['pnlDiagnosticOnly']
        functional=all(gates[k] for k in ['candidateSafetyZero','allocationConservation','allocationParentDebtBounded','secondSlotSubmitted','secondSlotActualFill','deferredResponsibilityScheduled','deferredResponsibilityResolved','generationDebtAttachedExactlyOnce','attachedDebtMatchesConfirmedSecondSlotFill','priorRepairPaidNotReset','noHiddenDebtDelta'])
        if not safety_zero or not cons or not bound or (fill_qty>EPS and not (attach_matches and no_hidden_gap)):
            decision='SECOND_SLOT_FILL_ACCOUNTING_FAIL'
        elif not rr.get('secondSlotKey'):
            decision='NO_SECOND_SLOT_SUBMIT_DIAGNOSE_ADMISSION'
        elif fill_qty<=EPS:
            decision='SECOND_SLOT_SUBMIT_NO_FILL'
        elif not applied:
            decision='SECOND_SLOT_FILL_ACCOUNTING_FAIL'
        elif not later_passive:
            decision='SECOND_SLOT_FILL_NO_LATER_REPAIR'
        elif pnl_delta<-1.0 or floor_delta<-1.0:
            decision='FUNCTIONAL_PASS_ECONOMIC_FAIL'
        else:
            decision='TWO_SLOT_FUNCTIONAL_AND_ECONOMIC_PASS'
        out={'version':'PHASE_NATIVE_TWO_SLOT_PARALLEL_CYCLE_BEHAVIOR_1945898_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baseline':bcmp,'candidate':ccmp,'delta':{'pnlDiagnosticOnly':pnl_delta,'floor':floor_delta,'fills':ccmp['fills']-bcmp['fills'],'rounds':ccmp['rounds']-bcmp['rounds']},'secondSlot':{'key':rr.get('secondSlotKey'),'submittedAt':rr.get('secondSlotSubmittedAt'),'fillAt':rr.get('secondSlotFillAt'),'fillQty':fill_qty,'checks':rr.get('twoSlotChecks'),'eligible':rr.get('twoSlotEligible'),'blocks':rr.get('twoSlotBlocks')},'generationDebtAttachEvents':attach,'generationEpochEvents':rr.get('generationEpochEvents',[])[:120],'secondSlotResponsibilityEvents':second_sched+second_res,'laterPassiveRepairFills':later_passive[:80],'allocationParents':parents,'finalRepairParentAllocation':final_parent,'safety':ss,'twoSlotEvents':rr.get('twoSlotEvents',[])[:240],'boundary':['one market 1945898','max one additional independent same-thesis-side economic slot','continuous strict-past management check','ResponsibilityTransition must explicitly allow EXPAND','GenerationAware AllocationLedger V3 differs from V2 only by idempotent confirmed-generation debt attachment','no Target runtime input','no threshold/price/qty tuning','inherited <=180s fence kept','realistic HFT only','no dream fill','no 8781']}
        op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json' if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':bcmp,'candidate':ccmp,'delta':out['delta'],'secondSlot':out['secondSlot'],'attach':attach,'laterPassiveRepairFills':later_passive[:6],'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
