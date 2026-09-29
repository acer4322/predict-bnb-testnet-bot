from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9; MID=1946653; pe=base.pe

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='audit_multislot_overflow_1946653_')); stop=threading.Event(); started=time.time()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'AUDIT_MULTISLOT_TRANSITION_OVERFLOW_1946653','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'AUDIT_MULTISLOT_TRANSITION_OVERFLOW_START','marketId':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape=tmp/'tapes'/f'{MID}.json.xz'; s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            r=s.run_locked(models,cr['winner']); cons,bound,parents=pe.alloc(s,r)
            v84={str(k):dict(v) for k,v in getattr(s,'v84Composite',{}).items()}
            allocation_events=list(getattr(s,'allocationV2Events',[]))
            transition_events=list(getattr(s,'transitionEvents',[]))
            prospective_events=list(getattr(s,'prospectiveTransitionEvents',[]))
            occ=getattr(s,'parentExecutionOccupancy',None)
            occ_rows={}
            if occ is not None:
                for pid in sorted(set(c.parent_id for c in occ.carriers.values())):
                    occ_rows[str(pid)]=occ.describe_parent(pid,float(s._parent_debt_now(pid)))
        finally:s.close()
        ss=pe.safety(r)
        overflow_entries=[]
        for k,m in v84.items():
            if float(m.get('overflowAllocated') or 0)>EPS or m.get('overflowBornAt') is not None:
                overflow_entries.append({'key':k,'side':m.get('side'),'parentId':m.get('parentId'),'lane':m.get('lane'),'gapAtSubmit':m.get('gapAtSubmit'),'fillSeen':m.get('fillSeen'),'repairAllocated':m.get('repairAllocated'),'overflowAllocated':m.get('overflowAllocated'),'overflowBornAt':m.get('overflowBornAt'),'overflowDebt':m.get('overflowDebt'),'overflowPaid':m.get('overflowPaid')})
        overflow_fill_events=[e for e in allocation_events if float(e.get('overflowInc') or 0)>EPS]
        born_times={int(x['overflowBornAt']) for x in overflow_entries if x.get('overflowBornAt') is not None}
        transition_after=[]
        for e in transition_events:
            t=int(e.get('t') or -1)
            if born_times and any(t>=b for b in born_times): transition_after.append(e)
        prospective_after=[]
        for e in prospective_events:
            t=int(e.get('t') or -1)
            if born_times and any(t>=b for b in born_times): prospective_after.append(e)
        p=parents.get('1') or parents.get(1) or {}
        physical=float(p.get('repairPaid') or 0)+float(p.get('overflowBorn') or 0)
        allocation_exact=abs(physical-sum(float(e.get('fillInc') or 0) for e in allocation_events if int(e.get('parentId') or -1)==1))<=1e-7
        repair_bound=float(p.get('repairPaid') or 0)<=float(p.get('initialDebt') or 0)+1e-7
        overflow_exact=abs(float(p.get('overflowBorn') or 0)-float(p.get('transitionOverflow') or 0))<=1e-7
        overflow_visible=any(float((e.get('repairDebtBySide') or {}).get('DOWN') or 0)>EPS or float((e.get('repairDebtBySide') or {}).get('UP') or 0)>EPS for e in transition_after)
        no_leak=float(ss.get('preBirthLeak') or 0)<=EPS; no_dup=float(ss.get('duplicateDebt') or 0)<=EPS
        gates={'behaviorSafetyBaseZero':all(float(v or 0)<=EPS for v in ss.values()),'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'repairPaidNeverExceedsInitialDebt':repair_bound,'physicalFillEqualsRepairPlusOverflow':allocation_exact,'overflowMatchesTransitionBucket':overflow_exact,'overflowBirthObserved':bool(overflow_entries),'overflowVisibleToTransitionFrontierPostBirth':overflow_visible,'zeroPreBirthLeak':no_leak,'zeroDuplicateDebt':no_dup}
        decision='TRANSITION_OVERFLOW_CHAIN_PASS' if all(gates.values()) else 'TRANSITION_OVERFLOW_CHAIN_DIAGNOSE'
        out={'version':'ETH_MULTISLOT_TRANSITION_OVERFLOW_AUDIT_1946653_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'decision':decision,'gates':gates,'safety':ss,'allocationParents':parents,'occupancyParents':occ_rows,'overflowCompositeEntries':overflow_entries,'overflowAllocationEvents':overflow_fill_events,'transitionEventsPostOverflow':transition_after[:120],'prospectiveEventsPostOverflow':prospective_after[:120],'candidateSummary':base.slim(r),'boundary':['behavior-inert audit only','EconomicHandoffLeaseLock execution frozen','Repair-first/overflow-second AllocationLedger V2 frozen','no threshold/model/qty/price/timing changes','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'gates':gates,'overflowEntries':overflow_entries,'overflowEvents':overflow_fill_events,'transitionPostCount':len(transition_after),'prospectivePostCount':len(prospective_after),'candidate':out['candidateSummary']},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
