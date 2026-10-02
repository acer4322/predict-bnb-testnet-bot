from __future__ import annotations
import bisect,collections,gzip,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
R=ROOT/'data/research/market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1'
OUT=ROOT/'.lan_worker_v1/target_safe_build_complete_teacher_v2_20260915';OUT.mkdir(parents=True,exist_ok=True)
HOLDOUT={2019018,2019143,2019008,2019427,2021315}
QREF=2436.779291626123
res=[json.loads(x) for x in (R/'market_results.jsonl').read_text().splitlines() if x.strip()]
winners={int(x['market_id']):x['winner'] for x in res}
windows={}
for line in (R/'public_snapshots.jsonl').read_text().splitlines():
    if not line.strip():continue
    x=json.loads(line);m=int(x['market_id'])
    if m not in windows:
        s=json.loads(x['snapshot_json']);end=int(s['windowEndMs']);windows[m]=(end-300000,end)
acts=[]
for line in (R/'target_actions.jsonl').read_text().splitlines():
    if not line.strip():continue
    a=json.loads(line)
    if a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:acts.append(a)
by=collections.defaultdict(list)
for a in acts:by[int(a['market_id'])].append(a)
for m in by:by[m].sort(key=lambda a:int(a['event_ms']))
rows=[];meta={}
for m,w in winners.items():
    aa=by[m];weak='DOWN' if w=='UP' else 'UP';start,end=windows[m]
    up=sum(float(a['shares']) for a in aa if a['side']=='UP');dn=sum(float(a['shares']) for a in aa if a['side']=='DOWN');tcost=sum(float(a['shares'])*float(a['price']) for a in aa)
    actual=(up if w=='UP' else dn)-tcost;opp=(dn if w=='UP' else up)-tcost
    standard=bool(actual>0 and opp<0)
    final_strong=sum(float(a['shares']) for a in aa if a['side']==w);assert final_strong>0
    src=ROOT/'.lan_worker_v1/v49_oracle_management_pressure_v2_20260914/inputs'/f'public_{m}.json.gz'
    d=json.load(gzip.open(src,'rt'));books=sorted(d['books'],key=lambda z:int(z['received_ms']));bts=[int(z['received_ms']) for z in books]
    true_first=None
    for sec in range(300):
        t=start+sec*1000;past=[a for a in aa if int(a['event_ms'])<t];strong=[a for a in past if a['side']==w];repair=[a for a in past if a['side']==weak]
        sq=sum(float(a['shares']) for a in strong);rq=sum(float(a['shares']) for a in repair);sc=sum(float(a['shares'])*float(a['price']) for a in strong);rc=sum(float(a['shares'])*float(a['price']) for a in repair);cost=sc+rc
        complete=max(0.,final_strong-sq)<=0.10*final_strong+1e-9;label=int(standard and complete)
        if label and true_first is None:true_first=sec
        j=bisect.bisect_left(bts,t)-1;avail=0;strong_bid=strong_ask=weak_bid=weak_ask=0.;sbd=sad=wbd=wad=0.;age=999999.
        if j>=0:
            b=books[j];age=max(0.,t-int(b['received_ms']));ub0=b.get('best_bid');ua0=b.get('best_ask');avail=int(ub0 is not None and ua0 is not None and 0<float(ub0)<float(ua0)<1)
            if avail:
                ub=float(ub0);ua=float(ua0);bids={float(p):float(q) for p,q in b.get('bids',[])};asks={float(p):float(q) for p,q in b.get('asks',[])}
                if w=='UP':
                    strong_bid,strong_ask=ub,ua;weak_bid,weak_ask=1-ua,1-ub;sbd,sad=bids.get(ub,0.),asks.get(ua,0.);wbd,wad=asks.get(ua,0.),bids.get(ub,0.)
                else:
                    strong_bid,strong_ask=1-ua,1-ub;weak_bid,weak_ask=ub,ua;sbd,sad=asks.get(ua,0.),bids.get(ub,0.);wbd,wad=bids.get(ub,0.),asks.get(ua,0.)
        def recent(side,ms):return sum(float(a['shares']) for a in past if a['side']==side and int(a['event_ms'])>=t-ms)
        ls=max((int(a['event_ms']) for a in strong),default=None);lr=max((int(a['event_ms']) for a in repair),default=None)
        rows.append(dict(market_id=m,sec=sec,phase=sec/300.,winner=w,label_safe_complete=label,standard_one_sided=standard,
            strong_qref=sq/QREF,repair_qref=rq/QREF,gross_qref=(sq+rq)/QREF,repair_ratio=(rq/sq if sq>1e-9 else 0.),strong_avg_price=(sc/sq if sq>1e-9 else 0.),repair_avg_price=(rc/rq if rq>1e-9 else 0.),strong_margin_qref=(sq-cost)/QREF,repair_margin_qref=(rq-cost)/QREF,cost_qref=cost/QREF,strong_fill_5_qref=recent(w,5000)/QREF,strong_fill_15_qref=recent(w,15000)/QREF,strong_fill_30_qref=recent(w,30000)/QREF,repair_fill_5_qref=recent(weak,5000)/QREF,repair_fill_15_qref=recent(weak,15000)/QREF,repair_fill_30_qref=recent(weak,30000)/QREF,seconds_since_strong=(999. if ls is None else (t-ls)/1000.),seconds_since_repair=(999. if lr is None else (t-lr)/1000.),last_strong_price=(float(strong[-1]['price']) if strong else 0.),last_repair_price=(float(repair[-1]['price']) if repair else 0.),book_available=avail,book_age_ms=age,strong_bid=strong_bid,strong_ask=strong_ask,weak_bid=weak_bid,weak_ask=weak_ask,strong_spread=(strong_ask-strong_bid if avail else 0.),weak_spread=(weak_ask-weak_bid if avail else 0.),strong_bid_depth_qref=sbd/QREF,strong_ask_depth_qref=sad/QREF,weak_bid_depth_qref=wbd/QREF,weak_ask_depth_qref=wad/QREF,final_strong_qref=final_strong/QREF,split=('holdout' if m in HOLDOUT else 'train')))
    meta[m]=dict(winner=w,target_actual=actual,target_opposite=opp,standard_one_sided=standard,true_safe_first=(true_first if true_first is not None else 301),split=('holdout' if m in HOLDOUT else 'train'))
