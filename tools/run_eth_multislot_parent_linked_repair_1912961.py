from __future__ import annotations
import argparse,json,os,sys,tempfile,threading,time,zipfile,shutil,importlib,importlib.util
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9;MID=1912961

def load(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START',flush=True)
    try:
        m=importlib.import_module(fullname);print(f'IMPORT_STAGE {fullname} PASS project',flush=True);return m
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);print(f'IMPORT_STAGE {fullname} PASS staged',flush=True);return m

ms=load('tools.run_eth_multislot_multiround_1912961','run_eth_multislot_multiround_1912961.py')
genmod=load('tools.eth_repair_modular.generation_aware_allocation_ledger','generation_aware_allocation_ledger.py')
GenerationAwareSharedParentDebtAllocationLedgerV3=genmod.GenerationAwareSharedParentDebtAllocationLedgerV3
x2=ms.x2

class ParentLinkedRepairThreeSlot(ms.MultiSlotMultiRound1912961):
    CONFIG_MAX_SLOTS=3
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        # Same Repair-first allocation API as V2, now with idempotent generation debt attachment.
        self.alignmentAllocationLedgerV2=GenerationAwareSharedParentDebtAllocationLedgerV3()
        self.parentLinkedSeen={}
        self.parentLinkEvents=[]
        self.pendingParentBind={}
        self.parentLinkedQty=0.0
        self.parentLinkApplied=0
        self.parentLinkPending=0
        self.parentLinkMismatch=0

    def _matching_parent_for_extra(self, expand_side):
        rp=getattr(self,'repairParent',None)
        expected='DOWN' if str(expand_side).upper()=='UP' else 'UP'
        if not isinstance(rp,dict):return None,expected,'NO_LIVE_REPAIR_PARENT'
        side=str(rp.get('side') or '').upper()
        if side!=expected:return None,expected,'LIVE_PARENT_SIDE_MISMATCH'
        return rp,expected,'MATCH'

    def _link_increment(self,t,sid,key,inc):
        z=self.slotRegistry.slots.get(int(sid)); expand_side=z.thesis_side if z is not None else self.carrierLedger.get(str(key),{}).get('side')
        rp,expected,reason=self._matching_parent_for_extra(expand_side)
        row={'t':int(t),'event':'EXTRA_SLOT_PARENT_LINK_CHECK','slotId':int(sid),'key':str(key),'expandSide':expand_side,'expectedRepairSide':expected,'fillIncQty':float(inc),'reason':reason}
        if rp is None:
            self.pendingParentBind[str(key)]={'slotId':int(sid),'key':str(key),'expandSide':str(expand_side),'expectedRepairSide':expected,'unboundQty':float(self.pendingParentBind.get(str(key),{}).get('unboundQty',0.0))+float(inc),'firstSeenAt':int(self.pendingParentBind.get(str(key),{}).get('firstSeenAt',t))}
            self.parentLinkPending+=1
            if reason=='LIVE_PARENT_SIDE_MISMATCH':self.parentLinkMismatch+=1
            self.parentLinkEvents.append(row);return False
        pid=int(rp.get('id'));row['parentId']=pid
        # If parent was not yet registered in alignment ledger, seed only the pre-extra component of current inventory gap.
        if self.alignmentAllocationLedgerV2.describe_parent(pid) is None:
            pay=self._current_payoffs();pre=max(0.0,float(pay.get('gap') or 0.0)-float(inc))
            self.alignmentAllocationLedgerV2.register_carrier(f'PARENT_{pid}_MULTISLOT_SEED',pid,pre)
            row['seededPreExtraDebt']=pre
        token=f'MULTISLOT:{key}:PARENT:{pid}'
        ar=self.alignmentAllocationLedgerV2.attach_generation_debt(pid,token,float(inc))
        row.update({'token':token,'attach':ar.__dict__,'parentAfter':self.alignmentAllocationLedgerV2.describe_parent(pid)})
        if ar.applied:
            self.parentLinkApplied+=1;self.parentLinkedQty+=float(ar.added_debt)
            if z is not None:self.slotRegistry.attach_repair_parent(int(sid),pid)
            g=getattr(self,'v48GenByKey',{}).get(str(key))
            if isinstance(g,dict):g['parentId']=pid;g['parentSide']=expected;g['parentLinkToken']=token
            # Confirmed extra Expand fill is a real new Repair-responsibility birth clock.
            if hasattr(self,'v89OverflowBirthClocks'):self.v89OverflowBirthClocks.add(int(t))
            if hasattr(self,'v89Events'):
                self.v89Events.append({'t':int(t),'event':'MULTISLOT_CONFIRMED_EXPAND_REPAIR_RESPONSIBILITY_BIRTH_CLOCK','slotId':int(sid),'key':str(key),'parentId':pid,'repairSide':expected,'addedDebt':float(ar.added_debt)})
        self.parentLinkEvents.append(row);return bool(ar.applied)

    def _try_pending_binds(self,t):
        for key,p in list(self.pendingParentBind.items()):
            qty=float(p.get('unboundQty') or 0.0)
            if qty<=EPS:self.pendingParentBind.pop(key,None);continue
            sid=int(p['slotId']);z=self.slotRegistry.slots.get(sid)
            rp,expected,reason=self._matching_parent_for_extra(p.get('expandSide'))
            if rp is None:continue
            # Remove before link to avoid double accumulation if something throws.
            self.pendingParentBind.pop(key,None)
            self._link_increment(int(t),sid,key,qty)

    def _sync_slots(self,t):
        before=dict(self._seenExtraFill)
        out=super()._sync_slots(int(t))
        # super updated cumulative actual fills; link only newly confirmed increments.
        for sid,key in list(self.extraSlotKeyById.items())+[(sid,key) for sid,key in self.slotRegistry.carrier_to_slot.items() if False]:
            pass
        # carrier_to_slot is key->slot; use it so filled terminal/live carriers remain discoverable.
        for key,sid in list(self.slotRegistry.carrier_to_slot.items()):
            cur=float(self._seenExtraFill.get(str(key),0.0));old=float(self.parentLinkedSeen.get(str(key),0.0))
            if cur>old+EPS:
                inc=cur-old;self.parentLinkedSeen[str(key)]=cur;self._link_increment(int(t),int(sid),str(key),inc)
        self._try_pending_binds(int(t))
        return out

    def run_parent_linked(self,models,winner):
        r=self.run_multislot(models,winner);self._sync_slots(int(self.capEnd))
        r.update({'parentLinkEvents':self.parentLinkEvents[:500],'parentLinkApplied':int(self.parentLinkApplied),'parentLinkedQty':float(self.parentLinkedQty),'parentLinkPendingCount':len(self.pendingParentBind),'parentLinkPending':self.pendingParentBind,'parentLinkMismatch':int(self.parentLinkMismatch),'generationAwareAlignmentLedger':self.alignmentAllocationLedgerV2.name,'generationAwareParentStates':{str(pid):self.alignmentAllocationLedgerV2.describe_parent(pid) for pid in self.alignmentAllocationLedgerV2.parents},'generationAwareAttachments':self.alignmentAllocationLedgerV2.describe_generation_attachments()})
        return r

