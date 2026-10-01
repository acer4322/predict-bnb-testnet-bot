"""Read-only BTC5M public books, authoritative outcome verification and cost evidence.

Writes a new export directory only. No orders, DB mutations, native replay or fit.
The nominal inventory used to locate flip events is the cloud script's assumption,
not an observation of actual fills or a strategy profitability evaluation.
"""
from __future__ import annotations

import argparse
import bisect
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import lzma
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COHORTS = [
    ('fresh100a', 'btc5m_cg1at_fresh100a_20260930'),
    ('fresh40f', 'btc5m_atbid_validation_fresh40f_20260929'),
    ('fresh40g', 'btc5m_cg1at_validation_fresh40g_20260929'),
    ('fresh40h', 'btc5m_cg1at_size_validation_fresh40h_20260929'),
]
API_BASE = 'https://api.predict.fun'
BOOK_FIELDS = ('source_ms', 'received_ms', 'best_bid', 'best_ask', 'bids', 'asks')
RANGE = re.compile(r'(\d{1,2})(?::(\d{2}))?(AM|PM)?\s*-\s*(\d{1,2})(?::(\d{2}))?(AM|PM)\s+ET', re.I)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == '.gz' else data)


def load(path):
    b = path.read_bytes()
    if path.suffix == '.gz': b = gzip.decompress(b)
    if path.suffix == '.xz': b = lzma.decompress(b)
    return json.loads(b)


def btc5m_title(title):
    if not str(title).startswith('Bitcoin Up or Down - '): return False
    m = RANGE.search(title)
    if not m: return False
    h1, m1, am1, h2, m2, am2 = m.groups()
    def minute(h, minute, am):
        return (int(h) % 12 + (12 if am.upper() == 'PM' else 0)) * 60 + int(minute or 0)
    end = minute(h2, m2, am2)
    return any((end - minute(h1, m1, am)) % 1440 == 5 for am in ([am1] if am1 else ['AM', 'PM']))


def tape_books(path, start, end, full=False):
    """Decode existing public L2 updates; omit matches and execution/private metadata."""
    tape = load(path)
    assert tape.get('schema', {}).get('updates') == '[sourceMs,receivedMs,orderCount,isCheckpoint,bids?,asks?,changes]'
    levels = {'bids': {}, 'asks': {}}
    rows = []
    chain_recv = None
    for u in sorted(tape['updates'], key=lambda x: (int(x[0]), int(x[1]))):
        src, recv, _, cp, cb, ca, changes = u
        src, recv = int(src), int(recv)
        if cp and cb is not None and ca is not None:
            levels = {k: {float(p): float(q) for p, q in v.items() if float(q) > 0}
                      for k, v in [('bids', cb), ('asks', ca)]}
            chain_recv = recv
        else:
            if chain_recv is None: continue
            chain_recv = max(chain_recv, recv)
            for side in ('bids', 'asks'):
                for p, _before, after, _delta in (changes or {}).get(side, []):
                    if float(after) <= 1e-9: levels[side].pop(float(p), None)
                    else: levels[side][float(p)] = float(after)
        # Original frozen exports include a boundary snapshot just before open.
        if src < start - 2000 or src > end + 2000: continue
        bids = sorted(levels['bids'].items(), reverse=True)
        asks = sorted(levels['asks'].items())
        if not bids or not asks: continue
        assert all(math.isfinite(p) and math.isfinite(q) and 0 < p < 1 and q > 0 for p, q in bids + asks)
        r = dict(source_ms=src, received_ms=recv, best_bid=bids[0][0], best_ask=asks[0][0],
                 bids=[list(x) for x in (bids if full else bids[:5])],
                 asks=[list(x) for x in (asks if full else asks[:5])])
        if full: r['chain_received_max_ms'] = chain_recv
        rows.append(r)
    return rows


