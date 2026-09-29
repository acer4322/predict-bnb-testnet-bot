from __future__ import annotations
import json,pathlib,collections,bisect,statistics
ROOT=pathlib.Path(__file__).resolve().parents[1];SRC=ROOT/'.lan_worker_v1/v49_oracle_proposal_add_memory_teacher_v2_20260915';rows=[json.loads(x) for x in (SRC/'data.jsonl').read_text().splitlines() if x.strip()];by=collections.defaultdict(list)
for r in rows:by[int(r['market_id'])].append(r)
out=[]
for m,rr in by.items():
 rr=sorted(rr,key=lambda x:int(x['t']));times=[];prices=[]
 for r in rr:
  t=int(r['t']);cur=float(r['proposed_add_price']);j=len(times);k30=bisect.bisect_left(times,t-30000);k10=bisect.bisect_left(times,t-10000);v30=prices[k30:j]+[cur];v10=prices[k10:j]+[cur]
  def fs(vals):
   ss=sorted(vals);n=len(ss);med=statistics.median(ss);rank=sum(x<=cur+1e-12 for x in ss)/n;return rank,ss[0],med,ss[-1]
  r30,mn30,med30,mx30=fs(v30);r10,mn10,med10,mx10=fs(v10);z=dict(r);z.update(add_price_rank30=r30,add_price_min30=mn30,add_price_median30=med30,add_price_max30=mx30,add_price_minus_min30=cur-mn30,add_price_minus_median30=cur-med30,add_price_rank10=r10,add_price_min10=mn10,add_price_median10=med10,add_price_minus_min10=cur-mn10);out.append(z);times.append(t);prices.append(cur)
out.sort(key=lambda x:(x['market_id'],x['t']));P=ROOT/'.lan_worker_v1/v49_oracle_add_memory_ownprice_teacher_v4_20260915';P.mkdir(exist_ok=True);(P/'data.jsonl').write_text('\n'.join(json.dumps(x,separators=(',',':')) for x in out)+'\n')
S=json.loads((SRC/'summary.json').read_text());extra=['add_price_rank30','add_price_min30','add_price_median30','add_price_max30','add_price_minus_min30','add_price_minus_median30','add_price_rank10','add_price_min10','add_price_median10','add_price_minus_min10'];S.update(status='PASS',features_add_price_history=extra,strict_past_add_price_history=True,rows=len(out));(P/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps({'status':'PASS','rows':len(out),'extra':extra}))
