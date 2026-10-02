from __future__ import annotations
import argparse,collections,json,shutil,sys,tempfile,zipfile
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9

import importlib.util
def _sib(name, fn):
    q=Path(__file__).resolve().with_name(fn); sp=importlib.util.spec_from_file_location(name,q); m=importlib.util.module_from_spec(sp); sys.modules[name]=m; sp.loader.exec_module(m); return m
try:
    import tools.run_eth_first_carrier_repair_liveness_2x2_1945869 as prev
except ImportError:
    prev=_sib('prev1945869','run_eth_first_carrier_repair_liveness_2x2_1945869.py')
try:
    from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
except ImportError:
    GenerationAwareSharedParentDebtAllocationLedgerV3=_sib('genledger','generation_aware_allocation_ledger.py').GenerationAwareSharedParentDebtAllocationLedgerV3
try:
    from tools.eth_repair_modular.generation_scoped_active_ownership import GenerationActiveOwnershipContext,GenerationScopedActiveOwnershipPolicyV1
except ImportError:
    _ga=_sib('genactive','generation_scoped_active_ownership.py'); GenerationActiveOwnershipContext=_ga.GenerationActiveOwnershipContext; GenerationScopedActiveOwnershipPolicyV1=_ga.GenerationScopedActiveOwnershipPolicyV1

pe=prev.pe

class GenerationDebtAttachMixin:
    def __init__(self,*a,**kw):
        self.generationDebtAttachEvents=[];self._generationDebtHandled=set()
        super().__init__(*a,**kw)
        # Safe at construction time: no market events/carriers have run yet.
        self.allocationLedgerV2=GenerationAwareSharedParentDebtAllocationLedgerV3()
    def _attach_new_epoch_after_resolution(self,t):
        out=super()._attach_new_epoch_after_resolution(t)
        for ev in list(getattr(self,'generationEpochEvents',[])):
            if ev.get('event')!='EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH':continue
            src=str(ev.get('sourceKey')); token=f"{src}:epoch:{int(ev.get('epoch') or 0)}"
            if token in self._generationDebtHandled:continue
            self._generationDebtHandled.add(token);pid=int(ev.get('parentId'));add=float(ev.get('addedDebt') or 0.0)
            ar=self.allocationLedgerV2.attach_generation_debt(pid,token,add)
            self.generationDebtAttachEvents.append({'t':int(t),'event':'GENERATION_DEBT_LEDGER_ATTACH','sourceKey':src,'parentId':pid,'epoch':int(ev.get('epoch') or 0),**ar.__dict__})
        return out
    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner);r.update({'generationAwareAllocationLedger':getattr(self.allocationLedgerV2,'name',None),'generationDebtAttachEvents':self.generationDebtAttachEvents,'generationDebtAttachApplied':sum(bool(x.get('applied')) for x in self.generationDebtAttachEvents)});return r

