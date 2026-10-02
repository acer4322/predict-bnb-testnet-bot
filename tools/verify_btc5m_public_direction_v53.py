"""Exercise the generated producer and canonical ledger without native/HFT."""
import ast
from copy import deepcopy
from dataclasses import asdict
import hashlib
import math
import sys
from types import SimpleNamespace
from prepare_btc5m_public_direction_v53 import PACKAGE, ROOT, R, STEM, dump, sha
import btc5m_dynamic_inventory_native_v1 as compiler


def make_context():
    c = compiler.compile_policy(PACKAGE, 'NO_DIRECTION', 'PUBLIC_MARKET', True)
    sys.path.insert(0, str(ROOT))
    from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.pair_core_economic_grant_ledger_v1 import Grant
    from tools.minimal_student_joint_policy_train_v1 import stable_features, softplus
    from tools.minimal_student_training_world_v2 import passive_ask, training_frame_id
    from tools.hft244_pair_route_legality_v1 import crossing_owners
    from tools.pair_core_asset_route_sizing_v2 import validate_size
    from btc5m_fixed15_research_condition_v1 import make_validator
    roles = sys.modules['roles_runtime'].roles
    nodes = list(ast.walk(ast.parse(c['source'])))
    pick = lambda kind,name: next(x for x in nodes if isinstance(x,kind) and x.name == name)
    tree = ast.Module(body=[pick(ast.ClassDef,'AtomicResponsibilityLedger'), pick(ast.FunctionDef,'envelope'), pick(ast.ClassDef,'Policy')], type_ignores=[])
    runner = compiler.load('public_component_runner', PACKAGE/'money_runner.py')
    ns = dict(math=math, hashlib=hashlib, deepcopy=deepcopy, EPS_ATOMIC=1e-9, roles=roles, theta=c['manifest']['theta'],
        _MODE='NO_DIRECTION', _DIRECTION_RULE='PUBLIC_MARKET', _DirectionBridge=c['bridge'].DirectionBridge,
        _ExposureIntent=runner.ExposureIntent, _AdditionGrowth=c['addition_growth'].AdditionGrowth,
        _GrowthHold=c['growth_hold'].GrowthHold, _TailNewStop=c['tail'].TailNewStop,
        _GeneralFiniteActive=c['general_finite_active'].GeneralFiniteActive,
        _ActiveOpportunity=c['opportunity'].ActiveOpportunity, _OPPORTUNITY_MODE='ONE_ACTIVE', _OWN_SNAPSHOT=c['gate'].snapshot,
        _CommitmentRepairProbe=c['maintenance_scope'].make_probe(c['commitment_repair']), _CoordinationProbe=c['coordination'].CoordinationProbe,
        _MoneyGate=lambda selection,mode:c['budget_class'](selection,mode,'AUTO_REPAIR',0.),
        _MONEY_SELECTION=c['manifest']['selection'], _MONEY_MODE='PARALLEL_PAYOFF_ZERO',
        _DemandGate=c['demand'].SingleRepairDemand, _DEMAND_SELECTION=c['manifest']['demand_selection'], _DEMAND_MODE='AUTO_REPAIR',
        stable_features=stable_features, softplus=softplus, validate_size=make_validator(validate_size), _goal_crossing=crossing_owners,
        passive_ask=passive_ask, training_frame_id=training_frame_id,
        _original_envelope=lambda f,p,ops:ops,
        a=SimpleNamespace(rearm_mode='RESPONSIBILITY_DUAL_CAPACITY',route_mode='PASSIVE_ONLY',active_admission='ANY_RESIDUAL',
            exact_active_source='',exact_active_time=-1,exact_frontier_time=-1,exact_frontier_side=None,exact_frontier_kind=None,exact_frontier_route='PASSIVE'))
    exec(compile(tree,'V53_GENERATED_PRODUCER_COMPONENT','exec'),ns)
    return c,roles,ns,FastOpenFundingLedger,OpenFundingProfile,Grant


