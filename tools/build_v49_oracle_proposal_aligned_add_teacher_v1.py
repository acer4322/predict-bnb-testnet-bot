from __future__ import annotations
import json,gzip,pathlib,collections,bisect,math
ROOT=pathlib.Path(__file__).resolve().parents[1];R=ROOT/'data/research';QREF=2436.779291626123
BASE=json.loads((R/'BTC5M_V49_C30_OFFICIAL_WINNER_ORACLE_FIXED_V1_20260914_RAW.json').read_text());rows0=BASE['rows'];winner={int(x['market_id']):x['winner'] for x in rows0}
summary=json.loads((ROOT/'.lan_worker_v1/repair_only_microworld_v2_price_20260914/data_summary.json').read_text());train=set(summary['train_markets']);valid=set(summary['validation_markets'])
acts=[json.loads(x) for x in (R/'market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1/target_actions.jsonl').read_text().splitlines() if x.strip()];aby=collections.defaultdict(list)
for a in acts:
 if a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:aby[int(a['market_id'])].append(a)
for m in aby:aby[m].sort(key=lambda x:int(x['event_ms']))
out=[]
for m,w in winner.items():
 p=R/'lan_worker_returns'/f'v49-c30-winner-oracle-fixed-{m}-20260914-v1'/'clock_trace.json.gz';tr=json.load(gzip.open(p,'rt'))
 frames={(int(x['index']),int(x['t'])):x for x in tr['bridge_frames']}; intents={(int(x['index']),int(x['t'])):x for x in tr['intent']}
 # one row per frame with at least one proposed strong PASSIVE NEW, before tail veto
 seen=set()
 for q in tr['tail_new_rows']:
  if q.get('side')!=w or q.get('route')!='PASSIVE':continue
  key=(int(q['index']),int(q['t']))
  if key in seen:continue
  seen.add(key);f=frames.get(key);it=intents.get(key)
  if f is None:continue
  st=f['state'];inv=st['inv'];cost=float(st['cost']);weak='DOWN' if w=='UP' else 'UP';gross=float(inv['UP'])+float(inv['DOWN']);net=(float(inv[w])-float(inv[weak]))/gross if gross>1e-12 else 0.;pair=1-abs(net) if gross>1e-12 else 0.
  book=f['book'];bids=book.get('bids') or {};asks=book.get('asks') or {};available=bool(bids and asks);wb=wa=wbd=wad=spread=0.
  if available:
   ub=max(float(x) for x in bids);ua=min(float(x) for x in asks)
   # json keys may be strings
   bd=float(bids.get(str(ub),bids.get(ub,0.)));ad=float(asks.get(str(ua),asks.get(ua,0.)))
   if weak=='UP':wb,wa,wbd,wad=ub,ua,bd,ad
   else:wb,wa,wbd,wad=1-ua,1-ub,ad,bd
   spread=max(0.,wa-wb)
  ao=(it or {}).get('atomic_outstanding') or {'UP':0.,'DOWN':0.}
  t=int(q['t']);future=[a for a in aby[m] if t<=int(a['event_ms'])<t+1000 and a['side']==w]
  target_qty=sum(float(a['shares']) for a in future);target_taker=sum(float(a['shares']) for a in future if a.get('role')=='TAKER')
  out.append(dict(market_id=m,index=key[0],t=t,winner=w,phase=max(0.,min(1.,(t-int(f['start']))/max(1,int(f['end'])-int(f['start'])))),
   gross_qref=gross/QREF,net_ratio=net,positive_gap_ratio=max(0.,net),weak_loss_qref=max(0.,cost-float(inv[weak]))/QREF,strong_margin_qref=(float(inv[w])-cost)/QREF,pair_coverage=pair,
   pending_repair_qref=float(st['pending_qty'][weak])/QREF,pending_add_qref=float(st['pending_qty'][w])/QREF,repair_debt_qref=float(ao.get(w,0.))/QREF,
   book_available=1 if available else 0,weak_bid=wb,weak_ask=wa,weak_spread=spread,weak_bid_depth_qref=wbd/QREF,weak_ask_depth_qref=wad/QREF,
   proposed_add_price=float(q['price']),proposed_add_qty_qref=float(q['qty'])/QREF,original_tail_blocked=bool(q.get('blocked')),
   target_add_next1s=int(target_qty>0),target_add_qty_next1s_qref=target_qty/QREF,target_taker_add_next1s=int(target_taker>0),split='train' if m in train else 'validation'))
out.sort(key=lambda x:(x['market_id'],x['t']))
P=ROOT/'.lan_worker_v1/v49_oracle_proposal_aligned_add_teacher_v1_20260914';P.mkdir(exist_ok=True)
(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
features=['gross_qref','net_ratio','positive_gap_ratio','weak_loss_qref','strong_margin_qref','pair_coverage','pending_repair_qref','pending_add_qref','repair_debt_qref','book_available','weak_bid','weak_ask','weak_spread','weak_bid_depth_qref','weak_ask_depth_qref','proposed_add_price']
S={'status':'PASS','rows':len(out),'train_rows':sum(x['split']=='train' for x in out),'validation_rows':sum(x['split']=='validation' for x in out),'train_markets':sorted(train),'validation_markets':sorted(valid),'features':features,'label':'Target winner-side BID acquisition in [OUR proposal t, t+1000ms)','strict_future_teacher_only':True,'winner_supplied_posthoc':True,'target_runtime_access':False,'original_tail_blocked_rows':sum(x['original_tail_blocked'] for x in out)}
(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps(S))