class GenerationActiveRearmMixin:
    def __init__(self,*a,**kw):
        self.generationActiveOwnershipPolicy=GenerationScopedActiveOwnershipPolicyV1();self.generationActiveRearmEvents=[];self._generationRearmPending={};self._generationRearmDone=set();super().__init__(*a,**kw)
    def _attach_new_epoch_after_resolution(self,t):
        before={(str(e.get('sourceKey')),int(e.get('epoch') or 0)) for e in getattr(self,'generationEpochEvents',[]) if e.get('event')=='EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH'}
        out=super()._attach_new_epoch_after_resolution(t)
        for ev in list(getattr(self,'generationEpochEvents',[])):
            if ev.get('event')!='EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH':continue
            tok=(str(ev.get('sourceKey')),int(ev.get('epoch') or 0))
            if tok in before or tok in self._generationRearmPending or tok in self._generationRearmDone:continue
            pid=int(ev.get('parentId'));a=getattr(self,'activeByParent',{}).get(pid,{})
            self._generationRearmPending[tok]={'parentId':pid,'epoch':tok[1],'sourceKey':tok[0],'oldActiveKey':str(a.get('key')) if a.get('key') else None,'paidAtAttach':float(ev.get('paidBase') or 0.0)}
            self.generationActiveRearmEvents.append({'t':int(t),'event':'GENERATION_ACTIVE_REARM_PENDING',**self._generationRearmPending[tok]})
        return out
    def _try_generation_active_rearm(self,t):
        for tok,ctx0 in list(self._generationRearmPending.items()):
            if tok in self._generationRearmDone:continue
            pid=int(ctx0['parentId']);old_key=ctx0.get('oldActiveKey')
            # If ownership disappeared naturally before this check, only clear a stale parent-level hard flag.
            current=getattr(self,'activeByParent',{}).get(pid)
            if old_key is None:
                if current is None:
                    getattr(self,'hardConfirmed',set()).discard(pid);self._generationRearmDone.add(tok);self.generationActiveRearmEvents.append({'t':int(t),'event':'GENERATION_ACTIVE_REARM_NO_OLD_OWNER','parentId':pid,'epoch':ctx0['epoch']})
                continue
            if current is not None and str(current.get('key'))!=str(old_key):
                # A new owner already exists; do not disturb it.
                self._generationRearmDone.add(tok);continue
            self._refresh_carrier_ledger(int(t));e=getattr(self,'carrierLedger',{}).get(str(old_key),{});o=getattr(self,'orders',{}).get(str(old_key),{})
            try:live=bool(o and prev.fcr.basev1.live(self.snap(o).get('status')))
            except Exception:live=False
            terminal=bool(e.get('terminalConfirmed'));filled=float(e.get('actualFilled') or 0.0)
            d=self.generationActiveOwnershipPolicy.evaluate(GenerationActiveOwnershipContext(parent_id=pid,new_epoch=int(ctx0['epoch']),old_active_key=str(old_key),old_active_live=live,old_active_terminal_confirmed=terminal,old_active_actual_filled=filled,paid_at_new_epoch=float(ctx0['paidAtAttach'])))
            self.generationActiveRearmEvents.append({'t':int(t),'event':'GENERATION_ACTIVE_REARM_EVALUATION','parentId':pid,'epoch':ctx0['epoch'],'oldActiveKey':old_key,'oldActiveLive':live,'oldActiveTerminal':terminal,'oldActiveFilled':filled,'paidAtAttach':ctx0['paidAtAttach'],'reason':d.reason,'allowArchive':bool(d.allow_archive_old_ownership)})
            if not d.allow_archive_old_ownership:continue
            getattr(self,'activeByParent',{}).pop(pid,None);getattr(self,'hardConfirmed',set()).discard(pid)
            st=getattr(self,'generationEpochByParent',{}).get(pid)
            if st is not None:st.active_owned=False
            self._generationRearmDone.add(tok)
            self.generationActiveRearmEvents.append({'t':int(t),'event':'GENERATION_TERMINAL_OLD_ACTIVE_ARCHIVED','parentId':pid,'epoch':ctx0['epoch'],'oldActiveKey':old_key})
    def _maybe_hard_active(self,t):
        self._try_generation_active_rearm(int(t));return super()._maybe_hard_active(t)
    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner);r.update({'generationScopedActiveOwnershipPolicy':self.generationActiveOwnershipPolicy.name,'generationActiveRearmEvents':self.generationActiveRearmEvents,'generationActiveRearmArchives':sum(x.get('event')=='GENERATION_TERMINAL_OLD_ACTIVE_ARCHIVED' for x in self.generationActiveRearmEvents)});return r

class A_Current(prev.D_Both):pass
class B_DebtAttach(GenerationDebtAttachMixin,prev.D_Both):pass
class C_ActiveRearm(GenerationActiveRearmMixin,prev.D_Both):pass
class D_Both(GenerationActiveRearmMixin,GenerationDebtAttachMixin,prev.D_Both):pass

