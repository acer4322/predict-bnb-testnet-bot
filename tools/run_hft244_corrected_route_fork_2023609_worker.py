"""Bounded consumed N/S/P identification on corrected receipts; LAN only."""
from collections import deque
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

BUNDLE = Path(__file__).resolve().parent
ROOT = Path('C:/BTC5M-worker/.tmp/hft244_corrected_route_fork_2023609_20260910_v1')
FROZEN = Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
BACKEND = Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA = '7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
STAGE_SHA = 'da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fifo(receipts, roles, expected):
    un = {'UP': deque(), 'DOWN': deque()}
    pairs = []
    cost = 0.
    for seq, r in enumerate(receipts, 1):
        assert r['sequence'] == seq
        side = 'UP' if r['side'] == 1 else 'DOWN'
        opp = 'DOWN' if side == 'UP' else 'UP'
        price = r['price'] if side == 'UP' else 1-r['price']
        left = r['qty']; cost += left*price
        while left > 1e-9 and un[opp]:
            e = un[opp][0]; q = min(left, e['qty'])
            pairs.append(dict(openOwner=e['owner'], closeOwner=roles[seq]['key'],
                              qty=q, openPrice=e['price'], closePrice=price,
                              grossMargin=q*(1-e['price']-price)))
            left -= q; e['qty'] -= q
            if e['qty'] <= 1e-9: un[opp].popleft()
        if left > 1e-9:
            un[side].append(dict(owner=roles[seq]['key'], qty=left, price=price))
    rest = [dict(side=s, **e) for s, rows in un.items() for e in rows]
    margin = sum(x['grossMargin'] for x in pairs)
    rcost = sum(x['qty']*x['price'] for x in rest)
    endpoints = {s: margin-rcost+sum(x['qty'] for x in rest if x['side'] == s) for s in un}
    assert abs(cost-expected['cost']) <= 1e-8
    assert all(abs(endpoints[s]-expected[s]) <= 1e-8 for s in un)
    return dict(matchedMargin=margin, matchedQty=sum(x['qty'] for x in pairs),
                remainingCost=rcost, remaining=rest, pairs=pairs, endpoints=endpoints)


def classify(s, p):
    delta = {k: p[k]-s[k] for k in ['UP','DOWN','fills','submits','qty','cost',
             'scopeCompletions','scopeFlips','peakAbsNet','netIntegralShareMs']}
    if all(abs(delta[k]) <= 1e-8 for k in ['UP','DOWN']):
        return dict(verdict='NO_TERMINAL_ROUTE_EFFECT', delta=delta)
    better = ('P' if all(delta[k] >= -1e-8 for k in ['UP','DOWN']) else
              'S' if all(delta[k] <= 1e-8 for k in ['UP','DOWN']) else None)
    if better is None: return dict(verdict='ROUTE_ENDPOINT_TRADEOFF', delta=delta)
    hi, lo = (p,s) if better == 'P' else (s,p)
    activity = all(hi[k] >= lo[k]-1e-8 for k in ['fills','qty','scopeCompletions','scopeFlips'])
    return dict(verdict='ROUTE_GROSS_DOMINANCE_CANDIDATE_SMOKE_ONLY' if activity else
                'ROUTE_DOMINANCE_WITH_ACTIVITY_REDUCTION', dominanceDirection=better,
                activityNondecrease=activity, delta=delta)


