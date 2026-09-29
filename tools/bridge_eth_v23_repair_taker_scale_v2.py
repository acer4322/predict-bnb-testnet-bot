from __future__ import annotations
import argparse,bisect,json,math,statistics,importlib.util
from pathlib import Path
from collections import defaultdict
import numpy as np,joblib
from sklearn.metrics import roc_auc_score
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('br',HERE/'bridge_eth_v23_repair_taker_hazard_v1.py');br=importlib.util.module_from_spec(s);s.loader.exec_module(br)
s2=importlib.util.spec_from_file_location('norm',HERE/'train_target_eth_repair_taker_scale_normalized_v2.py');norm=importlib.util.module_from_spec(s2);s2.loader.exec_module(norm)

def st(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':np.quantile(ys,.25).item() if ys else None,'p75':np.quantile(ys,.75).item() if ys else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--offline',default='data/research/r4_v0/p0_provenance_v1/ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--model',required=True);ap.add_argument('--raw',nargs='*',default=br.RAW_DEFAULT);ap.add_argument('--output',required=True);a=ap.parse_args()
 off=json.load(open(a.offline));raw,_=br.load_raw(a.raw);art=joblib.load(a.model);model=art['model'];assert list(art['features'])==list(norm.NORM_FEATURES)
 import sqlite3;c=sqlite3.connect(f'file:{Path(a.book_db).resolve().as_posix()}?mode=ro',uri=True);bymid=defaultdict(list)
 for r in off['rows']:bymid[int(r['marketId'])].append(r)
 rows=[]
 try:
  for mid,cycles in sorted(bymid.items()):
   rr=raw.get(mid)
   if rr is None:continue
   events=br.annotate_fills(rr['V23']);meta=c.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
   if meta is None:continue
   mend=int(meta[0]);mstart=mend-300000;cycles=sorted(cycles,key=lambda z:int(z['firstFillAt']));lo=min(int(z['firstFillAt']) for z in cycles)-2000;hi=max(int(z['packageRepairFillAt'] or (int(z['firstFillAt'])+30000)) for z in cycles)+1000;states=br.bookbase.load_states(c,mid,lo,hi,'received_at_ms');ts=[x[0] for x in states]
   for j,cy in enumerate(cycles):
    start=int(cy['firstFillAt']);next_first=int(cycles[j+1]['firstFillAt']) if j+1<len(cycles) else 10**30;end=int(cy['packageRepairFillAt']) if cy.get('completedWithin30sByTrace') and cy.get('packageRepairFillAt') is not None else min(start+30000,next_first);ps=[];sr=[]
    for cp in br.half_offset_points(mstart,start,end):
     ix=bisect.bisect_right(ts,cp)-1
     if ix<0:continue
     state=states[ix];age=cp-int(state[0])
     if age<0 or age>2000:continue
     inv=br.make_inventory(events,cp);ph=br.parent_history(events,cp);z=br.feature_row(inv,ph,cp,mend,state)
     if z is None:continue
     _,vals=z;nv=norm.rownorm(vals);x=np.asarray([np.nan if nv.get(k) is None else float(nv.get(k)) for k in norm.NORM_FEATURES],np.float32);p=float(model.predict_proba(x.reshape(1,-1))[0,1]);ps.append(p);sr.append({'t':cp,'p':p})
    warn=next((z['t'] for z in sr if z['p']>=.5),None);rows.append({'marketId':mid,'cycleIndex':int(cy['cycleIndex']),'completedWithin30s':bool(cy['completedWithin30sByTrace']),'maxHazard':max(ps) if ps else None,'medianHazard':statistics.median(ps) if ps else None,'warningAt':warn,'warningRateState':sum(x>=.5 for x in ps)/len(ps) if ps else None})
 finally:c.close()
 v=[r for r in rows if r['maxHazard'] is not None];comp=[r for r in v if r['completedWithin30s']];fail=[r for r in v if not r['completedWithin30s']];y=np.asarray([0 if r['completedWithin30s'] else 1 for r in v],int);p=np.asarray([r['maxHazard'] for r in v],float)
 out={'version':'ETH_V23_REPAIR_TAKER_SCALE_V2_BRIDGE','researchOnly':True,'coverage':{'cycles':len(v),'completed':len(comp),'failed':len(fail)},'metrics':{'failureAucFromMaxHazard':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'completedMax':st([r['maxHazard'] for r in comp]),'failedMax':st([r['maxHazard'] for r in fail]),'completedWarningRateAt05':sum(r['warningAt'] is not None for r in comp)/len(comp) if comp else None,'failedWarningRateAt05':sum(r['warningAt'] is not None for r in fail)/len(fail) if fail else None},'rows':rows,'boundary':['Frozen Target scale-normalized V2 only.','No refit or threshold sweep on OUR.','OUR completion labels evaluation only.']};Path(a.output).write_text(json.dumps(out,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