def prepare(out, new_count, extend=False):
    if (out / 'INPUTS.json').exists():
        if not extend: raise RuntimeError('INPUTS already exist: reuse frozen selection with fetch/export')
        initial=load(out/'INPUTS.json')
        old_ids={r['market_id'] for r in initial['records']}
        rows_needed=new_count-sum(r['cohort']=='new300' for r in initial['records'])
        assert rows_needed>0
    c = sqlite3.connect((ROOT/'data/target_wallet_official_v1.db').as_uri()+'?mode=ro', uri=True)
    c.execute('PRAGMA query_only=ON')
    c.row_factory = sqlite3.Row
    rows = [dict(x) for x in c.execute('SELECT market_id,asset,title,window_end_ms,status,winner,resolved_at_ms FROM target_markets WHERE asset=?', ('BTC',))]
    c.close()
    by_id = {int(r['market_id']): r for r in rows}
    old, manifest_hashes = [], {}
    for name, package in ([] if extend else COHORTS):
        p = ROOT/'data/research'/package
        market_file = p/'MARKETS.json'
        manifest_hashes[market_file.relative_to(ROOT).as_posix()] = sha(market_file.read_bytes())
        for mid in load(market_file)['markets']:
            mid = int(mid); r = by_id[mid]
            assert btc5m_title(r['title']) and r['window_end_ms'], mid
            end = int(r['window_end_ms']); public = p/'base/inputs'/f'public_{mid}.json.gz'
            assert public.exists(), public
            old.append(dict(market_id=mid, cohort=name, start=end-300000, end=end,
                            title=r['title'], public=public.relative_to(ROOT).as_posix(),
                            tape=f'data/execution_tape_v1/markets/{mid}.json.xz',
                            db_winner=r['winner'], db_observed_resolution_ms=r['resolved_at_ms']))
    if not extend:
        old_ids = {r['market_id'] for r in old}
        assert len(old) == len(old_ids) == 220
    new, skipped = [], []
    for r in sorted(rows, key=lambda r: int(r['market_id']), reverse=True):
        mid = int(r['market_id']); end = r['window_end_ms']
        if mid in old_ids or not end or not btc5m_title(r['title']): continue
        tape = ROOT/f'data/execution_tape_v1/markets/{mid}.json.xz'
        if not tape.exists(): continue
        start = int(end)-300000
        bs = tape_books(tape, start, int(end))
        # Same minimum rows as cloud loader; coverage includes its entire t12..288 grid.
        if len(bs) < 50 or bs[0]['source_ms'] > start+12000 or bs[-1]['source_ms'] < start+288000:
            skipped.append(dict(market_id=mid, reason='public_policy_window_not_covered')); continue
        new.append(dict(market_id=mid, cohort='new300', start=start, end=int(end), title=r['title'],
                        public=None, tape=tape.relative_to(ROOT).as_posix(), db_winner=r['winner'],
                        db_observed_resolution_ms=r['resolved_at_ms']))
        if len(new) == (rows_needed if extend else new_count): break
    assert len(new) == (rows_needed if extend else new_count), ('insufficient new books', len(new))
    if extend:
        assert not (out/'INPUTS_INITIAL520.json').exists()
        (out/'INPUTS_INITIAL520.json').write_bytes((out/'INPUTS.json').read_bytes())
        initial['records'].extend(new)
        initial['augmentation_reason']='One initially selected new market has authoritative 0.5/0.5 payout; preserve it as nonbinary and add the most recent eligible ID at extension time to obtain 300 new binary labels. Initial selection remains frozen; no PnL or direction selection.'
        dump(out/'INPUTS.json',initial)
        print(json.dumps({'additional_public_candidates':len(new),'total':len(initial['records'])}),flush=True)
        return
    cfg = dict(version='GRADUATION_LOCAL_PUBLIC_INPUTS_V1', offline_only=True, read_only=True,
               source_db='data/target_wallet_official_v1.db', public_selection='220 declared fresh100a/f/g/h plus most recent market_id descending new BTC5M tape coverage t12..288; no winner, PnL or confidence selection',
               original_cloud_220_identity='candidate match from four disjoint declared cohorts; cloud BOOKS_ROOT manifest was not supplied',
               db_winner_provenance='UNVERIFIED_FALLBACK_POSSIBLE; official API verification required',
               source_manifest_sha256=manifest_hashes, skipped=skipped, records=old+new)
    dump(out/'INPUTS.json', cfg)
    print(json.dumps({'selected_existing':len(old),'selected_new':len(new),'total':len(old)+len(new),'skipped':len(skipped)}), flush=True)


def public_api_key():
    token = os.environ.get('PREDICT_FUN_API_KEY')
    if not token:
        # Read only the public API access key; never load wallet/account settings.
        for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('PREDICT_FUN_API_KEY='):
                token = line.partition('=')[2].strip().strip('\"\''); break
    if not token: raise RuntimeError('public market API access unavailable')
    return token


