from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,threading,time,zipfile,importlib,importlib.util
from pathlib import Path
import joblib,numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9;MID=1912961

def load_or_staged(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START',flush=True)
    try:
        m=importlib.import_module(fullname);print(f'IMPORT_STAGE {fullname} PASS project',flush=True);return m
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);print(f'IMPORT_STAGE {fullname} PASS staged',flush=True);return m

x2=load_or_staged('tools.run_eth_alignment_execution_allocation_2x2_1912961','run_eth_alignment_execution_allocation_2x2_1912961.py')
regmod=load_or_staged('tools.eth_repair_modular.multi_slot_registry','multi_slot_registry.py')
capmod=load_or_staged('tools.eth_repair_modular.parallel_cycle_capacity','parallel_cycle_capacity.py')
transmod=load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
MultiSlotStateRegistryV1=regmod.MultiSlotStateRegistryV1
PhaseAdaptiveParallelCycleCapacityPolicy=capmod.PhaseAdaptiveParallelCycleCapacityPolicy
RepairFirstResponsibilityTransitionV1=transmod.RepairFirstResponsibilityTransitionV1
ResponsibilityTransitionContext=transmod.ResponsibilityTransitionContext
v1=x2.v1

class MultiSlotMultiRound1912961(x2.UncappedSharedAllocation):
    CONFIG_MAX_SLOTS=2
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.maxSlots=max(1,int(self.CONFIG_MAX_SLOTS))
        self.slotRegistry=MultiSlotStateRegistryV1()
        self.slotCapacity=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,self.maxSlots),(0.40,max(1,self.maxSlots-1)),(0.70,1)))
        self.slotTransition=RepairFirstResponsibilityTransitionV1()
        self.extraSlotKeyById={};self.extraSlotHistory=[];self.extraSlotBlocks={};self.extraSlotChecks=0
        self.slotBirths=0;self.slotReuses=0;self.slotMaterializations=0;self.completedAddedGenerations=0
        self._slotHadPriorLife=set();self._seenExtraFill={};self._seenGenerationComplete=set();self._slotBusy=False
        self.maxSimultaneousOccupiedSlots=1;self.occupiedTrace=[];self.slotGenerationEvents=[]
        self.fifoGenerationPaymentEvents=[]

    def _normalized_phase(self,t):
        start=int(self.capEnd)-300000
        return min(1.0,max(0.0,(int(t)-start)/300000.0))

    # Co-adaptation: legacy slot-1 unresolved test must not serialize against
    # carriers owned by other registered slots.
    def _v44_unresolved(self,t):
        self._refresh_carrier_ledger(int(t))
        extra=set(self.extraSlotKeyById.values())
        for k in getattr(self,'v44Keys',set()):
            if str(k) in extra:continue
            e=self.carrierLedger.get(k,{})
            if float(e.get('reservedOutstanding') or 0)>EPS:return True
            o=self.orders.get(k,{})
            if not o.get('terminal') and float(o.get('qty') or 0)-float(e.get('actualFilled') or 0)>EPS:return True
        return False

    # Co-adaptation: responsibility/generation payments are FIFO (oldest debt first),
    # not legacy V48 newest-first. Accounting quantity remains unchanged.
    def _pay_generations(self,t,pay,is_taker):
        rem=max(0.0,float(pay))
        if rem<=EPS:return
        if not getattr(self,'v48Generations',[]):
            self.v48DiscardedPreBirthRepairQty+=rem;return
        for g in self.v48Generations:
            owed=max(0.0,float(g['debt'])-float(g['paid']))
            if owed<=EPS:continue
            x=min(owed,rem);g['paid']+=x;rem-=x;self.v48GenerationPaidQty+=x
            ev={'t':int(t),'generationId':g['id'],'key':g.get('key'),'paidQty':x,'executionRole':'TAKER' if is_taker else 'MAKER','generationDebt':g['debt'],'generationPaid':g['paid'],'generationProgress':min(1.,g['paid']/(g['debt']+EPS)),'ordering':'FIFO_OLDEST_FIRST'}
            self.v48PaymentEvents.append(ev);self.fifoGenerationPaymentEvents.append(dict(ev))
            if rem<=EPS:break
        if rem>EPS:self.v48DiscardedPreBirthRepairQty+=rem
        self.v48GenerationOverpay=max(self.v48GenerationOverpay,max([float(g['paid'])-float(g['debt']) for g in self.v48Generations]+[0.]))

    def _block_slot(self,reason,row=None):
        self.extraSlotBlocks[reason]=int(self.extraSlotBlocks.get(reason,0))+1
        if row is not None:self.extraSlotHistory.append({**row,'event':'MULTISLOT_BLOCK','reason':reason})
        return False

    def _slot_terminal_unfilled(self,key):
        e=getattr(self,'carrierLedger',{}).get(str(key),{});o=getattr(self,'orders',{}).get(str(key),{})
        filled=float(e.get('actualFilled') or o.get('cum') or 0.0)
        terminal=bool(e.get('terminalConfirmed') or o.get('terminal'))
        return terminal and filled<=EPS

    def _sync_slots(self,t):
        self._refresh_carrier_ledger(int(t))
        for sid,key in list(self.extraSlotKeyById.items()):
            e=self.carrierLedger.get(str(key),{});cur=float(e.get('actualFilled') or 0.0);old=float(self._seenExtraFill.get(str(key),0.0))
            if cur>old+EPS:
                inc=cur-old;self._seenExtraFill[str(key)]=cur;self.slotMaterializations+=1 if old<=EPS else 0
                token=f'{key}:fill'
                self.slotRegistry.observe_expand_fill(str(key),inc,token)
                self.slotGenerationEvents.append({'t':int(t),'event':'EXTRA_SLOT_CONFIRMED_FILL','slotId':sid,'key':key,'incQty':inc,'cumQty':cur})
            g=getattr(self,'v48GenByKey',{}).get(str(key))
            if g is not None:
                st=self.slotRegistry.slots.get(int(sid))
                if st is not None:
                    st.confirmed_expand_qty=max(float(st.confirmed_expand_qty),float(g.get('debt') or 0.0))
                    st.confirmed_repair_qty=max(float(st.confirmed_repair_qty),float(g.get('paid') or 0.0))
                complete=float(g.get('debt') or 0.0)>EPS and float(g.get('paid') or 0.0)>=float(g.get('debt') or 0.0)-EPS
                terminal=bool(e.get('terminalConfirmed') or self.orders.get(str(key),{}).get('terminal'))
                if complete and terminal:
                    tag=(str(key),int(g.get('id') or -1))
                    if tag not in self._seenGenerationComplete:
                        self._seenGenerationComplete.add(tag);self.completedAddedGenerations+=1
                        self.slotGenerationEvents.append({'t':int(t),'event':'EXTRA_SLOT_GENERATION_COMPLETED','slotId':sid,'key':key,'generationId':g.get('id'),'debt':g.get('debt'),'paid':g.get('paid')})
                    if st is not None:st.terminal=True
                    self.extraSlotKeyById.pop(sid,None)
            elif self._slot_terminal_unfilled(str(key)):
                st=self.slotRegistry.slots.get(int(sid))
                if st is not None:st.terminal=True
                self.extraSlotKeyById.pop(sid,None)
                self.slotGenerationEvents.append({'t':int(t),'event':'EXTRA_SLOT_UNFILLED_TERMINAL_RELEASE','slotId':sid,'key':key})
        occupied=1+len(self.extraSlotKeyById)
        self.maxSimultaneousOccupiedSlots=max(self.maxSimultaneousOccupiedSlots,occupied)
        if not self.occupiedTrace or self.occupiedTrace[-1].get('occupied')!=occupied:
            self.occupiedTrace.append({'t':int(t),'phase':self._normalized_phase(t),'occupied':occupied,'keys':dict(self.extraSlotKeyById)})

    def _admission_context(self,t):
        th=getattr(self,'thesis',None);rp=getattr(self,'repairParent',None)
        if not isinstance(th,dict) or not bool(th.get('materialized')):return None,'FIRST_THESIS_NOT_MATERIALIZED'
        if not isinstance(rp,dict):return None,'NO_LIVE_REPAIR_PARENT'
        qv=v1.quotes(self.book)
        if not qv:return None,'NO_QUOTES'
        base_side=str(th.get('side') or '').upper();signal=self._signal_side(qv)
        if base_side not in ('UP','DOWN'):return None,'INVALID_THESIS_SIDE'
        f=self._coord_feature(int(t));x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
        if pE<.5:return None,'P_EXPAND_BELOW_FROZEN_THRESHOLD'
        rp_side=str(rp.get('side') or '').upper()
        debt_up=float(self._coordDebt if rp_side=='UP' else 0.0);debt_down=float(self._coordDebt if rp_side=='DOWN' else 0.0)
        other='DOWN' if base_side=='UP' else 'UP'
        candidates=[base_side,other]
        diags=[];transition_allowed=0
        for idx,side in enumerate(candidates):
            td=self.slotTransition.evaluate(ResponsibilityTransitionContext(side,debt_up,debt_down))
            d={'side':side,'source':'BASE_THESIS' if idx==0 else 'ALTERNATE_TRANSITION_ALLOWED','transitionAllow':bool(td.allow_expand_ownership),'transitionRole':td.bind_role,'transitionReason':td.reason}
            if not td.allow_expand_ownership or td.bind_role!='EXPAND':
                diags.append(d);continue
            transition_allowed+=1
            rec=self._v75_recoverability(int(t),side,qv);d['recoverability']=rec;d['recoverable']=bool(rec.get('recoverable'))
            diags.append(d)
            if not bool(rec.get('recoverable')):continue
            return {'thesis':th,'repairParent':rp,'qv':qv,'side':side,'baseThesisSide':base_side,'selectedSideSource':d['source'],'signalSide':signal,'signalAgreement':signal==side,'pExpand':pE,'recoverability':rec,'phase':self._normalized_phase(t),'candidateSideDiagnostics':diags},None
        if transition_allowed<=0:return None,'RESPONSIBILITY_TRANSITION_BLOCK_ALL_SIDES'
        return None,'UNRECOVERABLE_ALL_TRANSITION_ALLOWED_SIDES'

    def _maybe_open_extra_slots(self,t):
        if self.maxSlots<=1 or self._slotBusy:return False
        self._slotBusy=True
        try:
            ctx,reason=self._admission_context(int(t));self.extraSlotChecks+=1
            if ctx is None:
                self.extraSlotBlocks[reason]=int(self.extraSlotBlocks.get(reason,0))+1;return False
            phase=float(ctx['phase']);cap=int(self.slotCapacity.capacity_at(phase));allowed_extra=max(0,min(self.maxSlots,cap)-1)
            if len(self.extraSlotKeyById)>=allowed_extra:return self._block_slot('PHASE_SLOT_CAPACITY_FULL',{'t':int(t),'phase':phase,'cap':cap})
            free=[sid for sid in range(2,self.maxSlots+1) if sid not in self.extraSlotKeyById]
            if not free:return False
            opened=False;base_bid=float(ctx['qv'][ctx['side']]['bid'])
            # Fill currently available extra slots at one management clock. Each ordinal is
            # +0.01 from the previous slot, reanchored to the current live Maker bid each round.
            for rank,sid in enumerate(free[:allowed_extra-len(self.extraSlotKeyById)],start=1):
                px=round(base_bid+0.01*rank,2)
                row={'t':int(t),'phase':phase,'slotId':sid,'maxSlots':self.maxSlots,'liveBidAnchor':base_bid,'price':px,'baseThesisSide':ctx.get('baseThesisSide'),'selectedSide':ctx.get('side'),'selectedSideSource':ctx.get('selectedSideSource'),'signalSide':ctx.get('signalSide'),'signalAgreement':bool(ctx.get('signalAgreement')),'pExpand':ctx['pExpand'],'recoverabilityReason':ctx['recoverability'].get('reason'),'candidateSideDiagnostics':ctx.get('candidateSideDiagnostics')}
                if px<=EPS or px>=1.0-EPS:
                    self._block_slot('PRICE_OUT_OF_RANGE',row);continue
                qty=1.0/px if px>EPS else math.inf
                if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS:
                    self._block_slot('VENUE_MIN_INFEASIBLE',row);continue
                oid=self._new_objective('EXPAND',ctx['side'])['id'];n0=self.n
                self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane=f'MULTISLOT_{sid}'
                ok=self.submit(int(t),ctx['side'],px,qty)
                if not ok:
                    self._block_slot('SUBMIT_FAILED',row);continue
                key=f'{ctx["side"]}_{n0}'
                self.v44Keys.add(key);self.v44Submits+=1
                reused=sid in self._slotHadPriorLife
                if reused:self.slotReuses+=1
                self._slotHadPriorLife.add(sid);self.slotBirths+=1
                self.slotRegistry.create_slot(sid,born_phase=phase,thesis_side=ctx['side'],thesis_id=oid)
                self.slotRegistry.attach_expand_carrier(sid,key,oid);self.extraSlotKeyById[sid]=key;self._seenExtraFill[key]=0.0
                self.extraSlotHistory.append({**row,'event':'MULTISLOT_EXPAND_SUBMIT','key':key,'objectiveId':oid,'qty':qty,'reusedSlot':reused})
                opened=True
            return opened
        finally:self._slotBusy=False

    def process(self,t):
        super().process(t)
        self._sync_slots(int(t))
        self._maybe_open_extra_slots(int(t))

    def cancel_expired(self,t):
        super().cancel_expired(t)
        self._sync_slots(int(t))
        self._maybe_open_extra_slots(int(t))

    def run_multislot(self,models,winner):
        r=self.run_alignment(models,winner)
        self._sync_slots(int(self.capEnd))
        r.update({'requestedMaxSlots':self.maxSlots,'multiSlotRegistry':self.slotRegistry.describe(),'extraSlotHistory':self.extraSlotHistory[:1200],'extraSlotBlocks':self.extraSlotBlocks,'extraSlotChecks':self.extraSlotChecks,'slotBirths':self.slotBirths,'slotReuses':self.slotReuses,'slotMaterializations':self.slotMaterializations,'completedAddedGenerations':self.completedAddedGenerations,'maxSimultaneousOccupiedSlots':self.maxSimultaneousOccupiedSlots,'occupiedTrace':self.occupiedTrace[:300],'slotGenerationEvents':self.slotGenerationEvents[:500],'fifoGenerationPaymentEvents':self.fifoGenerationPaymentEvents[:500]})
        return r

