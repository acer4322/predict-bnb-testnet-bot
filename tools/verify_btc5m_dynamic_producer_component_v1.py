"""Invoke the generated producer on a pure canonical fixture; no native engine."""
import ast
from copy import deepcopy
from dataclasses import asdict
import hashlib
import math
import sys
from types import SimpleNamespace
import btc5m_dynamic_inventory_native_v1 as d


def check():
    c=d.compile_policy(d.PACKAGE,'NO_DIRECTION','INVENTORY',True)
    sys.path.insert(0,str(d.ROOT))
    from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.pair_core_economic_grant_ledger_v1 import Grant
    from tools.minimal_student_joint_policy_train_v1 import stable_features,softplus
    from tools.hft244_pair_route_legality_v1 import crossing_owners
    from tools.pair_core_asset_route_sizing_v2 import validate_size
    from btc5m_fixed15_research_condition_v1 import make_validator
    roles=sys.modules['roles_runtime'].roles
    nodes=list(ast.walk(ast.parse(c['source'])))
    pick=lambda kind,name: next(x for x in nodes if isinstance(x,kind) and x.name==name)
    tree=ast.Module(body=[pick(ast.ClassDef,'AtomicResponsibilityLedger'),pick(ast.FunctionDef,'envelope'),pick(ast.ClassDef,'Policy')],type_ignores=[])
    budget=c['budget_class']
    ns=dict(math=math,hashlib=hashlib,deepcopy=deepcopy,EPS_ATOMIC=1e-9,roles=roles,theta=c['manifest']['theta'],
        _MODE='NO_DIRECTION',_DIRECTION_RULE='INVENTORY',_DirectionBridge=c['bridge'].DirectionBridge,
        _ExposureIntent=c['ExposureIntent'] if 'ExposureIntent' in c else d.load('component_runner',d.PACKAGE/'money_runner.py').ExposureIntent,
        _AdditionGrowth=c['addition_growth'].AdditionGrowth,_GrowthHold=c['growth_hold'].GrowthHold,_TailNewStop=c['tail'].TailNewStop,
        _ActiveOpportunity=c['opportunity'].ActiveOpportunity,_OPPORTUNITY_MODE='ONE_ACTIVE',_OWN_SNAPSHOT=c['gate'].snapshot,
        _CommitmentRepairProbe=c['maintenance_scope'].make_probe(c['commitment_repair']),_CoordinationProbe=c['coordination'].CoordinationProbe,
        _MoneyGate=lambda selection,mode:budget(selection,mode,'AUTO_REPAIR',0.),_MONEY_SELECTION=c['manifest']['selection'],_MONEY_MODE='PARALLEL_QUANTITY',
        _DemandGate=c['demand'].SingleRepairDemand,_DEMAND_SELECTION=c['manifest']['demand_selection'],_DEMAND_MODE='AUTO_REPAIR',
        stable_features=stable_features,softplus=softplus,validate_size=make_validator(validate_size),_goal_crossing=crossing_owners,
        _original_envelope=lambda f,p,ops:ops,a=SimpleNamespace(rearm_mode='ALL'))
    exec(compile(tree,'V48_PURE_GENERATED_PRODUCER','exec'),ns)
    for rule in ('INVENTORY','REPAIR_GRACE'):
        ns['_DIRECTION_RULE']=rule;roles.configure('NO_DIRECTION',None)
        p=ns['Policy']();profile=OpenFundingProfile(max_live_owners=4096);ledger=FastOpenFundingLedger(profile)
        for pid,side in ((1,'UP'),(2,'DOWN')):ledger.issue(Grant(pid,'COMPONENT',side,0.,0.,0.,'EXPLICIT_RESEARCH_FIXTURE'))
        frame=dict(t=1,index=0,start=0,end=300,ledger=ledger,gateway_state_id='fixture',own_view=dict(inv=dict(UP=0.,DOWN=0.),cost=0.,n=0),
            world_profile=asdict(profile),book=dict(bids={.48:100.},asks={.50:100.}),quotes=dict(UP=dict(ask=.50),DOWN=dict(ask=.52)),cancellable={})
        ops=p.produce(frame)
        assert len(ops)==2 and all(o['role']=='PASSIVE_NEUTRAL_OPENING' for o in ops)
        assert len(p.bridge.frames)==1 and len(p.bridge.births)==2 and all(o['purpose']=='NEUTRAL' for o in p.bridge.births.values())
        assert all(len(b['demand'].rows)==0 for b in p.bridge.banks.values())
        for o in ops:ledger.reserve(o['key'],o['parent_id'],o['route'],o['qty'],o['price'],0.,now_ms=1,market_end_ms=300)
        up=next(o for o in ops if o['side']=='UP');ledger.confirm_terminal(up['key'],filled=15.,payment=15.*up['price'])
        frame.update(t=2,index=1);frame['own_view'].update(inv=dict(UP=15.,DOWN=0.),cost=15.*up['price'],n=2)
        p.bridge.observe(frame,p)
        assert roles.side=='UP' and p.demand is p.bridge.banks['UP']['demand']
        assert p.bridge.decisions[-1]['increments']==[dict(key=up['key'],side='UP',purpose='NEUTRAL',qty=15.)]
    out=dict(status='PASS',rules=2,generated_producer_first_frame=True,physical_receipt_bank_selection=True,local_native_jobs=0)
    d.dump('GENERATED_PRODUCER_COMPONENT',out);return out


if __name__=='__main__':print(check())
