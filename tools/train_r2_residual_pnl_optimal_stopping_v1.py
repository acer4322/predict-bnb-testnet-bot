from __future__ import annotations
import json,math
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,accuracy_score,log_loss
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
EPS=1e-9

def load(split):
 if split!='forward': return json.loads((D/f'r2_residual_pnl_action_values_v1_{split}.json').read_text())['rows']
 rows=[]
 for i in range(1,5): rows += json.loads((D/f'r2_residual_pnl_action_values_v1_forward_b{i}.json').read_text())['rows']
 return rows
raw={s:load(s) for s in ['train','validation','forward']}
# feature union from train only; identifiers/outcomes excluded by construction.
feat_names=sorted({k for r in raw['train'] for k,v in r['features'].items() if isinstance(v,(int,float)) or v is None})+['candidateDelayMs']

def vec(r):
 f=r['features']; out=[]
 for k in feat_names:
  v=r['candidateDelayMs'] if k=='candidateDelayMs' else f.get(k)
  try: x=float(v) if v is not None else np.nan
  except: x=np.nan
  out.append(x if math.isfinite(x) else np.nan)
 return out

def build_risk(rows):
 by={}
 for r in rows: by.setdefault(int(r['marketId']),[]).append(r)
 out=[]; market_oracle={}
 for mid,rs in by.items():
  rs=sorted(rs,key=lambda z:int(z['candidateDelayMs']))
  baseline=float(rs[0]['baselinePnl'])
  candidates=[]
  for r in rs:
   vals=r['actionPnl']; a='PASSIVE_PRIORITY' if vals['PASSIVE_PRIORITY']>=vals['TAKER_RECOVERY'] else 'TAKER_RECOVERY'
   candidates.append((float(vals[a]),int(r['candidateDelayMs']),a,r))
  best=max([(baseline,10**12,'BASELINE_R2',None)]+candidates,key=lambda z:(z[0],-z[1]))
  # require strictly positive improvement over baseline; otherwise never exercise.
  if best[0] <= baseline+EPS: best=(baseline,10**12,'BASELINE_R2',None)
  market_oracle[mid]={'baseline':baseline,'oraclePnl':best[0],'oracleDelayMs':None if best[3] is None else best[1],'oracleAction':best[2]}
  for r in rs:
   d=int(r['candidateDelayMs'])
   if best[3] is None:
    label=0
   elif d<best[1]: label=0
   elif d==best[1]: label=1
   else: break
   out.append({'marketId':mid,'row':r,'actNow':label,'actFamily':best[2] if label else None})
 return out,market_oracle
risk={}; oracle={}
for s in raw: risk[s],oracle[s]=build_risk(raw[s])
X={s:np.asarray([vec(x['row']) for x in risk[s]],float) for s in risk}; y={s:np.asarray([x['actNow'] for x in risk[s]],int) for s in risk}
models={
 'LOGIT':make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(max_iter=3000,class_weight='balanced',C=1.0)),
 'HGB':make_pipeline(SimpleImputer(strategy='median',add_indicator=True),HistGradientBoostingClassifier(max_depth=2,max_iter=100,learning_rate=0.05,l2_regularization=1.0,random_state=1))}

def metrics(m,s):
 p=m.predict_proba(X[s])[:,1]; pred=(p>=0.5).astype(int); yy=y[s]
 return {'n':len(yy),'positives':int(yy.sum()),'positiveRate':float(yy.mean()) if len(yy) else None,
  'auc':float(roc_auc_score(yy,p)) if len(set(yy))>1 else None,'ap':float(average_precision_score(yy,p)) if yy.sum()>0 else None,
  'balancedAccuracy':float(balanced_accuracy_score(yy,pred)),'accuracy':float(accuracy_score(yy,pred)),
  'logLoss':float(log_loss(yy,p,labels=[0,1])),'meanP':float(p.mean())}
# action-family classifier trained only on oracle exercise rows in TRAIN.
act_train=[x for x in risk['train'] if x['actNow']==1]
Xa=np.asarray([vec(x['row']) for x in act_train],float); ya=np.asarray([1 if x['actFamily']=='TAKER_RECOVERY' else 0 for x in act_train],int)
family=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(max_iter=3000,class_weight='balanced',C=1.0))
family.fit(Xa,ya)

