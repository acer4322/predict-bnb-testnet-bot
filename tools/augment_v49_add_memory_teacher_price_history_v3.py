from __future__ import annotations
import json,gzip,pathlib,collections,bisect,statistics
ROOT=pathlib.Path(__file__).resolve().parents[1];SRC=ROOT/'.lan_worker_v1/v49_oracle_proposal_add_memory_teacher_v2_20260915';rows=[json.loads(x) for x in (SRC/'data.jsonl').read_text().splitlines() if x.strip()];by=collections.defaultdict(list)
for r in rows:by[int(r['market_id'])].append(r)
PKG=ROOT/'.lan_worker_v1/v49_c30_winner_oracle_fixed_v1_20260914';out=[]
for m,rr in by.items():
 src=json.loads(gzip.decompress((PKG/'inputs'/f'public_{m}.json.gz').read_bytes()));books=src['books'];winner=rr[0]['winner'];weak='DOWN' if winner=='UP' else 'UP';hist=[];times=[]
 for b in books:
  rb=b.get('received_ms');ub=b.get('best_bid');ua=b.get('best_ask')
  if rb is None or ub is None or ua is None:continue
  wa=float(ua) if weak=='UP' else 1.-float(ub)
  if not 0<wa<1:continue
  times.append(int(rb));hist.append(wa)
 for r in rr:
  t=int(r['t']);j=bisect.bisect_right(times,t);k30=bisect.bisect_left(times,t-30000,0,j);k10=bisect.bisect_left(times,t-10000,0,j);v30=hist[k30:j];v10=hist[k10:j];cur=float(r['weak_ask'])
  def fs(vals):
   if not vals:return (0.,0.,0.,0.)
   ss=sorted(vals);n=len(ss);med=statistics.median(ss);rank=sum(x<=cur+1e-12 for x in ss)/n;return rank,ss[0],med,ss[-1]
  r30,mn30,med30,mx30=fs(v30);r10,mn10,med10,mx10=fs(v10);z=dict(r);z.update(weak_ask_rank30=r30,weak_ask_min30=mn30,weak_ask_median30=med30,weak_ask_max30=mx30,weak_ask_minus_min30=(cur-mn30 if v30 else 0.),weak_ask_minus_median30=(cur-med30 if v30 else 0.),weak_ask_rank10=r10,weak_ask_min10=mn10,weak_ask_median10=med10,weak_ask_minus_min10=(cur-mn10 if v10 else 0.),price_history30_available=int(bool(v30)),price_history10_available=int(bool(v10)));out.append(z)
out.sort(key=lambda x:(x['market_id'],x['t']));P=ROOT/'.lan_worker_v1/v49_oracle_add_memory_pricehist_teacher_v3_20260915';P.mkdir(exist_ok=True);(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
S=json.loads((SRC/'summary.json').read_text());extra=['weak_ask_rank30','weak_ask_min30','weak_ask_median30','weak_ask_max30','weak_ask_minus_min30','weak_ask_minus_median30','weak_ask_rank10','weak_ask_min10','weak_ask_median10','weak_ask_minus_min10','price_history30_available','price_history10_available'];S.update(status='PASS',features_price_history=extra,strict_past_price_history=True,rows=len(out));(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps({'status':'PASS','rows':len(out),'extra':extra}))
