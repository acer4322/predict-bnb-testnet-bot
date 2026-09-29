from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.isotonic import IsotonicRegression
EPS=1e-9
FEATURES=['repair_progress','debt_ratio','paircov','floor_ratio','best_pnl_ratio','gap_ratio','ms_since_expand','last_transition_expand','weak_avg_cost','dom_avg_cost']

def build(db,asset):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset=? and window_end_ms is not null",(asset,))}
 rows=list(c.execute("select parent_id,market_id,side,first_event_ms,average_price,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close()
 out=[];cur=None;u=d=cost=cu=cd=0.;debt=base=0.;last_expand_t=None;last_tr=0
 for r in rows:
  mid=int(r['market_id'])
  if mid!=cur:cur=mid;u=d=cost=cu=cd=0.;debt=base=0.;last_expand_t=None;last_tr=0
  sh=float(r['shares']);px=float(r['average_price']);t=int(r['first_event_ms']);pre=abs(u-d);g=u+d;paired=min(u,d)
  if debt>EPS and base>EPS and g>EPS:
   prog=max(0.,min(1.,(base-debt)/base));weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;au=cu/u if u>EPS else 0.;ad=cd/d if d>EPS else 0.;wav=au if weak=='UP' else ad if weak=='DOWN' else 0.;dav=ad if dom=='DOWN' else au if dom=='UP' else 0.
   x=[prog,debt/max(g,1.),2*paired/g,(paired-cost)/max(cost,1.),(max(u,d)-cost)/max(cost,1.),pre/max(g,1.),float(t-(last_expand_t or t)),1. if last_tr>0 else 0.,wav,dav]
   pu=u+sh if r['side']=='UP' else u;pd=d+sh if r['side']=='DOWN' else d;delta=abs(pu-pd)-pre
   if abs(delta)>EPS:out.append({'end':ends.get(mid),'x':x,'y':1 if delta>0 else 0})
  if r['side']=='UP':u+=sh;cu+=sh*px
  else:d+=sh;cd+=sh*px
  cost+=sh*px;post=abs(u-d);delta=post-pre
  if delta>EPS:debt=max(0.,debt)+delta;base=debt;last_expand_t=t;last_tr=1
  elif delta<-EPS and debt>EPS:
   debt=max(0.,debt-(-delta));last_tr=-1
   if debt<=EPS:debt=base=0.;last_expand_t=None
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();data={x:build(a.db,x) for x in ('BTC','ETH')}
 common=[]
 for asset in ('BTC','ETH'):common.extend(r['end'] for r in data[asset] if r['end'] is not None)
 # asset-specific 70/30 chronology cut, matching prior transfer style without future leakage
 cuts={asset:sorted(set(r['end'] for r in data[asset] if r['end'] is not None))[int(len(set(r['end'] for r in data[asset] if r['end'] is not None))*.70)] for asset in ('BTC','ETH')}
 def ds(asset,period):
  z=[r for r in data[asset] if r['end'] is not None and ((r['end']<cuts[asset]) if period=='early' else (r['end']>=cuts[asset]))];return np.asarray([r['x'] for r in z],float),np.asarray([r['y'] for r in z],int)
 out={'version':'TARGET_ETH_BTC_REEXPAND_ECONOMIC_TRANSFER_V9','features':FEATURES,'cuts':cuts,'tasks':{}}
 for tr,te in [('BTC','BTC'),('ETH','ETH'),('BTC','ETH'),('ETH','BTC')]:
  Xtr,ytr=ds(tr,'early');Xte,yte=ds(te,'late');m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=50,l2_regularization=4.,class_weight='balanced',random_state=19).fit(Xtr,ytr);p=m.predict_proba(Xte)[:,1]
  iso=IsotonicRegression(y_min=0,y_max=1,increasing=True,out_of_bounds='clip').fit(Xtr[:,0],ytr);p0=iso.predict(Xte[:,0])
  out['tasks'][f'{tr}_to_{te}']={'trainN':len(ytr),'testN':len(yte),'aucEconomic':float(roc_auc_score(yte,p)),'aucProgressOnly':float(roc_auc_score(yte,p0)),'deltaVsProgress':float(roc_auc_score(yte,p)-roc_auc_score(yte,p0))}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True)
if __name__=='__main__':main()