def check():
    c,roles,ns,Ledger,Profile,Grant = make_context()
    from public_direction import observe_public
    tests=[]
    for first in ('UP','DOWN'):
        other = 'DOWN' if first == 'UP' else 'UP'
        roles.configure('NO_DIRECTION',None)
        p=ns['Policy'](); p.total_frames=1487
        profile=Profile(max_live_owners=4096);ledger=Ledger(profile)
        for pid,side in ((1,'UP'),(2,'DOWN')):
            ledger.issue(Grant(pid,'COMPONENT',side,0.,0.,0.,'EXPLICIT_RESEARCH_FIXTURE'))
        f=dict(t=1,index=0,start=0,end=300000,ledger=ledger,gateway_state_id='fixture',
            own_view=dict(inv=dict(UP=0.,DOWN=0.),cost=0.,n=1),world_profile=asdict(profile),
            book=dict(bids={.49:100.},asks={.51:100.}),
            quotes=dict(UP=dict(ask=.51),DOWN=dict(ask=.51)),cancellable={})
        def quote(mid):
            f['book']=dict(bids={round(mid-.01,2):100.},asks={round(mid+.01,2):100.})
            f['quotes']=dict(UP=dict(ask=round(mid+.01,2)),DOWN=dict(ask=round(1-mid+.01,2)))
        def commit(ops):
            for o in ops:
                if o['kind']=='NEW':
                    ledger.reserve(o['key'],o['parent_id'],o['route'],o['qty'],o['price'],0.,now_ms=f['t'],market_end_ms=f['end'])
                    f['own_view']['n'] += 1
                    f['cancellable'][o['key']]=True
                elif o['kind']=='CANCEL':
                    ledger.request_cancel(o['key'])
        opening=p.produce(f)
        assert {o['side'] for o in opening}=={'UP','DOWN'} and roles.side is None
        commit(opening)
        owner=next(o for o in opening if o['side']==first)
        ledger.confirm_terminal(owner['key'],filled=15.,payment=15.*owner['price'])
        f['own_view']['inv'][first]=15.;f['own_view']['cost']=15.*owner['price']
        f.update(t=2000,index=10);quote(.75 if first=='UP' else .25)
        same=p.produce(f);commit(same)
        assert roles.side==first and p.intent.rows[-1]['desired'][first]>p.intent.rows[-1]['desired'][other]
        # Same inventory and pending owner(s), public reversal alone must switch.
        before=deepcopy(p.bridge.births)
        pending={k:(o.state,float(o.reserved_qty)) for k,o in ledger.carriers.items() if o.state!='TERMINAL'}
        first_bank=p.bridge.banks[first]
        first_bank['general_finite_active'].served_work_ids.add(7)
        f.update(t=3000,index=11);quote(.25 if first=='UP' else .75)
        reverse=p.produce(f)
        assert roles.side==other and f['own_view']['inv'][first]>f['own_view']['inv'][other]
        assert p.intent.rows[-1]['desired'][other]>p.intent.rows[-1]['desired'][first]
        assert any(o['kind']=='NEW' and o['side']==other for o in reverse), reverse
        assert all(p.bridge.births[k]==b for k,b in before.items())
        assert {k:(o.state,float(o.reserved_qty)) for k,o in ledger.carriers.items() if k in pending}==pending
        assert p.general_finite_active is p.bridge.banks[other]['general_finite_active']
        assert 7 not in p.general_finite_active.served_work_ids
        commit(reverse)
        # Reverse back without receiving the pending orders; bank work is retained.
        f.update(t=4000,index=12);quote(.80 if first=='UP' else .20)
        back=p.produce(f)
        assert roles.side==first and p.general_finite_active is first_bank['general_finite_active']
        assert 7 in p.general_finite_active.served_work_ids
        assert p.intent.held_amplitude is None
        assert p.intent.amplitude_rows[-1]['applied_exposure']>p.intent.amplitude_rows[-2]['applied_exposure']
        base=observe_public(f,p.theta)
        changed=deepcopy(f);changed['own_view']=dict(inv=dict(UP=99999.,DOWN=0.),cost=999.,n=999)
        assert observe_public(changed,p.theta)==base
        changed['target']={'winner':other,'future_direction':other}
        assert observe_public(changed,p.theta)==base
        tests.append(dict(first=first,flipped_to=other,pending_owners_preserved=len(pending),
            reversal_new_sides=[o['side'] for o in reverse if o['kind']=='NEW'],switches=2))
    out=dict(status='PASS',tests=tests,generated_producer=True,canonical_pending_preserved=True,
        physical_active_memory=True,dynamic_amplitude=True,inventory_and_target_invariant_public_authority=True,
        manifest_sha256=sha(PACKAGE/'manifest.json'),transformed_source_sha256=hashlib.sha256(c['source'].encode()).hexdigest(),local_native_jobs=0)
    dump(R/(STEM+'_COMPONENT.json'),out)
    print(out)
    return out


if __name__=='__main__':check()
