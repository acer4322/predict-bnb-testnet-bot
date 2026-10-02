from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
try:
 from train_market_capsule_mode_run_progress_efficiency_v1 import build
except ImportError:
 ROOT=Path(__file__).resolve().parents[1]
 if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
 from tools.train_market_capsule_mode_run_progress_efficiency_v1 import build
EPS=1e-9
FEATURES=['prev_mode_expand','maturity_qty_ratio','cum_mode_score','last_mode_score','decay_mode_score','decay_gap','decay_floor','decay_upside']
def enrich(df):
 x=df.copy();x['maturity_qty_ratio']=x['cum_qty']/x['last_qty'].clip(lower=EPS);x['cum_mode_score']=x['cum_gap_eff']+x['cum_floor_eff']+x['cum_upside_eff'];x['last_mode_score']=x['last_gap_eff']+x['last_floor_eff']+x['last_upside_eff'];x['decay_mode_score']=x['last_mode_score']-x['cum_mode_score'];x['decay_gap']=x['last_gap_eff']-x['cum_gap_eff'];x['decay_floor']=x['last_floor_eff']-x['cum_floor_eff'];x['decay_upside']=x['last_upside_eff']-x['cum_upside_eff'];return x
def model():return ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None)
def main():
 ap=argparse.ArgumentParser();[ap.add_argument('--'+x,required=True) for x in ['a','b','c','d','e','output']];ns=ap.parse_args();parts=[enrich(build(getattr(ns,k))) for k in ['a','b','c','d']];tr=pd.concat(parts,ignore_index=True);te=enrich(build(ns.e));p0=float(tr.y.mean());m=model();m.fit(tr[FEATURES],tr.y);p=m.predict_proba(te[FEATURES])[:,1];y=te.y.to_numpy();overall={'auc':float(roc_auc_score(y,p)),'logloss':float(log_loss(y,p)),'brier':float(brier_score_loss(y,p)),'baselineLogloss':float(log_loss(y,[p0]*len(y))),'baselineBrier':float(brier_score_loss(y,[p0]*len(y)))};by=[]
 for mid,g in te.assign(pred=p).groupby('market_id'):
  yy=g.y.to_numpy();pp=g.pred.to_numpy();ll=float(log_loss(yy,pp,labels=[0,1]));bl=float(log_loss(yy,[p0]*len(yy),labels=[0,1]));by.append({'marketId':int(mid),'delta':ll-bl,'improved':ll<bl})
 wins=sum(x['improved'] for x in by);gate={'market70pct':wins>=math.ceil(.70*len(by)),'overallLoglossLower':overall['logloss']<overall['baselineLogloss'],'overallBrierLower':overall['brier']<overall['baselineBrier'],'aucAtLeast55':overall['auc']>=.55};gate['pass']=all(gate.values());out={'version':'MARKET_CAPSULE_MODE_MARGINAL_PROGRESS_DECAY_E20_V1_RESULT_20260907','researchOnly':True,'features':FEATURES,'trainRows':len(tr),'trainMarkets':int(tr.market_id.nunique()),'testRows':len(te),'testMarkets':int(te.market_id.nunique()),'baselineStayProbabilityTrain':p0,'overall':overall,'marketWins':wins,'marketTotal':len(by),'marketWinRate':wins/len(by),'worstRegression':max(by,key=lambda x:x['delta']),'bestImprovement':min(by,key=lambda x:x['delta']),'promotionGate':gate};q=Path(ns.output);q.parent.mkdir(parents=True,exist_ok=True);q.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
