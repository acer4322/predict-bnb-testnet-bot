from __future__ import annotations
import argparse, collections, json, os, shutil, sys, tempfile, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))
EPS=1e-9
BASE_TTL_MS=5000

import importlib.util
def _sib(name, fn):
    q=Path(__file__).resolve().with_name(fn); sp=importlib.util.spec_from_file_location(name,q); m=importlib.util.module_from_spec(sp); sys.modules[name]=m; sp.loader.exec_module(m); return m
try:
    import tools.run_eth_first_carrier_active_relay_smoke as fcr
except ImportError:
    fcr=_sib('fcr_repair_liveness','run_eth_first_carrier_active_relay_smoke.py')
try:
    from tools.eth_repair_modular.payment_aware_queue_lease import PaymentAwareQueueLeaseContext, PaymentAwareRepairQueueLeasePolicyV1
except ImportError:
    _pa=_sib('payment_aware_lease','payment_aware_queue_lease.py'); PaymentAwareQueueLeaseContext=_pa.PaymentAwareQueueLeaseContext; PaymentAwareRepairQueueLeasePolicyV1=_pa.PaymentAwareRepairQueueLeasePolicyV1
try:
    from tools.eth_repair_modular.allocation_aware_parallel_repair_execution_budget import AllocationAwareCompositeParallelRepairPolicyV2
except ImportError:
    AllocationAwareCompositeParallelRepairPolicyV2=_sib('alloc_aware_parallel','allocation_aware_parallel_repair_execution_budget.py').AllocationAwareCompositeParallelRepairPolicyV2

pe=fcr.pe

class PaymentAwareRepairLeaseMixin:
    def __init__(self,*a,**kw):
        self.paymentAwareLeasePolicy=PaymentAwareRepairQueueLeasePolicyV1()
        self.paymentAwareLeaseEvents=[]
        self.paymentAwareLeaseCancelRequests=0
        super().__init__(*a,**kw)

    def _payment_aware_cancel_requests(self,t):
        qlease=getattr(self,'_qlease',{})
        for key,st in list(qlease.items()):
            e=getattr(self,'carrierLedger',{}).get(key,{})
            if str(e.get('objectiveRole') or '').upper()!='REPAIR': continue
            pid=e.get('parentId')
            if pid is None: continue
            o=getattr(self,'orders',{}).get(key,{})
            try:
                snap=self.snap(o) if o else {}
                live=bool(o and fcr.basev1.live(snap.get('status')))
            except Exception:
                live=False
            if not live: continue
            placed=int(o.get('placed') or e.get('submittedAt') or t)
            age=max(0,int(t)-placed)
            try: paid=float(self._paid_total(int(pid)))
            except Exception: paid=0.0
            d=self.paymentAwareLeasePolicy.evaluate(PaymentAwareQueueLeaseContext(
                objective_role='REPAIR', parent_id=int(pid), age_ms=age, base_ttl_ms=BASE_TTL_MS,
                parent_paid_qty=paid, actual_filled_qty=float(e.get('actualFilled') or 0.0),
                terminal_confirmed=bool(e.get('terminalConfirmed')), queue_progress_events=int(st.get('progress') or 0),
            ))
            if not d.revoke_progress_extension: continue
            if key in getattr(self,'cancelRequestedAt',{}): continue
            ok=bool(self._cancel_key(int(t),key))
            self.paymentAwareLeaseEvents.append({'t':int(t),'event':'PAYMENT_AWARE_REPAIR_LEASE_CANCEL_REQUEST','key':key,'parentId':int(pid),'ageMs':age,'parentPaid':paid,'queueProgressEvents':int(st.get('progress') or 0),'reason':d.reason,'cancelOk':ok})
            if ok:self.paymentAwareLeaseCancelRequests+=1

    def cancel_expired(self,t):
        self._payment_aware_cancel_requests(int(t))
        return super().cancel_expired(t)

    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner)
        r.update({'paymentAwareRepairQueueLeasePolicy':self.paymentAwareLeasePolicy.name,'paymentAwareLeaseCancelRequests':self.paymentAwareLeaseCancelRequests,'paymentAwareLeaseEvents':self.paymentAwareLeaseEvents[:200]})
        return r

class AllocationAwareRepairBudgetMixin:
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.sharedBudgetPolicy=AllocationAwareCompositeParallelRepairPolicyV2()

    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner)
        r['allocationAwareCompositeParallelRepairPolicy']=self.sharedBudgetPolicy.name
        return r

class A_Current(fcr.FirstCarrierActiveRelayHFT):
    pass
class B_PaymentAware(PaymentAwareRepairLeaseMixin,fcr.FirstCarrierActiveRelayHFT):
    pass
