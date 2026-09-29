from __future__ import annotations
import argparse,json,sqlite3,statistics
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
EPS=1e-9

def gr(u,d):
 g=u+d
 return abs(u-d)/g if g>EPS else 0.0

def rows_for(db,asset):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 rs=list(c.execute("select market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close();return rs

def build(db,asset):
 rs=rows_for(db,asset);out=[];cur=None;u=d=0.;prev_role=None;prev_rep_imp=None;cut=None
 # median market time cut for chronology
 mts=[];seen=set()
 for r in rs:
  mid=int(r['market_id'])
  if mid not in seen:mts.append(int(r['first_event_ms']));seen.add(mid)
 cut=int(np.median(mts)) if mts else 0
 cur=None;u=d=0.;prev_role=None;prev_rep_imp=None
 for r in rs:
  mid=int(r['market_id']);t=int(r['first_event_ms']);side=str(r['side']);sh=float(r['shares'])
  if mid!=cur:
   cur=mid;u=d=0.;prev_role=None;prev_rep_imp=None
  pre=gr(u,d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;role='REPAIR' if weak is not None and side==weak else 'EXPAND'
  if side=='UP':u+=sh
  else:d+=sh
  post=gr(u,d)
  imp=max(0.,pre-post) if role=='REPAIR' else None
  if prev_role=='REPAIR' and role in ('REPAIR','EXPAND') and prev_rep_imp is not None:
   out.append({'t':t,'switch':1 if role=='EXPAND' else 0,'lastRepairImprovement':prev_rep_imp,'chron':'early' if t<=cut else 'late'})
  if role=='REPAIR':prev_rep_imp=imp
  else:prev_rep_imp=None
  prev_role=role
 return out,cut

def summ(z):
 if not z:return {'n':0}
 sw=[r for r in z if r['switch']==1];ct=[r for r in z if r['switch']==0]
 vals=np.asarray([r['lastRepairImprovement'] for r in z],float);y=np.asarray([r['switch'] for r in z],int)
 # low improvement as switch score = plateau/stall hypothesis
 auc=float(roc_auc_score(y,-vals)) if len(set(y))>1 else None
 def q(a):
  if not a:return None
  v=[r['lastRepairImprovement'] for r in a]
  return {'n':len(v),'median':float(np.median(v)),'p25':float(np.quantile(v,.25)),'p75':float(np.quantile(v,.75))}
 return {'n':len(z),'switchRate':float(np.mean(y)),'lowImprovementSwitchAuc':auc,'switchAfterRepair':q(sw),'continueRepair':q(ct)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();assets={}
 for asset in ('BTC','ETH'):
  z,cut=build(a.db,asset);assets[asset]={'cut':cut,'all':summ(z),'early':summ([r for r in z if r['chron']=='early']),'late':summ([r for r in z if r['chron']=='late'])}
 out={'version':'TARGET_BTC_ETH_REPAIR_TURNING_POINT_V20','researchOnly':True,'boundary':['Target-only Maker actual-filled parent sequence','No Target numeric threshold copied into OUR','Tests structural hypothesis: after an actual REPAIR parent, low marginal gap-ratio improvement predicts switching to EXPAND rather than continuing REPAIR','Chronology early/late stability required before using the concept'],'assets':assets}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
