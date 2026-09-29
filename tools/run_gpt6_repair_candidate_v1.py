"""Consumed-market role x route falsification. Never imported by live runtime."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'tools', ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools.eth_repair_modular.persistent_execution_roles import CORE, SATELLITE, PersistentExecutionRoles

EPS = 1e-9


def json_default(value):
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, 'item'):
        return value.item()
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(type(value).__name__)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, default=json_default, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def make_adapter(hybrid, role_enabled, satellite_enabled):
    lock = hybrid.lock
    lease = hybrid.lease
    base = hybrid.base

    class RoleAdapter(hybrid.HybridPriceFanoutHFT):
        def __init__(self, *a, **kw):
            self.executionRoles = PersistentExecutionRoles()
            self.roleEvents = []
            self.gptTrace = []
            self.gptLiveRows = []
            self.gptMaxOverReserved = 0.0
            super().__init__(*a, **kw)

        def _managed_live(self, t):
            rows = super()._managed_live(t)
            self.gptLiveRows = rows
            return rows

        def _request_cancel(self, t, row, reason):
            if not role_enabled:
                method = hybrid.HybridPriceFanoutHFT._request_cancel if satellite_enabled else lock.EconomicHandoffLeaseLockHFT._request_cancel
                return method(self, t, row, reason)
            rows = [r for r in self.gptLiveRows if r['parentId'] == row['parentId'] and r['side'] == row['side']]
            chosen = self.executionRoles.select(rows, reason)
            if chosen is None:
                self.roleEvents.append(dict(t=t, event='CORE_QUEUE_RETAINED', key=row['key'], reason=reason))
                return False
            self.roleEvents.append(dict(t=t, event='ROLE_CANCEL_SELECTION', requestedKey=row['key'],
                                        selectedKey=chosen['key'], selectedRole=self.executionRoles.role(chosen['key']), reason=reason))
            if reason != 'FRONTIER_REANCHOR' or not satellite_enabled or self.executionRoles.role(chosen['key']) != SATELLITE:
                return lock.EconomicHandoffLeaseLockHFT._request_cancel(self, t, chosen, reason)

            cores = [r for r in rows if self.executionRoles.role(r['key']) == CORE]
            if not cores:
                return False
            side = chosen['side']
            target, bid, ask, priority = self._priority_target(side)
            if target is None or not priority.improved or not (EPS < target < ask - EPS):
                return False
            pid = int(chosen['parentId'])
            debt = float(self._parent_debt_now(pid))
            self._sync_parent_occupancy()
            rem = float(chosen['remaining'])
            available = float(self.parentExecutionOccupancy.available(pid, debt))
            legal = 1.0 / target
            qty = min(legal, rem, available + rem)
            others = [r for r in rows if r['key'] != chosen['key']]
            before = float(self._current_payoffs()['floor'])
            after, floors = self._floor_after_buys(side, [(r['price'], r['remaining']) for r in others] + [(target, qty)])
            safe = after is not None and after >= before - 1e-7 and all(f >= before - 1e-7 for f in floors)
            event = dict(t=t, event='ROLE_SATELLITE_PREFLIGHT', key=chosen['key'], parentId=pid,
                         target=target, bid=bid, ask=ask, qty=qty, legalQty=legal, debt=debt,
                         available=available, oldRemaining=rem, floorBefore=before, floorAfter=after,
                         coreKeys=[r['key'] for r in cores], jointFloorSafe=safe)
            if qty + EPS < legal or not safe:
                event['decision'] = 'BLOCK_LEGAL_OR_JOINT_FLOOR'
                self.roleEvents.append(event)
                return False
            # Reuse original cancel-pending occupancy and handoff; only selection/route differs.
            ok = lease.ReanchorHandoffLeaseHFT._request_cancel(self, t, chosen, reason)
            event['cancelRequested'] = bool(ok)
            if ok and isinstance(self.pendingRollingCancel, dict):
                self.pendingRollingCancel.update(hybridFanoutApprovedPrice=target, hybridFanoutApprovedQty=qty,
                    hybridFanoutApprovedAt=t, hybridFanoutCoreKeys=[r['key'] for r in cores])
                self.fanoutOpportunities += 1
                self.fanoutCancelRequests += 1
            self.roleEvents.append(event)
            return bool(ok)

        def _submit_replacement(self, t, meta, rows, best):
            old_key = meta['key']
            old_role = self.executionRoles.role(old_key)
            if role_enabled and meta.get('hybridFanoutApprovedPrice') is not None:
                # An approval before cancel is not authority to cross a changed current ask.
                qv = lock.v1.quotes(self.book)
                target = float(meta['hybridFanoutApprovedPrice'])
                side = meta['side']
                core_live = any(self.executionRoles.role(r['key']) == CORE and
                                r['parentId'] == meta['parentId'] for r in rows)
                if not core_live or not qv or target >= float(qv[side]['ask']) - EPS:
                    if bool(self.carrierLedger.get(old_key, {}).get('terminalConfirmed')):
                        self.pendingRollingCancel = None
                    self.roleEvents.append(dict(t=t, event='SATELLITE_LEASE_ABANDONED', key=old_key,
                                                reason='CORE_GONE_OR_CURRENT_MAKER_PRICE_INVALID'))
                    return False
            n0 = int(self.n)
            method = hybrid.HybridPriceFanoutHFT._submit_replacement if satellite_enabled else lock.EconomicHandoffLeaseLockHFT._submit_replacement
            ok = method(self, t, meta, rows, best)
            if not ok or not role_enabled:
                return ok
            key = f"{meta['side']}_{n0}"
            entry = self.carrierLedger[key]
            pid, side = int(meta['parentId']), meta['side']
            if old_role is not None:
                self.executionRoles.inherit(old_key, key, pid, side,
                    terminal_confirmed=bool(self.carrierLedger[old_key]['terminalConfirmed']))
            elif meta.get('economicLeaseApprovedPrice') is not None:
                self.executionRoles.bind(key, pid, side, CORE, lease_price=float(meta['economicLeaseApprovedPrice']))
                for row in rows:
                    if row['key'] != old_key and row['parentId'] == pid and self.executionRoles.role(row['key']) is None:
                        self.executionRoles.bind(row['key'], pid, side, SATELLITE)
            self.roleEvents.append(dict(t=t, event='ROLE_REPLACEMENT_BOUND', oldKey=old_key, newKey=key,
                parentId=pid, role=self.executionRoles.role(key), submittedQty=entry.get('submittedQty'),
                price=self.orders.get(key, {}).get('price'), economicLease=meta.get('economicLeaseApprovedPrice')))
            return ok

        def _capture_observed_state(self, t):
            # Read-only diagnostic state. Never fed to any authority method.
            if not hasattr(self, 'allocationLedgerV2') or not hasattr(self, 'inv'):
                return
            parents = {str(pid): self.allocationLedgerV2.describe_parent(pid) for pid in self.allocationLedgerV2.parents}
            floor, up, down, cost = self._raw_floor()
            parents = {k: {name: v[name] for name in ['initialDebt', 'remainingDebt', 'repairPaid']}
                       for k, v in parents.items() if v is not None}
            over = 0.0
            occupancy = getattr(self, 'parentExecutionOccupancy', None)
            if occupancy:
                for pid, p in parents.items():
                    over = max(over, occupancy.parent_reserved(int(pid)) - p['remainingDebt'])
            self.gptMaxOverReserved = max(self.gptMaxOverReserved, over)
            row = dict(t=int(t), floor=float(floor), best=float(max(up, down) - cost),
                       up=float(up), down=float(down), cost=float(cost), parents=parents)
            signature = {k: v for k, v in row.items() if k != 't'}
            old = {k: v for k, v in self.gptTrace[-1].items() if k != 't'} if self.gptTrace else None
            if signature != old:
                if self.gptTrace and self.gptTrace[-1]['t'] == int(t):
                    self.gptTrace[-1] = row
                else:
                    self.gptTrace.append(row)

        def process(self, t):
            result = super().process(t)
            self._capture_observed_state(t)
            return result

    return RoleAdapter


def summarize(sim, raw, parents):
    trace = sim.gptTrace
    area = 0.0
    completion = None
    for i, row in enumerate(trace):
        end = trace[i + 1]['t'] if i + 1 < len(trace) else int(sim.capEnd)
        unpaid = sum(p['remainingDebt'] for p in row['parents'].values())
        area += max(0, end - row['t']) / 1000 * unpaid
        if row['parents'] and unpaid <= EPS and completion is None:
            completion = row['t']
    required = ['actualFillEvents', 'pnlDiagnosticOnly', 'floor', 'repairParentBirths', 'repairParentCompletions']
    missing = [k for k in required if k not in raw]
    result = {k: raw.get(k) for k in required}
    result.update(semanticRounds=raw.get('v70dSemanticRounds', raw.get('rounds')),
        shareRepairSettlements=raw.get('v80ShareRepairSettlements'),
        managementCompletions=raw.get('v80ManagementCompletions'),
        ledgerPaidDebtCompletions=sum(p['initialDebt'] > EPS and p['remainingDebt'] <= EPS for p in parents.values()),
        ledgerRepairPaid=sum(p['repairPaid'] for p in parents.values()),
        ledgerRemainingDebt=sum(p['remainingDebt'] for p in parents.values()),
        sampledDebtTrackingAreaShareSeconds=area if trace else None,
        sampledAllDebtPaidAt=completion,
        worstObservedFloor=min(r['floor'] for r in trace) if trace else None,
        terminalBest=trace[-1]['best'] if trace else None,
        maxObservedOverReserved=sim.gptMaxOverReserved,
        coreFillQty=sum(float(sim.carrierLedger[k].get('actualFilled', 0)) for k in sim.executionRoles.entries if sim.executionRoles.role(k) == CORE),
        satelliteFillQty=sum(float(sim.carrierLedger[k].get('actualFilled', 0)) for k in sim.executionRoles.entries if sim.executionRoles.role(k) == SATELLITE),
        missingRequiredFields=missing)
    return result


def main():
    ap = argparse.ArgumentParser()
    for name in ['bundle', 'lifecycle-model', 'capability-model', 'dagger-cache', 'timing-model',
                 'economic-model', 'price-model', 'surplus-model', 'v44-model', 'v47-model']:
        ap.add_argument('--' + name, required=True)
    ap.add_argument('--market-id', type=int, default=1946475)
    ap.add_argument('--cells', default='A,B,C,D')
    ap.add_argument('--output', default='AUTO')
    ap.add_argument('--hft-path', help='Existing pinned HftBacktest dependency directory for isolated source deployments')
    args = ap.parse_args()
    if args.market_id != 1946475:
        raise ValueError('V1 preregistration authorizes consumed 1946475 only')
    cells = args.cells.split(',')
    if not cells or len(cells) != len(set(cells)) or any(c not in 'ABCD' or len(c) != 1 for c in cells):
        raise ValueError('invalid cells')
    out = Path(os.environ.get('BTC5M_LAN_RESULT_DIR', '.')) / 'result.json' if args.output == 'AUTO' else Path(args.output)
    start = time.time()
    write_json(out.parent / 'partial.json', dict(state='IMPORTING', cells=cells))
    print(json.dumps(dict(state='IMPORTING', cells=cells)), flush=True)
    if args.hft_path:
        sys.path.insert(0, str(Path(args.hft_path).resolve(strict=True)))
    # Fail once at the leaf before legacy fallback loaders repeat a missing dependency.
    import run_eth_dagger60_smoke_v1
    import faulthandler
    faulthandler.dump_traceback_later(240, repeat=True)
    import joblib
    import tools.run_eth_multislot_hybrid_price_fanout_1946475 as hybrid
    faulthandler.cancel_dump_traceback_later()
    pe = hybrid.pe
    manifest = {k: dict(path=str(v), sha256=sha(v)) for k, v in vars(args).items()
                if k in ['bundle', 'lifecycle_model', 'capability_model', 'dagger_cache', 'timing_model',
                         'economic_model', 'price_model', 'surplus_model', 'v44_model', 'v47_model']}
    source_manifest = {}
    for module in list(sys.modules.values()):
        path = getattr(module, '__file__', None)
        if path:
            p = Path(path).resolve()
            if p.is_relative_to(ROOT) and p.suffix == '.py' and p.is_file():
                source_manifest[str(p.relative_to(ROOT))] = sha(p)
    import hftbacktest
    hft_file = Path(hftbacktest.__file__).resolve()
    write_json(out.parent / 'inputs.json', dict(files=manifest, loadedSources=source_manifest,
        hftDependency=dict(path=str(hft_file), sha256=sha(hft_file), version=getattr(hftbacktest, '__version__', None))))
    write_json(out.parent / 'partial.json', dict(state='LOADING_MODELS', elapsed=time.time()-start))
    models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(args)
    t44 = joblib.load(args.v44_model)['models']['EVENT_VALUE_NORM']
    t47 = joblib.load(args.v47_model)['models']['GENERATION_AWARE_NORM']
    result = dict(version='GPT6_REPAIR_CANDIDATE_V1', researchOnly=True, marketId=args.market_id,
                  preregistered='GPT6_REPAIR_CANDIDATE_V1_PREREGISTERED_20260905.md', cells={}, inputs=manifest)
    with tempfile.TemporaryDirectory(prefix='gpt6_repair_v1_') as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(args.bundle) as z:
            cohort = json.loads(z.read('cohort.json'))
            name = f'tapes/{args.market_id}.json.xz'
            tape = tmp / f'{args.market_id}.json.xz'
            tape.write_bytes(z.read(name))
        row = next(r for r in cohort['rows'] if int(r['marketId']) == args.market_id)
        # The inherited runner accepts winner for terminal scoring only. Role module has no winner input.
        for cell in cells:
            print(json.dumps(dict(state='RUNNING', cell=cell, elapsed=time.time()-start)), flush=True)
            write_json(out.parent / 'partial.json', dict(state='RUNNING', cell=cell, completed=list(result['cells'])))
            cls = make_adapter(hybrid, cell in 'BD', cell in 'CD')
            sim = pe.make(cls, tape, models, life, cap, tim, econ, price, sur, t44, t47)
            try:
                raw = sim.run_hybrid(models, row['winner'])
                sim._capture_observed_state(int(sim.capEnd))
                conservation, bounded, parents = pe.alloc(sim, raw)
                metrics = summarize(sim, raw, parents)
                safety = pe.safety(raw)
                roles = {k: asdict(v) for k, v in sim.executionRoles.entries.items()}
                carriers = {k: {name: v.get(name) for name in ['side', 'parentId', 'objectiveId', 'objectiveRole',
                            'submittedQty', 'actualFilled', 'terminalConfirmed', 'lane', 'submittedAt']}
                            | {'orderPrice': sim.orders.get(k, {}).get('price')}
                            for k, v in sim.carrierLedger.items()}
                occupancy = raw.get('occupancyParents')
                checks = dict(allocationConservation=bool(conservation), allocationDebtBounded=bool(bounded),
                              safetyZero=bool(safety) and all(float(v) <= EPS for v in safety.values()),
                              occupancyEvidencePresent=bool(occupancy),
                              observedOccupancyBounded=metrics['maxObservedOverReserved'] <= 1e-7,
                              metricsPresent=not metrics['missingRequiredFields'])
                payload = dict(metrics=metrics, checks=checks, safety=safety, allocationParents=parents,
                               occupancyParents=occupancy, roles=roles, roleEvents=sim.roleEvents, carriers=carriers,
                               observedStateTrace=sim.gptTrace, raw=raw)
                write_json(out.parent / f'cell_{cell}.json', payload)
                result['cells'][cell] = dict(metrics=metrics, checks=checks, safety=safety)
                write_json(out, result)
                print(json.dumps(dict(state='CELL_DONE', cell=cell, metrics=metrics, checks=checks), default=json_default), flush=True)
            finally:
                sim.close()
    result['elapsedSeconds'] = time.time() - start
    result['status'] = 'COLLECTED_FOR_PREREGISTERED_GATE_REVIEW'
    write_json(out, result)
    write_json(out.parent / 'partial.json', dict(state='DONE', elapsed=result['elapsedSeconds']))


if __name__ == '__main__':
    main()