features=['strong_qref','repair_qref','gross_qref','repair_ratio','strong_avg_price','repair_avg_price','strong_margin_qref','repair_margin_qref','cost_qref','strong_fill_5_qref','strong_fill_15_qref','strong_fill_30_qref','repair_fill_5_qref','repair_fill_15_qref','repair_fill_30_qref','seconds_since_strong','seconds_since_repair','last_strong_price','last_repair_price','book_available','book_age_ms','strong_bid','strong_ask','weak_bid','weak_ask','strong_spread','weak_spread','strong_bid_depth_qref','strong_ask_depth_qref','weak_bid_depth_qref','weak_ask_depth_qref']
(OUT/'data.jsonl').write_text('\n'.join(json.dumps(r,separators=(',',':')) for r in rows)+'\n')
summary=dict(status='PASS',markets=sorted(meta),train_markets=sorted(m for m in meta if m not in HOLDOUT),holdout_markets=sorted(HOLDOUT),rows=len(rows),train_rows=sum(r['split']=='train' for r in rows),holdout_rows=sum(r['split']=='holdout' for r in rows),features=features,label='standard_one_sided_target_regime AND remaining_final_strong_qty <= 10% of final strong qty',strict_past_runtime_features=True,target_future_used_teacher_label_only=True,target_runtime_access=False,phase_diagnostic_only=True,market_meta={str(k):v for k,v in meta.items()})
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='market_meta'}))
