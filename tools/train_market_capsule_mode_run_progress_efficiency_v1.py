from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np,pandas as pd,duckdb
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss
FEATURES=['prev_mode_expand','run_count','cum_qty','last_qty','cum_gap_eff','cum_floor_eff','cum_upside_eff','last_gap_eff','last_floor_eff','last_upside_eff']
EPS=1e-9

def economic_role(r):
 u=float(r.pre_up_shares);d=float(r.pre_down_shares);gap=abs(u-d);side=str(r.action_side);q=float(r.action_shares)
 if gap<=EPS:return 'BALANCED'
 weak='UP' if u<d else 'DOWN';strong='DOWN' if weak=='UP' else 'UP'
 if side==strong:return 'EXPAND'
 if side==weak:return 'MIXED' if q>gap+EPS else 'REPAIR'
 return 'OTHER'

def build(path):
 con=duckdb.connect();df=con.execute("select market_id,seam_id,action_event_ms,same_timestamp_action_count,pre_up_shares,pre_down_shares,pre_floor,pre_upside,action_side,action_shares from read_parquet(?) order by market_id,action_event_ms,seam_id",[str(path)]).df();con.close()
 df['role']=df.apply(economic_role,axis=1);out=[]
 for mid,g in df.groupby('market_id',sort=False):
  rows=[r for _,r in g.iterrows() if int(r.same_timestamp_action_count)==1]
  # pure-role consecutive runs only; mixed/balanced reset run.
  for i in range(1,len(rows)):
   cur=rows[i];prev=rows[i-1];pr=str(prev.role);cr=str(cur.role)
   if pr not in {'REPAIR','EXPAND'} or cr not in {'REPAIR','EXPAND'}:continue
   # find pure consecutive run entry of previous mode ending at i-1
   j=i-1
   while j-1>=0 and str(rows[j-1].role)==pr:j-=1
   entry=rows[j]
   run=rows[j:i]
   run_count=len(run);cum_qty=sum(float(x.action_shares) for x in run);last_qty=max(float(prev.action_shares),EPS)
   entry_gap=abs(float(entry.pre_up_shares)-float(entry.pre_down_shares));cur_gap=abs(float(cur.pre_up_shares)-float(cur.pre_down_shares));prev_gap=abs(float(prev.pre_up_shares)-float(prev.pre_down_shares))
   ef=float(entry.pre_floor);cf=float(cur.pre_floor);pf=float(prev.pre_floor);eu=float(entry.pre_upside);cu=float(cur.pre_upside);pu=float(prev.pre_upside)
   if pr=='REPAIR':
    cgp=entry_gap-cur_gap; cfp=cf-ef; cup=eu-cu
    lgp=prev_gap-cur_gap; lfp=cf-pf; lup=pu-cu
   else:
    cgp=cur_gap-entry_gap; cfp=ef-cf; cup=cu-eu
    lgp=cur_gap-prev_gap; lfp=pf-cf; lup=cu-pu
   den=max(cum_qty,EPS)
   out.append({'market_id':int(mid),'y':1 if cr==pr else 0,'prev_mode_expand':1 if pr=='EXPAND' else 0,'run_count':run_count,'cum_qty':cum_qty,'last_qty':last_qty,'cum_gap_eff':cgp/den,'cum_floor_eff':cfp/den,'cum_upside_eff':cup/den,'last_gap_eff':lgp/last_qty,'last_floor_eff':lfp/last_qty,'last_upside_eff':lup/last_qty})
 return pd.DataFrame(out)

def evaluate(model,tr,te,p0):
 model.fit(tr[FEATURES],tr.y);p=model.predict_proba(te[FEATURES])[:,1];y=te.y.to_numpy()
 overall={'auc':float(roc_auc_score(y,p)),'logloss':float(log_loss(y,p)),'brier':float(brier_score_loss(y,p)),'baselineLogloss':float(log_loss(y,np.full(len(y),p0))),'baselineBrier':float(brier_score_loss(y,np.full(len(y),p0)))}
 by=[]
 for mid,g in te.assign(pred=p).groupby('market_id'):
  yy=g.y.to_numpy();pp=g.pred.to_numpy();ll=float(log_loss(yy,pp,labels=[0,1]));bl=float(log_loss(yy,np.full(len(yy),p0),labels=[0,1]));by.append({'marketId':int(mid),'n':len(g),'modelLogloss':ll,'baselineLogloss':bl,'delta':ll-bl,'improved':ll<bl})
 wins=sum(x['improved'] for x in by); gate={'market70pct':wins>=math.ceil(.70*len(by)),'overallLoglossLower':overall['logloss']<overall['baselineLogloss'],'overallBrierLower':overall['brier']<overall['baselineBrier'],'aucAtLeast55':overall['auc']>=.55};gate['pass']=all(gate.values())
 return {'overall':overall,'marketWins':wins,'marketTotal':len(by),'marketWinRate':wins/len(by),'gate':gate,'worstRegression':max(by,key=lambda x:x['delta']),'bestImprovement':min(by,key=lambda x:x['delta'])}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--a',required=True);ap.add_argument('--b',required=True);ap.add_argument('--c',required=True);ap.add_argument('--output',required=True);ns=ap.parse_args()
 A=build(ns.a);B=build(ns.b);C=build(ns.c);p0=float(A.y.mean())
 models={'LOGISTIC':Pipeline([('scale',StandardScaler()),('clf',LogisticRegression(C=1,max_iter=2000,random_state=1))]),'EXTRA_TREES':ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None)}
 res={k:{'B':evaluate(m,A,B,p0),'C':evaluate(m,A,C,p0)} for k,m in models.items()}
 out={'version':'MARKET_CAPSULE_MODE_RUN_PROGRESS_EFFICIENCY_V1_RESULT_20260907','researchOnly':True,'features':FEATURES,'coverage':{'A':{'rows':len(A),'markets':int(A.market_id.nunique()),'stayRate':float(A.y.mean())},'B':{'rows':len(B),'markets':int(B.market_id.nunique()),'stayRate':float(B.y.mean())},'C':{'rows':len(C),'markets':int(C.market_id.nunique()),'stayRate':float(C.y.mean())}},'baselineStayProbabilityTrain':p0,'models':res,'promotionGate':{'pass':any(v['B']['gate']['pass'] and v['C']['gate']['pass'] for v in res.values())}}
 p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
