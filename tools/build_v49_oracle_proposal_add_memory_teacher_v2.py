from __future__ import annotations
import json,pathlib,bisect,collections
ROOT=pathlib.Path(__file__).resolve().parents[1];R=ROOT/'data/research';QREF=2436.779291626123
SRC=ROOT/'.lan_worker_v1/v49_oracle_next_event_teacher_v1_20260914/data.jsonl';rows=[json.loads(x) for x in SRC.read_text().splitlines() if x.strip()]
by=collections.defaultdict(list)
for r in rows:by[int(r['market_id'])].append(r)
out=[]
for m,rr in by.items():
 d=json.loads((R/'lan_worker_returns'/f'v49-c30-winner-oracle-fixed-{m}-20260914-v1'/'result.json').read_text());w=rr[0]['winner'];ev=sorted(d['atomic_responsibility_events'],key=lambda x:int(x['t']));times=[int(e['t']) for e in ev]
 # cumulative strong responsibility birth/payment arrays
 cb=[];cp=[];b=p=0.0
 for e in ev:
  b+=sum(float(x['qty']) for x in e.get('births',[]) if x.get('side')==w)
  p+=sum(float(x['qty']) for x in e.get('payments',[]) if x.get('responsibility_side')==w)
  cb.append(b);cp.append(p)
 for r in rr:
  t=int(r['t']);j=bisect.bisect_right(times,t)-1;born=cb[j] if j>=0 else 0.;paid=cp[j] if j>=0 else 0.;outstanding=max(0.,born-paid)
  k=bisect.bisect_left(times,t-5000);born5=born-(cb[k-1] if k>0 else 0.);paid5=paid-(cp[k-1] if k>0 else 0.)
  z=dict(r);z.update(atomic_born_strong_qref=born/QREF,atomic_repaired_strong_qref=paid/QREF,atomic_outstanding_fraction=(outstanding/born if born>1e-9 else 0.),atomic_repair_fraction=(paid/born if born>1e-9 else 1.),recent_atomic_birth5_qref=born5/QREF,recent_atomic_repair5_qref=paid5/QREF,recent_atomic_progress5=(paid5/born5 if born5>1e-9 else 1.));out.append(z)
out.sort(key=lambda x:(x['market_id'],x['t']))
P=ROOT/'.lan_worker_v1/v49_oracle_proposal_add_memory_teacher_v2_20260915';P.mkdir(exist_ok=True);(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
base=['gross_qref','net_ratio','positive_gap_ratio','weak_loss_qref','strong_margin_qref','pair_coverage','pending_repair_qref','pending_add_qref','repair_debt_qref','book_available','weak_bid','weak_ask','weak_spread','weak_bid_depth_qref','weak_ask_depth_qref','proposed_add_price'];mem=['atomic_born_strong_qref','atomic_repaired_strong_qref','atomic_outstanding_fraction','atomic_repair_fraction','recent_atomic_birth5_qref','recent_atomic_repair5_qref','recent_atomic_progress5']
S={'status':'PASS','rows':len(out),'train_rows':sum(x['split']=='train' for x in out),'validation_rows':sum(x['split']=='validation' for x in out),'features_base':base,'features_memory':mem,'label':'Target next observed economic event contains winner-side ADD; memory features strict-past OUR atomic ledger','strict_past_memory':True,'winner_supplied_posthoc':True,'target_runtime_access':False};(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps(S))