def compact(r):
    s=ms.summary(r)
    return {**s,'parentLinkApplied':int(r.get('parentLinkApplied') or 0),'parentLinkedQty':float(r.get('parentLinkedQty') or 0.0),'parentLinkPendingCount':int(r.get('parentLinkPendingCount') or 0),'v48GenerationPaidQty':float(r.get('v48GenerationPaidQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',default='AUTO');a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='multislot_parent_link_1912961_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'MULTISLOT_PARENT_LINK_1912961','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTISLOT_PARENT_LINK_1912961_START','market':MID,'slots':3}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=x2.v38.v36.v34.v30.load_runtime(a);teacher=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];gen=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        # Reproduce current 3-slot control using current integrated class.
        C3=ms.make_variant(3);b=x2.base.make_simulator(C3,tape,models,life,cap,tim,econ,price,sur,teacher,gen)
        try:br=b.run_multislot(models,cr['winner'])
        finally:b.close()
        c=x2.base.make_simulator(ParentLinkedRepairThreeSlot,tape,models,life,cap,tim,econ,price,sur,teacher,gen)
        try:rr=c.run_parent_linked(models,cr['winner'])
        finally:c.close()
        bs=compact(br);cs=compact(rr);safe=cs['safetyZero'] and cs['allocationConservation']
        linked=int(rr.get('parentLinkApplied') or 0)>0 and float(rr.get('parentLinkedQty') or 0)>EPS
        paid=float(rr.get('v48GenerationPaidQty') or 0.0)>EPS
        completed=int(rr.get('completedAddedGenerations') or 0)>0
        decision='REJECT_PARENT_LINK_SAFETY_OR_ACCOUNTING' if not safe else ('FUNCTIONAL_PASS_PARENT_LINK_AND_REPAIR_PAYMENT' if paid else ('PARENT_LINK_PASS_EXECUTION_REPAIR_STILL_UNREACHED' if linked else 'PARENT_LINK_NOT_EXERCISED_DIAGNOSE'))
        out={'version':'MULTISLOT_PARENT_LINKED_REPAIR_3SLOT_1912961_RESULT_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'control3Slot':bs,'candidateParentLinked3Slot':cs,'delta':{'pnl':cs['pnlDiagnosticOnly']-bs['pnlDiagnosticOnly'],'terminalFloor':cs['terminalFloor']-bs['terminalFloor'],'worstFloor':cs['worstObservedFloor']-bs['worstObservedFloor'],'generationPaid':cs['v48GenerationPaidQty']-bs['v48GenerationPaidQty'],'completedAddedGenerations':cs['completedAddedGenerations']-bs['completedAddedGenerations']},'gates':{'safetyAndConservation':safe,'parentLinkExercised':linked,'generationPaymentObserved':paid,'completedAddedGenerationObserved':completed},'parentLinkEvents':rr.get('parentLinkEvents',[]),'generationAwareParentStates':rr.get('generationAwareParentStates'),'generationAwareAttachments':rr.get('generationAwareAttachments'),'slotGenerationEvents':rr.get('slotGenerationEvents',[]),'fifoGenerationPaymentEvents':rr.get('fifoGenerationPaymentEvents',[]),'registry':rr.get('multiSlotRegistry'),'extraSlotBlocks':rr.get('extraSlotBlocks'),'boundary':['same consumed market 1912961','3-slot current integrated control vs parent-linked 3-slot candidate','extra confirmed fill binds only to matching live opposite-side Repair parent','no invented parent and no synthetic payment','generation-aware parent debt attach exactly once','ResponsibilityTransition/slot direction/admission/phase capacity frozen from v4','no Target runtime input','realistic HFT only','no dream fill','no 8781']}
        op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json' if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'control':bs,'candidate':cs,'delta':out['delta'],'gates':out['gates'],'linkTail':out['parentLinkEvents'][-12:]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
