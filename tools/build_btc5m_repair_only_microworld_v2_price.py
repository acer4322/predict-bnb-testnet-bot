from __future__ import annotations
import bisect,gzip,json,math,pathlib,statistics
ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=ROOT/'.lan_worker_v1/repair_only_microworld_v1_20260914'
PUB=ROOT/'.lan_worker_v1/v49_c30_winner_oracle_fixed_v1_20260914/inputs'
OUT=ROOT/'.lan_worker_v1/repair_only_microworld_v2_price_20260914'
QREF=2436.779291626123
rows=[json.loads(x) for x in (SRC/'data.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
by={}
for r in rows:by.setdefault(int(r['market_id']),[]).append(r)
features=json.loads((SRC/'data_summary.json').read_text(encoding='utf-8'))['feature_names']
new_features=['book_available','book_age_s','weak_bid','weak_ask','weak_spread','weak_bid_depth_qref','weak_ask_depth_qref','weak_ask_rank30','recent_repair_price_available','recent_repair_avg30','weak_ask_minus_recent_repair_avg30']
for mid,rr in by.items():
    payload=json.loads(gzip.decompress((PUB/f'public_{mid}.json.gz').read_bytes()))
    start=int(payload['market']['window_start_ms'])
    books=[]
    for b in payload['books']:
        rb=b.get('received_ms');bid=b.get('best_bid');ask=b.get('best_ask')
        if rb is None or bid is None or ask is None:continue
        bid=float(bid);ask=float(ask)
        if not (0<bid<ask<1):continue
        bids=b.get('bids') or [];asks=b.get('asks') or []
        bd=float(bids[0][1]) if bids and len(bids[0])>=2 else 0.0
        ad=float(asks[0][1]) if asks and len(asks[0])>=2 else 0.0
        books.append((int(rb),bid,ask,bd,ad))
    books.sort(); bt=[x[0] for x in books]
    # strict-past Target repair trades for price history
    # use original Target source bundle to avoid actor/runtime mixing
    target_path=ROOT/'data/research/market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1/target_actions.jsonl'
    # defer global read once outside market loop via cache below
    for r in rr:
        t=start+int(r['second'])*1000
        j=bisect.bisect_left(bt,t)-1
        strong=r['strong_side'];weak='DOWN' if strong=='UP' else 'UP'
        if j<0:
            r.update(book_available=0,book_age_s=0.0,weak_bid=0.0,weak_ask=0.0,weak_spread=0.0,weak_bid_depth_qref=0.0,weak_ask_depth_qref=0.0)
        else:
            rb,bid,ask,bd,ad=books[j]
            if weak=='UP': wb,wa,wbd,wad=bid,ask,bd,ad
            else: wb,wa,wbd,wad=1.0-ask,1.0-bid,ad,bd
            r.update(book_available=1,book_age_s=min(60.0,max(0.0,(t-rb)/1000.0)),weak_bid=wb,weak_ask=wa,weak_spread=max(0.0,wa-wb),weak_bid_depth_qref=wbd/QREF,weak_ask_depth_qref=wad/QREF)
    # rank uses only current and prior strict-past one-second book observations, never future seconds
    hist=[]
    for r in sorted(rr,key=lambda x:int(x['second'])):
        if r['book_available']:
            current=float(r['weak_ask']); past=[x for sec,x in hist if sec>=int(r['second'])-30]
            if past:
                lo=min(past);hi=max(past)
                rank=(current-lo)/(hi-lo) if hi>lo+1e-12 else .5
            else:rank=.5
            r['weak_ask_rank30']=max(0.0,min(1.0,rank));hist.append((int(r['second']),current))
        else:r['weak_ask_rank30']=.5
# load Target once and add strict-past repair-price history
acts=[json.loads(x) for x in (ROOT/'data/research/market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1/target_actions.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
aby={}
for a in acts:
    if a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:aby.setdefault(int(a['market_id']),[]).append(a)
for mid,rr in by.items():
    payload=json.loads(gzip.decompress((PUB/f'public_{mid}.json.gz').read_bytes()));start=int(payload['market']['window_start_ms'])
    arr=sorted(aby.get(mid,[]),key=lambda x:int(x['event_ms']))
    for r in rr:
        t=start+int(r['second'])*1000;weak='DOWN' if r['strong_side']=='UP' else 'UP';lo=t-30000
        z=[a for a in arr if lo<=int(a['event_ms'])<t and a['side']==weak]
        q=sum(float(a['shares']) for a in z)
        if q>0:
            avg=sum(float(a['shares'])*float(a['price']) for a in z)/q
            r['recent_repair_price_available']=1;r['recent_repair_avg30']=avg;r['weak_ask_minus_recent_repair_avg30']=(float(r['weak_ask'])-avg) if r['book_available'] else 0.0
        else:
            r['recent_repair_price_available']=0;r['recent_repair_avg30']=0.0;r['weak_ask_minus_recent_repair_avg30']=0.0
allrows=sorted(rows,key=lambda x:(int(x['market_id']),int(x['second'])))
(OUT/'data.jsonl').write_text('\n'.join(json.dumps(r,separators=(',',':'),ensure_ascii=False) for r in allrows)+'\n',encoding='utf-8')
s=json.loads((SRC/'data_summary.json').read_text(encoding='utf-8'));s['feature_names']=features+new_features;s['version']='BTC5M_REPAIR_ONLY_MICROWORLD_V2_PRICE_20260914';s['price_features_strict_past']=True;s['book_missing_policy']='latest valid received_ms strictly before second start; unavailable flag if none';s['new_features']=new_features
(OUT/'data_summary.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps({'status':'PASS','rows':len(allrows),'features':len(s['feature_names']),'book_available_rate':sum(r['book_available'] for r in allrows)/len(allrows),'repair_price_history_rate':sum(r['recent_repair_price_available'] for r in allrows)/len(allrows)}))
