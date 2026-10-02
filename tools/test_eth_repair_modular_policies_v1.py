from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.completion import LegacyShareGapCompletionPolicy, EconomicResponsibilityCompletionPolicy
from tools.eth_repair_modular.ownership import LegacyExistingThesisOnlyPolicy, RecoverablePreSafeOwnershipPolicy
from tools.eth_repair_modular.handoff import LegacyAlwaysAllowActiveHandoffPolicy, RecoverabilityActiveHandoffPolicy
from tools.eth_repair_modular.generation import LegacyRecursiveGenerationPolicy, SingleResponsibilityGenerationPolicy
from tools.eth_repair_modular.contracts import CompletionContext, OwnershipContext, HandoffContext, GenerationContext

def main():
 tests=[]
 def ck(name,cond,detail):tests.append({'name':name,'pass':bool(cond),'detail':detail})
 c=CompletionContext(1,1,'DOWN',5,5,-0.35,False)
 a=LegacyShareGapCompletionPolicy().evaluate(c);b=EconomicResponsibilityCompletionPolicy().evaluate(c)
 ck('legacy_share_balance_marks_complete',a.management_complete,a.__dict__)
 ck('economic_profile_rejects_false_management_completion',b.share_repair_settled and not b.management_complete and b.open_economic_deficit,b.__dict__)
 c2=CompletionContext(2,2,'DOWN',5,5,0.2,False);b2=EconomicResponsibilityCompletionPolicy().evaluate(c2)
 ck('economic_profile_completes_only_nonnegative_floor',b2.management_complete,b2.__dict__)
 o1=LegacyExistingThesisOnlyPolicy().evaluate(OwnershipContext(1,280,False,.94,'DOWN',True));o2=RecoverablePreSafeOwnershipPolicy().evaluate(OwnershipContext(1,280,False,.94,'DOWN',True));o3=RecoverablePreSafeOwnershipPolicy().evaluate(OwnershipContext(1,280,False,.98,'DOWN',False))
 ck('legacy_no_presafe_owner',not o1.create_thesis,o1.__dict__);ck('economic_presafe_owner_when_recoverable',o2.create_thesis and o2.side=='DOWN',o2.__dict__);ck('economic_rejects_unrecoverable_owner',not o3.create_thesis,o3.__dict__)
 h1=LegacyAlwaysAllowActiveHandoffPolicy().evaluate(HandoffContext(1,'DOWN',True,False));h2=RecoverabilityActiveHandoffPolicy().evaluate(HandoffContext(1,'DOWN',True,False));h3=RecoverabilityActiveHandoffPolicy().evaluate(HandoffContext(1,'DOWN',True,True))
 ck('legacy_handoff_can_cross_unrecoverable',h1.allow_active_handoff,h1.__dict__);ck('economic_handoff_blocks_unrecoverable',not h2.allow_active_handoff,h2.__dict__);ck('economic_handoff_allows_recoverable',h3.allow_active_handoff,h3.__dict__)
 g1=SingleResponsibilityGenerationPolicy().evaluate(GenerationContext(True,2.0,1.0,1));g2=SingleResponsibilityGenerationPolicy().evaluate(GenerationContext(True,2.0,2.0,1));g3=LegacyRecursiveGenerationPolicy().evaluate(GenerationContext(True,2.0,0.0,5))
 ck('economic_generation_locked_until_debt_paid',not g1.unlocked and not g1.allow_new_responsibility,g1.__dict__);ck('economic_generation_unlocks_after_payment',g2.unlocked and g2.allow_new_responsibility,g2.__dict__);ck('legacy_recursive_profile_preserved_for_ab',g3.allow_new_responsibility,g3.__dict__)
 out={'version':'ETH_REPAIR_MODULAR_POLICIES_V1_MICROWORLD','tests':tests,'passed':sum(x['pass'] for x in tests),'total':len(tests),'allPass':all(x['pass'] for x in tests)};print(json.dumps(out,ensure_ascii=False,indent=2));raise SystemExit(0 if out['allPass'] else 1)
if __name__=='__main__':main()
