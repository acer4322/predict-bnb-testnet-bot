from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.multi_parent_responsibility_pool import MultiParentResponsibilityPoolV1

def ck(name,cond,detail=None):
    return {'name':name,'pass':bool(cond),'detail':detail}

def main():
    p=MultiParentResponsibilityPoolV1(); tests=[]
    ids=[]
    for i in range(5):
        x=p.birth(objective_family='STATE_SHAPING' if i%2 else 'PAIR_BALANCE',side='UP' if i%2 else 'DOWN',objective_key=f'obj{i}',debt=1+i*.2,born_at_ms=i*1000,birth_capacity=5);ids.append(x.parent_id)
    tests.append(ck('five_distinct_parents_can_coexist',p.live_count()==5,p.snapshot()))
    tests.append(ck('sixth_birth_blocked_at_capacity',not p.birth_decision(objective_family='PAIR_BALANCE',side='UP',objective_key='obj5',debt=1,birth_capacity=5).allow))
    # Capacity shrink never kills debt.
    before=p.total_remaining_debt(); d=p.birth_decision(objective_family='PAIR_BALANCE',side='UP',objective_key='late',debt=1,birth_capacity=3)
    tests.append(ck('capacity_shrink_blocks_new_birth',not d.allow and d.reason=='PHASE_BIRTH_CAPACITY_REACHED',d.__dict__))
    tests.append(ck('capacity_shrink_does_not_retire_existing',p.live_count()==5 and abs(p.total_remaining_debt()-before)<1e-9))
    # Parent-local reservations cannot double-spend capacity.
    p.reserve(ids[0],passive_qty=.6,active_qty=.4)
    try:
        p.reserve(ids[0],passive_qty=.8,active_qty=.8); over=False
    except ValueError as e: over=str(e)=='PARENT_CAPACITY_OVERRESERVED'
    tests.append(ck('same_parent_reservation_bounded',over))
    # Payment only touches addressed parent and uses Repair-first / Overflow-second.
    other_before=p.parents[ids[1]].remaining_debt
    r=p.apply_confirmed_fill(ids[0],1.25)
    tests.append(ck('repair_first_then_overflow',abs(r.repair_paid-1.0)<1e-9 and abs(r.overflow-.25)<1e-9 and r.completed,r.__dict__))
    tests.append(ck('cross_parent_debt_unchanged',abs(p.parents[ids[1]].remaining_debt-other_before)<1e-9))
    tests.append(ck('completed_parent_retires_without_touching_others',p.live_count()==4 and all(p.parents[i].live for i in ids[1:])))
    # Capacity below current live count still cannot kill parents, but service remains legal.
    tests.append(ck('existing_debt_service_survives_birth_freeze',p.may_service_existing_debt(ids[1]) and p.live_count()==4))
    bd=p.birth_decision(objective_family='STATE_SHAPING',side='DOWN',objective_key='tail-new',debt=2,birth_capacity=0,allow_new_speculative_birth=False)
    tests.append(ck('tail_new_speculative_birth_disabled',not bd.allow and bd.reason=='NEW_SPECULATIVE_BIRTH_DISABLED',bd.__dict__))
    # New generation belongs to one existing parent and must not reset paid debt.
    pid=ids[1]; old_init=p.parents[pid].initial_debt; old_paid=p.parents[pid].repair_paid
    p.apply_confirmed_fill(pid,.5); paid=p.parents[pid].repair_paid; p.attach_generation_debt(pid,1.25)
    tests.append(ck('generation_debt_adds_without_resetting_paid',abs(p.parents[pid].initial_debt-(old_init+1.25))<1e-9 and abs(p.parents[pid].repair_paid-paid)<1e-9 and paid>=old_paid))
    # Once concurrency falls through retirement, new birth can resume if capacity allows.
    for i in ids[2:]:
        if p.parents[i].live:p.apply_confirmed_fill(i,p.parents[i].remaining_debt)
    allow=p.birth_decision(objective_family='PAIR_BALANCE',side='UP',objective_key='new-after-retire',debt=1,birth_capacity=2)
    tests.append(ck('retirement_frees_birth_capacity',allow.allow,{'liveCount':p.live_count(),'decision':allow.__dict__}))
    out={'version':'ETH_MULTI_PARENT_RESPONSIBILITY_POOL_MICROWORLD_V1','researchOnly':True,'policy':p.name,'tests':tests,'passed':sum(x['pass'] for x in tests),'total':len(tests),'allPass':all(x['pass'] for x in tests),'boundary':['architecture-only; no HFT behavior change','phase birth capacity is caller supplied; no fixed 5/3/1 policy learned here','capacity shrink never deletes existing debt','existing-debt service remains legal when new speculative birth is disabled','parent-local Repair-first/overflow-second accounting only']}
    op=Path('data/research/r4_v0/p0_provenance_v1/ETH_MULTI_PARENT_RESPONSIBILITY_POOL_MICROWORLD_V1_RESULT_20260905.json');op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
