from __future__ import annotations
import bisect,gzip,json,pathlib,statistics
ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=ROOT/'.lan_worker_v1/target_opening_repair_admission_v1_20260915/data.jsonl'
rows=[json.loads(x) for x in SRC.read_text().splitlines() if x.strip()]
QREF=2436.779291626123
by={}
for r in rows:by.setdefault(int(r['market_id']),[]).append(r)
out=[]

def stats(vals,cur):
    if not vals:return (0.,0.,0.,0.)
    s=sorted(vals);n=len(s);med=s[n//2] if n%2 else .5*(s[n//2-1]+s[n//2]);rank=sum(x<=cur+1e-12 for x in s)/n
    return rank,s[0],med,s[-1]

for m,rr in by.items():
    pkg=ROOT/'.lan_worker_v1/v49_oracle_management_pressure_v2_20260914/inputs'/f'public_{m}.json.gz'
    d=json.load(gzip.open(pkg,'rt')); start=int(d['market']['window_start_ms']); books=sorted(d['books'],key=lambda x:int(x['received_ms']));ts=[int(x['received_ms']) for x in books]
    weak_hist=[]
    for r in sorted(rr,key=lambda x:int(x['sec'])):
        boundary=start+int(r['sec'])*1000
        j=bisect.bisect_left(ts,boundary)-1
        z=dict(r);avail=0;weak_bid=weak_ask=strong_bid=strong_ask=weak_bid_depth=weak_ask_depth=strong_bid_depth=strong_ask_depth=0.;age=999999.
        if j>=0:
            b=books[j];ub=float(b['best_bid']);ua=float(b['best_ask']);age=max(0.,boundary-int(b['received_ms']));avail=int(0<ub<ua<1)
            if avail:
                bids={float(p):float(q) for p,q in b.get('bids',[])};asks={float(p):float(q) for p,q in b.get('asks',[])}
                if r['weak']=='UP':
                    weak_bid,weak_ask=ub,ua;strong_bid,strong_ask=1-ua,1-ub
                    weak_bid_depth=bids.get(ub,0.);weak_ask_depth=asks.get(ua,0.);strong_bid_depth=asks.get(ua,0.);strong_ask_depth=bids.get(ub,0.)
                else:
                    weak_bid,weak_ask=1-ua,1-ub;strong_bid,strong_ask=ub,ua
                    weak_bid_depth=asks.get(ua,0.);weak_ask_depth=bids.get(ub,0.);strong_bid_depth=bids.get(ub,0.);strong_ask_depth=asks.get(ua,0.)
        # strict-past history: append current boundary observation only after computing ranks from prior boundaries
        h5=[p for t,p in weak_hist if t>=boundary-5000];h10=[p for t,p in weak_hist if t>=boundary-10000];h30=[p for t,p in weak_hist if t>=boundary-30000]
        r5,mn5,med5,mx5=stats(h5,weak_ask);r10,mn10,med10,mx10=stats(h10,weak_ask);r30,mn30,med30,mx30=stats(h30,weak_ask)
        z.update(book_available=avail,book_age_ms=age,weak_bid=weak_bid,weak_ask=weak_ask,weak_spread=max(0.,weak_ask-weak_bid) if avail else 0.,strong_bid=strong_bid,strong_ask=strong_ask,strong_spread=max(0.,strong_ask-strong_bid) if avail else 0.,weak_bid_depth_qref=weak_bid_depth/QREF,weak_ask_depth_qref=weak_ask_depth/QREF,strong_bid_depth_qref=strong_bid_depth/QREF,strong_ask_depth_qref=strong_ask_depth/QREF,
                 weak_ask_rank5=r5,weak_ask_min5=mn5,weak_ask_median5=med5,weak_ask_minus_min5=(weak_ask-mn5 if h5 and avail else 0.),weak_ask_minus_median5=(weak_ask-med5 if h5 and avail else 0.),
                 weak_ask_rank10=r10,weak_ask_min10=mn10,weak_ask_median10=med10,weak_ask_minus_min10=(weak_ask-mn10 if h10 and avail else 0.),weak_ask_minus_median10=(weak_ask-med10 if h10 and avail else 0.),
                 weak_ask_rank30=r30,weak_ask_min30=mn30,weak_ask_median30=med30,weak_ask_max30=mx30,weak_ask_minus_min30=(weak_ask-mn30 if h30 and avail else 0.),weak_ask_minus_median30=(weak_ask-med30 if h30 and avail else 0.),price_history5_available=int(bool(h5)),price_history10_available=int(bool(h10)),price_history30_available=int(bool(h30)))
        out.append(z)
        if avail: weak_hist.append((boundary,weak_ask))

P=ROOT/'.lan_worker_v1/target_opening_repair_admission_v3_book_20260915';P.mkdir(exist_ok=True)
(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
base=['strong_qref','weak_qref','gross_qref','gap_qref','abs_gap_qref','weak_to_strong','protection_coverage','strong_margin_qref','signed_weak_margin_qref','strong_since_repair_qref','seconds_since_repair','seconds_since_strong','strong_fill_3_qref','strong_fill_5_qref','strong_fill_10_qref','weak_fill_3_qref','weak_fill_5_qref','weak_fill_10_qref','last_side_strong','last_side_weak','same_side_streak_qty_qref','same_side_streak_n','last_repair_price','last_strong_price']
book=['book_available','book_age_ms','weak_bid','weak_ask','weak_spread','strong_bid','strong_ask','strong_spread','weak_bid_depth_qref','weak_ask_depth_qref','strong_bid_depth_qref','strong_ask_depth_qref','weak_ask_rank5','weak_ask_min5','weak_ask_median5','weak_ask_minus_min5','weak_ask_minus_median5','weak_ask_rank10','weak_ask_min10','weak_ask_median10','weak_ask_minus_min10','weak_ask_minus_median10','weak_ask_rank30','weak_ask_min30','weak_ask_median30','weak_ask_max30','weak_ask_minus_min30','weak_ask_minus_median30','price_history5_available','price_history10_available','price_history30_available']
S={'status':'PASS','rows':len(out),'train_rows':sum(x['split']=='train' for x in out),'validation_rows':sum(x['split']=='validation' for x in out),'book_available_rate':sum(x['book_available'] for x in out)/len(out),'features_base':base,'features_book':book,'strict_past_book':True,'selection':'latest received_ms < second boundary; no future backfill','window':'first_25_percent'}
(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps(S))
