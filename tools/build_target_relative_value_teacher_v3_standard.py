from __future__ import annotations
import bisect,collections,gzip,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
R=ROOT/'data/research/market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1'
OUT=ROOT/'.lan_worker_v1/target_relative_value_teacher_v3_standard_20260915';OUT.mkdir(parents=True,exist_ok=True)
QREF=2436.779291626123;HOLDOUT={2019018,2019143}
# results / settlement winner
res=[json.loads(x) for x in (R/'market_results.jsonl').read_text().splitlines() if x.strip()];winners={int(x['market_id']):x['winner'] for x in res}
windows={}
for line in (R/'public_snapshots.jsonl').read_text().splitlines():
 x=json.loads(line);m=int(x['market_id']);
 if m not in windows:
  s=json.loads(x['snapshot_json']);e=int(s['windowEndMs']);windows[m]=(e-300000,e)
acts=[]
for line in (R/'target_actions.jsonl').read_text().splitlines():
 a=json.loads(line)
 if a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:acts.append(a)
by=collections.defaultdict(list)
for a in acts:by[int(a['market_id'])].append(a)
for m in by:by[m].sort(key=lambda a:int(a['event_ms']))
standard=[];terminal={}
for m,w in winners.items():
 aa=by[m];up=sum(float(a['shares']) for a in aa if a['side']=='UP');dn=sum(float(a['shares']) for a in aa if a['side']=='DOWN');c=sum(float(a['shares'])*float(a['price']) for a in aa);act=(up if w=='UP' else dn)-c;opp=(dn if w=='UP' else up)-c;terminal[m]=(act,opp)
 if act>0 and opp<0:standard.append(m)
rows=[]
for m in standard:
 w=winners[m];weak='DOWN' if w=='UP' else 'UP';start,end=windows[m];aa=by[m]
 src=ROOT/'.lan_worker_v1/v49_oracle_management_pressure_v2_20260914/inputs'/f'public_{m}.json.gz';d=json.load(gzip.open(src,'rt'));books=sorted(d['books'],key=lambda z:int(z['received_ms']));bts=[int(z['received_ms']) for z in books]
 for sec in range(270):
  t=start+sec*1000;future_end=min(end,t+30000);past=[a for a in aa if int(a['event_ms'])<t];sa0=[a for a in past if a['side']==w];ra0=[a for a in past if a['side']==weak]
  sq=sum(float(a['shares']) for a in sa0);rq=sum(float(a['shares']) for a in ra0);sc=sum(float(a['shares'])*float(a['price']) for a in sa0);rc=sum(float(a['shares'])*float(a['price']) for a in ra0);c=sc+rc;sp=sq-c;wp=rq-c
  if sp<=0 or c<=1e-9 or wp/c < -0.50:continue
  fut=[a for a in aa if t<=int(a['event_ms'])<future_end];fs=sum(float(a['shares']) for a in fut if a['side']==w);fr=sum(float(a['shares']) for a in fut if a['side']==weak)
  if fs+fr<=1e-9:continue
  j=bisect.bisect_left(bts,t)-1;avail=0;sb=sa=wb=wa=sbd=sad=wbd=wad=0.;age=999999.
  if j>=0:
   b=books[j];age=max(0.,t-int(b['received_ms']));ub0=b.get('best_bid');ua0=b.get('best_ask');avail=int(ub0 is not None and ua0 is not None and 0<float(ub0)<float(ua0)<1)
   if avail:
    ub=float(ub0);ua=float(ua0);bids={float(p):float(q) for p,q in b.get('bids',[])};asks={float(p):float(q) for p,q in b.get('asks',[])}
    if w=='UP':sb,sa=ub,ua;wb,wa=1-ua,1-ub;sbd,sad=bids.get(ub,0.),asks.get(ua,0.);wbd,wad=sad,sbd
    else:sb,sa=1-ua,1-ub;wb,wa=ub,ua;sbd,sad=asks.get(ua,0.),bids.get(ub,0.);wbd,wad=sad,sbd
  def recent(side,ms):return sum(float(a['shares']) for a in past if a['side']==side and int(a['event_ms'])>=t-ms)
  ls=max((int(a['event_ms']) for a in sa0),default=None);lr=max((int(a['event_ms']) for a in ra0),default=None)
  rows.append(dict(market_id=m,sec=sec,phase=sec/300.,winner=w,split=('holdout' if m in HOLDOUT else 'train'),target_next30_strong_share=fs/(fs+fr),target_next30_strong_qty=fs,target_next30_repair_qty=fr,strong_qref=sq/QREF,repair_qref=rq/QREF,gross_qref=(sq+rq)/QREF,repair_ratio=(rq/sq if sq>1e-9 else 0.),strong_avg_price=(sc/sq if sq>1e-9 else 0.),repair_avg_price=(rc/rq if rq>1e-9 else 0.),strong_margin_qref=sp/QREF,repair_margin_qref=wp/QREF,cost_qref=c/QREF,best_roi=sp/c,floor_roi=wp/c,strong_fill_5_qref=recent(w,5000)/QREF,strong_fill_15_qref=recent(w,15000)/QREF,strong_fill_30_qref=recent(w,30000)/QREF,repair_fill_5_qref=recent(weak,5000)/QREF,repair_fill_15_qref=recent(weak,15000)/QREF,repair_fill_30_qref=recent(weak,30000)/QREF,seconds_since_strong=(999. if ls is None else (t-ls)/1000.),seconds_since_repair=(999. if lr is None else (t-lr)/1000.),last_strong_price=(float(sa0[-1]['price']) if sa0 else 0.),last_repair_price=(float(ra0[-1]['price']) if ra0 else 0.),book_available=avail,book_age_ms=age,strong_bid=sb,strong_ask=sa,weak_bid=wb,weak_ask=wa,strong_spread=(sa-sb if avail else 0.),weak_spread=(wa-wb if avail else 0.),strong_bid_depth_qref=sbd/QREF,strong_ask_depth_qref=sad/QREF,weak_bid_depth_qref=wbd/QREF,weak_ask_depth_qref=wad/QREF,relative_mid_price=((sb+sa)/2-(wb+wa)/2 if avail else 0.),pair_ask_sum=(sa+wa if avail else 0.)))