def simulate(m,split):
 # At each reachable checkpoint, ACT if p>=.5. If ACT, choose family model. If never ACT, baseline.
 pmap={}
 probs=m.predict_proba(X[split])[:,1]
 for x,p in zip(risk[split],probs): pmap[(x['marketId'],int(x['row']['candidateDelayMs']))]=float(p)
 by={}
 for r in raw[split]: by.setdefault(int(r['marketId']),[]).append(r)
 pnl=0.; base=0.; opc=0.; regrets=[]; acts=Counter(); rows=[]
 for mid,rs in by.items():
  rs=sorted(rs,key=lambda z:int(z['candidateDelayMs'])); b=float(rs[0]['baselinePnl']); base+=b; opc+=oracle[split][mid]['oraclePnl']
  chosenPnl=b; chosen='BASELINE_R2'; chosenD=None
  for r in rs:
   d=int(r['candidateDelayMs']); key=(mid,d)
   if key not in pmap: break
   if pmap[key] >= .5:
    pa=float(family.predict_proba(np.asarray([vec(r)],float))[0,1]); fam='TAKER_RECOVERY' if pa>=.5 else 'PASSIVE_PRIORITY'
    chosenPnl=float(r['actionPnl'][fam]); chosen=fam; chosenD=d; break
  pnl+=chosenPnl; acts[chosen]+=1; regrets.append(oracle[split][mid]['oraclePnl']-chosenPnl)
  rows.append({'marketId':mid,'baselinePnl':b,'policyPnl':chosenPnl,'oraclePnl':oracle[split][mid]['oraclePnl'],'chosenAction':chosen,'chosenDelayMs':chosenD,'oracleAction':oracle[split][mid]['oracleAction'],'oracleDelayMs':oracle[split][mid]['oracleDelayMs']})
 return {'markets':len(by),'baselineTotalPnl':base,'policyTotalPnl':pnl,'oracleTotalPnl':opc,'policyLiftVsBaseline':pnl-base,'capturedOracleLiftRate':(pnl-base)/(opc-base) if opc>base+EPS else None,'meanRegret':float(np.mean(regrets)),'actionCounts':dict(acts),'rows':rows}

report={'version':'R2_RESIDUAL_PNL_OPTIMAL_STOPPING_V1','researchOnly':True,'dreamFillAllowed':False,
 'splitMarkets':{s:len(set(r['marketId'] for r in raw[s])) for s in raw},'features':feat_names,
 'teacher':'Optimal stopping over fixed 0/1/3/5/10s HFT executable PnL actions. WAIT until market-level oracle exercise time; later rows censored unreachable.',
 'guards':['Train on train15 only','Validation15 and forward20 untouched by fitting','No winner/settlement/PnL/future fields in runtime features','Fixed probability threshold 0.5','No action/delay/threshold sweep'],
 'risksetCounts':{s:{'rows':len(risk[s]),'actNow':int(y[s].sum()),'wait':int(len(y[s])-y[s].sum())} for s in risk},
 'oracleActionCounts':{s:dict(Counter(v['oracleAction'] for v in oracle[s].values())) for s in oracle},'models':{},'familyTrain':{'rows':len(ya),'taker':int(ya.sum()),'passive':int(len(ya)-ya.sum())}}
for name,m in models.items():
 m.fit(X['train'],y['train']); report['models'][name]={'metrics':{s:metrics(m,s) for s in risk},'policyValue':{s:simulate(m,s) for s in risk}}
out=D/'r2_residual_pnl_optimal_stopping_v1_report.json'; out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
print(json.dumps({'report':str(out),'risksetCounts':report['risksetCounts'],'oracleActionCounts':report['oracleActionCounts'],'familyTrain':report['familyTrain'],'models':{n:{s:report['models'][n]['policyValue'][s] for s in ['validation','forward']} for n in report['models']}},ensure_ascii=False,indent=2))