class C_AllocationAware(AllocationAwareRepairBudgetMixin,fcr.FirstCarrierActiveRelayHFT):
    pass
class D_Both(PaymentAwareRepairLeaseMixin,AllocationAwareRepairBudgetMixin,fcr.FirstCarrierActiveRelayHFT):
    pass


def slim(r,ss,cons,bound,occ):
    parents=r.get('allocationV2Parents') or {}
    paid=sum(float(p.get('repairPaid') or 0.0) for p in parents.values())
    rem=sum(float(p.get('remainingDebt') or 0.0) for p in parents.values())
    overflow=sum(float(p.get('overflowBorn') or p.get('transitionOverflow') or 0.0) for p in parents.values())
    return {
        'fills':int(r.get('actualFillEvents') or 0),'submits':int(r.get('submits') or 0),
        'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),
        'pnl':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),
        'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),
        'firstCarrierActiveFillQty':float(r.get('firstCarrierActiveFillQty') or 0.0),
        'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0.0),
        'repairPaidTotal':paid,'remainingDebtTotal':rem,'overflowBornTotal':overflow,
        'paymentAwareLeaseCancelRequests':int(r.get('paymentAwareLeaseCancelRequests') or 0),
        'safety':ss,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occ),
        'reservationReasons':dict(collections.Counter(e.get('reason') for e in (r.get('reservationAwareEvents') or []) if e.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION')),
    }


def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix=f'first_carrier_repair_liveness_2x2_{mid}_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        cells=[]
        for name,cls in [('A_CURRENT',A_Current),('B_PAYMENT_AWARE_LEASE',B_PaymentAware),('C_ALLOCATION_AWARE_COMPOSITE',C_AllocationAware),('D_BOTH',D_Both)]:
            s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_first_carrier_relay(models,cr['winner']);cons,bound,parents=pe.alloc(s,r)
            finally:s.close()
            ss=pe.safety(r);safe=all(float(v or 0.0)<=EPS for v in ss.values());occ=all(float(p.get('overReservedQty') or 0.0)<=EPS for p in (r.get('occupancyParents') or {}).values())
            row={'cell':name,'metrics':slim(r,ss,cons,bound,occ),'allocationParents':parents,'paymentAwareLeaseEvents':r.get('paymentAwareLeaseEvents',[])[:80],'reservationEvents':[e for e in (r.get('reservationAwareEvents') or []) if e.get('reason') in ('ALLOW_MIN_LEGAL_ACTIVE_COMPOSITE_OVERFLOW','INSUFFICIENT_UNRESERVED_PARENT_CAPACITY','ABOVE_INHERITED_ECONOMIC_CEILING')][:120]}
            row['safe']=bool(safe and cons and bound and occ);cells.append(row);print(json.dumps({'cell':name,**row['metrics']},ensure_ascii=False),flush=True)
        by={x['cell']:x for x in cells};A=by['A_CURRENT']['metrics'];B=by['B_PAYMENT_AWARE_LEASE']['metrics'];C=by['C_ALLOCATION_AWARE_COMPOSITE']['metrics'];D=by['D_BOTH']['metrics']
        gates={
            'A_reproduces_unpaid_loss':A['repairPaidTotal']<=EPS and A['pnl']<0,
            'B_changes_queue_lease':B['paymentAwareLeaseCancelRequests']>0,
            'C_policy_exercised_or_block_shape_changes':C['reservationReasons']!=A['reservationReasons'],
            'D_safety_zero':by['D_BOTH']['safe'],
            'D_repair_payment_positive':D['repairPaidTotal']>EPS,
            'D_round_or_floor_improves':D['rounds']>A['rounds'] or D['floor']>A['floor']+EPS,
            'D_pnl_improves':D['pnl']>A['pnl']+EPS,
        }
        if not by['D_BOTH']['safe']:decision='REJECT_INTERACTION_SAFETY'
        elif gates['D_repair_payment_positive'] and gates['D_pnl_improves']:decision='KEEP_INTERACTION_FOR_ONE_MARKET_REPLICATION'
        else:decision='INTERACTION_INCOMPLETE_DIAGNOSE_NEXT_REPAIR_EXECUTION_SEAM'
        out={'version':'ETH_FIRST_CARRIER_POSTFILL_REPAIR_LIVENESS_2X2_1945869_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'cells':cells,'boundary':['first-carrier relay frozen across all cells','B changes only queue-progress lease extension eligibility after existing 5s base TTL when parent confirmed payment remains zero','C changes only venue-min Active Repair admission when no sibling reservation exists and whole parent debt is owned by current epoch; physical excess delegates to AllocationLedger V2 Repair-first/overflow-second','economic ceiling/payment-progress/ownership/<=180 fences frozen','no threshold/price/delay tuning; no Target runtime input; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
