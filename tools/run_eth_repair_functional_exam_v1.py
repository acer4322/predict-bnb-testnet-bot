from __future__ import annotations
import argparse, json, tempfile, zipfile, shutil, sys, statistics
from collections import deque
from pathlib import Path
import numpy as np

STAGING = Path(__file__).resolve().parent
if str(STAGING) not in sys.path:
    sys.path.insert(0, str(STAGING))

import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

EPS = 1e-9
SCENARIOS = {
    'CONTROL': {'fillObsLagMs': 0, 'ackReleaseLagMs': 0},
    'FILL_OBS_1000': {'fillObsLagMs': 1000, 'ackReleaseLagMs': 0},
    'FILL_OBS_3000': {'fillObsLagMs': 3000, 'ackReleaseLagMs': 0},
    'ACK_RELEASE_3000': {'fillObsLagMs': 0, 'ackReleaseLagMs': 3000},
    'COMPOUND_3000': {'fillObsLagMs': 3000, 'ackReleaseLagMs': 3000},
}


def role_from_inv(inv, side):
    u = float(inv['UP']); d = float(inv['DOWN'])
    weak = 'UP' if u < d-EPS else 'DOWN' if d < u-EPS else None
    return 'REPAIR' if weak is not None and side == weak else 'EXPAND'


