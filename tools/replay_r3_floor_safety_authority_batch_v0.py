from __future__ import annotations
import sys,json,sqlite3
from pathlib import Path
import numpy as np, joblib
from tools.replay_r3_floor_safety_authority_v0 import ROOT,DB,BUNDLES,build,probs,CHILD
OUTDIR=ROOT/'data'/'research'/'r3_v0'/'floor_safety_batches_v0'; OUTDIR.mkdir(parents=True,exist_ok=True)
def main():
 s=int(sys.argv[1]); e=int(sys.argv[2]); c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; test=mids[int(.8*len(mids)):]; rows=[]
 for m in test[s:e]: rows.extend(build(c,m))
 c.close(); feats=[r['feat'] for r in rows]
 if not rows: print('{}'); return
 pp=probs(BUNDLES[0],feats); pe=probs(BUNDLES[1],feats); ps=probs(BUNDLES[2],feats)
 exp=[]
 for i,r in enumerate(rows):
  act='PROTECT' if ps[i]>=.5 else 'REPAIR' if pp[i]<.5 else 'EXPAND' if pe[i]>=.5 else 'HOLD'
  if act=='EXPAND': exp.append(r)
 gates={}
 rules={'CURRENT_FLOOR_GE_0':lambda r:r['floor']>=0,'PROJECTED_FLOOR_GE_0':lambda r:r['projectedFloor']>=0,'PROJECTED_FLOOR_GE_5':lambda r:r['projectedFloor']>=5,'PROJECTED_FLOOR_GE_10':lambda r:r['projectedFloor']>=10}
 for n,fn in rules.items():
  k=[r for r in exp if fn(r)]; gates[n]={'kept':len(k),'correct':sum(r['actual']=='EXPAND' for r in k),'floors':[r['floor'] for r in k],'upsides':[r['upside'] for r in k],'projFloors':[r['projectedFloor'] for r in k],'projUpsides':[r['projectedUpside'] for r in k]}
 out={'start':s,'end':e,'markets':len(test[s:e]),'checkpoints':len(rows),'rawExpand':len(exp),'rawCorrect':sum(r['actual']=='EXPAND' for r in exp),'gates':gates}; p=OUTDIR/f'{s:04d}_{e:04d}.json'; p.write_text(json.dumps(out),encoding='utf-8'); print(json.dumps({'out':str(p),'checkpoints':len(rows),'rawExpand':len(exp),'gates':{k:{'kept':v['kept'],'precision':v['correct']/v['kept'] if v['kept'] else None} for k,v in gates.items()}},indent=2))
if __name__=='__main__':main()
