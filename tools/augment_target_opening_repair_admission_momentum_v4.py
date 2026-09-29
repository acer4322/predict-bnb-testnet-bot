from __future__ import annotations
import bisect,gzip,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=ROOT/'.lan_worker_v1/target_opening_repair_admission_v3_book_20260915/data.jsonl'
rows=[json.loads(x) for x in SRC.read_text().splitlines() if x.strip()]
QREF=2436.779291626123
by={}
for r in rows: by.setdefault(int(r['market_id']),[]).append(r)
out=[]

def prior(hist,boundary,lag_ms):
    cand=[x for x in hist if x[0] <= boundary-lag_ms]
    return cand[-1] if cand else None

for m,rr in by.items():
    pkg=ROOT/'.lan_worker_v1/v49_oracle_management_pressure_v2_20260914/inputs'/f'public_{m}.json.gz'
    d=json.load(gzip.open(pkg,'rt')); start=int(d['market']['window_start_ms']); books=sorted(d['books'],key=lambda x:int(x['received_ms'])); ts=[int(x['received_ms']) for x in books]
    hist=[]
    for r in sorted(rr,key=lambda x:int(x['sec'])):
        boundary=start+int(r['sec'])*1000
        j=bisect.bisect_left(ts,boundary)-1
        z=dict(r)
        cur=None
        if j>=0:
            b=books[j];ub=float(b['best_bid']);ua=float(b['best_ask']);avail=int(0<ub<ua<1)
            if avail:
                bids={float(p):float(q) for p,q in b.get('bids',[])}; asks={float(p):float(q) for p,q in b.get('asks',[])}
                if r['weak']=='UP':
                    wb,wa=ub,ua; sb,sa=1-ua,1-ub
                    wbd=bids.get(ub,0.);wad=asks.get(ua,0.);sbd=asks.get(ua,0.);sad=bids.get(ub,0.)
                else:
                    wb,wa=1-ua,1-ub; sb,sa=ub,ua
                    wbd=asks.get(ua,0.);wad=bids.get(ub,0.);sbd=bids.get(ub,0.);sad=asks.get(ua,0.)
                cur=(boundary,wb,wa,sb,sa,wbd/QREF,wad/QREF,sbd/QREF,sad/QREF)
        for lag in (1,3,5,10):
            p=prior(hist,boundary,lag*1000)
            if cur is not None and p is not None:
                z[f'weak_bid_delta{lag}']=cur[1]-p[1]; z[f'weak_ask_delta{lag}']=cur[2]-p[2]
                z[f'strong_bid_delta{lag}']=cur[3]-p[3]; z[f'strong_ask_delta{lag}']=cur[4]-p[4]
                z[f'weak_bid_depth_delta{lag}_qref']=cur[5]-p[5]; z[f'weak_ask_depth_delta{lag}_qref']=cur[6]-p[6]
                z[f'weak_depth_ratio{lag}']=(cur[6]+1e-6)/(cur[5]+1e-6)
                z[f'book_move{lag}_available']=1
            else:
                for k in ('weak_bid_delta','weak_ask_delta','strong_bid_delta','strong_ask_delta','weak_bid_depth_delta','weak_ask_depth_delta'):
                    z[f'{k}{lag}' if 'depth' not in k else f'{k}{lag}_qref']=0.0
                z[f'weak_depth_ratio{lag}']=1.0;z[f'book_move{lag}_available']=0
        # contemporaneous serviceability ratios from strict-past book
        z['weak_depth_ratio_now']=(float(z.get('weak_ask_depth_qref',0))+1e-6)/(float(z.get('weak_bid_depth_qref',0))+1e-6)
        z['cross_side_ask_depth_ratio']=(float(z.get('weak_ask_depth_qref',0))+1e-6)/(float(z.get('strong_ask_depth_qref',0))+1e-6)
        out.append(z)
        if cur is not None:hist.append(cur)
P=ROOT/'.lan_worker_v1/target_opening_repair_admission_v4_momentum_20260915';P.mkdir(exist_ok=True)
(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
base=json.loads((ROOT/'.lan_worker_v1/target_opening_repair_admission_v3_book_20260915/summary.json').read_text())
mom=[]
for lag in (1,3,5,10):
    mom += [f'weak_bid_delta{lag}',f'weak_ask_delta{lag}',f'strong_bid_delta{lag}',f'strong_ask_delta{lag}',f'weak_bid_depth_delta{lag}_qref',f'weak_ask_depth_delta{lag}_qref',f'weak_depth_ratio{lag}',f'book_move{lag}_available']
mom += ['weak_depth_ratio_now','cross_side_ask_depth_ratio']
S={'status':'PASS','rows':len(out),'features_base':base['features_base'],'features_book':base['features_book'],'features_momentum':mom,'strict_past_book':True,'selection':'latest received_ms < second boundary; lagged observations at or before boundary-lag','window':'first_25_percent'}
(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps(S))