class RepairExamSim(lp.LocalReservedBootSim):
    def __init__(self, tape, mode, models, fill_obs_lag_ms=0, ack_release_lag_ms=0):
        super().__init__(tape, mode, models)
        self.fillObsLagMs = int(fill_obs_lag_ms)
        self.ackReleaseLagMs = int(ack_release_lag_ms)
        self.truthInv = {'UP': 0.0, 'DOWN': 0.0}
        self.truthCost = 0.0
        self.pendingObs = deque()
        self.firstVisibleAt = {}
        self.cancelRequestedAt = {}
        self.submitRoleObserved = {}
        self.submitRoleTruth = {}
        self.firstFillSeen = set()
        self.actualFillEvents = 0
        self.partialFillEvents = 0
        self.lateFillAfterCancelEvents = 0
        self.repairToExpandAtFill = 0
        self.expandToRepairAtFill = 0
        self.roleStableAtFill = 0
        self.acceptedSubmitWhileSameSideUnobservedFill = 0
        self.acceptedSubmitWithObservedTruthRoleMismatch = 0
        self.maxUnobservedFillQty = 0.0
        self.obsApplied = 0
        self.obsDebtClears = 0
        self.obsDebtClearToNextSubmitMs = []
        self.lastDebtClearAt = None
        self.recoverySubmitCount = 0

    def unobserved_qty(self, side=None):
        if side is None:
            return sum(float(x['qty']) for x in self.pendingObs)
        return sum(float(x['qty']) for x in self.pendingObs if x['side'] == side)

    def _apply_due_observations(self, t):
        had = bool(self.pendingObs)
        while self.pendingObs and int(self.pendingObs[0]['due']) <= int(t):
            x = self.pendingObs.popleft()
            v1.Sim.record_fill(self, int(t), x['side'], float(x['qty']), float(x['price']))
            self.obsApplied += 1
        if had and not self.pendingObs:
            self.obsDebtClears += 1
            self.lastDebtClearAt = int(t)

    def process(self, t):
        self._apply_due_observations(t)
        for key, o in self.orders.items():
            s = self.snap(o)
            status = s.get('status')
            if status is not None and key not in self.firstVisibleAt:
                self.firstVisibleAt[key] = int(t)
            cum = float(s.get('cumExecQty') or 0.0)
            inc = max(0.0, cum - float(o.get('cum') or 0.0))
            if inc > EPS:
                px = v1.fill_price(o['side'], s, o['price'])
                pre_truth_role = role_from_inv(self.truthInv, o['side'])
                self.truthInv[o['side']] += inc
                self.truthCost += inc * px
                self.actualFillEvents += 1
                if status == 'PARTIALLY_FILLED' or cum < float(o.get('qty') or 0.0)-EPS:
                    self.partialFillEvents += 1
                if key in self.cancelRequestedAt:
                    self.lateFillAfterCancelEvents += 1
                if key not in self.firstFillSeen:
                    self.firstFillSeen.add(key)
                    submit_role = self.submitRoleObserved.get(key)
                    if submit_role == pre_truth_role:
                        self.roleStableAtFill += 1
                    elif submit_role == 'REPAIR' and pre_truth_role == 'EXPAND':
                        self.repairToExpandAtFill += 1
                    elif submit_role == 'EXPAND' and pre_truth_role == 'REPAIR':
                        self.expandToRepairAtFill += 1
                self.pendingObs.append({'due': int(t)+self.fillObsLagMs, 'side': o['side'], 'qty': inc, 'price': px, 'key': key})
                self.maxUnobservedFillQty = max(self.maxUnobservedFillQty, self.unobserved_qty())
                o['cum'] = cum
            o['status'] = status

        # Reconcile local pre-ack reservation against venue visibility, optionally with an ack/release lag.
        for side in ('UP', 'DOWN'):
            for key in list(self.localPending[side]):
                o = self.orders.get(key)
                if o is None:
                    self.localPending[side].pop(key, None)
                    continue
                s = self.snap(o); status = s.get('status')
                if status is None:
                    continue
                vis = int(self.firstVisibleAt.get(key, t))
                if int(t) - vis >= self.ackReleaseLagMs:
                    self.localPending[side].pop(key, None)

        # For lag=0, fills observed in same decision tick as venue event.
        self._apply_due_observations(t)

    def cancel_expired(self, t):
        for key, o in self.orders.items():
            s = self.snap(o)
            if v1.live(s.get('status')) and t-o['placed'] >= v1.TTL:
                cur = self.bt.orders(0).get(o['n'])
                if cur is not None and bool(cur.cancellable):
                    self.cancelRequestedAt.setdefault(key, int(t))
                    try:
                        self.bt.cancel(0, o['n'], False)
                    except Exception:
                        pass

    def submit(self, t, side, p, q):
        observed_role = role_from_inv(self.inv, side)
        truth_role = role_from_inv(self.truthInv, side)
        same_side_unobserved = self.unobserved_qty(side)
        n_before = self.n
        ok = super().submit(t, side, p, q)
        if not ok:
            return False
        key = f'{side}_{n_before}'
        self.submitRoleObserved[key] = observed_role
        self.submitRoleTruth[key] = truth_role
        if same_side_unobserved > EPS:
            self.acceptedSubmitWhileSameSideUnobservedFill += 1
        if observed_role != truth_role:
            self.acceptedSubmitWithObservedTruthRoleMismatch += 1
        if self.lastDebtClearAt is not None:
            self.obsDebtClearToNextSubmitMs.append(max(0, int(t)-int(self.lastDebtClearAt)))
            self.lastDebtClearAt = None
            self.recoverySubmitCount += 1
        return True

    def run_exam(self, models, winner):
        ups = sorted(self.payload['updates'], key=lambda u: (int(u[1]), int(u[0])))
        first = int(self.meta['firstReceivedMs']); v1.ex.advance_to(self.bt, first)
        end = int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t = int(u[1]); v1.ex.advance_to(self.bt, t); self.process(t); self.cancel_expired(t)
            ca = v1.apply(self.book, u); qv = v1.quotes(self.book)
            if not qv: continue
            if self.firstValid is None: self.firstValid = t
            if (end-t)/1000. <= 180: continue
            self.seed_if_needed(t, qv)
            if not self.seeded: continue
            x = self.features(t, qv, ca, end).reshape(1,-1)
            pa = float(models['action'].predict_proba(x)[0,1])
            if pa < models['actionTh']: continue
            ps = float(models['side'].predict_proba(x)[0,1])
            side = 'UP' if ps >= models['sideTh'] else 'DOWN'
            qty = max(.01, float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))))
            p = float(qv[side]['bid']); qty = max(qty, 1/p); qty = min(qty, 12.)
            self.submit(t, side, p, qty)
        end2 = int(self.meta['lastReceivedMs']); v1.ex.advance_to(self.bt, end2); self.process(end2)
        # Flush observation-only lag without changing venue execution.
        self._apply_due_observations(end2 + self.fillObsLagMs + self.ackReleaseLagMs + 1)
        pnl = self.inv.get(str(winner).upper(),0.) - self.cost
        gross = sum(self.inv.values())
        first_fill_total = self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
        return {
            'pnlDiagnosticOnly': pnl,
            'buyNotional': self.cost,
            'pairCoverage': 2*min(self.inv.values())/gross if gross>EPS else 0.,
            'absNet': abs(self.inv['UP']-self.inv['DOWN']),
            'submits': self.submits,
            'fillsObserved': self.fills,
            'actualFillEvents': self.actualFillEvents,
            'partialFillEvents': self.partialFillEvents,
            'lateFillAfterCancelEvents': self.lateFillAfterCancelEvents,
            'repairToExpandAtFirstFill': self.repairToExpandAtFill,
            'expandToRepairAtFirstFill': self.expandToRepairAtFill,
            'roleStableAtFirstFill': self.roleStableAtFill,
            'firstFillRoleEvents': first_fill_total,
            'repairToExpandRate': self.repairToExpandAtFill/first_fill_total if first_fill_total else 0.,
            'acceptedSubmitWhileSameSideUnobservedFill': self.acceptedSubmitWhileSameSideUnobservedFill,
            'acceptedSubmitWithObservedTruthRoleMismatch': self.acceptedSubmitWithObservedTruthRoleMismatch,
            'maxUnobservedFillQty': self.maxUnobservedFillQty,
            'obsDebtClears': self.obsDebtClears,
            'meanDebtClearToNextSubmitMs': statistics.mean(self.obsDebtClearToNextSubmitMs) if self.obsDebtClearToNextSubmitMs else None,
            'duplicateBlocked': self.duplicateBlocked,
            'localPendingBlocked': self.localPendingBlocked,
        }


