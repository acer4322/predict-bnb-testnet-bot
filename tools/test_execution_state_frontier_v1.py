from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.execution_state_frontier import ExecutionStateFrontier


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"PASS {name}")


def main():
    f=ExecutionStateFrontier()
    t=1788447033240
    check('empty_clock_allows', f.decision(t).allow_management)
    f.observe_material_fill(t,1.6949152542372878)
    d=f.decision(t)
    check('same_clock_unallocated_fill_blocks', not d.allow_management and d.reason=='UNRECONCILED_MATERIAL_FILL')
    check('unreconciled_qty_exact', abs(d.unreconciled_fill_qty-1.6949152542372878)<1e-9)
    f.commit_allocation(t,1.639344262295082)
    d=f.decision(t)
    check('partial_allocation_still_blocks', not d.allow_management)
    check('overflow_unallocated_exact', abs(d.unreconciled_fill_qty-0.05557099194220583)<1e-9)
    f.commit_allocation(t,0.05557099194220583)
    d=f.decision(t)
    check('full_allocation_allows', d.allow_management and d.reason=='FRONTIER_RECONCILED')
    check('next_clock_allows_without_fixed_delay', f.decision(t+1).allow_management)
    t2=t+100
    f.observe_material_fill(t2,2.0);f.observe_material_fill(t2,1.0)
    f.commit_allocation(t2,2.0)
    check('multi_fill_requires_all_siblings', not f.decision(t2).allow_management)
    f.commit_allocation(t2,1.0)
    check('multi_fill_all_committed_allows', f.decision(t2).allow_management)
    t3=t+200
    f.observe_material_fill(t3,0.0);f.commit_allocation(t3,0.0)
    check('zero_qty_no_false_block', f.decision(t3).allow_management)
    f.clear_before(t2)
    check('cleanup_preserves_current_frontier', f.decision(t2).allow_management)
    print('RESULT 8/8 PASS')

if __name__=='__main__':
    main()
