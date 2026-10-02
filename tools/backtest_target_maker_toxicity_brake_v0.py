from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / 'tools' / 'backtest_target_maker_time_only_no_inventory_v0.py'
spec = importlib.util.spec_from_file_location('td_base', BASE_PATH)
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = base
spec.loader.exec_module(base)

REPORT = ROOT / 'data' / 'research' / 'target_maker_toxicity_brake_v0_report.json'
VERSION = 'TARGET_MAKER_TOXICITY_BRAKE_V0'
TOXIC_TICKS = -1.0
CHECK_AFTER_MS = 1000
PAUSE_UNTIL_FROM_FILL_MS = 5000


def side_mid(snapshot: dict[str, Any], side: str) -> float | None:
    if side == 'UP':
        return base.snapshot_value(snapshot, 'predict_up_mid', 'predictUpMid')
    return base.snapshot_value(snapshot, 'predict_down_mid', 'predictDownMid')


class ToxicitySimulator(base.Simulator):
    def __init__(self) -> None:
        super().__init__(use_inventory=False)
        self.pending_checks: list[dict[str, Any]] = []
        self.pause_until: dict[str, int] = {'UP': 0, 'DOWN': 0}
        self.toxic_checks = 0
        self.toxic_triggers = 0
        self.dominant_toxic_triggers = 0
        self.suppressed_side_plans = 0

    def maker_only_dominant(self) -> str | None:
        up = sum(float(f['shares']) for f in self.maker_fills if str(f['side']) == 'UP')
        down = sum(float(f['shares']) for f in self.maker_fills if str(f['side']) == 'DOWN')
        if abs(up - down) <= 1e-9:
            return None
        return 'UP' if up > down else 'DOWN'

    def fill_existing(self, snapshot: dict[str, Any], snapshot_ns: int, now_ms: int) -> int:
        before = len(self.maker_fills)
        n = super().fill_existing(snapshot, snapshot_ns, now_ms)
        for f in self.maker_fills[before:]:
            mid = side_mid(snapshot, str(f['side']))
            if mid is not None:
                self.pending_checks.append({
                    'side': str(f['side']),
                    'fill_ms': int(f['at_ms']),
                    'fill_mid': float(mid),
                    'checked': False,
                })
        return n

    def update_toxicity(self, snapshot: dict[str, Any], now_ms: int) -> None:
        keep: list[dict[str, Any]] = []
        for x in self.pending_checks:
            if x['checked']:
                continue
            fill_ms = int(x['fill_ms'])
            if now_ms < fill_ms + CHECK_AFTER_MS:
                keep.append(x)
                continue
            # Only use the first available snapshot after 1s; if recorder skipped too far, discard.
            if now_ms > fill_ms + CHECK_AFTER_MS + 2000:
                continue
            side = str(x['side'])
            mid = side_mid(snapshot, side)
            if mid is None:
                keep.append(x)
                continue
            x['checked'] = True
            self.toxic_checks += 1
            move_ticks = (float(mid) - float(x['fill_mid'])) / base.maker_ebm.GRID
            if move_ticks <= TOXIC_TICKS + 1e-9:
                self.toxic_triggers += 1
                if self.maker_only_dominant() == side:
                    self.dominant_toxic_triggers += 1
                    self.pause_until[side] = max(self.pause_until.get(side, 0), fill_ms + PAUSE_UNTIL_FROM_FILL_MS)
        self.pending_checks = keep

    def decide_at(self, snapshot: dict[str, Any], now_ms: int) -> dict[str, Any]:
        d = base.time_depth_decide(snapshot, self.inventory(), use_inventory=False)
        rows = list(d.get('orders') or [])
        if not rows:
            return d
        kept = []
        for r in rows:
            side = str(r['side'])
            if now_ms < int(self.pause_until.get(side, 0)):
                self.suppressed_side_plans += 1
                continue
            kept.append(r)
        d = dict(d)
        d['orders'] = kept
        d['decision'] = 'QUOTE' if kept else 'IDLE'
        d['reason'] = 'TIME_DEPTH_TOXICITY_BRAKE'
        return d