def main():
    result_dir = Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    if '--child' not in sys.argv:
        helper = Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec = importlib.util.spec_from_file_location('bound', helper)
        bound = importlib.util.module_from_spec(spec); spec.loader.exec_module(bound)
        bound.bounded('route-fork', [sys.executable, str(Path(__file__).resolve()), '--child'], 180)
        return
    assert not ROOT.exists(), 'immutable root already exists'
    package = json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
    for f in package['files']: assert digest(BUNDLE/f['name']) == f['sha256']
    assert digest(FROZEN/'STAGE_MANIFEST.json') == STAGE_SHA
    binary = BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert digest(binary) == NATIVE_SHA
    reference_path = Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-owner-integration-2023609-20260910-v2/COMPACT.json')
    assert digest(reference_path) == package['referenceSha256']
    reference = json.loads(reference_path.read_text())
    verified = {}
    for rel, sha in json.loads((FROZEN/'STAGE_MANIFEST.json').read_text())['files'].items():
        if not (rel.endswith('.py') or rel in ['tapes/2023609.json.xz','public/2023609.json']): continue
        p = (FROZEN/rel).resolve()
        assert p.is_relative_to(FROZEN) and p.stat().st_size <= 2*1024**2
        assert digest(p) == sha
        dest = ROOT/rel; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dest)
        verified[rel] = sha
    for name in ['hft244_receipt_adapter_v1.py','hft244_research_owner_accounting_v1.py']:
        shutil.copy2(BUNDLE/name, ROOT/'tools'/name)
    (result_dir/'INPUT_PROVENANCE.json').write_text(json.dumps(dict(
        nativeSha256=NATIVE_SHA, stageSha256=STAGE_SHA, files=verified, package=package), indent=2))
    sys.path.insert(0, str(BACKEND)); import hftbacktest as h
    assert Path(h.__file__).resolve().parent == (BACKEND/'hftbacktest').resolve()
    sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
    from tools.run_root_native_composite_handback_v1 import make_fork, lab, util
    from tools.hft244_research_owner_accounting_v1 import install
    mod = importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    install(mod.v2.base, mod.v2, mod.r1.v82.v8, mod.v2.base.ex, binary, preview_lab=lab)
    Fork = make_fork(mod.BoundedCoreServiceFavorableRecycleSim, mod)

    class Checked(Fork):
        def _submit_active(self, t, side, role, q, score, diag):
            trace = self._lab.setdefault('activeTrace', [])
            assert len(trace) < 256, 'active trace cap'
            trace.append(dict(t=t, signature=lab.signature(self), native=util.native_state(self)))
            return super()._submit_active(t, side, role, q, score, diag)

        def process(self, t):
            start = len(self.splitEvents); value = super().process(t)
            assert util.cash_check(self.inv, self.cost, util.native_state(self))['pass']
            rows = [e for e in self.splitEvents[start:] if e.get('event') == 'ROLE_FILL_SPLIT']
            assert len(rows) == len(self._receipt_delta_rows)
            assert len(self._receipt_ledger.seen) <= 100, 'receipt cap'
            for e,r in zip(rows, self._receipt_delta_rows):
                assert e['key'] == r['key'] and e['receiptSequence'] == r['sequence']
                assert abs(e['price']-r['contractPrice']) <= 1e-12
                if e['role'] in {'ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND'}:
                    assert abs(e['repairAllocated']+e['overflowRealized']-e['fillInc']) <= 1e-8
            assert self.unauthorizedOverflowQty <= 1e-9 and self.repairQuotaExcessMax <= 1e-9
            return value

    for name, module in list(sys.modules.items()):
        if name.startswith('tools.') and getattr(module, '__file__', None):
            assert Path(module.__file__).resolve().is_relative_to(ROOT), name
    rows=[]; be=0; started=time.monotonic()
    result = dict(verdict='RUNNING', rows=rows, marketId=2023609, nativeSha256=NATIVE_SHA,
                  freshUsed=0, training=False, promotion=False, fullNetCost='UNRESOLVED',
                  generalization='NOT_IDENTIFIED', historicalLineageBefore=46)

    def save():
        result.update(attemptedBE=be, historicalLineageAfter=46+be, elapsedSeconds=time.monotonic()-started)
        blob = json.dumps(util.clean(result), indent=2).encode()
        assert len(blob) <= 512*1024, 'output cap'
        (result_dir/'COMPACT.json').write_bytes(blob)

    try:
        source=json.loads((ROOT/'public/2023609.json').read_text())
        for branch in ['N','S','P']:
            be += 1; sim = None
            (result_dir/'BE_ACCOUNTING.json').write_text(json.dumps(dict(attemptedBE=be, branch=branch, historicalLineageBefore=46)))
            try:
                sim = Checked(ROOT/'tapes/2023609.json.xz', branch, source)
                output=sim.run_r247('UP')  # endpoint convention, not realized winner
                d=sim._lab; ob=d['observer']; receipts=list(sim._receipt_ledger.seen.values())
                roles={e['receiptSequence']:e for e in sim.splitEvents if e.get('event')=='ROLE_FILL_SPLIT'}
                assert output['r247ServiceCorrectnessPass'] and sim.max_simultaneous_slots <= 4
                assert all(x <= 1e-8 for x in ob.max_error.values())
                row=dict(branch=branch, signature=lab.signature(sim), native=util.native_state(sim),
                    correctness=True, cashMaxError=ob.max_error, serviceChecks=dict(sim.serviceChecks),
                    UP=sim.inv['UP']-sim.cost, DOWN=sim.inv['DOWN']-sim.cost,
                    cost=sim.cost, qty=sum(sim.inv.values()), fills=sim.fills, submits=sim.submits,
                    scopeCompletions=sim.scopeCompletions, scopeFlips=sim.scopeFlips,
                    peakAbsNet=ob.peak_abs_net, netIntegralShareMs=ob.abs_net_integral_ms,
                    receipts=receipts, roleEvents=list(roles.values()), nativeFees=sim._receipt_ledger.native['fee'],
                    witness=d['witness'], selectedActive=d['selectedActive'], selectedPassive=d['selectedPassive'],
                    activeTrace=d.get('activeTrace', []))
                row['fifo']=fifo(receipts,roles,row)
                selected=d['selectedActive'] if branch=='S' else d['selectedPassive']
                key=selected.get('key') if selected else None
                row['selectedFillQty']=sum(e['fillInc'] for e in roles.values() if e['key']==key)
                rows.append(row); save()
                if branch=='S':
                    n=rows[0]
                    for k in ['signature','native','receipts','activeTrace']:
                        assert util.fingerprint(row[k])==util.fingerprint(n[k]), 'N/S parity: '+k
                    result['shamParity']=True
                    if not d['witness'] or not d['selectedActive']:
                        result['verdict']='NOT_EXERCISED'; break
                if branch=='P':
                    n,s=rows[:2]; w=d['witness']
                    assert util.fingerprint(w)==util.fingerprint(s['witness']), 'S/P witness mismatch'
                    assert w and d['selectedPassive'], 'missing handback'
                    matches=[i for i,x in enumerate(s['activeTrace']) if x['t']==w['t'] and x['signature']==w['prefixSignature']]
                    assert len(matches)==1, 'ambiguous boundary'
                    idx=matches[0]+1
                    assert util.fingerprint(n['activeTrace'][:idx])==util.fingerprint(s['activeTrace'][:idx])==util.fingerprint(row['activeTrace'][:idx])
                    result['prefixParity']=True
                    for k in ['UP','DOWN','cost','qty','fills','submits','scopeCompletions','scopeFlips','peakAbsNet','netIntegralShareMs','native','selectedPassive']:
                        assert util.clean(row[k])==reference[k], 'P reference mismatch: '+k
                    result['pReferenceParity']=True
                    result.update(classify(s,row))
                    result['deltaMatchedMargin']=row['fifo']['matchedMargin']-s['fifo']['matchedMargin']
            finally:
                if sim is not None: sim.close()
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),errorTrace=traceback.format_exc(limit=7))
    save()
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}), flush=True)
    if result['verdict'] in ['CORRECTNESS_STOP','RUNNING']: raise SystemExit(2)


if __name__ == '__main__': main()
