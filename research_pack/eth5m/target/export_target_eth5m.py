"""Offline Target ETH5M export; never imports runtime collectors or submits orders.

Freeze reads indexed official ledger / legacy inference tables in query-only
transactions. Export publishes anonymous public-match legs and depth evidence.
Reconstruct uses only the published anonymous inputs and checks byte parity.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
import gzip
import hashlib
import json
import lzma
import math
from pathlib import Path
import sqlite3
import time

VERSION = 'ETH5M_TARGET_OBSERVED_FILL_CARRIER_V1'
LOOKBACK_MS = 60000
EPS = 1e-8


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    raw = path.read_bytes()
    if path.suffix == '.gz': raw = gzip.decompress(raw)
    if path.suffix == '.xz': raw = lzma.decompress(raw)
    return json.loads(raw)


def encoded(value, compressed=False):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode()
    return gzip.compress(raw, mtime=0) if compressed else raw


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value, path.suffix == '.gz'))


def ro(path):
    con = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    return con


def proof(row, wallet):
    """Independently check official normalization against the cached API match."""
    raw = json.loads(row['raw_json'])
    assert int(raw['market']['id']) == row['market_id']
    at = int(datetime.fromisoformat(raw['executedAt'].replace('Z', '+00:00')).timestamp()*1000)
    assert at == row['event_ms']
    participants = raw.get('makers', []) if row['role'] == 'MAKER' else [raw['taker']]
    for p in participants:
        if str(p.get('signer', '')).lower() != wallet: continue
        if (str(p.get('hash') or '') or None) != row['order_hash']: continue
        amount = float(p.get('amount') if p.get('amount') is not None else raw['amountFilled'])/1e18
        price = float(p.get('price') if p.get('price') is not None else raw['priceExecuted'])/1e18
        outcome = p.get('outcome')
        if isinstance(outcome, dict): outcome = outcome.get('name') or outcome.get('label') or outcome.get('outcome')
        side = str(outcome).upper().strip()
        qtype = {'BUY':'BID', 'SELL':'ASK'}.get(str(p.get('quoteType')).upper(), str(p.get('quoteType')).upper())
        if side != row['side'] or qtype != row['quote_type']: continue
        if abs(amount-row['shares']) <= 1e-8 and abs(price-row['price']) <= 1e-10:
            return True
    return False


def freeze(a):
    assert not a.private.exists(), 'preserve prior frozen snapshot'
    selected = read(a.public_root/'SELECTION.json')['records']
    a.private.mkdir(parents=True)
    official = ro(a.repo/'data/target_wallet_official_v1.db')
    derived = ro(a.repo/'data/wallet_maker_book_inference_eth5m.db')
    wallet = derived.execute('SELECT target_wallet FROM maker_book_inference_meta LIMIT 1').fetchone()[0].lower()
    captured = int(time.time()*1000)
    counts = Counter(); rows_manifest = []
    for i, s in enumerate(selected):
        mid = s['market_id']
        fills = []
        for rr in official.execute('''SELECT e.leg_id,e.wallet,e.market_id,e.role,e.side,e.quote_type,e.order_hash,
          e.event_ms,e.observed_at_ms,e.price,e.shares,e.raw_json,c.observed_at_ms AS context_ms
          FROM wallet_shadow_target_events e LEFT JOIN wallet_shadow_target_event_context c USING(leg_id)
          WHERE e.asset='ETH' AND e.market_id=? ORDER BY e.event_ms,e.id''', (mid,)):
            r = dict(rr)
            assert r['wallet'].lower() == wallet, 'mixed identity'
            assert r['context_ms'] == r['observed_at_ms'], 'context clock mismatch'
            assert proof(r, wallet), 'cached official match normalization mismatch'
            assert r['role'] in ('MAKER','TAKER') and r['quote_type'] in ('BID','ASK')
            assert r['side'] in ('UP','DOWN') and 0 <= r['price'] <= 1 and r['shares'] > 0
            r.pop('raw_json'); r.pop('wallet'); r.pop('context_ms')
            fills.append(r); counts[r['role']+'_'+r['quote_type']] += 1
        parents = [dict(r) for r in derived.execute('SELECT * FROM maker_book_inference_v21_parent_lifecycles WHERE market_id=? ORDER BY first_target_ms,parent_id', (mid,))]
        alloc = [dict(r) for r in derived.execute('SELECT * FROM maker_book_inference_v21_allocations WHERE market_id=? ORDER BY source_ms,allocation_id', (mid,))]
        value = dict(market_id=mid, fills=fills, legacy_parents=parents, legacy_allocations=alloc)
        path = a.private/f'{mid}.json.gz'; save(path, value)
        rows_manifest.append(dict(market_id=mid, sha256=sha(path.read_bytes()), fills=len(fills), legacy_parents=len(parents)))
        if (i+1)%150 == 0: print(json.dumps(dict(frozen_markets=i+1)), flush=True)
    official.rollback(); official.close(); derived.rollback(); derived.close()
    manifest = dict(version=VERSION, captured_at_ms=captured, completed_at_ms=int(time.time()*1000),
      official_source='Cached Predict GET /v1/orders/matches, signerAddress=Target, isSignerMaker=true/false',
      snapshot_method='Separate read-only SQLite BEGIN transactions; rows checked against cached API raw match before removing raw/wallet fields.',
      target_identity_count=1, target_identity_matches_eth_collector_meta=True,
      official_raw_match_normalization_verified=True, context_receipt_clock_verified=True,
      role_quote_counts=dict(counts), records=rows_manifest,
      source_code_sha256={name:sha((a.repo/'src/predict_bot'/name).read_bytes()) for name in
        ('target_wallet_official_v1.py','predict_wallet_maker_book_inference_collector_eth5m.py','predict_wallet_maker_book_inference_collector_v2_1_impl.py')})
    save(a.private/'MANIFEST.json', manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('records','source_code_sha256')}, ensure_ascii=False), flush=True)


def anonymous_fills(raw, start_seq, start_parent):
    groups = defaultdict(list)
    for i, r in enumerate(raw):
        key = (r['role'], r['quote_type'], r['side'], round(r['price'], 10), r['order_hash'] or r['leg_id'])
        groups[key].append(i)
    ordered = sorted(groups, key=lambda key:(raw[groups[key][0]]['event_ms'], groups[key][0]))
    parent_by_key = {key:start_parent+i for i,key in enumerate(ordered)}
    private_to_public = {}
    fills = []
    for i,r in enumerate(raw):
        key = (r['role'],r['quote_type'],r['side'],round(r['price'],10),r['order_hash'] or r['leg_id'])
        parent = parent_by_key[key]
        private_to_public[(r['role'],r['quote_type'],r['order_hash'] or r['leg_id'],r['side'],round(r['price'],10))] = parent
        fills.append(dict(fill_seq=start_seq+i,parent_seq=parent,market_id=r['market_id'],event_ms=r['event_ms'],
          observed_at_ms=r['observed_at_ms'],side=r['side'],price=r['price'],shares=r['shares'],role=r['role'],
          quote_type=r['quote_type'],parent_identity_known=bool(r['order_hash']),
          source_proof_verified=True,event_precision_ms=1000,observed_clock='OFFICIAL_API_PAGE_RECEIPT'))
    return fills, private_to_public, start_parent+len(ordered)


def depth_evidence(tape, source_sha):
    updates = []; chain = 0
    for seq,u in enumerate(sorted(tape['updates'], key=lambda x:(int(x[0]),int(x[1]))), 1):
        src,recv,_,cp,_,_,changes = u
        chain = max(chain,int(recv))
        deltas = []
        for key,side in (('bids','BID'),('asks','ASK')):
            for p,before,after,delta in (changes or {}).get(key,[]):
                if abs(float(delta)) <= EPS: continue
                assert abs(float(after)-float(before)-float(delta)) < 1e-6
                deltas.append(dict(native_side=side,native_price=round(float(p),8),before_size=float(before),
                  after_size=float(after),delta=float(delta)))
        updates.append(dict(update_seq=seq,source_ms=int(src),received_ms=int(recv),availability_ms=chain,
          checkpoint=bool(cp),initial_snapshot=(seq==1),level_changes=deltas))
    return dict(market_id=tape['marketId'],source_archive_sha256=source_sha,updates=updates)


def features(books, side):
    src = [b['source_ms'] for b in books]
    avail = [max(b['received_ms'],b.get('chain_received_max_ms',b['received_ms'])) for b in books]
    assert src == sorted(src) and avail == sorted(avail)
    def at(t, received=False, mid=False):
        if t is None or not books: return None
        clock = avail if received else src
        if t < clock[0] or t > clock[-1]: return None
        i = bisect_right(clock,t)-1; b = books[i]
        bid,ask = b.get('best_bid'),b.get('best_ask')
        if mid:
            if bid is None or ask is None: return None
            p = (bid+ask)/2
            return p if side=='UP' else 1-p
        return bid if side=='UP' else (None if ask is None else 1-ask)
    return at


def reconstruct(fills, evidence, books, constructed_ms):
    groups = defaultdict(list)
    for f in fills:
        if f['role']=='MAKER' and f['quote_type']=='BID': groups[f['parent_seq']].append(f)
    level_changes = defaultdict(list)
    updates = evidence['updates']
    for u in updates:
        for ci,c in enumerate(u['level_changes']):
            key = (c['native_side'],round(c['native_price'],8))
            level_changes[key].append(dict(**c,update_seq=u['update_seq'],change_seq=ci,
              source_ms=u['source_ms'],received_ms=u['received_ms'],availability_ms=u['availability_ms'],
              initial_snapshot=u['initial_snapshot']))
    for changes in level_changes.values():
        changes.sort(key=lambda c:(c['received_ms'],c['update_seq'],c['change_seq']))
    used = defaultdict(float)
    rows = []; compat = []; allocations = []
    parent_order = sorted(groups, key=lambda p:(min(f['event_ms'] for f in groups[p]),p))
    source_times = [u['source_ms'] for u in updates]
    for parent in parent_order:
        legs = groups[parent]; f = legs[0]
        first = min(x['event_ms'] for x in legs); last = max(x['event_ms'] for x in legs)
        first_obs = min(x['observed_at_ms'] for x in legs if x['event_ms']==first)
        last_obs = max(x['observed_at_ms'] for x in legs)
        quantity = math.fsum(x['shares'] for x in legs)
        native_side = 'BID' if f['side']=='UP' else 'ASK'
        native_price = round(f['price'] if f['side']=='UP' else 1-f['price'],8)
        # Rebuild anonymous level inventory in receipt order as of this first
        # fill. Negative net changes drain the oldest depth lots (FIFO MODEL,
        # not venue proof); mismatched chains reset to an ineligible baseline.
        # Already assigned portions drain first within a lot, represented by
        # min(surviving, original-used). All original adds share one used ledger.
        lots = deque(); standing = 0.; resets = 0; survival_available = 0
        for c in level_changes[(native_side,native_price)]:
            if c['source_ms'] >= first or c['availability_ms'] > first_obs: continue
            survival_available = max(survival_available,c['availability_ms'])
            if abs(standing-c['before_size']) > 1e-6:
                lots.clear(); standing=c['after_size']; resets+=1
                if standing>EPS: lots.append(dict(c=c,remaining=standing,eligible=False))
                continue
            delta=c['delta']
            if delta>EPS:
                lots.append(dict(c=c,remaining=delta,eligible=not c['initial_snapshot']))
            else:
                remove=-delta
                while lots and remove>EPS:
                    q=min(remove,lots[0]['remaining']);remove-=q;lots[0]['remaining']-=q
                    if lots[0]['remaining']<=EPS: lots.popleft()
                assert remove<=1e-6, 'negative delta exceeds reconstructed level inventory'
            standing=c['after_size']
        cand=[]
        for lot in lots:
            c=lot['c'];key=(c['update_seq'],c['change_seq'])
            if not lot['eligible'] or not first-LOOKBACK_MS<=c['source_ms']<first: continue
            remaining=min(lot['remaining'],max(0.,c['delta']-used[key]))
            if remaining>EPS: cand.append(dict(**c,remaining=remaining,surviving=lot['remaining']))
        cand.sort(key=lambda c:(c['source_ms'],c['update_seq'],c['change_seq']))
        need = quantity; selected = []
        for c in reversed(cand):
            if need <= EPS: break
            if c['availability_ms'] > first_obs or c['remaining'] <= EPS: continue
            q = min(need,c['remaining']);used[(c['update_seq'],c['change_seq'])]+=q;need-=q
            aa = dict(market_id=f['market_id'],parent_seq=parent,update_seq=c['update_seq'],change_seq=c['change_seq'],
              source_ms=c['source_ms'],received_ms=c['received_ms'],availability_ms=c['availability_ms'],
              native_side=native_side,native_price=native_price,public_positive_delta=c['delta'],
              fifo_surviving_depth_at_first_fill=c['surviving'],allocated_shares=q,
              survival_evidence_available_ms=survival_available,survival_model='FIFO_ANONYMOUS_DEPLETION_ASSIGNED_FIRST')
            selected.append(aa); allocations.append(aa)
        allocated = math.fsum(x['allocated_shares'] for x in selected)
        coverage = min(1.,allocated/quantity)
        ready = min((x['source_ms'] for x in selected),default=None)
        nearest = max((x['source_ms'] for x in selected),default=None)
        state = 'FULL_LOWER_BOUND_SUPPORT' if coverage >= 1-1e-7 else 'PARTIAL_LOWER_BOUND_SUPPORT' if selected else 'UNKNOWN'
        start_i = max(0,bisect_left(source_times,first-LOOKBACK_MS)-1)
        end_i = bisect_left(source_times,first)
        segment = source_times[start_i:end_i]+[first]
        gap = max((b-a for a,b in zip(segment,segment[1:])),default=None)
        min_evidence = max([last_obs,survival_available]+[x['availability_ms'] for x in selected])
        row = dict(market_id=f['market_id'],parent_seq=parent,side=f['side'],price=f['price'],quote_type='BID',role='MAKER',
          first_fill_ms=first,last_fill_ms=last,first_fill_observed_ms=first_obs,last_fill_observed_ms=last_obs,
          fill_count=len(legs),fill_seqs=[x['fill_seq'] for x in legs],observed_filled_shares=quantity,
          order_quantity=None,order_quantity_status='UNKNOWN',unfilled_quantity=None,
          native_book_side=native_side,native_price=native_price,parent_identity_known=all(x['parent_identity_known'] for x in legs),
          candidate_carrier_ready_ms=ready,nearest_supporting_add_ms=nearest,candidate_lead_ms=None if ready is None else first-ready,
          allocated_support_shares=allocated,support_coverage=coverage,supporting_adds=len(selected),support_status=state,
          actual_placement_ms=None,placement_ownership='UNKNOWN',unfilled_orders='UNKNOWN',cancel_state='UNKNOWN',
          confidence=None,confidence_kind='NO_CALIBRATED_PROBABILITY',lookback_ms=LOOKBACK_MS,
          lookback_start_covered=bool(source_times and source_times[0]<=first-LOOKBACK_MS),max_source_gap_in_lookback_ms=gap,
          minimum_evidence_available_ms=min_evidence,reconstructed_at_ms=constructed_ms,
          depth_survival_model='FIFO_ANONYMOUS_DEPLETION_ASSIGNED_FIRST',level_chain_reset_count=resets,
          use='RETROSPECTIVE_OFFLINE_FILLED_PARENT_CARRIER_INFERENCE',future_parent_fills_used=True)
        rows.append(row)
        at = features(books,f['side'])
        cc = dict(anon_seq=parent,market_id=f['market_id'],side=f['side'],price=f['price'],filled_qty=quantity,
          placement_first_ms=ready,first_target_ms=first,resting_ms=None if ready is None else first-ready,
          confidence=None,placement_coverage=coverage,fill_allocation_coverage=None,post_action='UNKNOWN',
          best_bid_at_placement=at(ready),best_bid_at_fill=at(first),mid_side_at_fill=at(first,mid=True),
          mid_side_at_fill_plus5s=at(first+5000,mid=True),
          best_bid_at_placement_recv=at(ready,received=True),best_bid_at_fill_recv=at(first,received=True),
          mid_side_at_fill_recv=at(first,received=True,mid=True),mid_side_at_fill_plus5s_recv=at(first+5000,received=True,mid=True),
          placement_semantics='ANONYMOUS_CARRIER_CANDIDATE_NOT_OBSERVED_ORDER_BIRTH',support_status=state,
          minimum_evidence_available_ms=min_evidence,reconstructed_at_ms=constructed_ms,plus5s_fields='FUTURE_OFFLINE_DIAGNOSTIC')
        compat.append(cc)
    return rows,compat,allocations


LEGACY_FIELDS = ('market_id','target_side','native_book_side','target_price','native_price','first_target_ms','last_target_ms',
  'target_fill_count','target_filled_shares','expected_parent_shares','allocated_fill_shares','fill_allocation_coverage',
  'placement_allocated_shares','placement_coverage','placement_first_ms','placement_last_ms','resting_ms','post_action',
  'post_action_delay_ms','post_action_native_price','multi_fill_parent','observed_filled_near_18','placement_supports_18','confidence','inferred_at_ms')


def export(a):
    assert not a.out.exists(), 'preserve previously published target output'
    selection = read(a.public_root/'SELECTION.json'); manifest = read(a.private/'MANIFEST.json')
    snap = {r['market_id']:r for r in manifest['records']}
    a.out.mkdir(parents=True)
    all_fills=[]; all_rows=[]; all_compat=[]; all_alloc=[]; all_legacy=[]; legacy_alloc=[]; index=[]
    fill_seq=1; parent_seq=1; legacy_seq=1; constructed=int(time.time()*1000)
    for i,s in enumerate(selection['records']):
        mid=s['market_id']; path=a.private/f'{mid}.json.gz'
        assert sha(path.read_bytes())==snap[mid]['sha256']
        raw=read(path)
        ff,private_map,parent_seq=anonymous_fills(raw['fills'],fill_seq,parent_seq); fill_seq+=len(ff)
        archive=a.archives/f'{mid}.json.xz'
        assert sha(archive.read_bytes())==s['source_sha256']
        ev=depth_evidence(read(archive),s['source_sha256'])
        save(a.out/f'public_depth_evidence/{mid}.json.gz',ev)
        books=read(a.public_root/f'public_markets/{mid}/public_{mid}.json.gz')['books']
        rows,cc,alloc=reconstruct(ff,ev,books,constructed)
        save(a.out/f'markets/{mid}/target_fills.json.gz',ff)
        save(a.out/f'markets/{mid}/target_order_predictions.json.gz',rows)
        maker_qty=math.fsum(f['shares'] for f in ff if f['role']=='MAKER' and f['quote_type']=='BID')
        legacy_qty=math.fsum(p['target_filled_shares'] for p in raw['legacy_parents'])
        legacy_ids={}
        for p in raw['legacy_parents']:
            q={k:p.get(k) for k in LEGACY_FIELDS}
            q.update(legacy_seq=legacy_seq,official_parent_seq=private_map.get(('MAKER','BID',p['order_hash'] or p['parent_id'],p['target_side'],round(p['target_price'],10))),
              model_quantity_assumption_shares=18.,use='LEGACY_OFFLINE_INFERENCE_REFERENCE',
              confidence_kind='UNCALIBRATED_HEURISTIC',post_action_semantics='RETROSPECTIVE_PARENT_ASSOCIATION_NOT_OBSERVED_CANCEL')
            legacy_ids[p['parent_id']]=legacy_seq; legacy_seq+=1; all_legacy.append(q)
        for aa in raw['legacy_allocations']:
            if aa['parent_id'] not in legacy_ids: continue
            legacy_alloc.append(dict(legacy_seq=legacy_ids[aa['parent_id']],market_id=mid,
              **{k:aa.get(k) for k in ('allocation_kind','source_ms','native_book_side','native_price','public_delta_quantity','allocated_quantity','score')}))
        meta=dict(market_id=mid,window_start_ms=s['window_start_ms'],window_end_ms=s['window_end_ms'],
          official_fills=len(ff),role_quote_counts=dict(Counter(f['role']+'_'+f['quote_type'] for f in ff)),
          maker_bid_parents=len(rows),maker_bid_shares=maker_qty,prediction_support_counts=dict(Counter(r['support_status'] for r in rows)),
          legacy_parents=len(raw['legacy_parents']),legacy_maker_shares=legacy_qty,
          legacy_share_coverage=None if not maker_qty else legacy_qty/maker_qty,
          collector_quality_status=s['collector_quality_status'],source_archive_sha256=s['source_sha256'],
          official_fill_collection_completeness='UNKNOWN',unfilled_order_lifecycle='UNKNOWN')
        save(a.out/f'markets/{mid}/META.json',meta); index.append(meta)
        all_fills.extend(ff);all_rows.extend(rows);all_compat.extend(cc);all_alloc.extend(alloc)
        if (i+1)%150==0: print(json.dumps(dict(exported_markets=i+1,fills=len(all_fills),parents=len(all_rows))),flush=True)
    save(a.out/'target_fills_export.json.gz',all_fills)
    save(a.out/'target_order_predictions.json.gz',all_rows)
    save(a.out/'target_lifecycle_features.json.gz',all_compat)
    save(a.out/'placement_allocations.json.gz',all_alloc)
    save(a.out/'legacy_v21_reference.json.gz',all_legacy)
    save(a.out/'legacy_v21_allocations.json.gz',legacy_alloc)
    public_manifest={k:v for k,v in manifest.items() if k!='records'}
    public_manifest.update(private_snapshot_manifest_sha256=sha((a.private/'MANIFEST.json').read_bytes()),
      source_raw_payloads_published=False,identifiers_published=False)
    save(a.out/'SOURCE_PROVENANCE.json',public_manifest)
    save(a.out/'INDEX.json',dict(version=VERSION,selection_path='../SELECTION.json',
      selection_sha256=sha((a.public_root/'SELECTION.json').read_bytes()),reconstructed_at_ms=constructed,records=index))
    stats=dict(version=VERSION,markets=len(index),official_fills=len(all_fills),
      role_quote_counts=dict(Counter(f['role']+'_'+f['quote_type'] for f in all_fills)),
      maker_bid_parents=len(all_rows),maker_bid_fills=sum(r['fill_count'] for r in all_rows),
      maker_bid_shares=math.fsum(r['observed_filled_shares'] for r in all_rows),
      prediction_support_counts=dict(Counter(r['support_status'] for r in all_rows)),
      legacy_parents=len(all_legacy),legacy_partial_markets=sum(m['legacy_share_coverage']<1-1e-7 for m in index if m['legacy_share_coverage'] is not None),
      official_raw_match_proof_checked=True,unfilled_orders='UNKNOWN',actual_order_birth='UNKNOWN',calibrated_prediction_probability='UNKNOWN')
    save(a.out/'VALIDATION.json',stats)
    print(json.dumps(stats),flush=True)


def parity(a):
    index=read(a.out/'INDEX.json'); fills=read(a.out/'target_fills_export.json.gz')
    by=defaultdict(list)
    for f in fills: by[f['market_id']].append(f)
    rr=[];cc=[];aa=[]
    for m in index['records']:
        mid=m['market_id']
        ev=read(a.out/f'public_depth_evidence/{mid}.json.gz')
        books=read(a.public_root/f'public_markets/{mid}/public_{mid}.json.gz')['books']
        r,c,x=reconstruct(by[mid],ev,books,index['reconstructed_at_ms']);rr.extend(r);cc.extend(c);aa.extend(x)
        assert encoded(r,True)==(a.out/f'markets/{mid}/target_order_predictions.json.gz').read_bytes()
    for name,value in (('target_order_predictions.json.gz',rr),('target_lifecycle_features.json.gz',cc),('placement_allocations.json.gz',aa)):
        assert encoded(value,True)==(a.out/name).read_bytes(),name
    print(json.dumps(dict(public_inputs_reconstruction_byte_parity=True,markets=len(index['records']),parents=len(rr))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('freeze','export','reconstruct'))
    for name in ('repo','public-root','private','archives','out'): parser.add_argument('--'+name,type=Path)
    args=parser.parse_args()
    {'freeze':freeze,'export':export,'reconstruct':parity}[args.mode](args)
