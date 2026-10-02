from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
try:
 from train_market_capsule_mode_run_progress_efficiency_v1 import build,FEATURES
except ImportError:
 ROOT=Path(__file__).resolve().parents[1]
 if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
 from tools.train_market_capsule_mode_run_progress_efficiency_v1 import build,FEATURES

def base_model(): return ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None)

def decision_metrics(df,p):
 pred=(p>=.5).astype(int);y=df.y.to_numpy();base=np.ones(len(y),dtype=int)
 acc=float((pred==y).mean());bacc=float((base==y).mean());share=float((pred==0).mean());tp=int(((pred==0)&(y==0)).sum());prec=tp/max(1,int((pred==0).sum()));rec=tp/max(1,int((y==0).sum()))
 per=[]
 for mid,g in df.assign(pred=pred).groupby('market_id'):
  yy=g.y.to_numpy();pp=g.pred.to_numpy();a=float((pp==yy).mean());ba=float((np.ones(len(yy),dtype=int)==yy).mean());per.append({'marketId':int(mid),'delta':a-ba,'modelAccuracy':a,'baselineAccuracy':ba,'nonWorse':a>=ba,'improved':a>ba})
 nw=sum(x['nonWorse'] for x in per);worst=min(per,key=lambda x:x['delta'])
 gate={'overallAccuracyHigher':acc>bacc,'predictedSwitchShareAtLeast10pct':share>=.10,'switchRecallAtLeast20pct':rec>=.20,'switchPrecisionAbove50pct':prec>.50,'marketNonWorseAtLeast70pct':nw/len(per)>=.70,'worstRegressionAtLeastMinus15pp':worst['delta']>=-.15};gate['pass']=all(gate.values())
 return {'overallAccuracy':acc,'baselineAlwaysStayAccuracy':bacc,'accuracyLift':acc-bacc,'predictedSwitchShare':share,'switchPrecision':prec,'switchRecall':rec,'marketImproved':sum(x['improved'] for x in per),'marketNonWorse':nw,'marketTotal':len(per),'marketNonWorseRate':nw/len(per),'worstRegression':worst,'gate':gate}

def probs(y,p):return {'auc':float(roc_auc_score(y,p)),'logloss':float(log_loss(y,p)),'brier':float(brier_score_loss(y,p)),'pMin':float(np.min(p)),'pMedian':float(np.median(p)),'pMax':float(np.max(p))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--a',required=True);ap.add_argument('--b',required=True);ap.add_argument('--c',required=True);ap.add_argument('--d',required=True);ap.add_argument('--output',required=True);ns=ap.parse_args()
 A=build(ns.a);sets={'B':build(ns.b),'C':build(ns.c),'D':build(ns.d)}
 groups=A.market_id.to_numpy();gkf=GroupKFold(n_splits=5);oof=np.zeros(len(A))
 for tr,va in gkf.split(A[FEATURES],A.y,groups):
  m=base_model();m.fit(A.iloc[tr][FEATURES],A.iloc[tr].y);oof[va]=m.predict_proba(A.iloc[va][FEATURES])[:,1]
 cal=LogisticRegression(C=1,max_iter=2000,random_state=1);cal.fit(oof.reshape(-1,1),A.y)
 full=base_model();full.fit(A[FEATURES],A.y)
 res={}
 for k,df in sets.items():
  raw=full.predict_proba(df[FEATURES])[:,1];cp=cal.predict_proba(raw.reshape(-1,1))[:,1]
  res[k]={'rows':len(df),'markets':int(df.market_id.nunique()),'rawProbability':probs(df.y.to_numpy(),raw),'calibratedProbability':probs(df.y.to_numpy(),cp),'router':decision_metrics(df,cp)}
 out={'version':'MARKET_CAPSULE_MODE_RUN_ROUTER_PLATT_OOF_V1_RESULT_20260907','researchOnly':True,'features':FEATURES,'calibration':{'folds':5,'group':'market_id','oofRaw':probs(A.y.to_numpy(),oof),'plattCoef':float(cal.coef_[0][0]),'plattIntercept':float(cal.intercept_[0])},'tests':res,'promotionGate':{'allExternalDiagnosticGatesPass':all(v['router']['gate']['pass'] for v in res.values())}}
 p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