def authoritative_label(m):
    if m['status'] not in ('RESOLVED', 'SETTLED'): return None
    winners = [o['name'].upper() for o in m['outcomes'] if o['status'] == 'WON' or o.get('isWinner') is True or o.get('won') is True]
    return winners[0] if len(winners) == 1 and winners[0] in ('UP', 'DOWN') else None


def fetch(out):
    cfg = load(out/'INPUTS.json'); token = public_api_key()
    def one(r):
        mid = r['market_id']; path = out/'api'/f'{mid}.json'
        if path.exists(): return mid, load(path).get('winner'), 'cached'
        url = f'{API_BASE}/v1/markets/{mid}'
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={'Accept':'application/json','User-Agent':'research-readonly/1.0','x-api-key':token})
                with urllib.request.urlopen(req, timeout=15) as response: payload = json.load(response)
                d = payload.get('data', payload)
                assert int(d['id']) == mid and btc5m_title(d['title']), mid
                safe = dict(market_id=mid, title=d['title'], status=str(d.get('status', '')).upper(),
                            feeRateBps=d.get('feeRateBps'),
                            outcomes=[{k:o.get(k) for k in ('name','status','isWinner','won')} for o in d.get('outcomes', [])],
                            source=url, fetched_at_ms=int(time.time()*1000))
                safe['winner'] = authoritative_label(safe)
                dump(path, safe)
                time.sleep(.55)  # Two readers, at most 218 requests/min before network time.
                return mid, safe['winner'], 'fresh'
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(min(5, 1+attempt*2)); continue
                return mid, None, 'HTTP_'+str(e.code)
            except (OSError, ValueError, KeyError, AssertionError) as e:
                if attempt < 2: time.sleep(1); continue
                return mid, None, type(e).__name__
        return mid, None, 'UNKNOWN'
    done, unknown = 0, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(one, r) for r in cfg['records']]):
            mid, winner, status = future.result(); done += 1
            if winner is None: unknown.append(dict(market_id=mid, status=status))
            if done % 20 == 0 or done == len(cfg['records']):
                print(json.dumps({'official_checked':done,'total':len(cfg['records']),'unknown':len(unknown)}), flush=True)
    dump(out/'API_STATUS.json', dict(checked=done, unknown=unknown))


def pct(xs, q):
    if not xs: return None
    s = sorted(xs); pos=(len(s)-1)*q; lo=math.floor(pos); hi=math.ceil(pos)
    return s[lo] + (s[hi]-s[lo])*(pos-lo)


def depth(book, opp, qty, decision):
    if book is None: return None
    asks = book['asks'] if opp == 'UP' else [[round(1-p, 12), q] for p, q in book['bids']]
    bids = book['bids'] if opp == 'UP' else [[round(1-p, 12), q] for p, q in book['asks']]
    asks=sorted(asks); bids=sorted(bids, reverse=True)
    remain, spend = qty, 0.
    for p, q in asks:
        take=min(remain,q); spend+=take*p; remain-=take
        if remain <= 1e-9: break
    return dict(source_ms=book['source_ms'], received_ms=book['received_ms'],
                chain_received_max_ms=book['chain_received_max_ms'],
                source_age_ms=decision-book['source_ms'],
                observed_book_receive_minus_source_ms=book['received_ms']-book['source_ms'],
                available_by_decision=book['chain_received_max_ms']<=decision,
                opposite_ask=asks[0][0], opposite_bid=bids[0][0], spread=round(asks[0][0]-bids[0][0],12),
                opposite_asks_top3=asks[:3], opposite_asks_full=asks, top3_shares=sum(q for _,q in asks[:3]),
                full_observed_ask_levels=len(asks), full_observed_ask_shares=sum(q for _,q in asks),
                hedge_qty=qty, observed_depth_covers_qty=remain<=1e-9,
                hedge_vwap=spend/qty if remain<=1e-9 and qty else None,
                hedge_uncovered_qty=max(0,remain))


