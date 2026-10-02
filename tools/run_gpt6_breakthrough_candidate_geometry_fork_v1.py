"""Research-only current V3B same-prefix price/quantity ablation; no policy authority.

Reuses the existing native-prefix fork. Each branch changes one decision only,
then resumes unchanged V3B. Neither Target nor settlement enters the decision.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path.cwd()))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--specs', required=True)
    ap.add_argument('--source-dir', default='..')
    ap.add_argument('--intent-test', action='store_true', help='Bounded structural quote-intent falsification, with native-price placebo')
    a = ap.parse_args()
    source = Path(a.source_dir)
    f = load('geometry_native_fork', source/'run_management_mainline_v3b_role_switch_global_event_fork_v2.py')
    clean = load('geometry_clean_state', source/'extract_management_v1_v3b_preaction_rows_v1.py')
    branches = ('NATIVE', 'NEXT_REPAIR', 'NEXT_REEXPAND', 'REPAIR_DOUBLE',
                'EXPAND_DOUBLE', 'REPAIR_PAIR_ONLY', 'EXPAND_INSIDE', 'WAIT')
    if a.intent_test:
        branches = ('NATIVE','NEXT_REEXPAND','EXPAND_INSIDE','EXPAND_INSIDE_INTENT','EXPAND_NATIVE_INTENT')
    specs = json.loads(Path(a.specs).read_text(encoding='utf-8'))['states']

    class Fork(f.RoleSwitchFork):
        def __init__(self, tape, spec, branch):
            super().__init__(tape, spec, branch)
            self._end_ms = int(self.payload['market']['window_end_ms'])
            self.candidate_pre = None
            self.training_rows = []
            self.horizon = None
            self.open_attempts = self.no_submit_attempts = 0
            self.actual_last_process = None
            self.intent_preserve_count = 0
            self.intent_preserve_first = None

        def _request_cancel(self, t, sid, reason):
            # Only the intervention carrier owns this new quote representation.
            # A quote born inside the spread need not occur in historical public depth.
            # Preserve it only while still strictly inside the *current* spread and Pair-legal.
            # There is no dwell, cooldown, expiry extension, or protection after loss of validity.
            if self.branch in ('EXPAND_INSIDE_INTENT','EXPAND_NATIVE_INTENT') and reason=='SATELLITE_FRONTIER_REANCHOR':
                key=self.slot_key.get(sid)
                pre=self.candidate_pre
                if key==self.branchKey and pre and pre['sideBid']<pre['price']<pre['sideAsk']:
                    qv=f.base.v2.base.quotes(self.book)
                    p=pre['price'];side=pre['side']
                    if qv and float(qv[side]['bid'])<p<float(qv[side]['ask']) and self._pair_ok(side,p):
                        self.intent_preserve_count+=1
                        if self.intent_preserve_first is None:
                            self.intent_preserve_first={'t':int(t),'key':key,'price':p,'bid':float(qv[side]['bid']),'ask':float(qv[side]['ask'])}
                        return False
            return super()._request_cancel(t,sid,reason)

        def _submit_role(self, t, side, role, p, q, proj, source):
            capture = int(t) == int(self.spec['t']) and self.candidate_pre is None
            if capture:
                self.candidate_pre = clean.PreActionTrainingTraceSim._state_row(
                    self, t, side, role, p, q, 'PASSIVE', f'{side}_{self.n}', source)
            ok = super()._submit_role(t, side, role, p, q, proj, source)
            if capture and ok:
                self.training_rows.append(copy.deepcopy(self.candidate_pre))
            return ok

        def _force_one(self, t, qv, kind):
            if kind=='EXPAND_NATIVE_INTENT':
                return super()._force_one(t,qv,'NEXT_REEXPAND')
            if kind=='EXPAND_INSIDE_INTENT':
                kind='EXPAND_INSIDE'
            if kind == 'WAIT':
                return {'ok': True, 'submitted': False, 'reason': 'ONE_DECISION_OPPORTUNITY_COST_REFERENCE'}
            if kind in ('NEXT_REPAIR', 'NEXT_REEXPAND'):
                return super()._force_one(t, qv, kind)
            repair = kind.startswith('REPAIR')
            side = str(self.spec['weakSide'] if repair else self.spec['expandSide'])
            role = self._repair_role(side) if repair else 'SATELLITE_EXPAND'
            if self.q_pending_active is not None or len(self.slot_key) >= self.max_slots:
                return {'ok': False, 'reason': 'STRUCTURAL_UNAVAILABLE'}
            self.q_arm = self._forced_arm(t, qv, side, role) if repair else None
            try:
                if kind == 'REPAIR_PAIR_ONLY':
                    self.q_arm = None
                    c = f.base.MinimalPairRoleSim._candidate_from_levels(self, side, True, False)
                else:
                    c = self._candidate_from_levels(side, True, False)
                if c is None:
                    return {'ok': False, 'reason': 'NO_CANDIDATE'}
                p, q, proj = c
                if kind.endswith('DOUBLE'):
                    q *= 2.0
                if kind == 'EXPAND_INSIDE':
                    p = round(float(qv[side]['ask']) - f.v3b.TICK, 10)
                    q = 1.0/p if p > 0 else 0.0
                    if not float(qv[side]['bid']) < p < float(qv[side]['ask']) or not self._pair_ok(side, p):
                        return {'ok': False, 'reason': 'NO_PAIR_LEGAL_INSIDE_PRICE'}
                    if any(abs(float(x)-p) < f.EPS for x in self._used_prices(side)):
                        return {'ok': False, 'reason': 'INSIDE_PRICE_ALREADY_LIVE'}
                if not (0 < p < 1 and q > 0 and q <= 12+f.EPS and p*q >= 1-f.EPS):
                    return {'ok': False, 'reason': 'QUANTITY_OR_VENUE_LEGALITY'}
                # Managed repair carrier must not be enlarged past its serviceable FIFO debt.
                if repair and self.q_arm and 'passivePrice' in self.q_arm and q > self._aggregate_for_repair_side(side)+f.EPS:
                    return {'ok': False, 'reason': 'DOUBLE_EXCEEDS_MANAGED_DEBT'}
                n = int(self.n)
                ok = bool(self._submit_role(t, side, role, p, q, proj, 'GPT6_GEOMETRY_ABLATION_V1'))
                self.branchKey = f'{side}_{n}' if ok else None
                self.branchRole = role if ok else None
                return {'ok': ok, 'side': side, 'role': role, 'price': p, 'qty': q,
                        'key': self.branchKey, 'submitted': ok, 'reason': None if ok else 'SUBMIT_FALSE'}
            finally:
                self.q_arm = None

        def _open_one_option(self, t, qv, end):
            self.open_attempts += 1
            before = self.submits
            result = super()._open_one_option(t, qv, end)
            self.no_submit_attempts += int(self.submits == before)
            return result

        def process(self, t):
            result = super().process(t)
            self.actual_last_process = int(t)
            if self.seen and self.horizon is None and int(t) >= int(self.spec['t'])+5000:
                self.horizon = self._payoff_state(t)
            return result

    output = Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    rows = []
    with tempfile.TemporaryDirectory(prefix='gpt6_geometry_') as td:
        with zipfile.ZipFile(a.bundle) as z:
            cohort = {int(r['marketId']): r for r in json.loads(z.read('cohort.json'))['rows']}
            for s in specs:
                z.extract(f"tapes/{int(s['marketId'])}.json.xz", td)
        for i, s in enumerate(specs, 1):
            mid = int(s['marketId'])
            tape = Path(td)/'tapes'/f'{mid}.json.xz'
            native = f.v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:
                reference = native.run_qty('__UNSCORED__')
            finally:
                native.close()
            pair = {'marketId': mid, 'stateSpec': s, 'branches': {}}
            for b in branches:
                sim = Fork(tape, s, b)
                try:
                    raw = sim.run_branch()
                    terminal = f.terminal(raw, s)
                    # This value is appended only after simulation; never seen by action generation.
                    terminal['winnerPnlPosthoc'] = float(raw['raw']['upQty'] if cohort[mid]['winner']=='UP' else raw['raw']['downQty'])-float(raw['raw']['buyNotional'])
                    terminal['gross'] = terminal['upQty']+terminal['downQty']
                    lots = raw['raw'].get('quantityResponsibilities') or []
                    activity = {k: raw['raw'].get(k) for k in ('roleSubmits','roleFills','roleFillQty')}
                    activity.update({'completedResponsibilities': sum(float(x['remainingQty'])<=f.EPS for x in lots),
                        'partiallyPaidOpenResponsibilities': sum(float(x.get('paidQty') or 0)>f.EPS and float(x['remainingQty'])>f.EPS for x in lots),
                        'responsibilities': len(lots), 'openAttempts': sim.open_attempts,
                        'openAttemptWithoutSubmitFraction': sim.no_submit_attempts/max(1,sim.open_attempts)})
                    labels = clean.PreActionTrainingTraceSim.finalize_labels(sim)
                    intervention = raw['intervention'] or {}
                    available = b=='NATIVE' or bool((intervention.get('forced') or {}).get('ok'))
                    pair['branches'][b] = {'available': available, 'prefixDigest':raw['prefixDigest'],
                        'prefixState':raw['prefixState'], 'intervention': intervention,
                        'preAction':sim.candidate_pre, 'physicalLabel': labels[0] if len(labels)==1 else None,
                        'stateAtFirstClockAtOrAfter5s':sim.horizon,
                        'firstGlobalEvent':raw['firstGlobalEvent'], 'carrierResolution':raw['branchResolution'],
                        'terminal':terminal, 'activity':activity,
                        'intentPreserveCount':sim.intent_preserve_count,'intentPreserveFirst':sim.intent_preserve_first,
                        'observedThrough5s': sim.actual_last_process is not None and sim.actual_last_process>=int(s['t'])+5000,
                        'triggered':raw['seen']}
                    if b=='NATIVE':
                        pair['nativeBaselineParity'] = clean._eq(clean._physical_core(raw['raw']), clean._physical_core(reference))
                finally:
                    sim.close()
            bb = pair['branches']
            control = bb['NATIVE']
            same = bb.get('NEXT_REPAIR' if s['nativeClass']=='REPAIR' else 'NEXT_REEXPAND',control)
            checks = {'nativeBaselineParity': pair['nativeBaselineParity'],
                'sameClassTerminalParity': f.same_terminal(control['terminal'],same['terminal']),
                'allPrefixParity':len({x['prefixDigest'] for x in bb.values()})==1 and control['prefixDigest'] is not None,
                'allTriggered':all(x['triggered'] for x in bb.values()),
                'allLedgerClean':all(not x['terminal']['ledgerViolations'] for x in bb.values()),
                'max4':all(x['terminal']['maxSlots']<=4 for x in bb.values()),
                'allHorizonsObserved':all(x['observedThrough5s'] for x in bb.values()),
                'candidateExcludedFromPreState':all(x['preAction'] is None or x['preAction']['candidateAlreadyInState']==0 for x in bb.values()),
                'ordinaryRolesAvailable':all(bb[b]['available'] for b in ('NEXT_REPAIR','NEXT_REEXPAND') if b in bb)}
            if a.intent_test:
                checks['nativePriceIntentPlaceboParity']=f.same_terminal(bb['NEXT_REEXPAND']['terminal'],bb['EXPAND_NATIVE_INTENT']['terminal'])
                checks['nativePricePlaceboNeverPreserved']=bb['EXPAND_NATIVE_INTENT']['intentPreserveCount']==0
            pair.update(checks=checks,correctnessPass=all(checks.values()))
            rows.append(pair)
            # Persist each completed seam, allowing safe collection after interruption.
            (output/'rows.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'correctness':pair['correctnessPass'],
                              'unavailable':[b for b,x in bb.items() if not x['available']]}),flush=True)
    out = {'version':'GPT6_BREAKTHROUGH_QUOTE_INTENT_FORK_V1' if a.intent_test else 'GPT6_BREAKTHROUGH_CANDIDATE_GEOMETRY_FORK_V1','researchOnly':True,'runtimeAuthority':False,
        'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'seams':len(rows),'runs':len(rows)*len(branches),
        'rows':rows,'sha256':{'script':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'specs':hashlib.sha256(Path(a.specs).read_bytes()).hexdigest()},
        'limitations':['one intervention with native suffix, not learned repeated policy',
            'unavailable candidates retained but excluded from effect estimates; no fallback policy interpretation',
            'ACTIVE unavailable at these no-pending-Active seams under existing protected handoff; no artificial Active authority',
            'half-native notional is below venue minimum, so double quantity probes sizing nonlinearity, not a smaller legal policy',
            '5s state sampled at first process clock at or after horizon, exact carrier labels separately bounded to 5s',
            'open-attempt no-submit fraction is not eligible-opportunity HOLD rate',
            'completed responsibilities are exact FIFO payments, not full controller lifecycle cycle counts',
            'no Target inputs; inherited native receipt semantics, not a new source/receipt timestamp certification',
            'consumed development data only; no untouched promotion; no live8781']}
    (output/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({k:out[k] for k in ('seams','runs','allCorrectnessPass')}),flush=True)


if __name__ == '__main__':
    main()
