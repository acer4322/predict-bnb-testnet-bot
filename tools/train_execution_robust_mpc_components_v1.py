from __future__ import annotations

import json, math, statistics, sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.train_sequential_arbitration_option_v1 import CURRENT

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
ART=D/'execution_robust_mpc_components_v1.joblib'
REPORT=D/'execution_robust_mpc_components_v1_report.json'
TARGETS=[
 'deltaTargetErrorArea5s','deltaTargetErrorArea10s','deltaTargetErrorArea20s',
 'deltaCompletionCost5s','deltaCompletionCost10s','deltaCompletionCost20s'
]
CONFIGS=[
 {'name':'CONSENSUS_6_M0','votes':6,'sigmaMargin':0.0},
 {'name':'CONSENSUS_5_M0','votes':5,'sigmaMargin':0.0},
 {'name':'CONSENSUS_6_M05','votes':6,'sigmaMargin':0.5},
 {'name':'CONSENSUS_5_M05','votes':5,'sigmaMargin':0.5},
 {'name':'CONSENSUS_4_M05','votes':4,'sigmaMargin':0.5},
 {'name':'CONSENSUS_5_M10','votes':5,'sigmaMargin':1.0},
]


def finite(v:Any)->float:
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except Exception:return math.nan

def scale(vals:list[float])->float:
 z=[abs(x) for x in vals if math.isfinite(x) and abs(x)>1e-12]
 return max(float(statistics.median(z)) if z else 1.0,1e-9)
def X(rows:list[dict[str,Any]])->np.ndarray:
 return np.asarray([[finite((r.get('features') or {}).get(k)) for k in CURRENT] for r in rows],float)
def truth(r:dict[str,Any])->str:
 lab=str(r.get('paretoLabel'))
 return 'REPLACE' if lab=='REPLACE_DOMINATES' else 'KEEP' if lab=='KEEP_DOMINATES' else 'WAIT'
def model()->Pipeline:
 return Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('ridge',Ridge(alpha=3.0))])

def predict_components(bundle:dict[str,Any],rows:list[dict[str,Any]])->dict[str,np.ndarray]:
 xx=X(rows); out={}
 for k in TARGETS: out[k]=bundle['models'][k].predict(xx)
 return out

def policy(bundle:dict[str,Any], rows:list[dict[str,Any]], cfg:dict[str,Any])->dict[str,Any]:
 pp=predict_components(bundle,rows); pred=[]; details=[]
 for i,r in enumerate(rows):
  rep=keep=0; vals={}
  for k in TARGETS:
   y=float(pp[k][i]); sig=float(bundle['trainResidualStd'][k]); m=float(cfg['sigmaMargin'])*sig
   vals[k]=y
   if y < -m: rep+=1
   elif y > m: keep+=1
  if rep>=int(cfg['votes']) and keep==0: a='REPLACE'
  elif keep>=int(cfg['votes']) and rep==0: a='KEEP'
  else:a='WAIT'
  pred.append(a); details.append({'marketId':int(r['marketId']),'truth':truth(r),'pred':a,'replaceVotes':rep,'keepVotes':keep})
 tr=[truth(r) for r in rows]; n=len(rows)
 premature=sum(t=='WAIT' and p!='WAIT' for t,p in zip(tr,pred)); missed=sum(t!='WAIT' and p=='WAIT' for t,p in zip(tr,pred)); wrong=sum(t in {'KEEP','REPLACE'} and p in {'KEEP','REPLACE'} and t!=p for t,p in zip(tr,pred))
 reps=[r for r,p in zip(rows,pred) if p=='REPLACE' and r.get('deltaPnlDiagnostic') is not None]
 return {'n':n,'exactAccuracy':sum(t==p for t,p in zip(tr,pred))/n if n else None,'prematureActRate':premature/n if n else None,'prematureAct':premature,'missedDominance':missed,'wrongDominanceSide':wrong,'predicted':{a:pred.count(a) for a in ['WAIT','KEEP','REPLACE']},'replacePnlDiagnosticSum':sum(float(r['deltaPnlDiagnostic']) for r in reps),'replacePnlDiagnosticMarkets':len(reps),'rows':details}

def sign_metrics(bundle:dict[str,Any],rows:list[dict[str,Any]])->dict[str,Any]:
 pp=predict_components(bundle,rows); out={}
 for k in TARGETS:
  yy=np.asarray([finite(r.get(k))/bundle['targetScale'][k] for r in rows],float); pr=pp[k]
  mask=np.isfinite(yy); y=yy[mask]; p=pr[mask]
  out[k]={'n':int(mask.sum()),'maeScaled':float(np.mean(np.abs(y-p))) if len(y) else None,'signAccuracy':float(np.mean(np.sign(y)==np.sign(p))) if len(y) else None,'replaceHelpfulRecall':float(np.mean(p[y<0]<0)) if np.any(y<0) else None,'keepHelpfulRecall':float(np.mean(p[y>0]>0)) if np.any(y>0) else None}
 return out

def main()->int:
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
 parts={'train':rows[:100],'validation':rows[100:126],'forwardOos':rows[126:]}; tr=parts['train']; xx=X(tr)
 scales={k:scale([finite(r.get(k)) for r in tr]) for k in TARGETS}; models={}; resid={}
 for k in TARGETS:
  y=np.asarray([finite(r.get(k))/scales[k] for r in tr],float); m=model(); m.fit(xx,y); models[k]=m; rp=y-m.predict(xx); resid[k]=float(np.sqrt(np.mean(rp*rp)))
 bundle={'version':'EXECUTION_ROBUST_MPC_COMPONENTS_V1','researchOnly':True,'liveTradingChanges':False,'features':CURRENT,'targets':TARGETS,'targetScale':scales,'models':models,'trainResidualStd':resid,'ridgeAlpha':3.0,'semantics':'Predict six KEEP-vs-REPLACE cost components separately. Controller grants action authority only when multiple horizons/components agree beyond an uncertainty margin; otherwise WAIT.'}
 joblib.dump(bundle,ART)
 report={'version':'EXECUTION_ROBUST_MPC_COMPONENTS_V1_REPORT','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'split':{k:len(v) for k,v in parts.items()},'targetScale':scales,'trainResidualStd':resid,'componentMetrics':{},'configs':[],'guardrails':['Train first100 only; validation next26; Forward23 untouched by fit.','Winner/PnL not used to fit components or choose configs.','Natural sign boundary; fixed consensus/margin menu only.','PnL post-hoc diagnostic only.','No live changes.']}
 for s,rs in parts.items(): report['componentMetrics'][s]=sign_metrics(bundle,rs)
 for cfg in CONFIGS:
  e={'config':cfg}
  for s,rs in parts.items(): e[s]=policy(bundle,rs,cfg)
  report['configs'].append(e)
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 slim=[]
 for e in report['configs']:
  slim.append({'name':e['config']['name'],**{s:{k:e[s][k] for k in ['exactAccuracy','prematureActRate','missedDominance','wrongDominanceSide','predicted','replacePnlDiagnosticSum']} for s in parts}})
 print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'componentMetrics':report['componentMetrics'],'configs':slim},ensure_ascii=False,allow_nan=True))
 return 0
if __name__=='__main__': raise SystemExit(main())
