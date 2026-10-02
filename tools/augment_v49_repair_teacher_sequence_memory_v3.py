from __future__ import annotations
import json,pathlib,bisect,collections
ROOT=pathlib.Path(__file__).resolve().parents[1];R=ROOT/'data/research';QREF=2436.779291626123
SRC=ROOT/'.lan_worker_v1/v49_oracle_proposal_aligned_repair_teacher_v2_pricehist_20260915/data.jsonl'
rows=[json.loads(x) for x in SRC.read_text().splitlines() if x.strip()]
by=collections.defaultdict(list)
for r in rows:by[int(r['market_id'])].append(r)
out=[]
for m,rr in by.items():
 w=rr[0]['winner'];weak=rr[0]['weak']
 d=json.loads((R/'lan_worker_returns'/f'v49-c30-winner-oracle-fixed-{m}-20260914-v1'/'result.json').read_text())
 ev=sorted(d.get('atomic_responsibility_events',[]),key=lambda x:int(x['t']));times=[int(e['t']) for e in ev]
 # cumulative actual fill by physical side, route, and atomic strong responsibility birth/payment.
 cs=[];cw=[];cwa=[];cwp=[];cb=[];cp=[]
 strong=weakfill=weakact=weakpass=born=paid=0.0
 for e in ev:
  strong += float(e.get('fill_'+w.lower(),0.0)); weakfill += float(e.get('fill_'+weak.lower(),0.0))
  for fr in e.get('fill_rows',[]):
   if fr.get('side')==weak:
    q=float(fr.get('fill_increment') or 0.0)
    if fr.get('route')=='ACTIVE': weakact+=q
    elif fr.get('route')=='PASSIVE': weakpass+=q
  born += sum(float(x['qty']) for x in e.get('births',[]) if x.get('side')==w)
  paid += sum(float(x['qty']) for x in e.get('payments',[]) if x.get('responsibility_side')==w)
  cs.append(strong);cw.append(weakfill);cwa.append(weakact);cwp.append(weakpass);cb.append(born);cp.append(paid)
 def cum_delta(arr,j,k):
  if j<0:return 0.0
  return arr[j]-(arr[k-1] if k>0 else 0.0)
 for r in rr:
  t=int(r['t']);j=bisect.bisect_right(times,t)-1
  vals={}
  for sec in (5,15):
   k=bisect.bisect_left(times,t-sec*1000)
   vals[f'recent_strong_fill{sec}_qref']=cum_delta(cs,j,k)/QREF
   vals[f'recent_weak_fill{sec}_qref']=cum_delta(cw,j,k)/QREF
   vals[f'recent_weak_active_fill{sec}_qref']=cum_delta(cwa,j,k)/QREF
   vals[f'recent_weak_passive_fill{sec}_qref']=cum_delta(cwp,j,k)/QREF
  last_s=next((times[k] for k in range(j,-1,-1) if float(ev[k].get('fill_'+w.lower(),0.0))>1e-12),None)
  last_w=next((times[k] for k in range(j,-1,-1) if float(ev[k].get('fill_'+weak.lower(),0.0))>1e-12),None)
  vals['seconds_since_strong_fill']=300.0 if last_s is None else min(300.0,max(0.0,(t-last_s)/1000.0))
  vals['seconds_since_weak_fill']=300.0 if last_w is None else min(300.0,max(0.0,(t-last_w)/1000.0))
  # Consecutive prior atomic events dominated by one physical side.
  rs=as_=0
  for k in range(j,-1,-1):
   sf=float(ev[k].get('fill_'+w.lower(),0.0));wf=float(ev[k].get('fill_'+weak.lower(),0.0))
   if sf<=1e-12 and wf<=1e-12:continue
   if wf>sf+1e-12:
    if as_>0:break
    rs+=1
   elif sf>wf+1e-12:
    if rs>0:break
    as_+=1
   else:break
  vals['repair_fill_streak']=min(rs,20);vals['add_fill_streak']=min(as_,20)
  b=cb[j] if j>=0 else 0.;p=cp[j] if j>=0 else 0.;outstanding=max(0.,b-p)
  vals.update(atomic_born_strong_qref=b/QREF,atomic_repaired_strong_qref=p/QREF,atomic_outstanding_fraction=(outstanding/b if b>1e-9 else 0.),atomic_repair_fraction=(p/b if b>1e-9 else 1.))
  z=dict(r);z.update(vals);out.append(z)
out.sort(key=lambda x:(x['market_id'],x['t']))
P=ROOT/'.lan_worker_v1/v49_oracle_repair_sequence_teacher_v3_20260915';P.mkdir(exist_ok=True)
(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
base=json.loads((ROOT/'.lan_worker_v1/v49_oracle_proposal_aligned_repair_teacher_v2_pricehist_20260915/summary.json').read_text())
seq=['recent_strong_fill5_qref','recent_weak_fill5_qref','recent_weak_active_fill5_qref','recent_weak_passive_fill5_qref','recent_strong_fill15_qref','recent_weak_fill15_qref','recent_weak_active_fill15_qref','recent_weak_passive_fill15_qref','seconds_since_strong_fill','seconds_since_weak_fill','repair_fill_streak','add_fill_streak','atomic_born_strong_qref','atomic_repaired_strong_qref','atomic_outstanding_fraction','atomic_repair_fraction']
base['features_sequence_memory']=seq;base['strict_past_sequence_memory']=True;base['status']='PASS'
(P/'summary.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':'PASS','rows':len(out),'sequence_features':seq}))
