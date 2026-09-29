"""LAN-only data and exact-source method probes. NOT a native HFT or a trainer."""
from __future__ import annotations
import ast
from bisect import bisect_left
from collections import Counter, defaultdict
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

BUNDLE = Path(__file__).resolve().parent
MIDS = [2022527, 2022538, 2022602]
EPS = 1e-9


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(262144), b''):
            h.update(block)
    return h.hexdigest()


def extract_method(filename, name):
    path = BUNDLE / 'source' / filename
    source = path.read_text(encoding='utf-8')
    nodes = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(nodes) == 1, (filename, name)
    node = nodes[0]
    namespace = {'EPS': EPS, 'math': math, 'kprice': lambda p: round(float(p), 10),
                 'v2': SimpleNamespace(kprice=lambda p: round(float(p),10), NO_NEW_EXPOSURE_MS=180000)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name], dict(file=filename, function=name, firstLine=node.lineno,
                                lastLine=node.end_lineno, sourceSha256=sha(path))


def prices(levels):
    result = {}
    for level in levels:
        if isinstance(level, dict):
            p = level.get('price', level.get('p'))
            q = level.get('quantity', level.get('size', level.get('q',0)))
        else:
            p, q = level[0], level[1]
        result[float(p)] = float(q)
    return result


def build_probe():
    methods = {}
    evidence = []
    tasks = [('run_eth_target_grounded_distinct_multislot_v2_smoke.py', '_live_price_levels'),
             ('run_eth_target_grounded_distinct_multislot_v2_smoke.py', '_pair_ok'),
             ('run_eth_role_separated_minimal_pair_safety_smoke.py', '_candidate_from_levels'),
             ('run_eth_role_separated_minimal_pair_safety_smoke.py', '_submit_role'),
             ('run_eth_role_separated_multislot_v3_smoke.py', '_open_one_option')]
    for file, name in tasks:
        method, provenance = extract_method(file, name)
        methods[name] = method
        evidence.append(provenance)

    class Probe:
        # Explicit synthetic empty-inventory/no-pending context. No matching,
        # native receipt, economic authorization, teacher or policy rollout.
        def __init__(self, book, side='UP'):
            self.book = book
            self.un = {'UP': [], 'DOWN': []}
            self.minimal_pair_checks = self.minimal_pair_blocks = 0
            self.veto = Counter()
            self.slot_key = {}
            self.max_slots = 4
            self.serialize_same_side = False
            self.n = 1
            self.key_role = {}
            self.role_submits = Counter()
            self.marginal_pair_credit = defaultdict(float)
            self.marginal_pair_risk_spend = defaultdict(float)
            self.prebase_core_submits = self.prebase_same_side_satellite_submits = 0
            self.slot_history = []
            self.sent = []
            self.side = side
            self.role_calls = 0
            self.role_budget_blocks = Counter()

        def _used_prices(self, side): return set()
        def unmatched_avg(self, side): return None
        def _pair_edge(self, side, p, q): return 0., 0.
        def _state(self): return 'EMPTY', None, None
        def _physical_floor(self): return 0.
        def _live_role_rows(self, side=None): return []
        def _role_decision(self, qv):
            self.role_calls += 1
            return self.side, 'PROBE_CORE', True, False
        def submit(self, t, side, p, q):
            self.sent.append(dict(t=t, side=side, price=p, qty=q))
            self.n += 1
    for name, method in methods.items():
        setattr(Probe, name, method)
    return Probe, evidence


