from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.responsibility_transition import ResponsibilityTransitionContext,RepairFirstResponsibilityTransitionV1

p=RepairFirstResponsibilityTransitionV1();cases=[]
def ck(name,cond):
    cases.append((name,bool(cond)));print(('PASS' if cond else 'FAIL'),name)

d=p.evaluate(ResponsibilityTransitionContext('UP',0,0));ck('no_debt_allows_expand',d.allow_expand_ownership and d.bind_role=='EXPAND')
d=p.evaluate(ResponsibilityTransitionContext('UP',.0556,0));ck('same_side_up_debt_blocks_expand',not d.allow_expand_ownership and d.bind_role=='REPAIR' and abs(d.live_repair_debt-.0556)<1e-9)
d=p.evaluate(ResponsibilityTransitionContext('DOWN',.0556,0));ck('opposite_side_debt_does_not_block_down_expand',d.allow_expand_ownership)
d=p.evaluate(ResponsibilityTransitionContext('DOWN',0,.8));ck('same_side_down_debt_blocks_expand',not d.allow_expand_ownership and d.side=='DOWN')
d=p.evaluate(ResponsibilityTransitionContext(None,.2,.3));ck('no_thesis_no_expand',not d.allow_expand_ownership and d.bind_role=='NONE')
d=p.evaluate(ResponsibilityTransitionContext('UP',1e-12,0));ck('dust_debt_does_not_block',d.allow_expand_ownership)
d=p.evaluate(ResponsibilityTransitionContext('UP',0,.9));ck('other_side_repair_can_coexist_with_up_expand',d.allow_expand_ownership)
d=p.evaluate(ResponsibilityTransitionContext('UP',.9,.9));ck('same_side_debt_has_precedence_under_parallel_debts',not d.allow_expand_ownership and d.bind_role=='REPAIR')
print(f'RESULT {sum(v for _,v in cases)}/{len(cases)} PASS')
if not all(v for _,v in cases):raise SystemExit(1)
