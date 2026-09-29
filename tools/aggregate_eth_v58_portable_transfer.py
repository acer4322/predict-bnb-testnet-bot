from __future__ import annotations
import argparse,glob,importlib.util,json,sqlite3,math
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('cmp',HERE/'compare_eth_v53_our_target_same_market.py');cmp=importlib.util.module_from_spec(sp);sp.loader.exec_module(cmp)

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'meanScorePositive':float(p[y==1].mean()) if y.sum()>0 else None,'meanScoreNegative':float(p[y==0].mean()) if (y==0).sum()>0 else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 src=sorted(glob.glob(a.pattern));rows=[]
 for p in src:
  d=json.load(open(p,encoding='utf-8'))
  for rr in d.get('rows',[]):
   mid=int(rr['marketId'])
   for z in rr['functional'].get('v56Rows',[]):
    if z.get('feasible') and z.get('portableHazard') is not None:rows.append({'marketId':mid,**z})
 mids=sorted({r['marketId'] for r in rows});con=sqlite3.connect(a.target_db);ph=','.join('?'*len(mids));rr=con.execute(f"select market_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close();by={m:[] for m in mids}
 for r in rr:by[int(r[0])].append(r)
 active={m:sorted(int(e['t']) for e in cmp.target_events(by[m]) if e['role']=='ACTIVE_REPAIR') for m in mids}
 out={'version':'ETH_REPAIR_V58_PORTABLE_ROUTE_TRANSFER_SYNTHESIS','researchOnly':True,'behaviorChange':False,'sourceResults':src,'feasibleStates':len(rows),'markets':len(mids),'horizons':{}}
 for sec in (1,3,5):
  y=[];raw=[];port=[];h=sec*1000
  for z in rows:
   t=int(z['t']);lab=int(any(t<x<=t+h for x in active[z['marketId']]));y.append(lab);raw.append(float(z['hazard']));port.append(float(z['portableHazard']))
  out['horizons'][str(sec)]={'rawFrozen':met(y,raw),'portableV57':met(y,port),'aucDeltaPortableMinusRaw':(met(y,port)['auc']-met(y,raw)['auc']) if met(y,port)['auc'] is not None and met(y,raw)['auc'] is not None else None}
 out['boundary']=['Identical OUR realistic-HFT feasible states for both scores.','Target actual ACTIVE_REPAIR parent fills provide same-market future timing labels only.','No threshold selection or sweep.','No behavior change/no PnL gating/no 8781.']
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