def main():
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'), 'existing LAN job required'
    assert int(os.environ.get('OMP_NUM_THREADS','999')) <= 4
    out = Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    start = time.monotonic()
    result = dict(version='MINIMAL_STUDENT_PREFLIGHT_SMOKE3_V1', verdict='RUNNING',
                  mode='DATA_AND_EXACT_SOURCE_METHOD_PROBE_NOT_HFT', rows=[], tests=[],
                  HFT=0, training=0, liveChanges=0, freshRowsUsed=0,
                  nativeOwnStateCapture='NOT_RUN', policyModified=False)
    try:
        manifest = json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        assert manifest['markets'] == MIDS
        for name, meta in manifest['files'].items():
            path = (BUNDLE/name).resolve()
            assert path.is_relative_to(BUNDLE) and path.stat().st_size == meta['bytes']
            assert sha(path) == meta['sha256'], name
        result['packageManifestSha256'] = sha(BUNDLE/'MANIFEST.json')
        result['sourceManifestSha256'] = manifest['sourceManifestSha256']
        result['sourceCodeChecks'] = manifest['codeChecks']
        result['sizeDriftEvidence'] = manifest['expectedRecentDrift']
        Probe, evidence = build_probe()
        result['methodProvenance'] = evidence
        spec = importlib.util.spec_from_file_location('standalone_sizing_v2', BUNDLE/'source/pair_core_asset_route_sizing_v2.py')
        sizing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sizing)
        tests = result['tests']

        def check(name, cond):
            tests.append(dict(name=name, pass_=bool(cond)))
            assert cond, name

        def valid(asset, route, price, qty):
            try:
                sizing.validate_size(asset,route,price,qty)
                return True
            except ValueError:
                return False

        cases = [('BTC','PASSIVE',.5,18,True), ('BTC','PASSIVE',.5,30,True),
                 ('BTC','PASSIVE',.5,55,True), ('BTC','PASSIVE',.5,100,True),
                 ('BTC','PASSIVE',.5,17,False), ('ETH','PASSIVE',.5,12,True),
                 ('ETH','PASSIVE',.5,11,False), ('BTC','PASSIVE',.05,18,False),
                 ('BTC','PASSIVE',.05,20,True), ('ETH','PASSIVE',.07,12,False),
                 ('BTC','ACTIVE',.2,2,True), ('BTC','ACTIVE',.5,55,True),
                 ('ETH','ACTIVE',.2,2,True), ('ETH','ACTIVE',.5,55,True)]
        for asset,route,p,q,want in cases:
            check(f'sizing:{asset}:{route}:p{p}:q{q}:expected{want}', valid(asset,route,p,q)==want)
        for q in (30.,55.):
            probe=Probe({'bids':{.5:100.},'asks':{.51:100.}})
            ok=probe._submit_role(1,'UP','PROBE_CORE',.5,q,None,'EXPLICIT_TEST_SINK_NOT_NATIVE')
            check(f'legacy_submit_method_preserves_q{q}_NOT_VENUE_ACCEPTANCE',ok and probe.sent[0]['qty']==q)
        probe=Probe({'bids':{.5:100.},'asks':{.51:100.}})
        probe._open_one_option(120000,{},300000)
        check('legacy180s_blocks_before_role',probe.role_calls==0 and probe.veto['LATE_180S']==1)
        probe._open_one_option(119999,{},300000)
        check('legacy_pre180s_emits_fixed_notional1',bool(probe.sent) and abs(probe.sent[-1]['price']*probe.sent[-1]['qty']-1)<1e-12)
        low=Probe({'bids':{.05:100.},'asks':{.96:100.}})
        check('legacy_low_price_rejected_by_12share_filter',low._live_price_levels('UP')==[])
        check('corrected_BTC_005_minimum20',sizing.minimum_passive_quantity('BTC',.05,quantity_step=.01)==20.)

        for mid in MIDS:
            path=BUNDLE/f'input_{mid}.json.gz'
            with gzip.open(path,'rb') as f:
                raw=f.read(8*1024**2+1)
            assert len(raw)<=8*1024**2
            data=json.loads(raw)
            market=data['market'];a=data['targetActions'];parents=data['targetParents'];books=data['books']
            lo,hi=market['window_start_ms'],market['window_end_ms']
            errors=Counter()
            if len({x['source_leg_id'] for x in a})!=len(a):errors['duplicate_fill_id']+=1
            grouped={}
            for x in a:
                if not lo<=x['event_ms']<hi:errors['out_of_window']+=1
                if x['shares']<=0 or not 0<x['price']<1:errors['invalid_quantity_or_price']+=1
                if x['role'] not in ('MAKER','TAKER') or x['side'] not in ('UP','DOWN') or x['quote_type'] not in ('BID','ASK'):
                    errors['invalid_action_enum']+=1
                key=(x['role'],x['side'],x['quote_type'],x['order_hash'])
                g=grouped.setdefault(key,dict(shares=0.,cash=0.,legs=0,first=x['event_ms'],last=x['event_ms']))
                g['shares']+=x['shares'];g['cash']+=x['shares']*x['price'];g['legs']+=1
                g['first']=min(g['first'],x['event_ms']);g['last']=max(g['last'],x['event_ms'])
            max_q=max_cash=0.
            pkeys=set()
            for p in parents:
                key=(p['role'],p['side'],p['quote_type'],p['order_hash']);pkeys.add(key)
                if key not in grouped:errors['missing_parent_fill_group']+=1;continue
                g=grouped[key]
                dq=abs(g['shares']-p['shares']);dc=abs(g['cash']-p['shares']*p['average_price'])
                max_q=max(max_q,dq);max_cash=max(max_cash,dc)
                if dq>1e-8 or dc>1e-8 or g['legs']!=p['fill_legs'] or g['first']!=p['first_event_ms'] or g['last']!=p['last_event_ms']:
                    errors['parent_aggregate_mismatch']+=1
            if set(grouped)!=pkeys:errors['parent_identity_set_mismatch']+=1
            public=sorted((p for p in data['public'] if p['available_ms'] is not None), key=lambda p:(p['available_ms'],p['id']))
            times=[p['available_ms'] for p in public]
            def join_stats(clocks):
                counts=Counter();missing=Counter();ages=[]
                for t in clocks:
                    j=bisect_left(times,t)-1
                    counts['clocks']+=1
                    if j<0:counts['no_strict_past_public']+=1;continue
                    p=public[j]
                    counts['joined']+=1
                    if not p['available_ms']<t:counts['clock_violation']+=1
                    ages.append(t-p['available_ms'])
                    for k,v in p['features'].items():
                        if v is None:missing[k]+=1
                return dict(**dict(counts),clockViolation=counts['clock_violation'],
                    missingFeatureCounts=dict(missing),maxRecordedAgeMs=max(ages) if ages else None)
            book_clocks=[int(b['received_ms']) for b in books if lo<=b['received_ms']<hi]
            event_clocks=sorted({x['event_ms'] for x in a})
            generated=invalid=0;no_candidate=0;maxqty=0.;minqty=None;samples=[]
            early_books=0
            for b in books:
                t=int(b['received_ms'])
                if not lo<=t<hi-180000:continue
                if b['source_ms'] is not None and b['source_ms']>t:continue
                early_books+=1
                for side in ('UP','DOWN'):
                    probe=Probe({'bids':prices(b['bids']),'asks':prices(b['asks'])},side)
                    candidate=probe._candidate_from_levels(side)
                    if candidate is None:no_candidate+=1;continue
                    p,q,_=candidate;generated+=1
                    maxqty=max(maxqty,q);minqty=q if minqty is None else min(minqty,q)
                    if not valid('BTC','PASSIVE',p,q):invalid+=1
                    if len(samples)<2:samples.append(dict(t=t,side=side,price=p,qty=q,notional=p*q))
            maker=[p for p in parents if p['role']=='MAKER']
            original_fields=[n for n in manifest['sourceMetadata']['target_parents.parquet']['columns']
                             if n in ('requested_qty','original_qty','original_quantity','leaves_qty','terminal_status')]
            row=dict(marketId=mid,window=[lo,hi],sourceCapturePass=not bool(errors),
                tape=data['tape'],sourceErrors=dict(errors),targetFillLegs=len(a),
                targetFillLegsByRoute=dict(Counter(x['role'] for x in a)),targetObservedOrders=len(parents),
                makerObservedOrders=len(maker),makerCumulativeFillMean=sum(p['shares'] for p in maker)/len(maker) if maker else None,
                makerOrdersAbove18=sum(p['shares']>18+EPS for p in maker),
                makerMultiFillOrders=sum(p['fill_legs']>1 for p in maker),
                maxParentQuantityError=max_q,maxParentCashError=max_cash,
                originalQuantityFields=original_fields,originalRequestedQuantityCoverage=0,
                zeroFillOrderCoverage='UNIDENTIFIED_FROM_MATCHED_FILLS',
                publicRows=len(data['public']),publicMissingClockRows=len(data['public'])-len(public),
                publicJoinAtBookObservationClock=join_stats(book_clocks),
                publicJoinAtTargetFillClock_OBSERVATION_ONLY=join_stats(event_clocks),
                methodProbe=dict(context='EMPTY_INVENTORY_NO_PENDING_REAL_TOP5_BOOK_NOT_HFT',
                    earlyBookRows=early_books,generatedCandidates=generated,noCandidate=no_candidate,
                    candidatesViolatingCurrentBTCMinimum=invalid,minLegacyQty=minqty,maxLegacyQty=maxqty,
                    examples=samples),nativeOwnStateCapture='NOT_RUN',
                filteredInputSha256=sha(path))
            result['rows'].append(row)
            print(json.dumps(dict(marketId=mid,sourcePass=row['sourceCapturePass'],fills=len(a),
                parents=len(parents),candidates=generated,invalidBTC=invalid)),flush=True)
        all_source=all(r['sourceCapturePass'] for r in result['rows'])
        result['gates']=dict(selectedSourceIntegrity='PASS' if all_source else 'FAIL',
            originalOrderSizingTeacher='BLOCKED_MISSING_ORIGINAL_QTY_AND_TERMINAL',
            legacyStudentSizeDomain='FAIL_FIXED_NOTIONAL1_AND_INHERITED_MAX12',
            exactSubmitMethodQuantityTransport='PASS_METHOD_ONLY_NOT_NATIVE',
            standaloneCorrectedSizing='PASS_COMPONENT_ONLY_NOT_INTEGRATED',
            nativeOwnClosedLoopStateActionReceiptNextState='NOT_RUN_PRECONDITION_FAILED',
            promotion='NOT_AUTHORIZED')
        result['verdict']='BLOCKED_PRETRAINING_SIZE_DOMAIN_AND_ORIGINAL_ACTION_LABELS'
        result['nextAction']='Research-local explicit requested-quantity seam; remove fixed-ticket size from book-domain construction without enlarging grants or bypassing declared Pair/slot/180s rules; then qualified native own-state smoke. Preserve original qty UNKNOWN and cumulative fill separately.'
        result['limits']=['No native replay, matching, queue result or profitability evidence.',
            'AST method probes use a declared synthetic inventory context, not a three-market OUR rollout.',
            'Source joins at book/Target clocks are not native OUR decision-state capture.',
            'Public availability is based on saved clocks; original collector provenance unverified.',
            'Recent30/55 observed modes are not original-size labels or permission to enlarge capital.',
            'Active has separate size legality but remains unimplemented in this passive baseline.']
    except Exception as exc:
        result.update(verdict='PREFLIGHT_EXECUTION_ERROR',error=type(exc).__name__+': '+str(exc),
                      traceback=traceback.format_exc(limit=8))
    result['elapsedSeconds']=time.monotonic()-start
    result['testCount']=len(result['tests'])
    result['testsPassed']=sum(x['pass_'] for x in result['tests'])
    assert not any(x.startswith('hftbacktest') for x in sys.modules), 'HFT must not be imported'
    raw=json.dumps(result,indent=2,allow_nan=False).encode()
    assert len(raw)<128*1024
    (out/'COMPACT.json').write_bytes(raw)
    print(json.dumps(dict(verdict=result['verdict'],testsPassed=result['testsPassed'],
                          tests=result['testCount'],HFT=0,training=0,elapsedSeconds=result['elapsedSeconds'])),flush=True)
    if result['verdict']=='PREFLIGHT_EXECUTION_ERROR':raise SystemExit(2)


if __name__=='__main__':
    main()