price_features=['book_available','book_age_ms','strong_bid','strong_ask','weak_bid','weak_ask','strong_spread','weak_spread','strong_bid_depth_qref','strong_ask_depth_qref','weak_bid_depth_qref','weak_ask_depth_qref','relative_mid_price','pair_ask_sum','last_strong_price','last_repair_price']
state_features=['strong_qref','repair_qref','gross_qref','repair_ratio','strong_avg_price','repair_avg_price','strong_margin_qref','repair_margin_qref','cost_qref','best_roi','floor_roi','strong_fill_5_qref','strong_fill_15_qref','strong_fill_30_qref','repair_fill_5_qref','repair_fill_15_qref','repair_fill_30_qref','seconds_since_strong','seconds_since_repair']+price_features
(OUT/'data.jsonl').write_text('\n'.join(json.dumps(r,separators=(',',':')) for r in rows)+'\n');summary=dict(status='PASS',standard_markets=standard,train_markets=[m for m in standard if m not in HOLDOUT],holdout_markets=sorted(HOLDOUT),terminal={str(m):terminal[m] for m in standard},rows=len(rows),train_rows=sum(r['split']=='train' for r in rows),holdout_rows=sum(r['split']=='holdout' for r in rows),price_features=price_features,state_features=state_features,label='Target next30 settlement-winner strong share on standard one-sided Target markets',sampling='Target terminal actual>0/opposite<0; mature state strong payoff>0 and floor_roi>=-0.50',strict_past_runtime_features=True,target_future_used_label_only=True,phase_diagnostic_only=True)
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:summary[k] for k in ('status','standard_markets','train_markets','holdout_markets','rows','train_rows','holdout_rows')}))
