from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / 'run_lane_g_r264_reanchor_decision_time_trajectory_dump.py'
spec = importlib.util.spec_from_file_location('lane_g_reanchor_dt', BASE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(BASE_PATH)
dtmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtmod)

v2 = dtmod.v2
EPS = 1e-9
REPAIR_ROLES = {'ECONOMIC_CORE', 'SATELLITE_REPAIR'}


class ReanchorLifecycleContextDumpSim(dtmod.ReanchorDecisionTrajectoryDumpSim):
    """Behavior-inert higher-level lifecycle context at Repair reanchor decisions."""

    def __init__(self, tape, fanout_limit=1, max_slots=4):
        self.intentThesisSide = None
        self.intentThesisBornAt = None
        self.scopeBirthAt = {}
        super().__init__(tape, fanout_limit, max_slots)

    def _submit_role_v8(self, t, side, role, p, q, proj, split=None):
        before_n = self.n
        ok = super()._submit_role_v8(t, side, role, p, q, proj, split)
        if ok and role == 'PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide = str(side)
            self.intentThesisBornAt = int(t)
        return ok

    def _sync_scope(self, t, new_side, floor_before):
        old_gen = int(self.scopeGeneration)
        old_side = self.scopeSide
        out = super()._sync_scope(t, new_side, floor_before)
        if self.scopeSide is not None and (int(self.scopeGeneration) != old_gen or old_side is None or old_side != self.scopeSide):
            self.scopeBirthAt[int(self.scopeGeneration)] = int(t)
        return out

    def _fill_context(self, t, gen):
        events = []
        for e in self.splitEvents:
            if e.get('event') != 'ROLE_FILL_SPLIT':
                continue
            if int(e.get('generationAtSubmit') or -1) != int(gen):
                continue
            inc = float(e.get('fillInc') or 0.0)
            if inc <= EPS:
                continue
            role = str(e.get('role') or '')
            key = str(e.get('key') or '')
            if role in REPAIR_ROLES:
                kind = 'R'
            elif role == 'SATELLITE_EXPAND':
                kind = 'E'
            else:
                continue
            active = key in getattr(self, 'activeMeta', {})
            events.append({
                't': int(e.get('t') or 0), 'kind': kind, 'active': bool(active),
                'qty': inc, 'repairQty': float(e.get('repairAllocated') or 0.0),
            })
        repair = [e for e in events if e['kind'] == 'R']
        expand = [e for e in events if e['kind'] == 'E']
        active_rep = [e for e in repair if e['active']]
        seq = []
        for e in events:
            if not seq or seq[-1] != e['kind']:
                seq.append(e['kind'])
        re_trans = sum(1 for a, b in zip(seq, seq[1:]) if a == 'R' and b == 'E')
        er_trans = sum(1 for a, b in zip(seq, seq[1:]) if a == 'E' and b == 'R')
        cycles = 0
        state = 0
        for k in seq:
            if state == 0 and k == 'R':
                state = 1
            elif state == 1 and k == 'E':
                state = 2
            elif state == 2 and k == 'R':
                cycles += 1
                state = 1
        def since(rows):
            return None if not rows else int(t) - max(int(x['t']) for x in rows)
        return {
            'genRepairFillEvents': len(repair),
            'genExpandFillEvents': len(expand),
            'genActiveRepairFillEvents': len(active_rep),
            'genRepairFillClocks': len({e['t'] for e in repair}),
            'genExpandFillClocks': len({e['t'] for e in expand}),
            'genRepairQty': sum(float(e['repairQty']) for e in repair),
            'genExpandQty': sum(float(e['qty']) for e in expand),
            'genRepairExpandTransitions': re_trans,
            'genExpandRepairTransitions': er_trans,
            'genCompletedRERCycles': cycles,
            'msSinceLastRepairFill': since(repair),
            'msSinceLastExpandFill': since(expand),
            'msSinceLastActiveRepairFill': since(active_rep),
        }

    def _lifecycle_context(self, t, side):
        gen = int(self.scopeGeneration)
        start = int(self.meta.get('firstReceivedMs') or 0)
        end = int((self.payload.get('market') or {}).get('window_end_ms') or self.meta.get('lastReceivedMs') or start + 1)
        denom = max(1, end - start)
        phase = max(0.0, min(1.0, (int(t) - start) / denom))
        birth = self.scopeBirthAt.get(gen)
        ob = getattr(self, 'riskRepairObligations', {}).get(gen)
        if ob:
            born_qty = float(ob.get('bornQty') or 0.0)
            outstanding = float(ob.get('outstanding') or 0.0)
            repaid = float(ob.get('repaidQty') or 0.0)
            passive = float(ob.get('passiveRepaidQty') or 0.0)
            active = float(ob.get('activeRepaidQty') or 0.0)
            repaid_frac = (repaid / born_qty) if born_qty > EPS else 0.0
            ob_age = int(t) - int(ob.get('bornAt') or t)
            ob_live = 1 if outstanding > EPS and ob.get('closedAt') is None else 0
        else:
            born_qty = outstanding = repaid = passive = active = repaid_frac = 0.0
            ob_age = 0
            ob_live = 0
        try:
            overflow_ob = self._active_obligation()
        except Exception:
            overflow_ob = None
        overflow_out = float(overflow_ob.get('outstanding') or 0.0) if overflow_ob else 0.0
        overflow_live = 1 if overflow_ob and overflow_out > EPS else 0
        repair_lots = [x for x in getattr(self, 'repairLots', []) if int(x.get('generation') or -1) == gen and float(x.get('remaining') or 0.0) > EPS]
        service = [x for x in getattr(self, 'serviceLedger', {}).values() if int(x.get('generation') or -1) == gen]
        service_held = sum(float(x.get('held') or 0.0) for x in service)
        service_spent = sum(float(x.get('spent') or 0.0) for x in service)
        r263_used = 1 if gen in getattr(self, 'r263GenerationUsed', set()) else 0
        risk_used = 1 if gen in getattr(self, 'riskTrancheGenerationUsed', set()) else 0
        risk_keys = [k for k, x in getattr(self, 'riskTrancheMeta', {}).items() if int(x.get('generation') or -1) == gen]
        risk_filled = sum(1 for k in risk_keys if float(getattr(self, 'riskTrancheMeta', {}).get(k, {}).get('spent') or 0.0) > EPS)
        try:
            risk_debt = float(self._risk_debt_outstanding())
        except Exception:
            risk_debt = 0.0
        try:
            core_auth = float(self._available_core_service_authority())
        except Exception:
            core_auth = 0.0
        thesis = self.intentThesisSide
        repair_side = str(side)
        scope_side = self.scopeSide
        out = {
            'marketPhase': phase,
            'scopeGeneration': gen,
            'scopeAgeMs': 0 if birth is None else int(t) - int(birth),
            'scopeBirthKnown': 0 if birth is None else 1,
            'scopeCompletionsSoFar': int(getattr(self, 'scopeCompletions', 0)),
            'scopeFlipsSoFar': int(getattr(self, 'scopeFlips', 0)),
            'totalRepairProgressClocks': int(getattr(self, 'totalRepairProgressClocks', 0)),
            'scopeRepairProgressClocks': int(getattr(self, 'scopeRepairProgressClocks', 0)),
            'intentThesisKnown': 1 if thesis in {'UP', 'DOWN'} else 0,
            'scopeMatchesThesis': 1 if thesis in {'UP', 'DOWN'} and scope_side == thesis else 0,
            'repairSideMatchesThesis': 1 if thesis in {'UP', 'DOWN'} and repair_side == thesis else 0,
            'msSinceThesisBirth': 0 if self.intentThesisBornAt is None else int(t) - int(self.intentThesisBornAt),
            'riskObligationExists': 1 if ob else 0,
            'riskObligationLive': ob_live,
            'riskObligationAgeMs': ob_age,
            'riskBornQty': born_qty,
            'riskOutstandingQty': outstanding,
            'riskRepaidQty': repaid,
            'riskRepaidFrac': repaid_frac,
            'riskPassiveRepaidQty': passive,
            'riskActiveRepaidQty': active,
            'riskCarrierSubmits': int(ob.get('carrierSubmits') or 0) if ob else 0,
            'riskCarrierFills': int(ob.get('carrierFills') or 0) if ob else 0,
            'riskZeroFillTerminals': int(ob.get('zeroFillTerminals') or 0) if ob else 0,
            'preRepairReexpandUsed': r263_used,
            'riskTrancheGenerationUsed': risk_used,
            'riskTrancheKeyCount': len(risk_keys),
            'riskTrancheFilledKeyCount': risk_filled,
            'riskDebtOutstanding': risk_debt,
            'overflowObligationLive': overflow_live,
            'overflowObligationOutstanding': overflow_out,
            'repairLotCount': len(repair_lots),
            'repairLotRemainingQty': sum(float(x.get('remaining') or 0.0) for x in repair_lots),
            'coreServiceCarrierCount': len(service),
            'coreServiceHeldAuthority': service_held,
            'coreServiceSpentAuthority': service_spent,
            'availableCoreServiceAuthority': core_auth,
        }
        out.update(self._fill_context(t, gen))
        return out

    def _decision_row(self, t, sid, key, o):
        row = super()._decision_row(t, sid, key, o)
        ctx = self._lifecycle_context(int(t), str(o['side']))
        row.update({f'lc_{k}': v for k, v in ctx.items()})
        return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix='lane_g_reanchor_lifecycle_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co = {int(x['marketId']): x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:
                (tmp / f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows = []
        markets = []
        for m in mids:
            sim = ReanchorLifecycleContextDumpSim(tmp / f'{m}.json.xz', 1, 4)
            try:
                r = sim.run_dump(co[m]['winner'])
            finally:
                sim.close()
            for x in r['rows']:
                rows.append({'marketId': m, **x})
            markets.append({'marketId': m, 'decisionCount': len(r['rows']), 'correct': r['correct'], 'submits': r['submits'], 'fills': r['fills']})
            print(json.dumps(markets[-1], ensure_ascii=False), flush=True)
        out = {
            'version': 'LANE_G_R264_REANCHOR_LIFECYCLE_CONTEXT_DUMP_V1_20260907',
            'researchOnly': True,
            'behaviorChange': False,
            'markets': markets,
            'rows': rows,
            'gates': {'correctnessPass': all(x['correct'] for x in markets), 'decisionCount': len(rows)},
            'boundary': [
                'exact frozen R2.64 behavior; instrumentation only',
                'persistent thesis is first physically submitted PROBE_CORE side and diagnostic only',
                'lifecycle features are current or accumulated strict-past state at reanchor decision',
                'no future fill/cancel/priority-loss/winner/PnL/Target future action in rows',
                'no fresh/no runtime rule/no dream fill/no 8781',
            ],
        }
        op = (Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'gates': out['gates']}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
