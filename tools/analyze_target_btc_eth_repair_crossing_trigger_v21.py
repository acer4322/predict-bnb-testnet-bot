from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
EPS=1e-9

def load(db,asset):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 rs=list(c.execute("select market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close();return rs

def build(db,asset):
 rs=load(db,asset);mts=[];seen=set()
 for r in rs:
  m=int(r['market_id'])
  if m not in seen:mts.append(int(r['first_event_ms']));seen.add(m)
 cut=int(np.median(mts)) if mts else 0
 out=[];cur=None;u=d=0.;prev=None
 for r in rs:
  mid=int(r['market_id']);t=int(r['first_event_ms']);side=str(r['side']);q=float(r['shares'])
  if mid!=cur:cur=mid;u=d=0.;prev=None
  pre_net=u-d;pre_gap=abs(pre_net);weak='UP' if pre_net< -EPS else 'DOWN' if pre_net>EPS else None
  role='REPAIR' if weak is not None and side==weak else 'EXPAND'
  if side=='UP':u+=q
  else:d+=q
  post_net=u-d;post_gap=abs(post_net)
  crossed=(pre_net*post_net< -EPS) or (pre_gap>EPS and post_gap<=EPS)
  repair_frac=(pre_gap-post_gap)/pre_gap if role=='REPAIR' and pre_gap>EPS else None
  qty_gap=q/pre_gap if role=='REPAIR' and pre_gap>EPS else None
  currec={'t':t,'role':role,'crossed':crossed,'repairFrac':repair_frac,'qtyGap':qty_gap,'postGapRatio':post_gap/(u+d) if u+d>EPS else 0.}
  if prev is not None and prev['role']=='REPAIR':
   out.append({'t':t,'switch':1 if role=='EXPAND' else 0,'crossed':prev['crossed'],'repairFrac':prev['repairFrac'],'qtyGap':prev['qtyGap'],'postGapRatio':prev['postGapRatio'],'chron':'early' if t<=cut else 'late'})
  prev=currec
 return out,cut

def summ(z):
 if not z:return {'n':0}
 y=np.asarray([r['switch'] for r in z],int);cross=np.asarray([1 if r['crossed'] else 0 for r in z],int)
 def grp(v):
  a=[r for r in z if r['switch']==v]
  return {'n':len(a),'crossRate':float(np.mean([r['crossed'] for r in a])) if a else None,'repairFracMedian':float(np.median([r['repairFrac'] for r in a if r['repairFrac'] is not None])) if a else None,'qtyGapMedian':float(np.median([r['qtyGap'] for r in a if r['qtyGap'] is not None])) if a else None,'postGapRatioMedian':float(np.median([r['postGapRatio'] for r in a])) if a else None}
 rf=np.asarray([r['repairFrac'] for r in z],float)
 auc=float(roc_auc_score(y,rf)) if len(set(y))>1 else None
 return {'n':len(z),'switchRate':float(np.mean(y)),'repairFractionSwitchAuc':auc,'switchAfterRepair':grp(1),'continueRepair':grp(0),'switchGivenCross':float(np.mean(y[cross==1])) if np.any(cross==1) else None,'switchGivenNoCross':float(np.mean(y[cross==0])) if np.any(cross==0) else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();assets={}
 for asset in ('BTC','ETH'):
  z,cut=build(a.db,asset);assets[asset]={'cut':cut,'all':summ(z),'early':summ([r for r in z if r['chron']=='early']),'late':summ([r for r in z if r['chron']=='late'])}
 out={'version':'TARGET_BTC_ETH_REPAIR_CROSSING_TRIGGER_V21','researchOnly':True,'boundary':['Target-only Maker actual-filled parent sequence','No Target numeric threshold copied into OUR','Tests whether R->E is naturally induced by the preceding REPAIR fill changing inventory geometry/crossing weak-side identity','Chronology early/late stability required'],'assets':assets}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