def summarize(sim,r):
 cons,bound,parents=pe.alloc(sim,r);ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ=all(float(p.get('overReservedQty') or 0)<=EPS for p in (r.get('occupancyParents') or {}).values())
 physical=[]
 for key,e in getattr(sim,'carrierLedger',{}).items():
  q=float(e.get('actualFilled') or 0.0)
  if q<=EPS:continue
  o=getattr(sim,'orders',{}).get(key,{})
  physical.append({'key':str(key),'side':e.get('side') or o.get('side'),'qty':q,'price':o.get('price'),'role':e.get('objectiveRole') or o.get('objective_role'),'lane':e.get('lane'),'parentId':e.get('parentId'),'submittedAt':e.get('submittedAt') or o.get('placed')})
 physical.sort(key=lambda x:int(x.get('submittedAt') or 0))
 paid=sum(float(p.get('repairPaid') or 0) for p in parents.values());rem=sum(float(p.get('remainingDebt') or 0) for p in parents.values());init=sum(float(p.get('initialDebt') or 0) for p in parents.values())
 return {'fills':int(r.get('actualFillEvents') or 0),'submits':int(r.get('submits') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnl':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'allocationInitialDebt':init,'repairPaid':paid,'remainingDebt':rem,'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0),'generationDebtAttachApplied':int(r.get('generationDebtAttachApplied') or 0),'generationActiveRearmArchives':int(r.get('generationActiveRearmArchives') or 0),'physical':physical,'reservationReasons':dict(collections.Counter(e.get('reason') for e in (r.get('reservationAwareEvents') or []) if e.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION')),'safe':bool(safe and cons and bound and occ),'safety':ss,'allocationConservation':bool(cons),'allocationBounded':bool(bound),'occupancyBounded':bool(occ),'allocationParents':parents}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix='gen_debt_rearm_2x2_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz';rows=[]
  for name,cls in [('A_CURRENT',A_Current),('B_GENERATION_DEBT_ATTACH',B_DebtAttach),('C_GENERATION_ACTIVE_REARM',C_ActiveRearm),('D_BOTH',D_Both)]:
   s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
   try:r=s.run_first_carrier_relay(models,cr['winner']);m=summarize(s,r);events={'generationDebtAttachEvents':r.get('generationDebtAttachEvents',[]),'generationActiveRearmEvents':r.get('generationActiveRearmEvents',[]),'generationEpochEvents':r.get('generationEpochEvents',[]),'reservationAwareEvents':r.get('reservationAwareEvents',[])[:300]}
   finally:s.close()
   rows.append({'cell':name,'metrics':m,'events':events});print(json.dumps({'cell':name,**{k:v for k,v in m.items() if k not in ('physical','safety','allocationParents','reservationReasons')},'reservationReasons':m['reservationReasons'],'physical':m['physical']},ensure_ascii=False),flush=True)
  by={x['cell']:x['metrics'] for x in rows};A=by['A_CURRENT'];B=by['B_GENERATION_DEBT_ATTACH'];C=by['C_GENERATION_ACTIVE_REARM'];D=by['D_BOTH']
  gates={'A_reproduces_partial_repair_then_stall':A['repairPaid']>EPS and A['remainingDebt']>EPS,'B_generation_debt_attached':B['generationDebtAttachApplied']>0 and B['allocationInitialDebt']>A['allocationInitialDebt']+EPS,'C_old_active_archived':C['generationActiveRearmArchives']>0,'D_both_capabilities_exercised':D['generationDebtAttachApplied']>0 and D['generationActiveRearmArchives']>0,'D_safety_zero':D['safe'],'D_more_repair_payment_or_rounds':D['repairPaid']>A['repairPaid']+EPS or D['rounds']>A['rounds'],'D_floor_or_pnl_improves':D['floor']>A['floor']+EPS or D['pnl']>A['pnl']+EPS}
  if not D['safe']:decision='REJECT_GENERATION_INTERACTION_SAFETY'
  elif gates['D_more_repair_payment_or_rounds'] and gates['D_floor_or_pnl_improves']:decision='KEEP_GENERATION_DEBT_X_ACTIVE_REARM_FOR_REPLICATION'
  else:decision='GENERATION_INTERACTION_INCOMPLETE_DIAGNOSE_NEXT_EXECUTION_SEAM'
  out={'version':'ETH_GENERATION_DEBT_X_ACTIVE_REARM_2X2_1945869_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'cells':rows,'boundary':['previous first-carrier relay + payment-aware base-TTL handoff + allocation-aware composite policy frozen in all cells','B only adds idempotent new-generation debt to same shared parent AllocationLedger without resetting paid debt','C only archives previous-generation Active ownership after old carrier terminal confirmation and inclusion of old fill in new-epoch paid baseline','D combines B+C','old live/cancel-pending Active always blocks rearm','same physical Repair parent retained','no threshold/qty/price/delay tuning','<=180 fence, economic ceiling, payment progress and occupancy frozen','winner posthoc only; no Target runtime input; realistic HFT; no dream fill; no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
