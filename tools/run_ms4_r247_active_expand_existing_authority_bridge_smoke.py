from __future__ import annotations
import argparse, json, math, os, shutil, tempfile, zipfile, sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247

EPS = 1e-9
ACTIVE_WINDOW_MS = 500


class R247ActiveExpandExistingAuthorityBridge(r247.BoundedCoreServiceFavorableRecycleSim):
    """Research-only R247 bridge: one existing Passive Expand authority may relay Active.

    This does NOT mint a new economic objective. The first ordinary SATELLITE_EXPAND
    carrier that reaches a confirmed terminal zero-fill may, if still same generation
    and not superseded, transfer at most its original reserved notional into one
    venue-min Active child on the same side. Repair policy and all other continuation
    logic remain inherited from R247.
    """

    def __init__(self, tape, fanout_limit=1, max_slots=4):
        super().__init__(tape, fanout_limit, max_slots)
        self.bridgeSource = {}
        self.bridgeHandled = set()
        self.bridgeActiveKeys = set()
        self.bridgeActiveMeta = {}
        self.bridgeStats = Counter()
        self.bridgeEvents = []
        self.bridgeUsed = False
        self.bridgeAuthorityExcessMax = 0.0
        self.bridgeQtyExcessMax = 0.0
        self.bridgeGenerationMismatch = 0
        self.bridgeRoleAllocationMismatch = 0
        self.bridgeLateExposureCount = 0
        self.bridgeSubmitCount = 0
        self.bridgeFillQty = 0.0
        self.bridgeFillEvents = 0
        self._bridgeEndMs = None

    # ---------- ownership / capacity ----------
    def _live_bridge_active(self, side=None, generation=None):
        for key in list(self.bridgeActiveKeys):
            o = self.orders.get(key)
            m = self.bridgeActiveMeta.get(key)
            if not o or not m:
                continue
            if side is not None and str(o.get('side')) != str(side):
                continue
            if generation is not None and int(self.key_scope_gen.get(key, -1)) != int(generation):
                continue
            try:
                st = str(self.snap(o).get('status') or '').upper()
            except Exception:
                st = ''
            if st not in r247.v2.TERMINAL_STATUSES:
                return True
        return False

    def _physical_occupancy(self):
        return int(len(self.slot_key) + len(getattr(self, 'activeKeys', set())) + len(self.bridgeActiveKeys))

    def _reserved_current_expand_risk(self):
        total = float(super()._reserved_current_expand_risk())
        if self.scopeSide is None:
            return total
        for key in list(self.bridgeActiveKeys):
            if int(self.key_scope_gen.get(key, -1)) != int(self.scopeGeneration):
                continue
            o = self.orders.get(key)
            if not o:
                continue
            try:
                st = str(self.snap(o).get('status') or '').upper()
            except Exception:
                st = ''
            if st in r247.v2.TERMINAL_STATUSES:
                continue
            rem = max(0.0, float(o.get('qty') or 0.0) - float(o.get('cum') or 0.0))
            if rem > EPS:
                total += rem * float(o['price'])
        return float(total)

    def _submit_active(self, t, side, role, q, score, diag):
        # Frozen R247 Active Repair remains unchanged except shared physical max4.
        if self._physical_occupancy() >= self.max_slots:
            self.bridgeStats['BLOCK_REPAIR_ACTIVE_SHARED_CAPACITY'] += 1
            return False
        return super()._submit_active(t, side, role, q, score, diag)

    def _has_live_replenishment(self):
        return bool(super()._has_live_replenishment() or self._live_bridge_active())

    def _try_parallel_expand(self, t, side):
        if self._live_bridge_active(side=side, generation=self.scopeGeneration):
            self.bridgeStats['BLOCK_PARALLEL_PASSIVE_DUPLICATE'] += 1
            return False
        return super()._try_parallel_expand(t, side)

    def _submit_role_v8(self, t, side, role, p, q, proj, split=None):
        if role == 'SATELLITE_EXPAND' and self._live_bridge_active(side=side, generation=self.scopeGeneration):
            self.bridgeStats['BLOCK_PASSIVE_EXPAND_WHILE_ACTIVE_CHILD_LIVE'] += 1
            return False
        before_n = int(self.n)
        ok = super()._submit_role_v8(t, side, role, p, q, proj, split)
        if ok and role == 'SATELLITE_EXPAND':
            key = f'{side}_{before_n}'
            gen = int(self.key_scope_gen.get(key, self.scopeGeneration))
            self.bridgeSource[key] = {
                'key': key,
                'submitAt': int(t),
                'generation': gen,
                'side': str(side),
                'passivePrice': float(p),
                'submittedQty': float(q),
                'authorizedNotional': float(p) * float(q),
            }
        return ok

    def _open_one_option(self, t, qv, end):
        if self._physical_occupancy() >= self.max_slots:
            self.bridgeStats['BLOCK_NEW_PASSIVE_SHARED_CAPACITY'] += 1
            return
        return super()._open_one_option(t, qv, end)

    # ---------- exact one-hop relay ----------
    def _later_expand_source_exists(self, source_key):
        src = self.bridgeSource.get(source_key, {})
        t0 = int(src.get('submitAt') or 0)
        gen = int(src.get('generation') or -1)
        side = str(src.get('side') or '')
        for key, z in self.bridgeSource.items():
            if key == source_key:
                continue
            if int(z.get('generation') or -2) != gen or str(z.get('side') or '') != side:
                continue
            if int(z.get('submitAt') or 0) > t0:
                return True
        return False

    def _submit_active_expand_bridge(self, t, source_key):
        src = self.bridgeSource[source_key]
        if self._bridgeEndMs is not None and int(self._bridgeEndMs) - int(t) <= r247.v2.NO_NEW_EXPOSURE_MS:
            self.bridgeLateExposureCount += 1
            self.bridgeStats['BLOCK_LATE_180S'] += 1
            return False
        gen = int(src['generation'])
        side = str(src['side'])
        if self.scopeSide is None or int(self.scopeGeneration) != gen or str(self.scopeSide) != side:
            # Eligibility block only: the original Expand authority has expired/superseded
            # through a legitimate scope transition.  This is NOT a correctness mismatch.
            self.bridgeStats['BLOCK_SCOPE_OR_GENERATION_CHANGED'] += 1
            return False
        if self._physical_occupancy() >= self.max_slots:
            self.bridgeStats['BLOCK_ACTIVE_SHARED_CAPACITY'] += 1
            return False
        qv = r247.r1.v2.base.quotes(self.book)
        if not qv or qv.get(side, {}).get('ask') is None:
            self.bridgeStats['BLOCK_NO_ACTIVE_ASK'] += 1
            return False
        ask = float(qv[side]['ask'])
        if not math.isfinite(ask) or ask <= EPS:
            return False
        legal_qty = 1.0 / ask
        source_qty = float(src['submittedQty'])
        budget = float(src['authorizedNotional'])
        # One venue-min slice only. It must fit BOTH original share ceiling and original notional.
        q = float(legal_qty)
        notional = q * ask
        if q > source_qty + EPS:
            self.bridgeStats['BLOCK_VENUE_MIN_EXCEEDS_SOURCE_QTY'] += 1
            return False
        if notional > budget + EPS:
            self.bridgeStats['BLOCK_VENUE_MIN_EXCEEDS_SOURCE_BUDGET'] += 1
            return False
        available = float(self._available_expand_risk_credit())
        if notional > available + EPS:
            self.bridgeStats['BLOCK_CURRENT_CREDIT_NOT_AVAILABLE'] += 1
            return False
        self.bridgeAuthorityExcessMax = max(self.bridgeAuthorityExcessMax, max(0.0, notional - budget, notional - available))
        self.bridgeQtyExcessMax = max(self.bridgeQtyExcessMax, max(0.0, q - source_qty))

        n = int(self.n)
        self.n += 1
        native_side, native_price = r247.r1.v2.base.ex.native_order(side, ask)
        try:
            if native_side == 'BUY':
                rc = int(self.bt.submit_buy_order(0, n, native_price, q,
                    r247.r1.v2.base.ex.hbt.GTC, r247.r1.v2.base.ex.hbt.LIMIT, False))
            else:
                rc = int(self.bt.submit_sell_order(0, n, native_price, q,
                    r247.r1.v2.base.ex.hbt.GTC, r247.r1.v2.base.ex.hbt.LIMIT, False))
        except Exception:
            self.bridgeStats['ACTIVE_SUBMIT_EXCEPTION'] += 1
            return False

        key = f'{side}_{n}'
        self.orders[key] = {'n': n, 'side': side, 'price': ask, 'qty': q, 'cum': 0.0, 'placed': int(t), 'status': 'NEW'}
        self.placeHist.append((int(t), side, q, ask))
        self.submits += 1
        self.key_role[key] = 'SATELLITE_EXPAND'
        self.key_scope_gen[key] = gen
        self.role_submits['SATELLITE_EXPAND'] += 1
        self.keyRepairQuotaAuthorized[key] = 0.0
        self.keyRepairQuotaRemaining[key] = 0.0
        self.keyOverflowQtyAuthorized[key] = q
        self.keyOverflowQtyRemaining[key] = q
        self.totalOverflowQtyAuthorized += q
        self._last_new_receipt = int(t)
        self.bridgeActiveKeys.add(key)
        self.bridgeActiveMeta[key] = {
            'sourceKey': source_key,
            'submitAt': int(t),
            'fillSeen': 0.0,
            'sourceBudget': budget,
            'sourceQty': source_qty,
            'generation': gen,
        }
        self.bridgeSubmitCount += 1
        self.bridgeStats['ACTIVE_EXPAND_RELAY_SUBMIT'] += 1
        ev = {
            't': int(t), 'event': 'R247_ACTIVE_EXPAND_EXISTING_AUTHORITY_SUBMIT',
            'sourceKey': source_key, 'key': key, 'generation': gen, 'side': side,
            'passivePrice': float(src['passivePrice']), 'activeAsk': ask, 'qty': q,
            'sourceQtyCeiling': source_qty, 'sourceAuthorizedNotional': budget,
            'activeNotional': notional, 'availableCreditBefore': available, 'submitRc': rc,
        }
        self.bridgeEvents.append(ev)
        self.slot_history.append(ev)
        return True

    def _scan_passive_expand_zero_fill(self, t):
        if self.bridgeUsed:
            return
        candidates = sorted(self.bridgeSource.items(), key=lambda kv: (int(kv[1]['submitAt']), kv[0]))
        for key, src in candidates:
            if key in self.bridgeHandled:
                continue
            # R247 favorable-lot/hybrid children have different authority semantics; exclude them.
            if key in self.replenishmentKeys or key in self.hybridKeys:
                self.bridgeHandled.add(key)
                self.bridgeStats['SKIP_SPECIAL_REPLENISHMENT_OR_HYBRID'] += 1
                continue
            o = self.orders.get(key)
            if not o:
                continue
            try:
                s = self.snap(o)
                status = str(s.get('status') or '').upper()
                cum = float(s.get('cumExecQty') or o.get('cum') or 0.0)
            except Exception:
                continue
            if status not in r247.v2.TERMINAL_STATUSES:
                continue
            self.bridgeHandled.add(key)
            if cum > EPS:
                self.bridgeStats['PASSIVE_EXPAND_MATERIALIZED_NO_RELAY'] += 1
                continue
            if status != 'CANCELED':
                self.bridgeStats['ZERO_FILL_NONCANCELED_TERMINAL'] += 1
                continue
            if self._later_expand_source_exists(key):
                self.bridgeStats['FAILED_EXPAND_SUPERSEDED'] += 1
                continue
            self.bridgeUsed = True
            self.bridgeStats['ELIGIBLE_ZERO_FILL_SOURCE'] += 1
            self.bridgeEvents.append({'t': int(t), 'event': 'R247_ACTIVE_EXPAND_SOURCE_ZERO_FILL', **src, 'terminalStatus': status})
            self._submit_active_expand_bridge(t, key)
            return

    def _manage_active_expand(self, t):
        for key in list(self.bridgeActiveKeys):
            o = self.orders.get(key)
            m = self.bridgeActiveMeta.get(key)
            if not o or not m:
                self.bridgeActiveKeys.discard(key)
                continue
            cur = float(o.get('cum') or 0.0)
            old = float(m.get('fillSeen') or 0.0)
            if cur > old + EPS:
                inc = cur - old
                m['fillSeen'] = cur
                self.bridgeFillQty += inc
                self.bridgeFillEvents += 1
                self.bridgeStats['ACTIVE_EXPAND_RELAY_FILL_EVENT'] += 1
                ev = {'t': int(t), 'event': 'R247_ACTIVE_EXPAND_EXISTING_AUTHORITY_FILL', 'key': key,
                      'sourceKey': m['sourceKey'], 'incQty': inc, 'cumQty': cur,
                      'price': float(o['price']), 'generation': int(m['generation'])}
                self.bridgeEvents.append(ev)
                self.slot_history.append(ev)
            try:
                s = self.snap(o)
                status = str(s.get('status') or '').upper()
                live = r247.r1.v2.base.live(status)
            except Exception:
                status = ''
                live = False
            if live and int(t) - int(m['submitAt']) >= ACTIVE_WINDOW_MS and not o.get('cancelRequested'):
                co = self.bt.orders(0).get(o['n'])
                if co is not None and bool(co.cancellable):
                    try:
                        self.bt.cancel(0, o['n'], False)
                        o['cancelRequested'] = True
                        self.bridgeStats['ACTIVE_EXPAND_CANCEL_REMAINDER'] += 1
                    except Exception:
                        pass
            if status in r247.v2.TERMINAL_STATUSES:
                self.keyOverflowQtyRemaining[key] = 0.0
                self.bridgeActiveKeys.discard(key)
                self.bridgeStats['ACTIVE_EXPAND_TERMINAL_' + status] += 1

    def process(self, t):
        before = len(self.splitEvents)
        super().process(t)
        # Verify any bridge fill was allocated as pure Expand (0 Repair + all overflow).
        for ev in self.splitEvents[before:]:
            if ev.get('event') != 'ROLE_FILL_SPLIT':
                continue
            key = str(ev.get('key'))
            if key not in self.bridgeActiveMeta:
                continue
            inc = float(ev.get('fillInc') or 0.0)
            rq = float(ev.get('repairAllocated') or 0.0)
            oq = float(ev.get('overflowRealized') or 0.0)
            if str(ev.get('role')) != 'SATELLITE_EXPAND' or abs(rq) > EPS or abs(oq - inc) > 1e-8:
                self.bridgeRoleAllocationMismatch += 1
        self._manage_active_expand(t)

    def _refresh_slots(self, t):
        super()._refresh_slots(t)
        self._manage_active_expand(t)
        self._scan_passive_expand_zero_fill(t)

    def run_bridge(self, winner):
        self._bridgeEndMs = int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        r = super().run_r247(winner)
        self._manage_active_expand(int(self.meta['lastReceivedMs']))
        r.update({
            'bridgeVersion': 'MS4_R247_ACTIVE_EXPAND_EXISTING_AUTHORITY_BRIDGE_V1',
            'bridgeStats': dict(self.bridgeStats),
            'bridgeEvents': self.bridgeEvents[:500],
            'bridgeSubmitCount': int(self.bridgeSubmitCount),
            'bridgeFillQty': float(self.bridgeFillQty),
            'bridgeFillEvents': int(self.bridgeFillEvents),
            'bridgeAuthorityExcessMax': float(self.bridgeAuthorityExcessMax),
            'bridgeQtyExcessMax': float(self.bridgeQtyExcessMax),
            'bridgeGenerationMismatch': int(self.bridgeGenerationMismatch),
            'bridgeRoleAllocationMismatch': int(self.bridgeRoleAllocationMismatch),
            'bridgeLateExposureCount': int(self.bridgeLateExposureCount),
            'bridgeCorrectnessPass': bool(
                self.bridgeAuthorityExcessMax <= EPS and
                self.bridgeQtyExcessMax <= EPS and
                self.bridgeGenerationMismatch == 0 and
                self.bridgeRoleAllocationMismatch == 0 and
                self.bridgeLateExposureCount == 0 and
                self.bridgeSubmitCount <= 1 and
                bool(r.get('r247ServiceCorrectnessPass')) and
                float(r.get('unauthorizedOverflowQty', 0.0)) <= EPS and
                float(r.get('repairQuotaExcessMax', 0.0)) <= EPS
            )
        })
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix='r247_active_expand_bridge_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort = {int(x['marketId']): x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:
                (tmp / f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows = []
        cmp = []
        for mid in mids:
            cr = cohort[mid]
            tape = tmp / f'{mid}.json.xz'
            bsim = r247.BoundedCoreServiceFavorableRecycleSim(tape, 1, 4)
            try:
                b = bsim.run_r247(cr['winner'])
            finally:
                bsim.close()
            csim = R247ActiveExpandExistingAuthorityBridge(tape, 1, 4)
            try:
                c = csim.run_bridge(cr['winner'])
            finally:
                csim.close()
            rows += [
                {'marketId': mid, 'cell': 'R247_CONTROL', 'winnerPostHocOnly': cr['winner'], **b},
                {'marketId': mid, 'cell': 'R247_ACTIVE_EXPAND_EXISTING_AUTHORITY_BRIDGE', 'winnerPostHocOnly': cr['winner'], **c},
            ]
            d = {
                'marketId': mid,
                'relaySubmits': int(c['bridgeSubmitCount']),
                'relayFillEvents': int(c['bridgeFillEvents']),
                'relayFillQty': float(c['bridgeFillQty']),
                'pnlDelta': float(c['pnlDiagnosticOnly']) - float(b['pnlDiagnosticOnly']),
                'floorDelta': float(c['floor']) - float(b['floor']),
                'bestDelta': float(c['best']) - float(b['best']),
                'fillDelta': int(c['fillEvents']) - int(b['fillEvents']),
                'submitDelta': int(c['submits']) - int(b['submits']),
                'baselinePnl': float(b['pnlDiagnosticOnly']),
                'candidatePnl': float(c['pnlDiagnosticOnly']),
                'baselineFloor': float(b['floor']),
                'candidateFloor': float(c['floor']),
                'baselineBest': float(b['best']),
                'candidateBest': float(c['best']),
                'correct': bool(c['bridgeCorrectnessPass']),
            }
            cmp.append(d)
            print(json.dumps(d, ensure_ascii=False), flush=True)

        agg = {
            'markets': len(mids),
            'relaySubmits': sum(x['relaySubmits'] for x in cmp),
            'relayFillEvents': sum(x['relayFillEvents'] for x in cmp),
            'relayFillQty': sum(x['relayFillQty'] for x in cmp),
            'deltaPnl': sum(x['pnlDelta'] for x in cmp),
            'deltaFloor': sum(x['floorDelta'] for x in cmp),
            'deltaBest': sum(x['bestDelta'] for x in cmp),
            'deltaFills': sum(x['fillDelta'] for x in cmp),
            'baselinePnl': sum(x['baselinePnl'] for x in cmp),
            'candidatePnl': sum(x['candidatePnl'] for x in cmp),
            'baselineFloor': sum(x['baselineFloor'] for x in cmp),
            'candidateFloor': sum(x['candidateFloor'] for x in cmp),
            'baselineBest': sum(x['baselineBest'] for x in cmp),
            'candidateBest': sum(x['candidateBest'] for x in cmp),
            'baselineWorstPnl': min(x['baselinePnl'] for x in cmp),
            'candidateWorstPnl': min(x['candidatePnl'] for x in cmp),
            'baselineFills': sum(int(next(r['fillEvents'] for r in rows if r['marketId']==x['marketId'] and r['cell']=='R247_CONTROL')) for x in cmp),
            'candidateFills': sum(int(next(r['fillEvents'] for r in rows if r['marketId']==x['marketId'] and r['cell']=='R247_ACTIVE_EXPAND_EXISTING_AUTHORITY_BRIDGE')) for x in cmp),
        }
        fill_ret = (agg['candidateFills'] / agg['baselineFills']) if agg['baselineFills'] else 1.0
        correctness = all(x['correct'] for x in cmp)
        exercised = agg['relayFillQty'] > EPS
        pareto = bool(exercised and correctness and agg['deltaPnl'] > 0 and agg['deltaFloor'] >= -EPS and agg['deltaBest'] >= -EPS and agg['candidateWorstPnl'] >= agg['baselineWorstPnl'] - EPS and fill_ret >= 0.90)
        if not exercised:
            verdict = 'NOT_EXERCISED'
        elif not correctness:
            verdict = 'ENGINEERING_DEFECT_ONLY'
        elif pareto:
            verdict = 'PARETO_PASS'
        elif agg['deltaPnl'] > 0:
            verdict = 'TRADEOFF_ONLY'
        else:
            verdict = 'FAIL'
        out = {
            'version': 'MS4_R247_ACTIVE_EXPAND_EXISTING_AUTHORITY_BRIDGE_SMOKE3_V1',
            'researchOnly': True,
            'runtimeAuthority': False,
            'markets': mids,
            'rows': rows,
            'comparison': cmp,
            'aggregate': {**agg, 'fillRetention': fill_ret},
            'gates': {'correctnessPass': correctness, 'exercisePass': exercised, 'paretoPass': pareto},
            'verdict': verdict,
            'boundary': [
                'R247 control frozen',
                'first ordinary Passive SATELLITE_EXPAND terminal zero-fill only',
                'one relay attempt maximum per market',
                'same generation and side',
                'later same-generation Expand source supersedes old source',
                'venue-min Active child only',
                'active qty <= original source qty',
                'active notional <= original passive reserved notional and current available credit',
                'no new economic objective or Repair authority',
                'shared max4 physical capacity',
                '<=180s no new exposure unchanged',
                '500ms active remainder execution window inherited from V64-style primitive',
                'realistic HFT / no dream fill / no 8781',
                'consumed smoke only; no locked cohort',
            ]
        }
        op = (Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'verdict': verdict, 'aggregate': out['aggregate'], 'gates': out['gates']}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