def make_variant(n):
    class C(MultiSlotMultiRound1912961):CONFIG_MAX_SLOTS=int(n)
    C.__name__=f'MultiSlotMultiRound_{n}'
    return C

def summary(r,shared=True):
    fills=list(r.get('alignmentFillEvents') or [])
    floors=[float(x.get('floorAfterReceipt')) for x in fills if x.get('floorAfterReceipt') is not None]
    safety=x2.safety_summary(r,shared)
    conservation=abs(float(r.get('v84CompositeFillQty') or 0.0)-float(r.get('v84RepairAllocatedQty') or 0.0)-float(r.get('v84OverflowAllocatedQty') or 0.0))<=1e-7
    return {'fills':int(r.get('actualFillEvents') or 0),'semanticRounds':int(r.get('v70dSemanticRounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'v44Submits':int(r.get('v44Submits') or 0),'v48GenerationBirths':int(r.get('v48GenerationBirths') or 0),'v48GenerationDebtQty':float(r.get('v48GenerationDebtQty') or 0.0),'v48GenerationPaidQty':float(r.get('v48GenerationPaidQty') or 0.0),'activeCompositeSubmits':int(r.get('v89cActiveCompositeSubmits') or 0),'activeCompositeFillQty':float(r.get('v89cActiveCompositeFillQty') or 0.0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'terminalFloor':float(r.get('floor') or 0.0),'worstObservedFloor':min(floors+[0.0]),'slotBirths':int(r.get('slotBirths') or 0),'slotReuses':int(r.get('slotReuses') or 0),'slotMaterializations':int(r.get('slotMaterializations') or 0),'completedAddedGenerations':int(r.get('completedAddedGenerations') or 0),'maxSimultaneousOccupiedSlots':int(r.get('maxSimultaneousOccupiedSlots') or 1),'allocationConservation':conservation,'safetyZero':all(float(v or 0)<=EPS for v in safety.values()),'safety':safety}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',default='AUTO');a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='multislot_multiround_1912961_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'MULTISLOT_MULTIROUND_1912961','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTISLOT_MULTIROUND_1912961_START','cells':['LEGACY_LIFO',1,2,3,4]}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};cr=co[MID]
        models,life,cap,tim,econ,price,sur=x2.v38.v36.v34.v30.load_runtime(a);teacher=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];gen=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        rows=[];details={}
        # Legacy current deep-cycle control (LIFO V48 ordering).
        sim=x2.base.make_simulator(x2.UncappedSharedAllocation,tape,models,life,cap,tim,econ,price,sur,teacher,gen)
        try:r=sim.run_alignment(models,cr['winner'])
        finally:sim.close()
        s=summary(r);rows.append({'cell':'LEGACY_LIFO','maxSlots':1,**s});details['LEGACY_LIFO']={'v48Generations':r.get('v48Generations',[]),'v48PaymentRows':r.get('v48PaymentRows',[])[:120]}
        print(json.dumps({'progress':'LEGACY_LIFO','summary':s},ensure_ascii=False),flush=True)
        for n in (1,2,3,4):
            cls=make_variant(n);sim=x2.base.make_simulator(cls,tape,models,life,cap,tim,econ,price,sur,teacher,gen)
            try:r=sim.run_multislot(models,cr['winner'])
            finally:sim.close()
            s=summary(r);row={'cell':f'FIFO_{n}SLOT','maxSlots':n,**s};rows.append(row)
            details[row['cell']]={'registry':r.get('multiSlotRegistry'),'extraSlotHistory':r.get('extraSlotHistory',[])[:300],'slotGenerationEvents':r.get('slotGenerationEvents',[])[:300],'occupiedTrace':r.get('occupiedTrace',[])[:200],'fifoGenerationPaymentEvents':r.get('fifoGenerationPaymentEvents',[])[:200],'blocks':r.get('extraSlotBlocks')}
            print(json.dumps({'progress':row['cell'],'summary':s},ensure_ascii=False),flush=True)
        fifo=[x for x in rows if str(x['cell']).startswith('FIFO_')]
        safe=[x for x in fifo if x['safetyZero'] and x['allocationConservation']]
        ranked=sorted(safe,key=lambda x:(x['semanticRounds'],x['repairParentCompletions'],x['completedAddedGenerations'],x['slotReuses'],x['fills'],x['pnlDiagnosticOnly'],-x['maxSlots']),reverse=True)
        best=ranked[0] if ranked else None
        gates={'allFifoCellsSafetyZero':all(x['safetyZero'] for x in fifo),'allFifoCellsAllocationConserved':all(x['allocationConservation'] for x in fifo),'multiSlotActuallyExercised':any(x['maxSimultaneousOccupiedSlots']>=2 for x in fifo if x['maxSlots']>=2),'multiRoundReuseObserved':any(x['slotReuses']>0 or x['completedAddedGenerations']>0 for x in fifo if x['maxSlots']>=2)}
        decision='MULTISLOT_MULTIROUND_INTEROP_PASS' if all(gates.values()) else ('MULTISLOT_FUNCTIONAL_BUT_NO_REUSE_YET' if gates['allFifoCellsSafetyZero'] and gates['allFifoCellsAllocationConserved'] and gates['multiSlotActuallyExercised'] else 'MULTISLOT_INTEROP_OR_EXECUTION_FAIL')
        out={'version':'MULTISLOT_MULTIROUND_1912961_RESULT_V4_SLOT_LOCAL_DIRECTION','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'rows':rows,'bestSameMarketSlotCount':({'maxSlots':best['maxSlots'],'cell':best['cell'],'semanticRounds':best['semanticRounds'],'repairParentCompletions':best['repairParentCompletions'],'completedAddedGenerations':best['completedAddedGenerations'],'slotReuses':best['slotReuses'],'fills':best['fills'],'pnlDiagnosticOnly':best['pnlDiagnosticOnly'],'terminalFloor':best['terminalFloor'],'worstObservedFloor':best['worstObservedFloor']} if best else None),'details':details,'selectionCaveat':'One consumed same-market architecture training replay only. Slot count is development guidance, not promotion/generalization authority.','globalObjectiveReminder':{'avgWinningPnlGt':2.0,'eachLosingPnlGt':-1.0,'winRateGt':0.5},'boundary':['single market 1912961 full 5m realistic-HFT replay','FIFO_1SLOT isolates generation-order co-adaptation before slot-count effect','2/3/4 slots use live-bid reanchored +0.01 execution ladder and reusable extra slots','legacy self.thesis/repairParent are execution focus only for extra-slot registry','legacy global one-Expand lock not used for extra registered slots','confirmed fills only create V48 generation debt','shared-parent Repair-first physical AllocationLedger retained','ResponsibilityTransition/recoverability/pExpand/<=180s admission retained','no Target runtime input','no dream fill','no 8781']}
        op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json' if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'best':out['bestSameMarketSlotCount'],'rows':rows},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
