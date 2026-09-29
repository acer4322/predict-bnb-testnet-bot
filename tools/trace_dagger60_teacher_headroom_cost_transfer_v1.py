from __future__ import annotations
import argparse, json, statistics, tempfile, zipfile, shutil, sys
from pathlib import Path

STAGING = Path(__file__).resolve().parent
if str(STAGING) not in sys.path:
    sys.path.insert(0, str(STAGING))

import run_eth_dagger60_smoke_v1 as v1
import run_dagger60_teacher_oracle_fresh100_policy_economics_v1 as base


class HeadroomTraceSim(base.TeacherOracleLocalPendingSim):
    def __init__(self, tape: Path, target_traj):
        super().__init__(tape, target_traj)
        self.targetCost = {'UP': 0.0, 'DOWN': 0.0}
        self.lastTargetFillPrice = {'UP': None, 'DOWN': None}
        self.actionTrace = []

    def advance_target(self, t):
        # Same strict-past target-share accumulation as the frozen teacher,
        # plus behavior-inert cost telemetry.
        while self.ti < len(self.traj) and int(self.traj[self.ti]['t']) < t:
            r = self.traj[self.ti]
            side = str(r['side'])
            q = float(r['shares'])
            p = float(r['price'])
            self.target[side] += q
            self.targetCost[side] += q * p
            self.lastTargetFillPrice[side] = p
            self.ti += 1

    def oracle_action_with_meta(self, qv):
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
            opp = 'DOWN' if side == 'UP' else 'UP'
            oppq = sum(float(a) for a, _ in self.un[opp])
            sameq = sum(float(a) for a, _ in self.un[side])
            target_abs = abs(float(self.target['UP']) - float(self.target['DOWN']))
            branch = 'HEADROOM' if oppq <= v1.EPS else 'PAIR_BACKED'
            if not self.econ_ok(side, p, qty):
                continue
            opp_avg = self.unmatched_avg(opp) if oppq > v1.EPS else None
            tshares = float(self.target[side])
            tvwap = self.targetCost[side] / tshares if tshares > v1.EPS else None
            lastp = self.lastTargetFillPrice[side]
            gross = float(self.inv['UP'] + self.inv['DOWN'])
            floor = float(min(self.inv['UP'], self.inv['DOWN']) - self.cost)
            best = float(max(self.inv['UP'], self.inv['DOWN']) - self.cost)
            pair_cov = float(2.0 * min(self.inv['UP'], self.inv['DOWN']) / gross) if gross > v1.EPS else 0.0
            meta = {
                'side': side,
                'qty': float(qty),
                'proposedBid': p,
                'branch': branch,
                'targetAbs': target_abs,
                'targetSideCumShares': tshares,
                'targetSideCumVWAP': tvwap,
                'lastTargetSameSideFillPrice': lastp,
                'premiumVsTargetVWAP': p - tvwap if tvwap is not None else None,
                'premiumVsLastTargetFill': p - float(lastp) if lastp is not None else None,
                'sameUnmatchedQty': sameq,
                'oppositeUnmatchedQty': float(oppq),
                'oppositeUnmatchedAvgCost': float(opp_avg) if opp_avg is not None else None,
                'preUp': float(self.inv['UP']),
                'preDown': float(self.inv['DOWN']),
                'preCost': float(self.cost),
                'preFloor': floor,
                'preBest': best,
                'preAbsNet': float(abs(self.inv['UP'] - self.inv['DOWN'])),
                'prePairCoverage': pair_cov,
                'reservedSide': float(self.reserved_authoritative(side)),
            }
            return 1, side, qty, meta
        return 0, 'UP', 0.0, None

    def run_teacher_trace(self, winner):
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

            act, side, qty, meta = self.oracle_action_with_meta(qv)
            if not act:
                continue
            self.teacherOpportunities += 1
            if self.firstTeacherT is None:
                self.firstTeacherT = t
            self.lastTeacherT = t
            p = float(qv[side]['bid'])
            accepted = bool(self.submit(t, side, p, qty))
            meta = dict(meta)
            meta.update({
                't': int(t),
                'secondsLeft': float((end - t) / 1000.0),
                'accepted': accepted,
            })
            self.actionTrace.append(meta)
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
            'actionTrace': self.actionTrace,
        }


