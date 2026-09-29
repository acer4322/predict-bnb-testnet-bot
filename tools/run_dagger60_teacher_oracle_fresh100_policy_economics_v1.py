from __future__ import annotations
import argparse, json, math, statistics, tempfile, zipfile, shutil, sys
from pathlib import Path
from collections import deque

STAGING = Path(__file__).resolve().parent
if str(STAGING) not in sys.path:
    sys.path.insert(0, str(STAGING))

import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp


class TeacherOracleLocalPendingSim(lp.LocalReservedBootSim):
    def __init__(self, tape: Path, target_traj):
        # BOOK_IMBALANCE bootstrap does not use models.
        super().__init__(tape, 'BOOK_IMBALANCE', {})
        self.traj = sorted(target_traj or [], key=lambda r: int(r['t']))
        self.ti = 0
        self.target = {'UP': 0.0, 'DOWN': 0.0}
        self.teacherOpportunities = 0
        self.teacherAccepted = 0
        self.teacherBlocked = 0
        self.teacherQty = 0.0
        self.newExposureAtOrBelow180 = 0
        self.firstTeacherT = None
        self.lastTeacherT = None

    def oracle_action_authoritative(self, qv):
        ds = []
        for side in ('UP', 'DOWN'):
            d = max(
                0.0,
                float(self.target[side])
                - float(self.inv[side])
                - float(self.reserved_authoritative(side)),
            )
            ds.append((d, side))
        for d, side in sorted(ds, reverse=True):
            if d <= 0.25:
                continue
            p = float(qv[side]['bid'])
            legal = 1.0 / p if p > 0 else 1e9
            qty = d
            if qty < legal:
                if qty < 0.5 * legal:
                    continue
                qty = legal
            qty = min(float(qty), 12.0)
            if self.econ_ok(side, p, qty):
                return 1, side, qty
        return 0, 'UP', 0.0

    def run_teacher(self, winner):
        ups = sorted(self.payload['updates'], key=lambda u: (int(u[1]), int(u[0])))
        first = int(self.meta['firstReceivedMs'])
        v1.ex.advance_to(self.bt, first)
        end = int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])

        for u in ups:
            t = int(u[1])
            v1.ex.advance_to(self.bt, t)
            self.process(t)
            self.cancel_expired(t)
            v1.apply(self.book, u)
            # Exact teacher source contract: only placements strictly earlier than current receipt.
            self.advance_target(t)
            qv = v1.quotes(self.book)
            if not qv:
                continue
            if self.firstValid is None:
                self.firstValid = t
            if (end - t) / 1000.0 <= 180.0:
                continue
            self.seed_if_needed(t, qv)
            if not self.seeded:
                continue

            act, side, qty = self.oracle_action_authoritative(qv)
            if not act:
                continue
            self.teacherOpportunities += 1
            if self.firstTeacherT is None:
                self.firstTeacherT = t
            self.lastTeacherT = t
            p = float(qv[side]['bid'])
            accepted = bool(self.submit(t, side, p, qty))
            if accepted:
                self.teacherAccepted += 1
                self.teacherQty += float(qty)
                if (end - t) / 1000.0 <= 180.0:
                    self.newExposureAtOrBelow180 += 1
            else:
                self.teacherBlocked += 1

        end2 = int(self.meta['lastReceivedMs'])
        v1.ex.advance_to(self.bt, end2)
        self.process(end2)
        winner = str(winner).upper()
        opp = 'UP' if winner == 'DOWN' else 'DOWN'
        pnl = float(self.inv[winner] - self.cost)
        opposite = float(self.inv[opp] - self.cost)
        gross = float(self.inv['UP'] + self.inv['DOWN'])
        floor = float(min(self.inv['UP'], self.inv['DOWN']) - self.cost)
        best = float(max(self.inv['UP'], self.inv['DOWN']) - self.cost)
        pair = float(2.0 * min(self.inv['UP'], self.inv['DOWN']) / gross) if gross > v1.EPS else 0.0
        return {
            'pnl': pnl,
            'oppositePnl': opposite,
            'buyNotional': float(self.cost),
            'up': float(self.inv['UP']),
            'down': float(self.inv['DOWN']),
            'floor': floor,
            'bestPnl': best,
            'absNet': float(abs(self.inv['UP'] - self.inv['DOWN'])),
            'pairCoverage': pair,
            'submits': int(self.submits),
            'fills': int(self.fills),
            'duplicateBlocked': int(self.duplicateBlocked),
            'localPendingBlocked': int(self.localPendingBlocked),
            'teacherOpportunities': int(self.teacherOpportunities),
            'teacherAccepted': int(self.teacherAccepted),
            'teacherBlocked': int(self.teacherBlocked),
            'teacherQtyAccepted': float(self.teacherQty),
            'targetPlacementsConsumed': int(self.ti),
            'targetPlacementsAvailable': int(len(self.traj)),
            'targetFinalUp': float(self.target['UP']),
            'targetFinalDown': float(self.target['DOWN']),
            'newExposureAtOrBelow180': int(self.newExposureAtOrBelow180),
            'firstTeacherT': self.firstTeacherT,
            'lastTeacherT': self.lastTeacherT,
        }


def profit_factor(rows):
    gp = sum(max(0.0, float(r['pnl'])) for r in rows)
    gl = -sum(min(0.0, float(r['pnl'])) for r in rows)
    return gp / gl if gl > 0 else (float('inf') if gp > 0 else None)


