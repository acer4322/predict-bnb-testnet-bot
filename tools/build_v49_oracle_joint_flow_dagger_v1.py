from __future__ import annotations
import json,gzip,pathlib,collections,bisect
ROOT=pathlib.Path(__file__).resolve().parents[1];R=ROOT/'data/research';RET=R/'lan_worker_returns';QREF=2436.779291626123
split=json.loads((ROOT/'.lan_worker_v1/repair_only_microworld_v2_price_20260914/data_summary.json').read_text());train=set(split['train_markets']);valid=set(split['validation_markets'])
base_rows=json.loads((R/'BTC5M_V49_C30_OFFICIAL_WINNER_ORACLE_FIXED_V1_20260914_RAW.json').read_text())['rows'];winners={int(x['market_id']):x['winner'] for x in base_rows}
acts=[json.loads(x) for x in (R/'market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1/target_actions.jsonl').read_text().splitlines() if x.strip()];aby=collections.defaultdict(list)
for a in acts:
 if a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:aby[int(a['market_id'])].append(a)
for m in aby:aby[m].sort(key=lambda x:(int(x['event_ms']),x.get('side',''),x.get('role','')))
traces=[]
for m in winners:traces.append((m,'BASELINE',f'v49-c30-winner-oracle-fixed-{m}-20260914-v1'))
stress=[2020843,2019008,2021100,2021302,2021217,2020718]
policies=[('P3','v49-oracle-pressure-p3-{m}-20260914-v1'),('STATEFREQ','v49-oracle-statefreq-{m}-20260914-v1'),('FULLFREQ','v49-oracle-fullfreq-{m}-20260914-v1'),('NEXTFREQ','v49-oracle-nexteventfreq-{m}-20260914-v1'),('HYBRID','v49-oracle-hybridfreq-{m}-20260914-v1'),('PROP1S','v49-oracle-proposalgate-{m}-20260914-v1'),('NEXTGATE','v49-oracle-nexteventgate-{m}-20260914-v1'),('SOFT','v49-oracle-softtail-{m}-20260914-v2')]
for m in stress:
 for tag,pat in policies:traces.append((m,tag,pat.format(m=m)))