def cost_event(r, public_books, full_books):
    """Locate one flip using cloud policy decisions; never compute PnL or labels."""
    start=r['start']; ts=[b['source_ms'] for b in public_books]; full_ts=[b['source_ms'] for b in full_books]
    inv={'UP':0.,'DOWN':0.}; fav=None
    for t in range(12,290,2):
        decision=start+t*1000; i=bisect.bisect_right(ts, decision)-1
        if i<0: continue
        b=public_books[i]; mid=(b['best_bid']+b['best_ask'])/2
        if fav is None: fav='UP' if mid>=.5 else 'DOWN'
        fm=mid if fav=='UP' else 1-mid
        if t>12 and fm<=.4:
            if inv[fav]<150: return None
            opp='DOWN' if fav=='UP' else 'UP'; qty=sum(inv.values())
            j=bisect.bisect_right(full_ts,decision)-1
            source=full_books[j] if j>=0 else None
            received=next((v for v in reversed(full_books[:j+1]) if v['chain_received_max_ms']<=decision),None)
            assert source is None or abs(source['best_bid']-b['best_bid'])<1e-9 and abs(source['best_ask']-b['best_ask'])<1e-9
            return dict(market_id=r['market_id'], cohort=r['cohort'], decision_ms=decision, decision_elapsed_s=t,
                        favourite=fav, opposite_side=opp, favourite_mid=fm,
                        nominal_inventory_assuming_cloud_fills=inv.copy(), nominal_favourite_qty=inv[fav],
                        inventory_observed=False, source_clock=depth(source,opp,qty,decision),
                        received_available_clock=depth(received,opp,qty,decision),
                        order_submit_to_ack_ms=None, order_submit_to_fill_ms=None)
        cur='UP' if mid>=.5 else 'DOWN'; cm=mid if cur=='UP' else 1-mid
        if .60<=cm<=.80 and sum(inv.values())<300: inv[cur]+=15
    return None


