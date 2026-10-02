from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))

def sibling(name,path):
    p=Path(path); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

base=sibling('v83_epoch_for_same_parent_parallel',Path(__file__).resolve().with_name('run_eth_v83_existing_parent_responsibility_generation_epoch_hft_1916869.py'))
from tools import run_eth_dagger60_smoke_v1 as v1

EPS=1e-9
FIXED=1916869
ECON_CEILING=0.6124560577773237
CONTROL_FLOOR=-0.8390312285187402
CONTROL_FILLS=3
CONTROL_ROUNDS=0
CONTROL_REPAIR_PARENT_BIRTHS=1
birth=base.birth; v38=base.v38; v80=base.v80

class SameParentParallelRepairHFT(base.ExistingParentResponsibilityGenerationEpochHFT):
    """Tier-0 coupled structural candidate.

    Only mutation relative to the frozen epoch candidate:
      * Active execution capacity is authoritative same-parent unresolved Repair debt.
      * One minimum-legal Active Repair child may coexist with the live passive Repair carrier.
      * No disconnect prerequisite.
    Frozen V36/V84 shared-carrier reconciliation and AllocationLedger V2 remain downstream.
    """
    def __init__(self,*a,**kw):
        self.parallelRepairChecks=0
        self.parallelRepairEligible=0
        self.parallelRepairSubmits=0
        self.parallelRepairActiveKeys=set()
        self.parallelRepairEvents=[]
        super().__init__(*a,**kw)

    def _find_parent_repair_anchor(self,pid):
        live=None
        if hasattr(self,'_find_passive'):
            try: live=self._find_passive(pid)
            except Exception: live=None
        if live is not None:
            return live, True
        cand=[]
        for key,e in getattr(self,'carrierLedger',{}).items():
            try:
                if int(e.get('parentId') or -1)!=int(pid): continue
            except Exception:
                continue
            if str(e.get('objectiveRole') or '').upper()!='REPAIR': continue
            if str(e.get('lane') or '').upper().startswith('ACTIVE_') or 'ACTIVE_REPAIR' in str(e.get('lane') or '').upper(): continue
            o=getattr(self,'orders',{}).get(key)
            placed=int((o or {}).get('placed') or e.get('submittedAt') or 0)
            rem=max(0.0,float(e.get('submittedQty') or (o or {}).get('qty') or 0.0)-float(e.get('actualFilled') or 0.0))
            cand.append((placed,str(key),e,rem,o))
        if not cand: return None, False
        cand.sort(key=lambda x:x[0])
        _,key,e,rem,o=cand[-1]
        return (key,e,rem,o), False

    def _maybe_hard_active(self,t):
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict):
            return super()._maybe_hard_active(t)
        pid=int(rp.get('id')); side=str(rp.get('side'))
        st=getattr(self,'generationEpochByParent',{}).get(pid)
        if st is None or not getattr(st,'armed',False):
            return super()._maybe_hard_active(t)

        self.parallelRepairChecks += 1
        parent_fill_now=float(self._parent_actual_fill(pid))
        churn_now=int(self._parent_churn_count(pid))
        paid_now=float(self._paid_total(pid))
        obs=st.observe(parent_fill_now=parent_fill_now,churn_now=churn_now,paid_total_now=paid_now)
        pay=self._current_payoffs()
        seconds_left=(int(self.capEnd)-int(t))/1000.0
        qv=v1.quotes(self.book)
        ask=None; legal=None
        if qv and side in qv and qv[side].get('ask') is not None:
            ask=float(qv[side]['ask'])
            legal=(1.0/ask) if ask>EPS else math.inf
        debt=float(self._manager_debt_for_parent(pid,float(pay['gap'])))
        active_owned=pid in getattr(self,'activeByParent',{}) or bool(getattr(st,'active_owned',False))
        hard_confirmed=pid in getattr(self,'hardConfirmed',set())
        psv,passive_live=self._find_parent_repair_anchor(pid)

        reason='ALLOW_MIN_LEGAL_ACTIVE_SHARED_PARENT_BUDGET'
        projected=None
        if side in ('UP','DOWN') and ask is not None and legal is not None and math.isfinite(legal):
            weak=float(pay['up'] if side=='UP' else pay['down'])
            other=float(pay['down'] if side=='UP' else pay['up'])
            projected=min(weak+(1.0-ask)*legal, other-ask*legal)
        if side not in ('UP','DOWN'): reason='NO_REPAIR_PARENT_SIDE'
        elif not bool(obs.get('armed')): reason='NOT_ARMED'
        elif active_owned or hard_confirmed: reason='ACTIVE_ALREADY_OWNED'
        elif bool(obs.get('paymentProgress')): reason='PAYMENT_PROGRESS_CONTINUE_PASSIVE'
        elif seconds_left<=180.0: reason='LATE_NO_NEW_ACTIVE_EXPOSURE'
        elif float(pay['floor'])>=-EPS or debt<=EPS: reason='NO_NEGATIVE_FLOOR_REPAIR_NEED'
        elif psv is None: reason='NO_PARENT_REPAIR_LINEAGE_ANCHOR'
        elif ask is None or legal is None or not math.isfinite(legal): reason='NO_EXECUTABLE_ACTIVE_FRONTIER'
        elif ask>ECON_CEILING+EPS: reason='ABOVE_INHERITED_ECONOMIC_CEILING'
        elif legal<=EPS or legal>12.0+EPS: reason='ILLEGAL_PHYSICAL_SLICE'
        elif legal>debt+EPS: reason='LEGAL_MIN_EXCEEDS_AUTHORITATIVE_PARENT_DEBT'
        elif projected is None or projected+EPS<float(pay['floor']): reason='PROJECTED_FLOOR_DAMAGING'

        ev={'t':int(t),'event':'SAME_PARENT_PARALLEL_REPAIR_EVALUATION','parentId':pid,'parentSide':side,
            'epoch':int(st.epoch),'postEpochChurn':int(obs.get('postEpochChurn') or 0),
            'paymentProgress':bool(obs.get('paymentProgress')),'paidSinceEpoch':float(obs.get('paidSinceEpoch') or 0.0),
            'floor':float(pay['floor']),'projectedFloorAfterActive':projected,'managerDebt':debt,'liveAsk':ask,
            'legalPhysicalQty':legal,'secondsLeft':seconds_left,'passiveLive':bool(passive_live),'lineageAnchorExists':psv is not None,'reason':reason,
            'allow':reason=='ALLOW_MIN_LEGAL_ACTIVE_SHARED_PARENT_BUDGET'}
        self.parallelRepairEvents.append(ev)
        if reason!='ALLOW_MIN_LEGAL_ACTIVE_SHARED_PARENT_BUDGET':
            return False

        self.parallelRepairEligible += 1
        passive_key,e,rem,o=psv
        oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
        q=float(legal)
        if hasattr(self,'hardConfirmed'): self.hardConfirmed.add(pid)
        if hasattr(self,'hardEventConfirmedCount'): self.hardEventConfirmedCount += 1
        st.active_owned=True
        ok=self._submit_active(int(t),pid,str(passive_key),side,float(ask),q,oid)
        if ok:
            self.parallelRepairSubmits += 1
            self.generationEpochActiveSubmits += 1
            active=getattr(self,'activeByParent',{}).get(pid,{})
            ak=active.get('key')
            if ak:
                self.parallelRepairActiveKeys.add(str(ak)); self.generationEpochActiveFillKeys.add(str(ak))
                if hasattr(self,'v84Composite'):
                    self.v84Composite[str(ak)]={'key':str(ak),'side':side,'parentId':pid,'price':float(ask),
                        'submittedQty':q,'gapAtSubmit':debt,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,
                        'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,
                        'lane':'V83_SAME_PARENT_PARALLEL_ACTIVE_REPAIR'}
                    if hasattr(self,'v84CompositeSubmits'): self.v84CompositeSubmits += 1
            self.parallelRepairEvents.append({'t':int(t),'event':'SAME_PARENT_PARALLEL_ACTIVE_REPAIR_SUBMIT',
                'parentId':pid,'side':side,'passiveKey':str(passive_key),'activeKey':ak,'ask':float(ask),'qty':q,
                'managerDebt':debt,'projectedFloorAfterActive':projected})
        else:
            st.active_owned=False
            if hasattr(self,'hardConfirmed'): self.hardConfirmed.discard(pid)
        return bool(ok)

    def run_parallel(self,models,winner):
        r=super().run_candidate(models,winner)
        active_fill=0.0
        for key in self.parallelRepairActiveKeys:
            m=getattr(self,'v84Composite',{}).get(key,{})
            active_fill+=float(m.get('fillSeen') or 0.0)
        r.update({'parallelRepairChecks':self.parallelRepairChecks,'parallelRepairEligible':self.parallelRepairEligible,
            'parallelRepairSubmits':self.parallelRepairSubmits,'parallelRepairActiveFillQty':active_fill,
            'parallelRepairEvents':self.parallelRepairEvents[:400]})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    if int(a.market_id)!=FIXED: raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='v83_same_parent_parallel_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'V83_SAME_PARENT_PARALLEL_REPAIR','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'V83_SAME_PARENT_PARALLEL_REPAIR_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape=tmp/'tapes'/f'{FIXED}.json.xz'
        c=SameParentParallelRepairHFT(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,
            price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try: rr=c.run_parallel(models,cr['winner'])
        finally: c.close()
        ss=birth.base.front.safety(rr)
        cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        parents=rr.get('allocationV2Parents') or {str(pid):c.allocationLedgerV2.describe_parent(pid) for pid in getattr(c,'allocationLedgerV2',{}).parents} if hasattr(c,'allocationLedgerV2') else {}
        parent_ok=all(float(x.get('repairPaid') or 0)<=float(x.get('initialDebt') or 0)+1e-7 and float(x.get('remainingDebt') or 0)>=-EPS for x in parents.values() if x)
        duplicate_parent=max(0,int(rr.get('repairParentBirths') or 0)-CONTROL_REPAIR_PARENT_BIRTHS)
        safety_zero=all(float(v)<=EPS for v in ss.values())
        rounds=int(rr.get('v70dSemanticRounds') or rr.get('rounds') or 0)
        active_fill=float(rr.get('parallelRepairActiveFillQty') or 0.0)
        gates={'candidateClockExercised':int(rr.get('parallelRepairEligible') or 0)>0,
            'activeRepairSubmit':int(rr.get('parallelRepairSubmits') or 0)>=1,
            'physicalActionSupport':active_fill>EPS or rounds>CONTROL_ROUNDS,
            'allSafetyAccountingZero':safety_zero,'allocationConservation':cons,
            'repairAllocationNeverExceedsAuthoritativeParentDebt':parent_ok,'duplicateRepairParentBirths':duplicate_parent==0,
            'terminalFloor':float(rr.get('floor') or 0.0)>=CONTROL_FLOOR-1e-7}
        if not gates['candidateClockExercised'] or not gates['activeRepairSubmit']:
            decision='REJECT_OR_DIAGNOSE_SAME_PARENT_PARALLEL_REPAIR_INTEGRATION'
        elif not all([gates['allSafetyAccountingZero'],gates['allocationConservation'],gates['repairAllocationNeverExceedsAuthoritativeParentDebt'],gates['duplicateRepairParentBirths'],gates['terminalFloor']]):
            decision='REJECT_SAME_PARENT_PARALLEL_REPAIR_SAFETY_OR_FLOOR'
        elif not gates['physicalActionSupport']:
            decision='STOP_EXECUTION_INCONCLUSIVE_SUBMIT_NO_FILL_OR_ROUND_GAIN'
        else:
            decision='TIER0_PASS_KEEP_FOR_ONE_MARKET_REPLICATION'
        out={'version':'ETH_V83_SAME_PARENT_PARALLEL_REPAIR_HFT_SMOKE_1916869_RESULT','date':'2026-09-04','researchOnly':True,
            'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,
            'frozenControl':{'fills':CONTROL_FILLS,'rounds':CONTROL_ROUNDS,'floor':CONTROL_FLOOR,'activeRepairFillQty':0.0},
            'candidate':{'fills':rr.get('actualFillEvents'),'pnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),
                'rounds':rounds,'activeRepairFillQty':active_fill,'parallelRepairChecks':rr.get('parallelRepairChecks'),
                'parallelRepairEligible':rr.get('parallelRepairEligible'),'parallelRepairSubmits':rr.get('parallelRepairSubmits'),
                'repairParentBirths':rr.get('repairParentBirths'),'v84CompositeFillQty':rr.get('v84CompositeFillQty'),
                'v84RepairAllocatedQty':rr.get('v84RepairAllocatedQty'),'v84OverflowAllocatedQty':rr.get('v84OverflowAllocatedQty'),
                'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty')},
            'safety':ss,'allocationParents':parents,'parallelRepairEvents':rr.get('parallelRepairEvents',[]),
            'allocationEvents':rr.get('allocationV2Events',[])[:240],
            'boundary':['single market 1916869','candidate only; frozen control artifact reused','same-parent aggregate Repair debt','one min-legal Active child maximum','passive stays live until confirmed execution reconciliation','shared AllocationLedger V2 Repair-first/overflow-second','inherited economic ceiling frozen','exact two-outcome projected floor non-damaging','payment progress blocks new Active','<=180s fence frozen','no pExpand/price/delay tuning','realistic HFT','no dream fill','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'gates':gates,'candidate':out['candidate'],'safety':ss,'firstEvents':out['parallelRepairEvents'][:12]},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
