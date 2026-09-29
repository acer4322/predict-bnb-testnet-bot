"""V47 local falsification and real frozen-callback tests; no native execution."""
import ast
from copy import deepcopy
import json
import math
import sys
from types import SimpleNamespace

from prepare_btc5m_transfer_structural_v1 import ROOT,R,read,sha,load
from verify_btc5m_transfer_components_v1 import same,swap
from hft244_pair_route_legality_v1 import crossing_owners
from btc5m_dual_opening_inventory_roles_v1 import InventoryRoles,PhysicalWorkBank,PhysicalGrowthBank,inventory_direction,passive_prices,opening_plan

STEM='BTC5M_DUAL_OPENING_INVENTORY_ROLES_V1_20260914'
PARENT=ROOT/'.lan_worker_v1/frozen_new_market_v44_20260913_v1'


def dump(tag,x):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def state(up,down,cost=90.,pu=0.,pd=0.):
    return dict(inv=dict(UP=up,DOWN=down),cost=cost,payoff=dict(UP=up-cost,DOWN=down-cost),pending_qty=dict(UP=pu,DOWN=pd),pending_cash=dict(UP=pu*.4,DOWN=pd*.3),owners=[])


def main():
    assert not (R/(STEM+'_RESULT.json')).exists(),'Completed component evidence must not be silently replaced'
    m=read(PARENT/'manifest.json');assert all(sha(PARENT/k)==h for k,h in m['files'].items())
    component=ROOT/'tools/btc5m_dual_opening_inventory_roles_v1.py'
    protocol=dict(status='FROZEN_BEFORE_LOCAL_CHECKS',user_direction_update='Dynamic greater confirmed inventory side; ties retain previous, initial ties have no direction',
        user_opening='Two legal Passive15 proposals at opening; no guarantee of execution',
        opening_scope='Neutral phase has at most one pair of nonterminal owners. Wait for canonical terminal before another tied-state pair. Same-plan CANCEL is not release; never use pending quantities to choose direction.',
        parent_manifest_sha256=sha(PARENT/'manifest.json'),component_sha256=sha(component),
        tests=['Mirror direction including pending-only changes and repeated reversals','Whole valid binary-book tick grid with inherited quote offset and actual own-cross helper','Partial fill, CANCEL_PENDING, UNKNOWN, stale and end-of-market proposals','Actual frozen FiniteGoal counterexample under a role switch and physically bound work banks','Actual frozen AdditionGrowth anchors retained separately by physical strong side','Four saved native paths read-only: three unique V45 paths plus V44 1977248 reversal'],
        dedup='V6 selected first OWN net; V31 held magnitude; V33 role mapping was fixed after first selection. V45 exposed nonneutral bootstrap. This tests dual neutral opening plus repeated inventory-side changes with physical work/anchor identity; no located earlier equivalent in current protocols and handoffs.',
        supersedes='V46 amplitude-only pair was staged but submitted zero times; retain package without dispatch',
        parameter_boundary='Parent theta, Passive15, minimum1 and Active variable semantics unchanged. V46 fixed-reference amplitude is not adopted by this component.',
        interpretation='Pure proposals, synthetic state transitions and counterfactual selectors over frozen OUR states are component evidence only. No invented fills or PnL.',
        existing_solution='Reuse frozen FiniteGoal, AdditionGrowth and native own-cross preflight. No alternative simulator.',
        native_gate='No dynamic whole-policy native until all stateful Active/renewal/maintenance modules and the atomic ledger have explicit physical-side semantics, plus canonical gateway integration and audits.',
        maximum_native_jobs=0,model_fits=0,parameter_search=0,plots=0)
    if not (R/(STEM+'_PROTOCOL.json')).exists():dump('PROTOCOL',protocol)
    else:assert read(R/(STEM+'_PROTOCOL.json'))==protocol
    direction_checks=0
    for u in (0.,.53,1.84,15.,45.,100.):
        for dn in (0.,.53,1.84,15.,45.,100.):
            for previous in (None,'UP','DOWN'):
                actual=inventory_direction(dict(UP=u,DOWN=dn),previous)
                expected='UP' if u>dn+1e-8 else 'DOWN' if dn>u+1e-8 else previous
                assert actual==expected
                same(swap(actual),inventory_direction(dict(UP=dn,DOWN=u),swap(previous)))
                direction_checks+=1
    c=InventoryRoles();sequence=[(0,0),(1.84,0),(1.84,15),(15,15),(30,15),(30,31),(31,31)]
    selected=[]
    for i,(u,dn) in enumerate(sequence):
        f=dict(t=i,index=i,own_view=dict(inv=dict(UP=u,DOWN=dn)),pending_qty=dict(UP=10000.,DOWN=0.),owners=[dict(key='DOWN_old',side='DOWN',state='CANCEL_PENDING')])
        old=deepcopy(f);selected.append(c.observe(f));same(f,old)
    assert selected==[None,'UP','DOWN','DOWN','UP','DOWN','DOWN']
    base=dict(t=1,start=0,end=300000,index=0,own_view=dict(n=1,inv=dict(UP=0.,DOWN=0.)),cancellable={})
    grid=0;legal=0;blocked=0
    for bid_tick in range(1,98):
        for ask_tick in range(bid_tick+1,100):
            book=dict(best_bid=bid_tick/100,best_ask=ask_tick/100)
            prices=passive_prices(book,m['theta'][9]);asks=dict(UP=book['best_ask'],DOWN=round(1-book['best_bid'],10))
            frame=deepcopy(base);before=deepcopy(frame)
            out=opening_plan(frame,None,prices,asks,[],crossing_owners,2.)
            same(frame,before);grid+=1
            if out['operations']:
                assert len(out['operations'])==2 and {o['side'] for o in out['operations']}=={'UP','DOWN'}
                assert [o['key'] for o in out['operations']]==['UP_1','DOWN_2']
                assert all(o['qty']==15. and o['qty']*o['price']>=1-1e-8 and o['price']<asks[o['side']] for o in out['operations'])
                assert not crossing_owners('DOWN',out['operations'][1]['price'],out['operations'][:1]);legal+=1
            else:assert out['reason']=='BOTH_LEGS_MUST_HAVE_LEGAL_PASSIVE15_PRICE';blocked+=1
            mirrored=dict(best_bid=round(1-book['best_ask'],10),best_ask=round(1-book['best_bid'],10))
            mp=passive_prices(mirrored,m['theta'][9]);same(mp,swap(prices))
    quote=dict(UP=.55,DOWN=.39);asks=dict(UP=.60,DOWN=.44)
    neutral=opening_plan(base,None,quote,asks,[],crossing_owners,2.)
    # Full15 NEW requests; .53 is an observed partial fill, not a new small ticket.
    owner=dict(key='UP_1',side='UP',state='SUBMITTED',qty=14.47,limit=.55,filled=.53)
    owner_checks=0
    for lifecycle in ('SUBMITTED','CANCEL_PENDING','UNKNOWN','TERMINAL'):
        o=dict(owner,state=lifecycle);f=deepcopy(base);f['cancellable']={'UP_1':True}
        before=deepcopy(o);out=opening_plan(f,None,dict(UP=.45,DOWN=.49),dict(UP=.60,DOWN=.54),[o],crossing_owners,2.)
        same(o,before)
        if lifecycle=='SUBMITTED':assert len(out['operations'])==1 and out['operations'][0]['kind']=='CANCEL'
        elif lifecycle in ('CANCEL_PENDING','UNKNOWN'):assert out['operations']==[]
        else:assert len(out['operations'])==2 and all(p['kind']=='NEW' for p in out['operations'])
        assert not any(p['kind']=='NEW' for p in out['operations']) if lifecycle!='TERMINAL' else True
        owner_checks+=1
    for t in (-1,300000,300001):
        f=dict(base,t=t);assert not opening_plan(f,None,quote,asks,[],crossing_owners,2.)['operations'];owner_checks+=1
    # Initial simultaneous equal fills do not invent UP alpha. They remain neutral.
    tied=dict(base,own_view=dict(n=3,inv=dict(UP=15.,DOWN=15.)))
    assert inventory_direction(tied['own_view']['inv']) is None
    assert len(opening_plan(tied,None,quote,asks,[],crossing_owners,2.)['operations'])==2
    assert opening_plan(tied,'DOWN',quote,asks,[],crossing_owners,2.)['handled'] is False
    sys.path.insert(0,str(PARENT));from roles_runtime import roles
    goal=load('frozen_physical_goal_probe',PARENT/'persistent_gate.py').FiniteGoal
    growth=load('frozen_physical_growth_probe',PARENT/'addition_growth.py').AdditionGrowth
    roles.configure('KNOWN_FINAL_DIRECTION','UP');initial=state(100.,40.,90.,pd=10.)
    unbound=goal(initial,15.);bank=PhysicalWorkBank(goal,roles);bound=bank.birth('UP',initial,15.)
    roles.side='DOWN';history=deepcopy(roles.rows);reversed_state=state(100.,110.,120.,pu=15.,pd=10.);unchanged=deepcopy(reversed_state)
    wrong=unbound.update(reversed_state,1,300);right=bound.update(reversed_state,1,300)
    assert wrong['target']==right['target']==65.
    assert wrong['status']==right['status']=='CONFIRMED_TARGET_REACHED'
    assert wrong['acquired_since_birth']==60. and right['acquired_since_birth']==70.
    assert wrong['pending_down']==15. and right['pending_down']==10.
    assert roles.side=='DOWN';same(roles.rows,history);same(reversed_state,unchanged)
    # Both pending amounts stay in their original physical accounts after switching.
    assert right['pending_down']==10. and right['pending_cash_up']==6.
    newer=bank.birth('DOWN',state(100.,150.,140.,pu=15.),15.)
    assert newer.goal.target==130. and newer.weak=='UP'
    progress=bank.observe(state(110.,160.,150.,pu=20.,pd=5.),2,300)
    assert progress['DOWN']['remaining_confirmed']==20. and progress['UP']['status']=='CONFIRMED_TARGET_REACHED'
    desired=dict(UP=100.,DOWN=200.)
    assert bank.desired(desired,'DOWN')==dict(UP=130.,DOWN=200.)
    before=deepcopy(desired);same(bound.desired(desired,'DOWN'),desired);same(desired,before)
    ended=bank.observe(state(110.,160.,150.,pu=20.,pd=5.),300,300)
    assert ended['DOWN']['status']=='WITHDRAWN_MARKET_END' and ended['DOWN']['pending_down']==20.
    anchors=PhysicalGrowthBank(growth)
    anchors.update('UP',0,dict(UP=100.,DOWN=40.),80.,dict(UP=200.,DOWN=100.))
    first=deepcopy(anchors.by_side['UP'].anchor)
    anchors.update('DOWN',1,dict(UP=100.,DOWN=110.),105.,dict(UP=100.,DOWN=240.))
    second=deepcopy(anchors.by_side['DOWN'].anchor)
    assert first['original_strong_desired']==200. and second['original_strong_desired']==240.
    anchors.update('UP',2,dict(UP=130.,DOWN=110.),120.,dict(UP=400.,DOWN=150.))
    same(anchors.by_side['UP'].anchor,first);same(anchors.by_side['DOWN'].anchor,second)
    # Frozen OUR states only: dynamic selector is descriptive, never a new fill path.
    v45=read(R/'BTC5M_FROZEN_NEW_MARKET_PANEL_V1_20260913_RESULT.json')
    paths=[x for x in v45['results'] if x['arm']=='v44_known' or (x['market']==2127218 and x['arm']=='v44_no_direction')]
    paths.append(dict(market=1977248,arm='v44_original_known',job_id='fixed15-core-loop-1977248-tail-acquisition-ablation-20260913-v1'))
    replay=[];opening_examples=[]
    for path in paths:
        folder=R/'lan_worker_returns'/path['job_id'];tr=read(folder/'clock_trace.json.gz');n=read(folder/'result.json')
        invroles=InventoryRoles()
        for r in tr['direction_rows']:
            f=dict(t=r['t'],index=r['index'],own_view=dict(inv=r['inv']));invroles.observe(f)
        inp=read(PARENT/f"inputs/public_{path['market']}.json.gz");start=inp['market']['window_start_ms']
        diffs=[dict(t=a['t'],seconds=(a['t']-start)/1000,inv=a['inv'],dynamic=a['side'],frozen=b['side']) for a,b in zip(invroles.rows,tr['direction_rows']) if a['side']!=b['side'] and abs(a['inv']['UP']-a['inv']['DOWN'])>1e-8]
        replay.append(dict(market=path['market'],arm=path['arm'],job_id=path['job_id'],observed_frames=len(invroles.rows),births_and_switches=[dict(x,seconds=(x['t']-start)/1000) for x in invroles.changes],different_nonzero_inventory_frames=len(diffs),first_difference=diffs[0] if diffs else None,trace_sha256=sha(folder/'clock_trace.json.gz')))
        firstrow=tr['intent'][0];index=firstrow['index'];b=inp['books'][index]
        prices=passive_prices(b,m['theta'][9]);a=dict(UP=b['best_ask'],DOWN=round(1-b['best_bid'],10))
        f=dict(t=firstrow['t'],index=index,start=start,end=inp['market']['window_end_ms'],own_view=dict(n=1,inv=dict(UP=0.,DOWN=0.)),cancellable={})
        proposal=opening_plan(f,None,prices,a,[],crossing_owners,1+math.log1p(math.exp(m['theta'][11])))
        assert b['source_ms']<=b['received_ms']==f['t']
        opening_examples.append(dict(market=path['market'],arm=path['arm'],seconds=(f['t']-start)/1000,source_ms=b['source_ms'],received_ms=b['received_ms'],proposal=proposal,old_first_plan=tr['plans'][index]['operations']))
    audit=ast.parse(component.read_text(encoding='utf-8'));assert not any(isinstance(x,(ast.Import,ast.ImportFrom)) and 'hftbacktest' in ast.unparse(x) for x in ast.walk(audit))
    result=dict(status='COMPONENT_PASS',native_status='NOT_IMPLEMENTED_OR_DISPATCHED',direction_checks=direction_checks,quote_grid_checks=grid,legal_pairs=legal,illegal_price_pairs_deferred=blocked,lifecycle_boundary_checks=owner_checks,
        switching_sequence=[dict(inv=dict(UP=u,DOWN=dn),selected=s) for (u,dn),s in zip(sequence,selected)],
        work_side_counterexample=dict(initial=initial,after=reversed_state,unbound=wrong,physically_bound=right),
        work_bank_terminal=ended,growth_anchor_bank=dict(UP=first,DOWN=second),saved_path_selector_replays=replay,opening_proposals=opening_examples,
        provenance=dict(parent_manifest_sha256=sha(PARENT/'manifest.json'),component_sha256=sha(component),protocol_sha256=sha(R/(STEM+'_PROTOCOL.json'))),
        integration_remaining=['Canonical draft reserve and gateway path for the neutral pair','Physical side ownership for opportunity/commitment/renewal/growth-hold histories and maintenance','Atomic responsibility batches must retain physical UP/DOWN across role changes','Full dynamic trace/accounting audit before second-PC native'],
        not_claimed=['Any new economic result','Dual fills guaranteed','Direction alpha learned','Existing native models changed','Whole core-loop dynamic integration complete'],
        model_fits=0,parameter_search=0,native_jobs=0,plots=0)
    dump('RESULT',result);dump('PROGRESS',dict(status='COMPONENT_COMPLETE_NATIVE_INTEGRATION_PENDING',submissions=0,pending_worker_jobs=[]))
    print(json.dumps({k:result[k] for k in ('status','direction_checks','quote_grid_checks','legal_pairs','illegal_price_pairs_deferred','lifecycle_boundary_checks','saved_path_selector_replays')},ensure_ascii=False))


if __name__=='__main__':main()