def export(out):
    cfg=load(out/'INPUTS.json'); labels=[]; proof=[]; events=[]; latency=[]; provenance=[]
    unknown=[]; source_mismatches=[]; legacy_match_checks=[]; nonbinary_books=[]
    for idx,r in enumerate(cfg['records'],1):
        mid=r['market_id']; ap=out/'api'/f'{mid}.json'
        api=load(ap) if ap.exists() else None
        if not api: unknown.append(mid); continue
        winner=authoritative_label(api)
        if not winner: unknown.append(mid)
        else: labels.append(dict(market_id=mid,winner=winner))
        proof.append(dict(api,cohort=r['cohort'],db_winner=r['db_winner'],db_observed_resolution_ms=r['db_observed_resolution_ms']))
        if winner and r['db_winner'] and r['db_winner']!=winner: source_mismatches.append(mid)
        tape=ROOT/r['tape']; full=tape_books(tape,r['start'],r['end'],full=True)
        if r['public']:
            original=load(ROOT/r['public']); bs=[{k:b[k] for k in BOOK_FIELDS} for b in original['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
            assert original['market']['window_start_ms']==r['start'] and original['market']['window_end_ms']==r['end']
            # Consequential independent comparison: reconstruct every original frozen book.
            normalized=[{k:b[k] for k in BOOK_FIELDS} for b in full]
            normalized=[dict(b,bids=b['bids'][:5],asks=b['asks'][:5]) for b in normalized]
            # Legacy freezes may exclude their single after-window row; match by time.
            by_time={(b['source_ms'],b['received_ms']):b for b in normalized}
            matches=sum(by_time.get((b['source_ms'],b['received_ms']))==b for b in bs)
            legacy_match_checks.append(dict(market_id=mid,matched=matches,total=len(bs)))
            assert matches==len(bs),('frozen book mismatch',mid,matches,len(bs))
        else:
            bs=[{k:b[k] for k in BOOK_FIELDS} for b in full]
            for b in bs: b['bids']=b['bids'][:5]; b['asks']=b['asks'][:5]
        obj=dict(market=dict(market_id=mid,asset='BTC',timeframe='5M',window_start_ms=r['start'],window_end_ms=r['end']),books=bs)
        pp=out/'public_inputs'/f'public_{mid}.json.gz'
        if winner: dump(pp,obj)
        else: nonbinary_books.append(obj)
        delays=[b['received_ms']-b['source_ms'] for b in bs]
        latency.append(dict(market_id=mid,n=len(delays),p10_ms=pct(delays,.1),median_ms=pct(delays,.5),p90_ms=pct(delays,.9),p99_ms=pct(delays,.99),negative_samples=sum(d<0 for d in delays)))
        ev=cost_event(r,bs,full)
        if ev:
            ev['feeRateBps']=api['feeRateBps']; events.append(ev)
        provenance.append(dict(market_id=mid,cohort=r['cohort'],public_sha256=sha(pp.read_bytes()) if winner else None,tape_sha256=sha(tape.read_bytes()),
                               source_public_sha256=sha((ROOT/r['public']).read_bytes()) if r['public'] else None,
                               books=len(bs),first_source_ms=bs[0]['source_ms'],last_source_ms=bs[-1]['source_ms']))
        if idx%50==0: print(json.dumps({'exported':idx,'total':len(cfg['records'])}),flush=True)
    existing={r['market_id'] for r in cfg['records'] if r['cohort']!='new300'}
    old100={r['market_id'] for r in cfg['records'] if r['cohort']=='fresh100a'}
    dump(out/'GRADUATION_LABELS_ALL_TRUE.json',dict(records=labels))
    dump(out/'GRADUATION_LABELS_EXISTING220_TRUE.json',dict(records=[r for r in labels if r['market_id'] in existing]))
    dump(out/'GRADUATION_LABELS_REPAIRED120_TRUE.json',dict(records=[r for r in labels if r['market_id'] in existing-old100]))
    dump(out/'GRADUATION_LABELS_NEW300_TRUE.json',dict(records=[r for r in labels if r['market_id'] not in existing]))
    dump(out/'GRADUATION_NONBINARY_BOOKS.json.gz',dict(records=nonbinary_books,note='0.5/0.5 resolved markets, excluded from public_* naming so cloud loader cannot silently infer binary winners'))
    nonbinary=load(out/'GRADUATION_NONBINARY_SETTLEMENTS.json')['records'] if (out/'GRADUATION_NONBINARY_SETTLEMENTS.json').exists() else []
    known_nonbinary={r['market_id'] for r in nonbinary if r['payout_per_share']=={'UP':.5,'DOWN':.5}}
    dump(out/'GRADUATION_OFFICIAL_LABEL_PROVENANCE.json',dict(records=proof,unknown_binary_winner_ids=unknown,confirmed_nonbinary_settlements=nonbinary,unverified_settlement_ids=sorted(set(unknown)-known_nonbinary),db_winner_mismatches=source_mismatches,collector_resolved_at_is_local_observation_not_official_exchange_resolution_time=True))
    dump(out/'GRADUATION_COST_FEATURES.json.gz',dict(version='PUBLIC_DEPTH_AND_BOOK_OBSERVATION_COSTS_V1',offline_only=True,
         policy='flip_hedge_scaled.sim t12..288 step2, initial favourite, first favourite-mid<=.4; nominal favourite qty>=150, hedge entire nominal inventory',
         inventory_scope='Cloud assumed-fill inventory only; no actual OUR order submission or fill proof',
         source_time_caveat='source-clock snapshots may not yet be received; received_available_clock requires checkpoint/delta dependency chain received by decision',
         flip_trigger_timeline='Cloud source-clock event, not actual received-clock policy replay. Received-available depth is a price snapshot only; actual live flip decision timestamp is unobserved.',
         depth_caveat='Observed public liquidity, no guarantee it remains at order arrival, no queue/matching simulation',
         latency_scope='received_ms-source_ms book observation only, unsynchronized clocks possible; order ACK/FILL latency UNKNOWN',
         records=events,book_observation_latency_by_market=latency))
    dump(out/'GRADUATION_PUBLIC_PROVENANCE.json',dict(records=provenance,source_manifest_sha256=cfg['source_manifest_sha256'],selection=cfg['public_selection'],augmentation_reason=cfg.get('augmentation_reason'),legacy_frozen_book_matches=legacy_match_checks))
    summary=dict(labels=len(labels),existing220_binary=sum(r['market_id'] in existing for r in labels),repaired120_binary=sum(r['market_id'] in existing-old100 for r in labels),new300_binary=sum(r['market_id'] not in existing for r in labels),nonbinary_markets=unknown,db_mismatches=source_mismatches,
                 flip_depth_events=len(events),public_bytes=sum(p.stat().st_size for p in (out/'public_inputs').glob('*.gz')),
                 costs_bytes=(out/'GRADUATION_COST_FEATURES.json.gz').stat().st_size,original_cloud_220_identity=cfg['original_cloud_220_identity'],model_fits=0,worker_jobs=0,orders_submitted=0)
    dump(out/'SUMMARY.json',summary);print(json.dumps(summary),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','extend','fetch','export']);ap.add_argument('--out',required=True);ap.add_argument('--new-count',type=int,default=300);a=ap.parse_args()
    out=Path(a.out).resolve()
    if a.mode=='prepare': prepare(out,a.new_count)
    elif a.mode=='extend': prepare(out,a.new_count,extend=True)
    elif a.mode=='fetch': fetch(out)
    else: export(out)


if __name__=='__main__': main()