def aggregate(rows):
    def sm(k): return sum(float(r.get(k) or 0) for r in rows)
    first = sm('firstFillRoleEvents')
    return {
        'markets': len(rows),
        'activeMarkets': sum(float(r['buyNotional'])>EPS for r in rows),
        'meanBuy': statistics.mean(float(r['buyNotional']) for r in rows),
        'meanSubmits': statistics.mean(float(r['submits']) for r in rows),
        'meanAbsNet': statistics.mean(float(r['absNet']) for r in rows),
        'meanPairCoverage': statistics.mean(float(r['pairCoverage']) for r in rows),
        'actualFillEvents': int(sm('actualFillEvents')),
        'partialFillEvents': int(sm('partialFillEvents')),
        'lateFillAfterCancelEvents': int(sm('lateFillAfterCancelEvents')),
        'firstFillRoleEvents': int(first),
        'repairToExpandAtFirstFill': int(sm('repairToExpandAtFirstFill')),
        'repairToExpandRate': sm('repairToExpandAtFirstFill')/first if first else None,
        'acceptedSubmitWhileSameSideUnobservedFill': int(sm('acceptedSubmitWhileSameSideUnobservedFill')),
        'acceptedSubmitWithObservedTruthRoleMismatch': int(sm('acceptedSubmitWithObservedTruthRoleMismatch')),
        'maxUnobservedFillQty': max(float(r['maxUnobservedFillQty']) for r in rows) if rows else 0.,
        'meanDuplicateBlocked': statistics.mean(float(r['duplicateBlocked']) for r in rows),
        'meanLocalPendingBlocked': statistics.mean(float(r['localPendingBlocked']) for r in rows),
        'pnlDiagnosticOnly': sm('pnlDiagnosticOnly'),
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--bundle', required=True); ap.add_argument('--output', required=True); ap.add_argument('--markets', type=int, default=24)
    a = ap.parse_args(); tmp = Path(tempfile.mkdtemp(prefix='eth_repair_exam_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']; traj = json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models, off1, off2 = lp.train_models(tmp, cohort, traj)
        test = [r for r in cohort if r['split']!='TRAIN40']
        n = min(int(a.markets), len(test))
        idx = sorted(set(int(x) for x in np.linspace(0, len(test)-1, n)))
        selected = [test[i] for i in idx]
        allrows=[]; summaries={}
        for scenario, cfg in SCENARIOS.items():
            rows=[]
            for i,cr in enumerate(selected,1):
                sim=RepairExamSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'])
                try:r=sim.run_exam(models,cr['winner'])
                finally:sim.close()
                r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
                if i%8==0: print(json.dumps({'scenario':scenario,'progress':i,'of':len(selected),'unsafeUnobservedSubmits':sum(x['acceptedSubmitWhileSameSideUnobservedFill'] for x in rows),'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows)}),flush=True)
            summaries[scenario]=aggregate(rows)
        control=summaries['CONTROL']
        gates={
            'controlNoSilentRepairToExpand': (control['repairToExpandAtFirstFill']==0),
            'fillLag1000NoSubmitOnUnobservedSameSideFill': summaries['FILL_OBS_1000']['acceptedSubmitWhileSameSideUnobservedFill']==0,
            'fillLag3000NoSubmitOnUnobservedSameSideFill': summaries['FILL_OBS_3000']['acceptedSubmitWhileSameSideUnobservedFill']==0,
            'compoundNoSubmitOnUnobservedSameSideFill': summaries['COMPOUND_3000']['acceptedSubmitWhileSameSideUnobservedFill']==0,
            'ackLagDoesNotCollapseActivity': summaries['ACK_RELEASE_3000']['meanSubmits'] >= 0.5*control['meanSubmits'],
            'compoundDoesNotCollapseActivity': summaries['COMPOUND_3000']['meanSubmits'] >= 0.5*control['meanSubmits'],
        }
        payload={
            'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V1',
            'researchOnly':True,
            'performanceGraduationEligible':False,
            'cohort':'24 deterministic evenly-spaced markets from consumed Fresh101 development cohort',
            'selectedMarketIds':[int(r['marketId']) for r in selected],
            'boundary':[
                'Realistic HftBacktest/Predict execution remains authoritative; perturbations change controller observation/ack timing only, never fabricate venue fills',
                'Frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation; no Target runtime objective/winner',
                'Winner/PnL is diagnostic only and excluded from functional pass gates',
                'Exam asks whether repair/objective ownership survives fill-observation lag and acknowledgement lag',
                'A REPAIR carrier silently materializing as EXPAND without explicit reauthorization is a functional violation',
                'Submitting same-side responsibility while an actual same-side fill remains unobserved is a functional violation',
                'Fresh101 is consumed development evidence only; no promotion claim',
            ],
            'round1Offline':off1,'round2Offline':off2,'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,
        }
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(payload,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'allPass':payload['allPass'],'gates':gates,'scenarios':summaries},ensure_ascii=False),flush=True)
    finally:
        shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