def run_toxic(snapshots: list[dict[str, Any]], seed_rows: list[dict[str, Any]]) -> ToxicitySimulator:
    sim = ToxicitySimulator()
    seed_idx = 0
    for item in snapshots:
        now_ms = int(item['decision_ms']); snap = dict(item['snapshot'])
        snapshot_ns = int(base.num(snap.get('timestampNs')) or base.num(snap.get('timestamp_ns')) or (now_ms * 1_000_000))
        maker_filled = sim.fill_existing(snap, snapshot_ns, now_ms)
        sim.update_toxicity(snap, now_ms)
        decision = sim.decide_at(snap, now_ms)
        seed_filled_this = False
        while seed_idx < len(seed_rows) and int(seed_rows[seed_idx]['filled_at_ms']) <= now_ms:
            sim.apply_seed(seed_rows[seed_idx]); seed_idx += 1; seed_filled_this = True
        sim.apply_plan(decision, snapshot_ns, now_ms, allow_new=(maker_filled == 0 and not seed_filled_this))
    while seed_idx < len(seed_rows):
        sim.apply_seed(seed_rows[seed_idx]); seed_idx += 1
    return sim


def pack(sim: base.Simulator, winner: str) -> dict[str, Any]:
    maker = [dict(x) for x in sim.maker_fills]
    all_fills = maker + [
        {'side': s['side'], 'price': s['price'], 'shares': s['shares'], 'at_ms': s['filled_at_ms']}
        for s in sim.seed_fills
    ]
    out = {
        'pnlUsdt': base.pnl_from_fills(all_fills, winner),
        'placements': sim.placements,
        'cancels': sim.cancels,
        'makerFills': len(maker),
        'pair': base.fifo_pair(maker),
        'makerImbalance': base.imbalance_metrics(maker),
        'portfolio': base.portfolio_metrics(all_fills),
    }
    if isinstance(sim, ToxicitySimulator):
        out['toxicity'] = {
            'checks': sim.toxic_checks,
            'toxicTriggers': sim.toxic_triggers,
            'dominantToxicTriggers': sim.dominant_toxic_triggers,
            'suppressedSidePlans': sim.suppressed_side_plans,
        }
    return out


