"""Read-only attribution of the frozen V12 controller's economic/control gaps."""
import json
import math
import sys
from aggregate_btc5m_exposure_intent_ablation_v1 import get, RET
from audit_btc5m_target_core_loop_topology_v1 import ROOT, R, read, sha
sys.path.insert(0, str(ROOT))
from tools.hft244_pair_route_legality_v1 import crossing_owners
from tools.pair_core_asset_route_sizing_v2 import validate_size


def main():
    package = ROOT / '.lan_worker_v1/parallel_payoff_2026085_20260913_v1'
    m = read(package / 'manifest.json'); cut = m['selection']['t']; start = 1788758100000
    source = (package / 'frozen_runner.py').read_text(encoding='utf-8')
    assert sha(package / 'frozen_runner.py') == m['files']['frozen_runner.py']
    assert "exposure=math.tanh(w[3]*x['own_net'])" in source
    assert "gross=math.exp(w[5]+w[6]*progress)" in source
    folder = RET / 'parallel-payoff-2026085-quantity-20260913-v1'
    result, tr = get(folder); rows = [r for r in tr['intent'] if r['t'] >= cut]
    exposed = []; blocked = []
    for r in rows:
        inv = r['inv']; net = (inv['UP']-inv['DOWN'])/(1+sum(inv.values()))
        assert abs(r['applied_exposure']-abs(math.tanh(result['theta'][3]*net))) < 1e-12
        desired_fraction = (r['desired']['UP']-r['desired']['DOWN'])/sum(r['desired'].values())
        assert abs(desired_fraction-r['applied_exposure']) < 1e-12
        gap = inv['UP']-inv['DOWN']; need = max(0., gap-r['reserved_qty']['DOWN'])
        deficit = max(0., r['desired']['DOWN']-inv['DOWN']-r['reserved_qty']['DOWN'])
        payoff = inv['DOWN']-r['cost']
        if payoff < 0 and need >= 18:
            item = dict(t=r['t'], down_payoff=payoff, share_gap=gap, uncovered_quantity=need,
                        manager_down_deficit=deficit, desired=r['desired'], inv=inv,
                        reserved_qty=r['reserved_qty'], reserved_cash=r['reserved_cash'])
            exposed.append(item)
            if deficit < 18: blocked.append(item)
    blocked_times = {r['t'] for r in blocked}
    concurrent_new_up = [(p['t'], o) for p in tr['plans'] if p['t'] in blocked_times
                         for o in p['operations'] if o['kind']=='NEW' and o['side']=='UP']
    worst = min((r for r in tr['states'] if r['t']>=cut), key=lambda r:min(r['inv'].values())-r['cost'])
    at_worst = max((r for r in rows if r['t']<=worst['t']),key=lambda r:r['t'])
    last = rows[-1]
    qgap = worst['inv']['UP']-worst['inv']['DOWN']
    pending = at_worst['reserved_qty']['DOWN']
    proposals = []
    for row in tr['money_rows']:
        if row['t'] < cut or row['side'] != 'DOWN': continue
        state = row['state']; price = row['price']
        uncovered = max(0., state['inv']['UP']-state['inv']['DOWN']-state['pending_qty']['DOWN'])
        potential = state['payoff']['DOWN']+state['pending_qty']['DOWN']-state['pending_cash']['DOWN']-state['pending_cash']['UP']
        cash_need = max(0., -potential/(1-price))
        proposed = round(math.floor((min(30.,uncovered,cash_need)+1e-10)/.01)*.01,8)
        if state['payoff']['DOWN'] < 0 and 0 < row['requested'] < 18 and proposed >= 18:
            validate_size('BTC','PASSIVE',price,proposed,quantity_step=.01)
            conflicts = crossing_owners('DOWN',price,[dict(key=o['key'],side=o['side'],price=o['limit']) for o in state['owners']])
            proposals.append(dict(t=row['t'],price=price,original_requested=row['requested'],proposed=proposed,
                quantity_need=uncovered,money_need=cash_need,conflicting_owners=conflicts,state=state))
    eligible = [x for x in proposals if not x['conflicting_owners']]
    assert eligible and eligible[0]['t']==1788758120779
    out = dict(status='COMPLETE', scope='Frozen V12 quantity arm only; actor anatomy, not Target private controller evidence.',
        result_sha256=sha(folder/'result.json'), trace_sha256=sha(folder/'clock_trace.json.gz'),
        source_sha256=sha(package/'frozen_runner.py'), fitted_weights_changed=False,
        formula=dict(direction_magnitude='abs(tanh(-0.8 * current_own_net_fraction))',
                     gross='exp(w5+w6*fixed_train_event_progress)',
                     cost_enters_original_desired=False,
                     limitation='These are the current frozen actor formulas, not a claim about every model in the repository.'),
        demand_observations=dict(postcut_rows=len(rows), negative_down_with_at_least_minimum_uncovered=len(exposed),
            below_minimum_manager_deficit=len(blocked), new_up_orders_in_these_frames=len(concurrent_new_up),
            first_examples=blocked[:3], all_blocked_rows=blocked,
            limitation='Uncovered quantity and negative payoff do not prove every hypothetical extra order legal or economically desirable. This is a proposal-stage bottleneck, not a submit/fill counterfactual.'),
        worst=dict(state=worst, seconds_after_market_start=(worst['t']-start)/1000.,
            floor=min(worst['inv'].values())-worst['cost'], same_time_intent=at_worst,
            down_pending_qty=pending, gap=qgap, additional_quantity_capacity=max(0.,qgap-pending),
            note='Most repair quantity is already reserved. Pending is not a completed fill; this observation does not authorize duplicate repair or ignoring cancellation pending.'),
        terminal_intent=dict(t=last['t'], applied_exposure=last['applied_exposure'], desired=last['desired'],
            actual_net=last['inv']['UP']-last['inv']['DOWN'],
            note='The oracle fixes only the sign. The magnitude collapses with current net; independent desired exposure strength is absent here.'),
        next_static_checkpoint=dict(selection='First postcut existing DOWN candidate with negative payoff, original subminimum request, sufficient quantity/cash capacity for the existing30 ticket, and no current own cross.',
            candidate_visits=len(proposals),no_current_cross_visits=len(eligible),first=eligible[0],
            sizing_and_current_cross_check=True,native_order_accepted=None,filled=None,
            limitation='Proposal-stage diagnostic only. No economic authorization, gateway reservation, submission, queue fill or controller-maintenance counterfactual has been executed.'),
        correction='V12 maximum adverse exposure is at257.779 seconds, late in the market. Earlier wording calling the maximum early-stage is superseded.',
        priority=['Economic desired exposure and repair allocation before ticket filtering.',
                  'Received execution progress versus still-pending service and ongoing strong-side spend.',
                  'Joint whole-path assessment retaining both payoffs, activity, direction and adverse-time area.'])
    (R/'BTC5M_CORE_LOOP_CONTROL_GAP_V1_20260913.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='COMPLETE', demand={k:v for k,v in out['demand_observations'].items() if k not in ('all_blocked_rows','first_examples','limitation')},
        worst={k:out['worst'][k] for k in ('seconds_after_market_start','floor','down_pending_qty','gap','additional_quantity_capacity')},terminal_intent=out['terminal_intent'])))


if __name__=='__main__': main()
