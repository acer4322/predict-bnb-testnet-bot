"""Behavior-inert native scheduler probe before Active Repair commitment."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_root_dual_legal_label_smoke_v1 as lab
from tools import run_root_btc5m_source_smoke_v1 as util


class ProposalReached(Exception):
    pass


def passive_probe(sim, base, t, quotes, end):
    """Run original scheduler on copied state, stop BEFORE any transport."""
    clone = lab.preview_copy(sim)
    clone.__class__ = base
    found = {}
    counters = ['veto', 'splitBlocks', 'role_budget_blocks', 'r239', 'r247']
    before = {k: dict(getattr(clone, k)) for k in counters}

    def capture_role(self, tt, side, role, p, q, proj, split=None):
        found['candidate'] = dict(t=tt, side=side, role=role, price=p, qty=q,
                                  projection=proj, split=copy.deepcopy(split))
        return base._submit_role_v8(self, tt, side, role, p, q, proj, split)

    def capture_transport(self, tt, side, p, q):
        candidate = found.get('candidate')
        assert candidate and (tt, side, p, q) == (
            candidate['t'], candidate['side'], candidate['price'], candidate['qty'])
        found['transportReached'] = True
        raise ProposalReached()

    clone._submit_role_v8 = types.MethodType(capture_role, clone)
    clone.submit = types.MethodType(capture_transport, clone)
    try:
        base._open_one_option(clone, t, copy.deepcopy(quotes), end)
    except ProposalReached:
        pass
    found['counterDelta'] = {
        k: {n: v-before[k].get(n, 0) for n, v in dict(getattr(clone, k)).items()
            if v != before[k].get(n, 0)} for k in counters}
    found['status'] = ('TRANSPORT_PROPOSAL_ONLY' if found.get('transportReached')
                       else 'NO_NATIVE_TRANSPORT_PROPOSAL')
    return util.clean(found)


def matched_repair(w):
    p = w['passive']; c = p.get('candidate') or {}; s = c.get('split') or {}
    return bool(w['activeAccepted'] and p.get('transportReached') and
                c.get('side') == w['activeSide'] and
                c.get('role') in {'ECONOMIC_CORE', 'SATELLITE_REPAIR'} and
                s.get('overflowQty', float('inf')) <= 1e-9 and
                s.get('repairQty', 0) >= c.get('qty', float('inf'))-1e-9)


def main():
    assert Path.cwd().resolve() == ROOT
    assert not (ROOT/'.lan_worker_v1/staging').exists()
    manifest = json.loads((ROOT/'AUDIT_MANIFEST.json').read_text())
    for rel, sha in manifest['files'].items():
        assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() == sha, rel
    sys.path.insert(0, 'C:/BTC5M-worker/.tmp/hftbacktest_244')
    mod = importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    base = mod.BoundedCoreServiceFavorableRecycleSim
    prior = json.loads((ROOT/'prior.json').read_text())
    refs = {r['marketId']: r for r in prior['rows'] if r['branch'] == 'N'}
    public = json.loads((ROOT/'dual_public.json').read_text())
    output = Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    rows = []; be = 0; start = time.time()

    class Audit(base):
        def __init__(self, tape, source):
            self._lab = {'observer': util.Observer(source), 'witnesses': [],
                         'attempts': 0, 'accepted': 0, 'matched': 0}
            super().__init__(tape, 1, 4)

        def process(self, t):
            r = super().process(t)
            self._lab['observer'].post_process(self, t)
            return r

        def _submit_active(self, t, side, role, q, score, diag):
            d = self._lab; d['attempts'] += 1
            prefix = lab.signature(self); cash = util.native_state(self)
            qv = mod.r1.v2.base.quotes(self.book)
            end = int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
            p = passive_probe(self, base, t, qv, end)
            assert lab.signature(self) == prefix, 'preview source mutation'
            assert util.native_state(self) == cash, 'preview backend mutation'
            ob = d['observer']; pub = util.latest_public(ob.public, ob.times, t)
            assert pub is None or pub['availableMs'] < t
            key = f'{side}_{self.n}'
            w = dict(t=t, prefixSignature=prefix, state=util.clean(util.state(self)),
                     activeSide=side, activeRole=role, activeQty=q, quotes=qv,
                     coreContext=self._coreServiceContext,
                     pendingCoreEvidence=copy.deepcopy(self.pendingCoreEvidence),
                     pendingFailure=util.clean(self.pendingFailure),
                     public=pub, passive=p)
            ok = super()._submit_active(t, side, role, q, score, diag)
            w['activeAccepted'] = bool(ok)
            w['activeOwner'] = key if ok else None
            w['activeReceipt'] = next((util.clean(x) for x in reversed(self.executionDecisions)
                if x.get('event') == 'MS4_R2_ACTIVE_REPAIR_SUBMIT' and x.get('key') == key), None) if ok else None
            w['matchedRepairProposal'] = matched_repair(w)
            d['accepted'] += int(bool(ok)); d['matched'] += int(w['matchedRepairProposal'])
            if len(d['witnesses']) < 12: d['witnesses'].append(w)
            return ok

    try:
        for mid in manifest['markets']:
            be += 1
            (output/'BE_ACCOUNTING.json').write_text(json.dumps({'attemptedBE': be, 'marketId': mid}))
            sim = Audit(ROOT/f'tapes/{mid}.json.xz', public[str(mid)])
            try:
                r = sim.run_r247('UP'); d = sim._lab; ob = d['observer']
                sig = lab.signature(sim, r)
                correct = (sig == refs[mid]['signature'] and
                    util.cash_check(sim.inv, sim.cost, util.native_state(sim))['pass'] and
                    all(v <= 1e-8 for v in ob.max_error.values()) and r['r247ServiceCorrectnessPass'] and
                    r['unauthorizedOverflowQty'] <= 1e-9 and r['repairQuotaExcessMax'] <= 1e-9 and
                    sim.max_simultaneous_slots <= 4)
                row = dict(marketId=mid, signature=sig, correct=bool(correct),
                           attempts=d['attempts'], accepted=d['accepted'], matched=d['matched'],
                           witnessCoverageComplete=d['attempts'] <= 12, witnesses=d['witnesses'],
                           fills=sim.fills, submits=sim.submits, cashMaxError=ob.max_error)
                rows.append(row)
                print(json.dumps({k:v for k,v in row.items() if k != 'witnesses'}), flush=True)
                assert correct, 'source/substrate/full-policy parity'
            finally:
                sim.close()
        accepted = sum(r['accepted'] for r in rows); matched = sum(r['matched'] for r in rows)
        verdict = ('NOT_EXERCISED_ACTIVE' if not accepted else
                   'NO_MATCHED_REPAIR_SUPPORT' if not matched else 'PRE_ACTIVE_PROPOSAL_SUPPORT_ONLY')
        result = dict(verdict=verdict, rows=rows)
    except Exception as e:
        result = dict(verdict='CORRECTNESS_STOP', error=type(e).__name__+': '+str(e), rows=rows)
    result.update(attemptedBE=be, elapsedSeconds=time.time()-start, behaviorChanges=0,
                  modelsTrained=0, freshUsed=0, economicValue='NOT_ESTIMATED', promotion=False)
    blob = json.dumps(util.clean(result), indent=2).encode()
    assert len(blob) <= 512*1024
    (output/'COMPACT.json').write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k != 'rows'}), flush=True)
    if result['verdict'] == 'CORRECTNESS_STOP': raise SystemExit(2)


if __name__ == '__main__':
    main()
