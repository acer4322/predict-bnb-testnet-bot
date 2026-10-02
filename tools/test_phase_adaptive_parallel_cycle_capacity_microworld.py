from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.parallel_cycle_capacity import (
    PhaseAdaptiveParallelCycleCapacityPolicy, ParallelCycleCapacityContext, ParallelCycleSlotState
)

def S(i,born,debt=1.0,expand=True,progress=False,reachable=False,priority=0.0):
    return ParallelCycleSlotState(i,born,True,expand,debt,progress,reachable,priority)

def snap(d):
    return {'capacity':d.new_cycle_capacity,'activeExpandSlots':d.active_expand_slots,'allowNewBirth':d.allow_new_cycle_birth,'reason':d.reason,
            'slots':[{'id':x.slot_id,'mode':x.mode,'requestActiveRepair':x.request_active_repair,'reason':x.reason} for x in d.slot_decisions]}

def main():
    p=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,4),(0.5,3),(0.9,2)))
    checks={}; cases={}
    slots=(S(1,.05,priority=.1),S(2,.08,priority=.2),S(3,.12,priority=.3),S(4,.20,priority=.4))
    d=p.evaluate(ParallelCycleCapacityContext(.25,slots,True)); cases['early4']=snap(d)
    checks['earlyFourPreserved']=d.active_expand_slots==4 and not d.allow_new_cycle_birth and all(x.mode=='EXPAND_AND_REPAIR' for x in d.slot_decisions)
    d5=p.evaluate(ParallelCycleCapacityContext(.25,slots,True)); checks['fifthDeniedAtFull']=not d5.allow_new_cycle_birth
    # At 0.5 the highest repair-priority slot drains. It has no passive progress and active reachability.
    slots_mid=(S(1,.05,priority=.1),S(2,.08,priority=.2),S(3,.12,priority=.3),S(4,.20,progress=False,reachable=True,priority=.9))
    dm=p.evaluate(ParallelCycleCapacityContext(.55,slots_mid,True)); cases['midDrain']=snap(dm)
    drains=[x for x in dm.slot_decisions if x.mode=='DRAIN_REPAIR_ONLY']
    checks['midExactlyOneDrain']=len(drains)==1 and drains[0].slot_id==4
    checks['midDrainRequestsActiveWhenNoProgressReachable']=drains[0].request_active_repair
    checks['debtNotErasedByModeChange']=sum(s.repair_debt for s in slots_mid)==4.0 and len(dm.slot_decisions)==4
    # Late phase: 2 expand slots retained; one drain has passive progress and one has no active reachability.
    slots_late=(S(1,.05,priority=.1),S(2,.08,priority=.2),S(3,.12,progress=True,reachable=True,priority=.8),S(4,.20,progress=False,reachable=False,priority=.9))
    dl=p.evaluate(ParallelCycleCapacityContext(.95,slots_late,True)); cases['lateDrain']=snap(dl)
    ld=[x for x in dl.slot_decisions if x.mode=='DRAIN_REPAIR_ONLY']
    checks['lateExactlyTwoDrain']=len(ld)==2 and {x.slot_id for x in ld}=={3,4}
    checks['progressSuppressesActiveRepairRequest']=next(x for x in ld if x.slot_id==3).request_active_repair is False
    checks['unreachableSuppressesActiveRepairRequest']=next(x for x in ld if x.slot_id==4).request_active_repair is False
    # Completing one of two retained expansion responsibilities frees one late slot.
    completed_late=(S(1,.05,priority=.1),S(3,.12,expand=False,progress=True,reachable=True,priority=.8),S(4,.20,expand=False,progress=False,reachable=False,priority=.9))
    df=p.evaluate(ParallelCycleCapacityContext(.95,completed_late,True)); cases['freedCapacity']=snap(df)
    checks['completionFreesRecoverableBirth']=df.active_expand_slots==1 and df.allow_new_cycle_birth
    du=p.evaluate(ParallelCycleCapacityContext(.95,completed_late,False)); cases['unrecoverableCandidate']=snap(du)
    checks['unrecoverableCandidateDenied']=not du.allow_new_cycle_birth
    checks['phaseOnlyTimeCoordinate']=not hasattr(ParallelCycleCapacityContext,'seconds_left')
    out={'version':'PHASE_ADAPTIVE_PARALLEL_CYCLE_CAPACITY_MICROWORLD_RESULT','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,
         'developmentEnvelopeOnly':[[0.0,4],[0.5,3],[0.9,2]],'checks':checks,'cases':cases,'allPass':all(checks.values()),
         'decision':'PASS_TO_BEHAVIOR_INERT_HFT_SERIALIZATION_OPPORTUNITY_SHADOW' if all(checks.values()) else 'FAIL_FIX_POLICY',
         'boundary':['no order submit','no Repair allocation','no Target runtime input','normalized phase only','capacity shrink preserves live debt','active Repair request requires drain mode + no passive progress + independent route reachability']}
    path=Path('data/research/r4_v0/p0_provenance_v1/PHASE_ADAPTIVE_PARALLEL_CYCLE_CAPACITY_MICROWORLD_RESULT_20260905.json'); path.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))
if __name__=='__main__': main()