for tag,j in [('REPAIRFREQ','v49-oracle-hybrid-repairfreq-2021100-20260915-v1'),('MEMORY','v49-oracle-repairfreq-memoryadd-2021100-20260915-v1'),('PRICEHIST','v49-oracle-pricehist-repairfreq-memoryadd-2021100-20260915-v1'),('SEQUENCE','v49-oracle-sequence-repairfreq-memoryadd-2021100-20260915-v1')]:traces.append((2021100,tag,j))
out=[];missing=[]
for m,tag,jid in traces:
 folder=RET/jid
 if not (folder/'result.json').exists() or not (folder/'clock_trace.json.gz').exists():missing.append(jid);continue
 d=json.loads((folder/'result.json').read_text());
 if d.get('status')!='COMPLETE' or not d.get('safety_gate',{}).get('pass',False):continue
 tr=json.load(gzip.open(folder/'clock_trace.json.gz','rt'));w=winners[m];weak='DOWN' if w=='UP' else 'UP'
 frames={(int(x['index']),int(x['t'])):x for x in tr.get('bridge_frames',[])};intents={(int(x['index']),int(x['t'])):x for x in tr.get('intent',[])}
 # collect both role proposals per frame from tail observer
 props=collections.defaultdict(lambda:{'add':None,'repair':None})
 for q in tr.get('tail_new_rows',[]):
  if q.get('route')!='PASSIVE':continue
  key=(int(q['index']),int(q['t']));side=q.get('side')
  if side==w and props[key]['add'] is None:props[key]['add']=q
  elif side==weak and props[key]['repair'] is None:props[key]['repair']=q
 ev=sorted(d.get('atomic_responsibility_events',[]),key=lambda x:int(x['t']));etimes=[int(e['t']) for e in ev]
 cb=[];cp=[];born=paid=0.0
 for e in ev:
  born+=sum(float(x['qty']) for x in e.get('births',[]) if x.get('side')==w);paid+=sum(float(x['qty']) for x in e.get('payments',[]) if x.get('responsibility_side')==w);cb.append(born);cp.append(paid)
 aa=aby[m];ats=[int(a['event_ms']) for a in aa]
 for key,pq in props.items():
  f=frames.get(key);it=intents.get(key)
  if f is None:continue
  st=f['state'];inv=st['inv'];cost=float(st['cost']);gross=float(inv['UP'])+float(inv['DOWN']);net=(float(inv[w])-float(inv[weak]))/gross if gross>1e-12 else 0.;pair=1-abs(net) if gross>1e-12 else 0.;sm=(float(inv[w])-cost)/QREF;wm=(float(inv[weak])-cost)/QREF;loss=max(0.,-wm);gain=max(0.,sm);risk=loss/(gain+loss) if gain+loss>1e-12 else 0.
  book=f['book'];bids=book.get('bids') or {};asks=book.get('asks') or {};available=bool(bids and asks);wb=wa=wbd=wad=spread=0.
  if available:
   ub=max(float(x) for x in bids);ua=min(float(x) for x in asks);bd=float(bids.get(str(ub),bids.get(ub,0.)));ad=float(asks.get(str(ua),asks.get(ua,0.)))
   if weak=='UP':wb,wa,wbd,wad=ub,ua,bd,ad
   else:wb,wa,wbd,wad=1-ua,1-ub,ad,bd
   spread=max(0.,wa-wb)
  t=int(key[1]);ei=bisect.bisect_right(etimes,t)-1;b=cb[ei] if ei>=0 else 0.;p=cp[ei] if ei>=0 else 0.;outs=max(0.,b-p)
  def recent(sec):
   sf=wf=waq=wpq=0.0;cut=t-sec*1000
   for e in reversed(ev[:ei+1] if ei>=0 else []):
    if int(e['t'])<cut:break
    sf+=float(e.get('fill_'+w.lower(),0.0));wf+=float(e.get('fill_'+weak.lower(),0.0))
    for fr in e.get('fill_rows',[]):
     if fr.get('side')!=weak:continue
     qq=float(fr.get('fill_increment') or 0.0)
     if fr.get('route')=='ACTIVE':waq+=qq
     elif fr.get('route')=='PASSIVE':wpq+=qq
   return sf,wf,waq,wpq
  s5,w5,a5,wp5=recent(5);s15,w15,a15,wp15=recent(15)
  last_s=last_w=None;rs=ads=0
  for k in range(ei,-1,-1):
   e=ev[k];sf=float(e.get('fill_'+w.lower(),0.0));wf=float(e.get('fill_'+weak.lower(),0.0))
   if last_s is None and sf>1e-12:last_s=int(e['t'])
   if last_w is None and wf>1e-12:last_w=int(e['t'])
  for k in range(ei,-1,-1):
   e=ev[k];sf=float(e.get('fill_'+w.lower(),0.0));wf=float(e.get('fill_'+weak.lower(),0.0))
   if sf<=1e-12 and wf<=1e-12:continue
   if wf>sf+1e-12:
    if ads>0:break
    rs+=1
   elif sf>wf+1e-12:
    if rs>0:break
    ads+=1
   else:break
  ti=bisect.bisect_left(ats,t);mode='NONE';delay=None;addq=repq=0.0
  if ti<len(aa):
   et=ats[ti];bucket=[]
   while ti<len(aa) and int(aa[ti]['event_ms'])==et:bucket.append(aa[ti]);ti+=1
   addq=sum(float(a['shares']) for a in bucket if a['side']==w);repq=sum(float(a['shares']) for a in bucket if a['side']==weak);mode='MIXED' if addq>0 and repq>0 else 'ADD' if addq>0 else 'REPAIR' if repq>0 else 'NONE';delay=max(0,et-t)
  total=addq+repq;repair_share=(repq/total if total>1e-12 else .5)
  ao=(it or {}).get('atomic_outstanding') or {w:outs};addp=float(pq['add']['price']) if pq['add'] is not None else 0.;repp=float(pq['repair']['price']) if pq['repair'] is not None else 0.
  out.append(dict(market_id=m,source_policy=tag,index=key[0],t=t,winner=w,split='train' if m in train else 'validation',validation_kind=('SHIFTED' if m==2020718 and tag!='BASELINE' else 'BASELINE' if m in valid else 'TRAIN'),gross_qref=gross/QREF,net_ratio=net,positive_gap_ratio=max(0.,net),signed_weak_margin_qref=wm,weak_loss_qref=loss,strong_margin_qref=sm,risk_ratio=risk,pair_coverage=pair,pending_repair_qref=float(st['pending_qty'][weak])/QREF,pending_add_qref=float(st['pending_qty'][w])/QREF,repair_debt_qref=float(ao.get(w,outs))/QREF,uncovered_repair_debt_qref=max(0.,float(ao.get(w,outs))-float(st['pending_qty'][weak]))/QREF,book_available=int(available),weak_bid=wb,weak_ask=wa,weak_spread=spread,weak_bid_depth_qref=wbd/QREF,weak_ask_depth_qref=wad/QREF,proposal_add_available=int(pq['add'] is not None),proposal_repair_available=int(pq['repair'] is not None),proposed_add_price=addp,proposed_repair_price=repp,atomic_born_strong_qref=b/QREF,atomic_repaired_strong_qref=p/QREF,atomic_outstanding_fraction=(outs/b if b>1e-9 else 0.),atomic_repair_fraction=(p/b if b>1e-9 else 1.),recent_strong_fill5_qref=s5/QREF,recent_weak_fill5_qref=w5/QREF,recent_weak_active_fill5_qref=a5/QREF,recent_weak_passive_fill5_qref=wp5/QREF,recent_strong_fill15_qref=s15/QREF,recent_weak_fill15_qref=w15/QREF,recent_weak_active_fill15_qref=a15/QREF,recent_weak_passive_fill15_qref=wp15/QREF,seconds_since_strong_fill=300. if last_s is None else min(300.,max(0.,(t-last_s)/1000)),seconds_since_weak_fill=300. if last_w is None else min(300.,max(0.,(t-last_w)/1000)),repair_fill_streak=min(rs,20),add_fill_streak=min(ads,20),next_mode=mode,next_event_delay_ms=delay,next_repair_share=repair_share,next_event_has_repair=int(mode in ('REPAIR','MIXED')),next_event_has_add=int(mode in ('ADD','MIXED')),next_add_qty_qref=addq/QREF,next_repair_qty_qref=repq/QREF))