def mean_or_none(xs):
    xs = [float(x) for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def market_trace_summary(r):
    acts = [a for a in r['actionTrace'] if a['accepted']]
    h = [a for a in acts if a['branch'] == 'HEADROOM']
    p = [a for a in acts if a['branch'] == 'PAIR_BACKED']
    return {
        'marketId': int(r['marketId']),
        'acceptedActions': len(acts),
        'headroomAccepted': len(h),
        'pairBackedAccepted': len(p),
        'headroomActionShare': len(h) / len(acts) if acts else 0.0,
        'headroomQty': sum(float(a['qty']) for a in h),
        'pairBackedQty': sum(float(a['qty']) for a in p),
        'headroomQtyShare': sum(float(a['qty']) for a in h) / sum(float(a['qty']) for a in acts) if acts else 0.0,
        'meanHeadroomPremiumVsTargetVWAP': mean_or_none(a['premiumVsTargetVWAP'] for a in h),
        'meanHeadroomPremiumVsLastTargetFill': mean_or_none(a['premiumVsLastTargetFill'] for a in h),
        'positivePremiumVWAPFraction': (sum((a['premiumVsTargetVWAP'] or 0) > 0 for a in h if a['premiumVsTargetVWAP'] is not None) / sum(a['premiumVsTargetVWAP'] is not None for a in h)) if any(a['premiumVsTargetVWAP'] is not None for a in h) else None,
        'positivePremiumLastFraction': (sum((a['premiumVsLastTargetFill'] or 0) > 0 for a in h if a['premiumVsLastTargetFill'] is not None) / sum(a['premiumVsLastTargetFill'] is not None for a in h)) if any(a['premiumVsLastTargetFill'] is not None for a in h) else None,
        'meanHeadroomTargetAbs': mean_or_none(a['targetAbs'] for a in h),
        'meanHeadroomPreAbsNet': mean_or_none(a['preAbsNet'] for a in h),
        'pnl': float(r['pnl']),
        'oppositePnl': float(r['oppositePnl']),
        'floor': float(r['floor']),
        'bestPnl': float(r['bestPnl']),
        'absNet': float(r['absNet']),
        'pairCoverage': float(r['pairCoverage']),
        'fills': int(r['fills']),
        'submits': int(r['submits']),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--target-traj', required=True)
    ap.add_argument('--reference', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    ref = json.load(open(args.reference, encoding='utf-8'))
    ref_by_mid = {int(r['marketId']): r for r in ref['rows']}
    compare_keys = ['pnl','oppositePnl','buyNotional','up','down','floor','bestPnl','absNet','pairCoverage','submits','fills']

    tmp = Path(tempfile.mkdtemp(prefix='dagger60_teacher_headroom_trace_'))
    try:
        with zipfile.ZipFile(args.bundle) as zf:
            zf.extractall(tmp)
        cohort = json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']
        td = json.load(open(args.target_traj, encoding='utf-8'))
        traj = td['trajectory']
        fresh = [r for r in cohort if r.get('split') != 'TRAIN40']
        supported = [r for r in fresh if str(r['marketId']) in traj]
        rows = []
        mismatches = []
        for i, cr in enumerate(supported, 1):
            mid = int(cr['marketId'])
            sim = HeadroomTraceSim(tmp / 'tapes' / f'{mid}.json.xz', traj[str(mid)])
            try:
                r = sim.run_teacher_trace(cr['winner'])
            finally:
                sim.close()
            r.update({'marketId': mid, 'winner': cr['winner']})
            rr = ref_by_mid.get(mid)
            if rr is None:
                mismatches.append({'marketId':mid,'reason':'missing_reference'})
            else:
                diffs = {}
                for k in compare_keys:
                    av = float(r[k]); bv = float(rr[k])
                    if abs(av - bv) > 1e-9:
                        diffs[k] = {'trace':av,'reference':bv,'delta':av-bv}
                if diffs:
                    mismatches.append({'marketId':mid,'diffs':diffs})
            rows.append(r)
            if i % 20 == 0 or i == len(supported):
                print(json.dumps({'progress':i,'of':len(supported),'mismatchN':len(mismatches),'actions':sum(len(x['actionTrace']) for x in rows)}), flush=True)

        summaries = [market_trace_summary(r) for r in rows]
        accepted = [a for r in rows for a in r['actionTrace'] if a['accepted']]
        headroom = [a for a in accepted if a['branch']=='HEADROOM']
        pair = [a for a in accepted if a['branch']=='PAIR_BACKED']
        out = {
            'version':'DAGGER60_TEACHER_HEADROOM_COST_TRANSFER_AUDIT_V1',
            'behaviorInert': len(mismatches)==0,
            'terminalParityPass': len(mismatches)==0,
            'mismatches': mismatches,
            'supportedMarkets': len(rows),
            'aggregate': {
                'acceptedActions': len(accepted),
                'headroomAccepted': len(headroom),
                'pairBackedAccepted': len(pair),
                'headroomActionShare': len(headroom)/len(accepted) if accepted else 0.0,
                'headroomQty': sum(float(a['qty']) for a in headroom),
                'pairBackedQty': sum(float(a['qty']) for a in pair),
                'headroomQtyShare': sum(float(a['qty']) for a in headroom)/sum(float(a['qty']) for a in accepted) if accepted else 0.0,
                'meanHeadroomPremiumVsTargetVWAP': mean_or_none(a['premiumVsTargetVWAP'] for a in headroom),
                'meanHeadroomPremiumVsLastTargetFill': mean_or_none(a['premiumVsLastTargetFill'] for a in headroom),
            },
            'marketSummaries': summaries,
            'rows': rows,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok':len(mismatches)==0,'aggregate':out['aggregate'],'mismatchN':len(mismatches)}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