def max_drawdown(rows):
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for r in rows:
        eq += float(r['pnl'])
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def summarize(rows):
    if not rows:
        return {'markets': 0}
    p = [float(r['pnl']) for r in rows]
    buy = [float(r['buyNotional']) for r in rows]
    total_pnl = sum(p)
    total_buy = sum(buy)
    wins = sum(x > 0 for x in p)
    active = [r for r in rows if float(r['buyNotional']) > v1.EPS]
    best_market = max(p)
    return {
        'markets': len(rows),
        'activeMarkets': len(active),
        'pnl': total_pnl,
        'buyNotional': total_buy,
        'roi': total_pnl / total_buy if total_buy > 0 else None,
        'winRate': wins / len(rows),
        'profitFactor': profit_factor(rows),
        'meanPnl': statistics.mean(p),
        'medianPnl': statistics.median(p),
        'meanBuy': statistics.mean(buy),
        'fills': sum(int(r['fills']) for r in rows),
        'submits': sum(int(r['submits']) for r in rows),
        'meanPairCoverage': statistics.mean(float(r['pairCoverage']) for r in rows),
        'positiveFloorRate': sum(float(r['floor']) >= 0 for r in rows) / len(rows),
        'meanFloor': statistics.mean(float(r['floor']) for r in rows),
        'aggregateFloor': sum(float(r['floor']) for r in rows),
        'aggregateBest': sum(float(r['bestPnl']) for r in rows),
        'meanAbsNet': statistics.mean(float(r['absNet']) for r in rows),
        'maxWin': max(p),
        'maxLoss': min(p),
        'leaveOneBestOutPnl': total_pnl - best_market,
        'maxSequentialDrawdown': max_drawdown(rows),
        'teacherOpportunities': sum(int(r['teacherOpportunities']) for r in rows),
        'teacherAccepted': sum(int(r['teacherAccepted']) for r in rows),
        'teacherBlocked': sum(int(r['teacherBlocked']) for r in rows),
        'newExposureAtOrBelow180': sum(int(r['newExposureAtOrBelow180']) for r in rows),
    }


def block_summaries(rows, block=20):
    out = []
    for i in range(0, len(rows), block):
        rr = rows[i:i+block]
        s = summarize(rr)
        s['startIndex'] = i + 1
        s['endIndex'] = i + len(rr)
        s['firstMarketId'] = int(rr[0]['marketId']) if rr else None
        s['lastMarketId'] = int(rr[-1]['marketId']) if rr else None
        out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--target-traj', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--market-limit', type=int, default=0)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='dagger60_teacher_oracle_fresh100_'))
    try:
        with zipfile.ZipFile(args.bundle) as zf:
            zf.extractall(tmp)
        cohort = json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']
        td = json.load(open(args.target_traj, encoding='utf-8'))
        traj = td['trajectory']
        fresh = [r for r in cohort if r.get('split') != 'TRAIN40']
        supported = [r for r in fresh if str(r['marketId']) in traj]
        missing = [int(r['marketId']) for r in fresh if str(r['marketId']) not in traj]
        if args.market_limit > 0:
            supported = supported[:args.market_limit]

        rows = []
        for i, cr in enumerate(supported, 1):
            mid = int(cr['marketId'])
            sim = TeacherOracleLocalPendingSim(tmp / 'tapes' / f'{mid}.json.xz', traj[str(mid)])
            try:
                r = sim.run_teacher(cr['winner'])
            finally:
                sim.close()
            r.update({
                'marketId': mid,
                'winner': cr['winner'],
                'targetPnlPosthoc': cr.get('targetPnl'),
                'targetBuyPosthoc': cr.get('targetBuy'),
            })
            rows.append(r)
            if i % 10 == 0 or i == len(supported):
                print(json.dumps({
                    'progress': i,
                    'of': len(supported),
                    'pnlSoFar': sum(float(x['pnl']) for x in rows),
                    'winsSoFar': sum(float(x['pnl']) > 0 for x in rows),
                    'teacherAccepted': sum(int(x['teacherAccepted']) for x in rows),
                }), flush=True)

        out = {
            'version': 'DAGGER60_TEACHER_ORACLE_FRESH100_POLICY_ECONOMICS_V1',
            'posthocDiagnosticOnly': True,
            'deployablePolicyEvidence': False,
            'boundary': [
                'Fresh101 Target Maker trajectory source was saved posthoc for diagnostic use only',
                'BOOK_IMBALANCE bootstrap is identical to frozen Local Pending candidate seed semantics',
                'Local Pending authoritative reservation and realistic HFT execution retained',
                'Teacher objective-gap/econ_ok action authority replaces student decisions after bootstrap',
                'Target placement enters objective only when placement.t < current receipt t',
                '<=180s no new exposure',
                'winner and Target PnL used only after replay for scoring',
                'missing trajectory markets are excluded without imputation',
            ],
            'sourceMeta': {
                'trajectoryVersion': td.get('version'),
                'trajectoryPosthocDiagnosticOnly': td.get('posthocDiagnosticOnly'),
                'trajectoryNoRuntimeUse': td.get('noRuntimeUse'),
                'trajectoryCutoffExclusive': td.get('cutoffExclusive'),
                'trajectoryMarketsDeclared': td.get('marketsWithTrajectory'),
                'freshMarketsInBundle': len(fresh),
                'supportedMarketsBeforeLimit': sum(str(r['marketId']) in traj for r in fresh),
                'missingMarketIds': missing,
                'marketLimit': args.market_limit,
            },
            'summary': summarize(rows),
            'chronologyBlocks': block_summaries(rows, 20),
            'rows': rows,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'summary': out['summary'], 'missingMarketIds': missing}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
