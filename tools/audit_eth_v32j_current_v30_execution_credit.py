from __future__ import annotations
import json, sqlite3, zlib
from pathlib import Path
from collections import defaultdict
import numpy as np
from tools import audit_eth_v32f_joint_parent_reachability_v1 as base

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=B/'ETH_REPAIR_V32I_CURRENT_V30_PARENT_WAIT_OPTION_VALUE_RESULT.json'
OUT=B/'ETH_REPAIR_V32J_CURRENT_V30_EXECUTION_CREDIT_EXPLORATORY.json'
V30FILES=[
 B/'ETH_REPAIR_V30_DIRECTIONAL_THESIS_CYCLE_SMOKE_A_20260902.json',B/'ETH_REPAIR_V30_DIRECTIONAL_THESIS_CYCLE_SMOKE_B_20260902.json',
 B/'ETH_REPAIR_V30_CONFIRMATORY_CONSUMED_A_20260902.json',B/'ETH_REPAIR_V30_CONFIRMATORY_CONSUMED_B_20260902.json']
EPS=1e-9

def auc(scores,labels):
 p=[float(s) for s,y in zip(scores,labels) if y and s is not None];n=[float(s) for s,y in zip(scores,labels) if (not y) and s is not None]
 if not p or not n:return None
 w=t=0
 for a in p:
  for b in n:
   if a>b:w+=1
   elif a==b:t+=1
 return (w+.5*t)/(len(p)*len(n))

def med(xs):
 xs=[float(x) for x in xs if x is not None and np.isfinite(float(x))]
 return float(np.median(xs)) if xs else None

def depth_path(con,mid,start,end,side,price):
 cp=con.execute('select received_at_ms from maker_book_inference_updates where market_id=? and is_checkpoint=1 and received_at_ms<? order by received_at_ms desc,id desc limit 1',(mid,start)).fetchone()
 st=int(cp[0]) if cp else start-30000
 rows=list(con.execute('select id,received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and received_at_ms>=? and received_at_ms<=? order by received_at_ms,id',(mid,st,end)))
 book={'bids':{},'asks':{}}; depths=[]; behind=[]
 target_native=round(price,12) if side=='UP' else round(1.0-price,12)
 for r in rows:
  t=int(r['received_at_ms'])
  if int(r['is_checkpoint']):
   book={'bids':{float(k):float(v) for k,v in (base.dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (base.dec(r['native_asks_z']) or {}).items()}}
  else: base.apply_changes(book,base.dec(r['changes_z']) or {})
  if t<start:continue
  if side=='UP':
   d=float(book['bids'].get(target_native,book['bids'].get(price,0.0)) or 0.0); bb=max(book['bids']) if book['bids'] else None; rb=bb
  else:
   d=float(book['asks'].get(target_native,book['asks'].get(1.0-price,0.0)) or 0.0); ua=min(book['asks']) if book['asks'] else None; rb=(1.0-ua) if ua is not None else None
  depths.append((t,d)); behind.append(max(0.0,(rb-price)/0.01) if rb is not None else 0.0)
 if not depths:return None
 vals=[d for _,d in depths]; init=vals[0]; last=vals[-1]; mn=min(vals); gd=gu=0.0
 for a,b in zip(vals,vals[1:]):
  if b<a:gd+=a-b
  elif b>a:gu+=b-a
 den=max(init,EPS)
 return {'checks':len(vals),'initialDepth':init,'lastDepth':last,'minDepth':mn,'maxPublicLevelDepletion':max(0.0,init-mn),
         'publicLevelDepletionFraction':max(0.0,init-mn)/den,'persistentNetDepletionFraction':max(0.0,init-last)/den,
         'grossDepletionFraction':gd/den,'grossReplenishmentFraction':gu/den,'replenishmentToDepletion':gu/max(gd,EPS),
         'levelZeroSeen':bool(mn<=EPS),'atBestReceiptFraction':float(np.mean([x<.5 for x in behind])) if behind else None,
         'maxBehindTicks':max(behind) if behind else None,'durationMs':depths[-1][0]-depths[0][0]}

def main():
 src=json.loads(SRC.read_text(encoding='utf-8')); parents=src['parents']
 cancel={}
 for fp in V30FILES:
  d=json.loads(fp.read_text(encoding='utf-8'))
  for row in d['rows']:
   mid=int(row['marketId'])
   for q in row['functional'].get('queueLeaseEvents',[]): cancel[(mid,int(q['placed']))]=int(q['cancelAt'])
 con=sqlite3.connect(f'file:{base.DB.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 rows=[]
 for p in parents:
  start=int(p['first']['t']); end=cancel.get((p['marketId'],start),int(p['second']['t']))
  q=depth_path(con,p['marketId'],start,end,p['repairSide'],float(p['first']['price']))
  if not q:continue
  z=dict(p);z['firstCarrierCredit']=q;rows.append(z)
 con.close()
 rec=[r for r in rows if r['recovered']]; fail=[r for r in rows if r['failed']];labels=[r['recovered'] for r in rows]
 feats=['publicLevelDepletionFraction','persistentNetDepletionFraction','grossDepletionFraction','grossReplenishmentFraction','replenishmentToDepletion','atBestReceiptFraction','maxBehindTicks']
 metrics={}
 for k in feats:
  vals=[r['firstCarrierCredit'][k] for r in rows]
  metrics[k]={'aucRecovered':auc(vals,labels),'recoveredMedian':med([r['firstCarrierCredit'][k] for r in rec]),'failedMedian':med([r['firstCarrierCredit'][k] for r in fail])}
 out={'version':'ETH_REPAIR_V32J_CURRENT_V30_EXECUTION_CREDIT_EXPLORATORY','researchOnly':True,'behaviorChange':False,'actionAuthority':False,
      'currentParentCount':len(rows),'recovered':len(rec),'failed':len(fail),'metrics':metrics,'rows':rows,
      'interpretationBoundary':'Exploratory because several approximate queue-quality values were inspected before this full current-V30 reconstruction. Any promising feature requires preregistered confirmation on V30-unseen consumed markets.',
      'boundary':['current V30 labels only','strict-past first-carrier book path ends before replacement','no winner/PnL','no threshold sweep','no Taker authority','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'n':len(rows),'recovered':len(rec),'failed':len(fail),'metrics':metrics},ensure_ascii=False))
if __name__=='__main__':main()
