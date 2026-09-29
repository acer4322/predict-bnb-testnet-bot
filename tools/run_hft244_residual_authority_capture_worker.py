"""One behavior-inert S authority/counter capture; LAN only."""
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
ROOT = Path('C:/BTC5M-worker/.tmp/hft244_residual_authority_capture_20260910_v1')
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
        bound.bounded('authority-capture', [sys.executable, str(Path(__file__).resolve()), '--child'], 180)
        return
    assert not ROOT.exists(), 'immutable root already exists'
    package = json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
    for f in package['files']: assert digest(BUNDLE/f['name']) == f['sha256']
    assert digest(FROZEN/'STAGE_MANIFEST.json') == STAGE_SHA
    binary = BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert digest(binary) == NATIVE_SHA
    reference_path = Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-corrected-route-fork-2023609-20260910-v1/COMPACT.json')
    assert digest(reference_path) == package['referenceSha256']
    reference = next(r for r in json.loads(reference_path.read_text())['rows'] if r['branch']=='S')
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
        def _lab_snapshot(self, t):
            signature=lab.signature(self)
            snap=dict(t=t, state=util.clean(util.state(self)),
                coreActiveMaterialized=self.coreActiveMaterialized,
                debt=self._scope_debt_qty(),
                reservedRepair=self._reserved_repair_quota(self._repair_side()),
                serviceClaim=self._service_claim(),
                availableExpand=self._available_expand_risk_credit(),
                availableCore=self._available_core_service_authority(),
                reservedExpand=self._reserved_current_expand_risk(),
                quotes=util.clean(mod.v2.base.quotes(self.book)),
                counters={k:dict(getattr(self,k)) for k in
                    ['veto','splitBlocks','role_budget_blocks','r247','r246','r26','queueRouting']})
            assert signature==lab.signature(self), 'snapshot mutated policy'
            return snap

        def _open_one_option(self, t, qv, end):
            exercised=self.coreActiveMaterialized and len(self._receipt_ledger.seen)>=2
            before=self._lab_snapshot(t) if exercised else None
            value=super()._open_one_option(t,qv,end)
            if exercised:
                cap=self._lab.setdefault('authorityCapture',dict(calls=0,reasons=[],counterTotals={}))
                cap['calls']+=1
                if 'firstDecision' not in cap: cap['firstDecision']=before
                after=self._lab_snapshot(t)
                cap['lastDecision']=after
                delta={k:{n:v-before['counters'][k].get(n,0) for n,v in vals.items()
                          if v!=before['counters'][k].get(n,0)} for k,vals in after['counters'].items()}
                for k,vals in delta.items():
                    total=cap['counterTotals'].setdefault(k,{})
                    for n,v in vals.items(): total[n]=total.get(n,0)+v
                ident=util.fingerprint(delta)
                seen=self._lab.setdefault('reasonSignatures',set())
                if ident not in seen:
                    seen.add(ident)
                    if len(cap['reasons'])<8:
                        cap['reasons'].append(dict(t=t,end=end,delta=delta,
                            beforeOrderCount=len(before['state']['orders']),
                            afterOrderCount=len(after['state']['orders'])))
            return value

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
            if self.coreActiveMaterialized and len(self._receipt_ledger.seen)>=2 and 'afterService' not in self._lab:
                self._lab['afterService']=self._lab_snapshot(t)
            return value

    for name, module in list(sys.modules.items()):
        if name.startswith('tools.') and getattr(module, '__file__', None):
            assert Path(module.__file__).resolve().is_relative_to(ROOT), name
    rows=[]; be=0; started=time.monotonic()
    result = dict(verdict='RUNNING', rows=rows, marketId=2023609, nativeSha256=NATIVE_SHA,
                  freshUsed=0, training=False, promotion=False, fullNetCost='UNRESOLVED',
                  generalization='NOT_IDENTIFIED', historicalLineageBefore=49)

    def save():
        result.update(attemptedBE=be, historicalLineageAfter=49+be, elapsedSeconds=time.monotonic()-started)
        blob = json.dumps(util.clean(result), indent=2).encode()
        assert len(blob) <= 512*1024, 'output cap'
        (result_dir/'COMPACT.json').write_bytes(blob)

    try:
        source=json.loads((ROOT/'public/2023609.json').read_text())
        for branch in ['S']:
            be += 1; sim = None
            (result_dir/'BE_ACCOUNTING.json').write_text(json.dumps(dict(attemptedBE=be, branch=branch, historicalLineageBefore=49)))
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
                assert util.fingerprint(row)==util.fingerprint(reference), 'S full row reference parity'
                result['referenceParity']=True
                result['capture']=dict(afterService=d.get('afterService'),
                    scheduler=d.get('authorityCapture'),terminal=sim._lab_snapshot(sim._lab['observer'].prev_t))
                exercised=bool(d.get('afterService') and d.get('authorityCapture',{}).get('calls'))
                result['verdict']='AUTHORITY_CAPTURE_PARITY_SUPPORTED' if exercised else 'NOT_EXERCISED'
            finally:
                if sim is not None: sim.close()
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),errorTrace=traceback.format_exc(limit=7))
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in {'rows','capture'}}), flush=True)
    if result['verdict'] in ['CORRECTNESS_STOP','RUNNING']: raise SystemExit(2)


if __name__ == '__main__': main()