out.sort(key=lambda x:(x['split'],x['market_id'],x['source_policy'],x['t']))
P=ROOT/'.lan_worker_v1/v49_oracle_joint_flow_dagger_v1_20260915';P.mkdir(exist_ok=True);(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
features=['gross_qref','net_ratio','positive_gap_ratio','signed_weak_margin_qref','weak_loss_qref','strong_margin_qref','risk_ratio','pair_coverage','pending_repair_qref','pending_add_qref','repair_debt_qref','uncovered_repair_debt_qref','book_available','weak_bid','weak_ask','weak_spread','weak_bid_depth_qref','weak_ask_depth_qref','proposal_add_available','proposal_repair_available','proposed_add_price','proposed_repair_price','atomic_born_strong_qref','atomic_repaired_strong_qref','atomic_outstanding_fraction','atomic_repair_fraction','recent_strong_fill5_qref','recent_weak_fill5_qref','recent_weak_active_fill5_qref','recent_weak_passive_fill5_qref','recent_strong_fill15_qref','recent_weak_fill15_qref','recent_weak_active_fill15_qref','recent_weak_passive_fill15_qref','seconds_since_strong_fill','seconds_since_weak_fill','repair_fill_streak','add_fill_streak']
counts=collections.Counter((x['split'],x['validation_kind'],x['source_policy']) for x in out);S={'status':'PASS','rows':len(out),'train_rows':sum(x['split']=='train' for x in out),'validation_rows':sum(x['split']=='validation' for x in out),'features':features,'train_markets':sorted(train),'validation_markets':sorted(valid),'missing':missing,'group_counts':{'|'.join(k):v for k,v in counts.items()},'label':'Target next-event repair share after CURRENT-POLICY OUR proposal state','strict_past_our_features':True,'teacher_future_label_only':True,'target_runtime_access':False};(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps({'status':'PASS','rows':len(out),'train':S['train_rows'],'validation':S['validation_rows'],'missing':missing,'groups':len(counts)}))
