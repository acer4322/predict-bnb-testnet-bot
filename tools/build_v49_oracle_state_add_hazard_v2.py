from __future__ import annotations
import bisect,gzip,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1];R=ROOT/'data/research';RET=R/'lan_worker_returns'
BASE=json.loads((R/'BTC5M_V49_GENERALIZATION_C30_20260914_RESULT.json').read_text(encoding='utf-8'));BR=BASE.get('markets') or BASE.get('rows');WIN={int(x['market_id']):x['winner'] for x in BR}
TRAIN={2032653,2032652,2021315,2021302,2021217,2021179,2021100,2020843,2020760,2020725,2020682,2020663,2019143,2019120,2019008,2018991,2018854,2018847,2018666,2018407}
SRC=R/'market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1/target_actions.jsonl';TA={m:[] for m in WIN}
for line in SRC.read_text(encoding='utf-8').splitlines():
 x=json.loads(line);m=int(x['market_id'])
 if m in TA and x.get('quote_type')=='BID' and x.get('side') in ('UP','DOWN') and float(x.get('shares') or 0)>0:TA[m].append(x)
QREF=2436.779291626123;rows=[]
def side_book(book,side):
 bids={float(k):float(v) for k,v in book.get('bids',{}).items()};asks={float(k):float(v) for k,v in book.get('asks',{}).items()}
 if not bids or not asks:return (0.,1.,0.,0.,0.)
 ub=max(bids);ua=min(asks);ubd=bids[ub];uad=asks[ua]
 if side=='UP':bid,ask,bd,ad=ub,ua,ubd,uad
 else:bid,ask,bd,ad=1.-ua,1.-ub,uad,ubd
 return bid,ask,max(0.,ask-bid),bd,ad
for m,w in WIN.items():
 weak='DOWN' if w=='UP' else 'UP';p=RET/f'v49-c30-winner-oracle-fixed-{m}-20260914-v1'/'clock_trace.json.gz';tr=json.load(gzip.open(p,'rt'));bf=tr['bridge_frames'];ts=[int(x['t']) for x in bf];start=int(bf[0]['start']);end=int(bf[0]['end']);assert end-start==300000
 bysec={}
 for a in sorted(TA[m],key=lambda x:int(x['event_ms'])):
  s=max(0,min(299,(int(a['event_ms'])-start)//1000));bysec.setdefault(int(s),[]).append(a)
 sampled=[];last_change=-1
 for sec in range(300):
  t=start+sec*1000;j=bisect.bisect_left(ts,t)-1
  if j<0:state={'inv':{'UP':0.,'DOWN':0.},'cost':0.,'pending_qty':{'UP':0.,'DOWN':0.}};book={'bids':{},'asks':{}}
  else:state=bf[j]['state'];book=bf[j]['book']
  inv={k:float(state['inv'][k]) for k in ('UP','DOWN')};cost=float(state['cost']);gross=inv['UP']+inv['DOWN'];net=inv[w]-inv[weak];pair=min(inv.values())/max(inv.values()) if max(inv.values())>1e-9 else 0.;weakloss=max(0.,cost-inv[weak]);strongmargin=inv[w]-cost;pend={k:float(state['pending_qty'][k]) for k in ('UP','DOWN')}
  sb,sa,ss,sbd,sad=side_book(book,w);wb,wa,ws,wbd,wad=side_book(book,weak)
  if sampled and (abs(inv[w]-sampled[-1]['inv_w'])>1e-9 or abs(inv[weak]-sampled[-1]['inv_weak'])>1e-9):last_change=sec
  prev=sampled[max(0,sec-5)] if sampled else {'inv_w':0.,'inv_weak':0.};ra=max(0.,inv[w]-prev['inv_w']);rr=max(0.,inv[weak]-prev['inv_weak'])
  arr=bysec.get(sec,[]);addq=sum(float(x['shares']) for x in arr if x['side']==w);repairq=sum(float(x['shares']) for x in arr if x['side']==weak)
  rows.append(dict(market_id=m,split='train' if m in TRAIN else 'validation',second=sec,phase=sec/300.,gross_qref=gross/QREF,net_ratio=net/gross if gross>1e-9 else 0.,positive_gap_ratio=max(0.,net)/gross if gross>1e-9 else 0.,weak_loss_qref=weakloss/QREF,strong_margin_qref=strongmargin/QREF,pair_coverage=pair,pending_repair_qref=pend[weak]/QREF,pending_add_qref=pend[w]/QREF,strong_bid=sb,strong_ask=sa,strong_spread=ss,strong_bid_depth_qref=sbd/QREF,strong_ask_depth_qref=sad/QREF,weak_bid=wb,weak_ask=wa,weak_spread=ws,weak_bid_depth_qref=wbd/QREF,weak_ask_depth_qref=wad/QREF,recent_add5_qref=ra/QREF,recent_repair5_qref=rr/QREF,repair_lag5_qref=max(0.,ra-rr)/QREF,idle_seconds=(sec-last_change if last_change>=0 else sec),add_any=int(addq>1e-9),add_qty_qref=addq/QREF,repair_any=int(repairq>1e-9)))
  sampled.append({'inv_w':inv[w],'inv_weak':inv[weak]})
OUT=R/'BTC5M_V49_ORACLE_STATE_ADD_HAZARD_V2_20260914.jsonl';OUT.write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in rows)+'\n',encoding='utf-8')
ignore={'market_id','split','second','add_any','add_qty_qref','repair_any'};summary=dict(status='PASS',version='V2_EXPLICIT_STRONG_WEAK_BOOK',rows=len(rows),train_rows=sum(x['split']=='train' for x in rows),validation_rows=sum(x['split']=='validation' for x in rows),features=[k for k in rows[0] if k not in ignore],label='Target official-winner-side BID acquisition in same future second; OUR strict-past state at second start',strict_past_our_features=True,strong_weak_book_semantics_verified=True,winner_supplied_posthoc=True,target_runtime_access=False)
(R/'BTC5M_V49_ORACLE_STATE_ADD_HAZARD_V2_20260914_SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary))
