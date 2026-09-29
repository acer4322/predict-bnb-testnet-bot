from __future__ import annotations

import argparse, copy, json, math, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import HftBookAdapter, load_public_snapshots, load_reference_paper, new_controller
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod
from tools import hftbacktest_execution_shift_audit_v0 as ex

OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
VERSION = 'HFT_INTEGRATED_CONTROL_SEMIMDP_V1_FIXED'
EPS = 1e-9
TERMINAL = {'FILLED','REJECTED','EXPIRED','CANCELED'}


def run_market(mid: int, route_mode: str = 'REVISION_ACK') -> dict[str, Any]:
    snaps = load_public_snapshots(mid)
    if not snaps:
        raise RuntimeError(f'no public snapshots for {mid}')
    paper = load_reference_paper(mid)
    win = winners([mid]).get(mid)
    a = HftBookAdapter(mid, 1092, 273, 'risk', 'mid')
    c = new_controller(a)

    # Venue children live outside Frozen-R2's ephemeral PaperOrder slots.
    children: dict[str, dict[str, Any] | None] = {'UP': None, 'DOWN': None}
    pending_objective: dict[str, dict[str, Any] | None] = {'UP': None, 'DOWN': None}
    objective_events: list[dict[str, Any]] = []
    maker_fills: list[dict[str, Any]] = []
    taker_fills: list[dict[str, Any]] = []
    taker_attempts: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    invariant_violations: list[dict[str, Any]] = []
    metrics = {
        'r2ObjectiveEmits': 0, 'newVenueChildren': 0, 'coalescedObjectiveEmits': 0,
        'venueChildTerminals': 0, 'makerFillEvents': 0, 'makerFilledShares': 0.0,
        'takerAttempts': 0, 'takerFillEvents': 0, 'takerFilledShares': 0.0,
        'maxVenueLiveChildren': 0, 'maxInternalR2OrdersAfterStep': 0, 'routeRevisionCancels': 0, 'routeRevisionSubmits': 0,
    }

    orig_add = c._add_order
    orig_cancel = c._cancel_order
    orig_taker = c._record_taker

    def child_live(ch: dict[str, Any] | None) -> bool:
        if ch is None:
            return False
        s = ex.order_snapshot(a.bt, int(ch['num']))
        return str(s.get('status') or 'NONE') not in TERMINAL

    def harvest(now: int) -> None:
        for side in ('UP','DOWN'):
            ch = children.get(side)
            if ch is None:
                continue
            s = ex.order_snapshot(a.bt, int(ch['num']))
            cum = float(s.get('cumExecQty') or 0.0)
            old = float(ch.get('prevCum') or 0.0)
            if cum > old + EPS:
                q = cum - old
                native = s.get('execPrice')
                px = float(ch['price'])
                if native is not None and math.isfinite(float(native)):
                    px = float(native) if side == 'UP' else 1.0 - float(native)
                fm = int((s.get('exchangeTs') or now * 1_000_000) // 1_000_000)
                pre_net = c.inventory.maker_up - c.inventory.maker_down
                pre_g = c.inventory.maker_up + c.inventory.maker_down
                pre_pc = 2 * min(c.inventory.maker_up, c.inventory.maker_down) / pre_g if pre_g > EPS else 1.0
                c.inventory.apply({'event_ms': fm, 'role': 'MAKER', 'side': side, 'price': px, 'shares': q})
                c.current_metrics['makerFills'] += 1
                c.run_metrics['makerFills'] += 1
                metrics['makerFillEvents'] += 1
                metrics['makerFilledShares'] += q
                maker_fills.append({'atMs': fm, 'observedAtMs': now, 'side': side, 'price': px, 'shares': q, 'orderNum': int(ch['num'])})
                # Preserve R2 overlap semantics, but trigger only from confirmed actual fills.
                post_net = c.inventory.maker_up - c.inventory.maker_down
                if bool(ch.get('occupiedBeforeActual')):
                    predom = 'UP' if pre_net > EPS else 'DOWN' if pre_net < -EPS else None
                    if predom == side and abs(pre_net) >= mod.SHARES - EPS and abs(post_net) > abs(pre_net) + 1:
                        c.episode = {'kind':'OVERLAP','side':side,'risk_start_ms':fm,'risk_pre_abs':abs(pre_net),'start_ms':fm,'pre_abs':abs(pre_net),'start_abs':abs(post_net),'expansion':abs(post_net)-abs(pre_net),'pre_pc':pre_pc,'unresolved':False}
                        c.readiness = False
                        c.current_metrics['excursions'] += 1
                        c.run_metrics['excursions'] += 1
                ch['prevCum'] = cum
            st = str(s.get('status') or 'NONE')
            if st in TERMINAL:
                metrics['venueChildTerminals'] += 1
                objective_events.append({'atMs': now, 'action':'CHILD_TERMINAL_RETURN_TO_R2','side':side,'orderNum':int(ch['num']),'status':st,'cumExecQty':cum})
                children[side] = None
                po = pending_objective.get(side)
                if po is not None:
                    num2 = int(a.next_num); a.next_num += 1
                    rc2 = ex.submit_native(a.bt, num2, side, float(po['price']), float(po['shares']))
                    objective_events.append({'atMs':now,'action':'ROUTE_REVISION_SUBMIT_AFTER_ACK','side':side,'price':float(po['price']),'shares':float(po['shares']),'reason':po['reason'],'orderNum':num2,'submitRc':int(rc2)})
                    if rc2 == 0:
                        children[side] = {'num':num2,'side':side,'price':float(po['price']),'qty':float(po['shares']),'prevCum':0.0,'submittedAtMs':now,'reason':po['reason'],'decisionId':po['decisionId'],'occupiedBeforeActual':bool((c.inventory.maker_up if side=='UP' else c.inventory.maker_down)>EPS),'cancelRequested':False}
                        metrics['newVenueChildren'] += 1; metrics['routeRevisionSubmits'] += 1
                    pending_objective[side] = None

    def add_wrap(side: str, now: int, snapshot_ns: int, decision_id: str, reason: str, p: float, snapshot: dict[str, Any], allow_stack: bool=True, bypass_guard: bool=False) -> bool:
        # Let Frozen R2 decide whether an objective exists and compute its quote/reason.
        before = set(c.orders)
        made = orig_add(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack, bypass_guard)
        if not made:
            return False
        new = [k for k in c.orders if k not in before]
        if not new:
            return False
        key = new[0]
        o = c.orders[key]
        metrics['r2ObjectiveEmits'] += 1
        live = child_live(children.get(side))
        event = {'atMs':now,'action':'R2_OBJECTIVE_EMIT','side':side,'reason':reason,'price':float(o.price),'shares':float(o.shares),'decisionId':decision_id,'coalesced':live}
        if live:
            ch = children[side]
            assert ch is not None
            same_objective = (str(route_mode).upper() == 'STICKY_CHILD') or (abs(float(ch['price'])-float(o.price)) < mod.GRID/2 and str(ch.get('reason')) == str(reason))
            if same_objective:
                metrics['coalescedObjectiveEmits'] += 1
                event['ownerOrderNum'] = int(ch['num'])
                event['objectiveRelation'] = 'SAME_OBJECTIVE_COALESCE'
            else:
                # A material Frozen-R2 objective revision may supersede the stale venue route.
                # Preserve the newest objective, request cancel exactly once, and wait for terminal ACK.
                pending_objective[side] = {'price':float(o.price),'shares':float(o.shares),'reason':reason,'decisionId':decision_id}
                event['ownerOrderNum'] = int(ch['num']); event['objectiveRelation'] = 'ROUTE_REVISION_PENDING_ACK'
                if not bool(ch.get('cancelRequested')):
                    cur = a.bt.orders(0).get(int(ch['num']))
                    if cur is not None and bool(cur.cancellable):
                        try:
                            a.bt.cancel(0,int(ch['num']),False); ch['cancelRequested']=True; ch['cancelRequestedAtMs']=now; metrics['routeRevisionCancels'] += 1
                            objective_events.append({'atMs':now,'action':'ROUTE_REVISION_CANCEL_REQUEST','side':side,'orderNum':int(ch['num']),'oldPrice':float(ch['price']),'newPrice':float(o.price),'oldReason':ch.get('reason'),'newReason':reason})
                        except Exception:
                            pass
                metrics['coalescedObjectiveEmits'] += 1
        else:
            num = int(a.next_num); a.next_num += 1
            rc = ex.submit_native(a.bt, num, side, float(o.price), float(o.shares))
            if rc == 0:
                children[side] = {'num':num,'side':side,'price':float(o.price),'qty':float(o.shares),'prevCum':0.0,'submittedAtMs':now,'reason':reason,'decisionId':decision_id,'occupiedBeforeActual':bool((c.inventory.maker_up if side=='UP' else c.inventory.maker_down)>EPS),'cancelRequested':False}
                metrics['newVenueChildren'] += 1
                event['ownerOrderNum'] = num
            else:
                event['submitRc'] = int(rc)
        objective_events.append(event)
        # Critical semantic split: strategy objective is captured, but venue child does not occupy
        # Frozen R2's ephemeral max-2 PaperOrder slots. No dream fill is credited.
        c.orders.pop(key, None)
        c.last_closed[key] = int(now)
        return True

    def cancel_wrap(key: tuple[str,int], at_ms: int, reason: str) -> None:
        # Internal paper objective may already have been captured/removed. Never cancel a venue child
        # merely because paper bookkeeping tries to retire that ephemeral object.
        if key in c.orders:
            orig_cancel(key, at_ms, reason)

    def fill_no_dream(snapshot: dict[str, Any], now: int) -> list[dict[str, Any]]:
        # Real venue fills are harvested separately; paper fill proxy is disabled.
        harvest(now)
        return []

    def taker_wrap(side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:
        metrics['takerAttempts'] += 1
        max_price = min(.99, float(price)+.02)
        num = int(a.next_num); a.next_num += 1
        native_side, native_px = ex.native_order(side, max_price)
        if native_side == 'BUY':
            rc = int(a.bt.submit_buy_order(0,num,native_px,float(mod.SHARES),ex.hbt.GTC,ex.LIMIT,False))
        else:
            rc = int(a.bt.submit_sell_order(0,num,native_px,float(mod.SHARES),ex.hbt.GTC,ex.LIMIT,False))
        att = {'atMs':now,'side':side,'orderNum':num,'submitRc':rc,'decisionId':decision_id,'maxPrice':max_price}
        taker_attempts.append(att)
        if rc != 0:
            return False
        target = now + 2200
        a.advance(mid,target); harvest(target)
        s = ex.order_snapshot(a.bt,num); cum=float(s.get('cumExecQty') or 0.0)
        if cum <= EPS:
            att['result']='NO_FILL'; return False
        native=s.get('execPrice'); px=float(price)
        if native is not None and math.isfinite(float(native)):
            px=float(native) if side=='UP' else 1.0-float(native)
        fm=int((s.get('exchangeTs') or target*1_000_000)//1_000_000)
        c.inventory.apply({'event_ms':fm,'role':'TAKER','side':side,'price':px,'shares':cum})
        fee=mod.taker_fee(cum,px,mod.FEE_BPS); c.taker_fee_spent += fee; c.last_taker_ms=fm
        c.current_metrics['takerFills'] += 1; c.run_metrics['takerFills'] += 1
        metrics['takerFillEvents'] += 1; metrics['takerFilledShares'] += cum
        taker_fills.append({'atMs':fm,'side':side,'price':px,'shares':cum,'feeUsdt':fee,'orderNum':num})
        att['result']='FILLED';att['shares']=cum;att['price']=px
        return True

    c._add_order = add_wrap
    c._cancel_order = cancel_wrap
    c._fill_orders = fill_no_dream
    c._record_taker = taker_wrap

    try:
        for s in snaps:
            t=int(s['sampledAtMs'])
            if int(a.bt.current_timestamp//1_000_000) > t:
                continue
            a.advance(mid,t); harvest(t)
            c._step(dict(s))
            metrics['maxVenueLiveChildren'] = max(metrics['maxVenueLiveChildren'], sum(child_live(children[x]) for x in ('UP','DOWN')))
            metrics['maxInternalR2OrdersAfterStep'] = max(metrics['maxInternalR2OrdersAfterStep'], len(c.orders))
            if len(c.orders) > 0:
                invariant_violations.append({'atMs':t,'kind':'INTERNAL_SLOT_NOT_RELEASED','count':len(c.orders)})
            if c.last_decision is not None and int(c.last_decision.get('decisionMs') or -1)==t:
                decisions.append(copy.deepcopy(c.last_decision))
        terminal=int(a.meta['lastReceivedMs']); a.advance(mid,terminal); harvest(terminal)
        final=c.inventory.features(terminal); final.pop('_combined_net',None)
    finally:
        a.close()

    gross=float(final['combined_gross']); net=float(final['combined_net']); up=(gross+net)/2; down=(gross-net)/2
    payout=up if win=='UP' else down if win=='DOWN' else math.nan
    cost=float(c.inventory.maker_up_cost+c.inventory.maker_down_cost+c.inventory.taker_up_cost+c.inventory.taker_down_cost+c.taker_fee_spent)
    pnl=float(payout-cost) if math.isfinite(payout) else None
    return {
        'version':VERSION,'routeMode':str(route_mode).upper(),'researchOnly':True,'liveTradingChanges':False,'marketId':mid,'winner':win,
        'paperReference':{'makerOrders':len(paper['orders']),'takerFills':len(paper['takers'])},
        'actualExecution':{'realizedPnl':pnl,'makerFilledShares':metrics['makerFilledShares'],'takerFilledShares':metrics['takerFilledShares'],'takerFeesUsdt':float(c.taker_fee_spent),'finalPortfolio':final,'combinedFinalAbsNet':float(final['combined_abs_net']),'pairedCoverage':float(final['combined_paired_coverage'])},
        'cycle':{'metrics':metrics,'r2RunMetrics':dict(c.run_metrics),'objectiveEvents':objective_events,'makerFills':maker_fills,'takerFills':taker_fills,'takerAttempts':taker_attempts,'decisionCount':len(decisions),'invariantViolations':invariant_violations},
        'boundary':'Frozen R2 models/logic unchanged. Venue-live Maker children are external objective owners and do not occupy Frozen-R2 ephemeral PaperOrder slots. No dream fill mutates inventory/cost. Same-side R2 emits coalesce while a child is live; only confirmed HftBacktest fills update inventory. Fixed structural smoke; no learned executor or threshold tuning.'
    }


def main() -> int:
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,action='append',required=True);ap.add_argument('--route-mode',choices=['REVISION_ACK','STICKY_CHILD'],default='REVISION_ACK');ap.add_argument('--output',type=str,default='hft_integrated_control_semimdp_v1_smoke.json');args=ap.parse_args()
    rows=[]
    for mid in args.market_id:
        r=run_market(mid,route_mode=args.route_mode);rows.append(r);print(json.dumps({'marketId':mid,'pnl':r['actualExecution']['realizedPnl'],'makerFilledShares':r['actualExecution']['makerFilledShares'],'finalAbs':r['actualExecution']['combinedFinalAbsNet'],'pairCoverage':r['actualExecution']['pairedCoverage'],'cycle':r['cycle']['metrics'],'violations':len(r['cycle']['invariantViolations'])},ensure_ascii=False),flush=True)
    agg={'markets':len(rows),'pnl':sum(float(r['actualExecution']['realizedPnl'] or 0) for r in rows),'positive':sum(float(r['actualExecution']['realizedPnl'] or 0)>0 for r in rows),'invariantViolations':sum(len(r['cycle']['invariantViolations']) for r in rows),'makerFilledShares':sum(float(r['actualExecution']['makerFilledShares']) for r in rows)}
    rep={'version':VERSION+'_REPORT','researchOnly':True,'aggregate':agg,'rows':rows};OUT.mkdir(parents=True,exist_ok=True);p=OUT/args.output;p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'aggregate':agg,'path':str(p)},ensure_ascii=False,indent=2));return 0

if __name__=='__main__':
    raise SystemExit(main())