def main() -> int:
    our = base.ro(base.DEFAULT_OUR_DB); target = base.ro(base.DEFAULT_TARGET_DB)
    try:
        snapshots = base.load_snapshots(our)
        seeds = base.load_seeds(our)
        baseline = base.load_baseline(our)
        winners = base.load_winners(target)
        markets = sorted(set(snapshots) & set(winners))
        rows = []
        for m in markets:
            seed_rows = list(seeds.get(m, []))
            no = base.run_sim(snapshots[m], seed_rows, use_inventory=False)
            hard = base.run_sim(snapshots[m], seed_rows, use_inventory=True)
            tox = run_toxic(snapshots[m], seed_rows)
            winner = winners[m]
            base_fills = [dict(x) for x in baseline.get(m, {}).get('fills', [])]
            base_maker = [f for f in base_fills if str(f.get('channel')) == 'MAKER']
            bpack = {
                'pnlUsdt': base.pnl_from_fills(base_fills, winner),
                'placements': int(baseline.get(m, {}).get('placements', 0)),
                'cancels': int(baseline.get(m, {}).get('cancels', 0)),
                'makerFills': len(base_maker),
                'pair': base.fifo_pair(base_maker),
                'makerImbalance': base.imbalance_metrics(base_maker),
                'portfolio': base.portfolio_metrics(base_fills),
            }
            rows.append({'marketId':m,'winner':winner,'baseline':bpack,'timeDepthNoInventory':pack(no,winner),'timeDepthHardInventory':pack(hard,winner),'toxicityBrake':pack(tox,winner)})

        def vals(which: str, key: str) -> list[float]: return [float(r[which][key]) for r in rows]
        def nest(which: str, g: str, key: str) -> list[float]: return [float(r[which][g][key]) for r in rows]
        report: dict[str, Any] = {
            'reportVersion': VERSION,
            'researchOnly': True,
            'liveTradingChanges': False,
            'hypothesis': 'A strict-past 1s adverse-selection markout brake on the currently dominant Maker side can reduce the catastrophic inventory drift of a permissive time-depth Maker without using future winner or Taker actions.',
            'policy': {
                'base': 'same coarse Target Layer-1 time-depth curve as TIME_ONLY V0; both UP and DOWN normally quote',
                'toxicity': 'after a Maker fill, at first available snapshot >=1s later, if that token mid has fallen >=1 tick and that side is Maker-only dominant, suppress only that side until fill+5s; opposite side continues',
                'fixedNotSwept': {'markoutHorizonMs':1000,'toxicMoveTicks':-1,'pauseUntilFillPlusMs':5000},
                'sameAsPriorV0': ['18 shares/order','pair cap <=0.99','ask-touch fill proxy','1s same side+tick refill cooldown','recorded OPEN_SEED fills'],
            },
            'graduationReference': {'initialPositiveMarketRate':0.35,'note':'first-stage milestone, not final Target similarity'},
            'coverage': {'markets':len(rows),'settledMarkets':len(rows)},
            'summary': {},
            'comparisons': {},
            'rows': rows,
            'guards': ['This is a historical replay on recorder snapshots, not fresh blind validation.','The toxicity threshold/horizon/pause duration are one fixed V0 copied from the Target empirical test; no parameter sweep.','Do not interpret success as proof of Target private controller semantics.'],
        }
        for which in ('baseline','timeDepthNoInventory','timeDepthHardInventory','toxicityBrake'):
            p = vals(which,'pnlUsdt')
            report['summary'][which] = {
                'pnlUsdt': sum(p),
                'positiveMarkets': sum(x>0 for x in p),
                'positiveMarketRate': sum(x>0 for x in p)/len(p) if p else None,
                'pnlPerMarket': base.stats(p),
                'placementsPerMarket': base.stats(vals(which,'placements')),
                'cancelsPerMarket': base.stats(vals(which,'cancels')),
                'makerFillsPerMarket': base.stats(vals(which,'makerFills')),
                'pairedCoverage': base.stats(nest(which,'pair','pairedCoverage')),
                'lockedEdgeUsdt': sum(nest(which,'pair','lockedEdgeUsdt')),
                'pairedShares': sum(nest(which,'pair','pairedShares')),
                'edgePerPairedShare': (sum(nest(which,'pair','lockedEdgeUsdt'))/sum(nest(which,'pair','pairedShares'))) if sum(nest(which,'pair','pairedShares'))>0 else None,
                'finalAbsNetShares': base.stats(nest(which,'makerImbalance','finalAbsNetShares')),
                'peakAbsNetShares': base.stats(nest(which,'makerImbalance','peakAbsNetShares')),
                'riskDeficitUsdt': base.stats(nest(which,'portfolio','riskDeficitUsdt')),
            }
        a=[float(r['toxicityBrake']['pnlUsdt'])-float(r['timeDepthNoInventory']['pnlUsdt']) for r in rows]
        b=[float(r['toxicityBrake']['pnlUsdt'])-float(r['timeDepthHardInventory']['pnlUsdt']) for r in rows]
        report['comparisons']={
            'toxicityMinusNoInventory': {'sumPnlDelta':sum(a),'better':sum(x>1e-9 for x in a),'worse':sum(x<-1e-9 for x in a),'tie':sum(abs(x)<=1e-9 for x in a),'deltaPerMarket':base.stats(a)},
            'toxicityMinusHardInventory': {'sumPnlDelta':sum(b),'better':sum(x>1e-9 for x in b),'worse':sum(x<-1e-9 for x in b),'tie':sum(abs(x)<=1e-9 for x in b),'deltaPerMarket':base.stats(b)},
            'toxicityTotals': {
                'checks':sum(int(r['toxicityBrake']['toxicity']['checks']) for r in rows),
                'toxicTriggers':sum(int(r['toxicityBrake']['toxicity']['toxicTriggers']) for r in rows),
                'dominantToxicTriggers':sum(int(r['toxicityBrake']['toxicity']['dominantToxicTriggers']) for r in rows),
                'suppressedSidePlans':sum(int(r['toxicityBrake']['toxicity']['suppressedSidePlans']) for r in rows),
            }
        }
        REPORT.parent.mkdir(parents=True,exist_ok=True)
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        our.close(); target.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
