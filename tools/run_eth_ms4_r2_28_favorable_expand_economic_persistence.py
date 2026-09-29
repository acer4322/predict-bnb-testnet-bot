from __future__ import annotations
import argparse, json, os, shutil, tempfile, zipfile, sys, importlib.util
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_STAGED = Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp = importlib.util.spec_from_file_location('r28', _STAGED)
    r28 = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28

v2 = r28.v2
EPS = 1e-9


class FavorableExpandEconomicPersistenceSim(r28.FanoutRoleCapacitySim):
    """R2.28: preserve an already-authorized favorable Expand queue.

    This changes only SATELLITE_EXPAND frontier reanchor cancellation. It creates no
    new capacity and leaves ordinary CAP1 monetary-credit reservation/consumption intact.
    """
    def __init__(self, tape, max_slots=4):
        super().__init__(tape, 1, max_slots)
        self.r228 = Counter()
        self.r228Events = []

    def _live_same_generation_opposite_core(self, expand_side: str):
        opp = 'DOWN' if str(expand_side) == 'UP' else 'UP'
        rows = []
        for sid, key, o, role in self._live_role_rows(role='ECONOMIC_CORE'):
            if int(self.key_scope_gen.get(key, -1)) != int(self.scopeGeneration):
                continue
            if str(o.get('side')) != opp:
                continue
            rows.append((sid, key, o, role))
        return rows[-1] if rows else None

    def _reanchor_stale(self, t: int):
        # Frozen V7/CAP1 logic, with one narrow exception for an already-live
        # SATELLITE_EXPAND whose economic pair remains favorable.
        for sid, key in list(self.slot_key.items()):
            o = self.orders.get(key)
            if not o or o.get('cancelRequested'):
                continue
            role = self.key_role.get(key, 'UNASSIGNED')
            side = str(o['side'])
            p = v2.kprice(o['price'])
            levels = [v2.kprice(x) for x in self._live_price_levels(side)]
            if role == 'ECONOMIC_CORE':
                if p in levels and self._pair_ok(side, p):
                    self.corePreservedClocks += 1
                    continue
                self._request_cancel(t, sid, 'CORE_INVALIDATED')
                continue
            if p not in levels:
                if role == 'SATELLITE_EXPAND' and int(self.key_scope_gen.get(key, -1)) == int(self.scopeGeneration):
                    core = self._live_same_generation_opposite_core(side)
                    if core is not None:
                        _, core_key, core_o, _ = core
                        pair_sum = float(p) + float(core_o['price'])
                        if pair_sum <= 1.0 + EPS:
                            self.r228['ECONOMIC_PERSISTENCE_KEEP'] += 1
                            ev = {
                                't': int(t), 'event': 'ECONOMIC_PERSISTENCE_KEEP',
                                'key': key, 'generation': int(self.scopeGeneration),
                                'side': side, 'expandPrice': float(p),
                                'coreKey': core_key, 'corePrice': float(core_o['price']),
                                'pairSum': float(pair_sum)
                            }
                            if len(self.r228Events) < 2000:
                                self.r228Events.append(ev)
                            if len(self.slot_history) < 1800:
                                self.slot_history.append(ev)
                            continue
                if self._request_cancel(t, sid, 'SATELLITE_FRONTIER_REANCHOR'):
                    self.reanchors += 1

    def run_r228(self, winner):
        r = super().run_cap(winner)
        r['r228Stats'] = dict(self.r228)
        r['r228Events'] = self.r228Events
        r['economicPersistenceKeeps'] = int(self.r228.get('ECONOMIC_PERSISTENCE_KEEP', 0))
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix='ms4_r228_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co = {int(x['marketId']): x for x in json.load(open(tmp/'cohort.json', encoding='utf-8'))['rows']}
        rows = []
        for mid in mids:
            cr = co[mid]
            tape = tmp/'tapes'/f'{mid}.json.xz'
            ctl = r28.FanoutRoleCapacitySim(tape, 1, 4)
            try:
                b = ctl.run_cap(cr['winner'])
            finally:
                ctl.close()
            sim = FavorableExpandEconomicPersistenceSim(tape, 4)
            try:
                c = sim.run_r228(cr['winner'])
            finally:
                sim.close()
            rows += [
                {'marketId': mid, 'cell': 'MS4_R28_CAP1_CONTROL', 'winnerPostHocOnly': cr['winner'], **b},
                {'marketId': mid, 'cell': 'MS4_R228_FAVORABLE_EXPAND_ECONOMIC_PERSISTENCE', 'winnerPostHocOnly': cr['winner'], **c},
            ]
            print(json.dumps({
                'marketId': mid,
                'control': {'fills': b['fillEvents'], 'submits': b['submits'], 'pnl': b['pnlDiagnosticOnly'], 'floor': b['floor'], 'best': b['best']},
                'candidate': {'fills': c['fillEvents'], 'submits': c['submits'], 'pnl': c['pnlDiagnosticOnly'], 'floor': c['floor'], 'best': c['best'], 'keeps': c['economicPersistenceKeeps']},
                'unauth': c['unauthorizedOverflowQty'], 'quotaExcess': c['repairQuotaExcessMax']
            }, ensure_ascii=False), flush=True)

        B = {r['marketId']: r for r in rows if r['cell'] == 'MS4_R28_CAP1_CONTROL'}
        C = {r['marketId']: r for r in rows if r['cell'] == 'MS4_R228_FAVORABLE_EXPAND_ECONOMIC_PERSISTENCE'}
        cmp = []
        for m in mids:
            b, c = B[m], C[m]
            cmp.append({
                'marketId': m,
                'fillDelta': c['fillEvents'] - b['fillEvents'],
                'fillRetention': c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,
                'submitDelta': c['submits'] - b['submits'],
                'pnlDelta': c['pnlDiagnosticOnly'] - b['pnlDiagnosticOnly'],
                'floorDelta': c['floor'] - b['floor'],
                'bestDelta': c['best'] - b['best'],
                'economicPersistenceKeeps': c['economicPersistenceKeeps'],
            })
        correctness = all(float(C[m].get('unauthorizedOverflowQty', 0.0)) <= EPS and float(C[m].get('repairQuotaExcessMax', 0.0)) <= EPS for m in mids)
        anti = all(C[m]['fillEvents'] >= 0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents'] > 0)
        aggregate_no_suppress = sum(C[m]['fillEvents'] for m in mids) >= sum(B[m]['fillEvents'] for m in mids)
        exercised = sum(C[m]['economicPersistenceKeeps'] for m in mids) > 0
        out = {
            'version': 'MS4_R2_28_FAVORABLE_EXPAND_ECONOMIC_PERSISTENCE_V1',
            'researchOnly': True, 'runtimeAuthority': False, 'markets': mids,
            'rows': rows, 'comparisonVsCap1': cmp,
            'gates': {
                'correctnessPass': correctness,
                'antiCollapse50pctPass': anti,
                'aggregateFillsNotSuppressed': aggregate_no_suppress,
                'economicPersistenceExercised': exercised,
            },
            'boundary': [
                'CAP1 risk capacity is unchanged',
                'existing SATELLITE_EXPAND keeps reserving/consuming ordinary monetary credit',
                'only frontier-reanchor cancellation may be suppressed for an already-authorized Expand',
                'keep requires same-generation live opposite ECONOMIC_CORE and pairSum<=1',
                'no new order, slot, risk capacity, or pair veto',
                'all Repair/Active/fanout/overflow accounting frozen',
                '<=180s unchanged', 'no Target/winner/future runtime input',
                'realistic HFT', 'no dream fill', 'no 8781'
            ]
        }
        op = Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper() == 'AUTO' else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'gates': out['gates'], 'comparison': cmp}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    main()
