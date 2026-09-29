from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
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
FAMS={'FULL':['prev_mode_expand','run_count','cum_qty','last_qty','cum_gap_eff','cum_floor_eff','cum_upside_eff','last_gap_eff','last_floor_eff','last_upside_eff'],'EFFICIENCY_ONLY':['prev_mode_expand','cum_gap_eff','cum_floor_eff','cum_upside_eff','last_gap_eff','last_floor_eff','last_upside_eff'],'DURATION_ONLY':['prev_mode_expand','run_count','cum_qty','last_qty']}
def model():return ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None)
def ev(tr,te,fs,p0):
 m=model();m.fit(tr[fs],tr.y);p=m.predict_proba(te[fs])[:,1];y=te.y.to_numpy();overall={'auc':float(roc_auc_score(y,p)),'logloss':float(log_loss(y,p)),'brier':float(brier_score_loss(y,p)),'baselineLogloss':float(log_loss(y,[p0]*len(y))),'baselineBrier':float(brier_score_loss(y,[p0]*len(y)))};by=[]
 for mid,g in te.assign(pred=p).groupby('market_id'):
  yy=g.y.to_numpy();pp=g.pred.to_numpy();ll=float(log_loss(yy,pp,labels=[0,1]));bl=float(log_loss(yy,[p0]*len(yy),labels=[0,1]));by.append({'marketId':int(mid),'delta':ll-bl,'improved':ll<bl})
 wins=sum(x['improved'] for x in by);gate={'market70pct':wins>=math.ceil(.70*len(by)),'overallLoglossLower':overall['logloss']<overall['baselineLogloss'],'overallBrierLower':overall['brier']<overall['baselineBrier'],'aucAtLeast55':overall['auc']>=.55};gate['pass']=all(gate.values());return {'overall':overall,'marketWins':wins,'marketTotal':len(by),'marketWinRate':wins/len(by),'gate':gate,'worstRegression':max(by,key=lambda x:x['delta'])}
def main():
 ap=argparse.ArgumentParser();[ap.add_argument('--'+x,required=True) for x in ['a','b','c','d','output']];ns=ap.parse_args();A=build(ns.a);sets={'B':build(ns.b),'C':build(ns.c),'D':build(ns.d)};p0=float(A.y.mean());res={}
 for name,fs in FAMS.items():res[name]={k:ev(A,df,fs,p0) for k,df in sets.items()}
 out={'version':'MARKET_CAPSULE_MODE_RUN_SIGNAL_ABLATION_V1_RESULT_20260907','researchOnly':True,'baselineStayProbabilityTrain':p0,'featureFamilies':FAMS,'results':res,'allExternalPass':{n:all(v['gate']['pass'] for v in r.values()) for n,r in res.items()}};p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
