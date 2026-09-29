from __future__ import annotations
import json,math,random
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FILES={'train':'r2_residual_multicheckpoint_train15_v0.json','validation':'r2_residual_multicheckpoint_validation15_v0.json','forward':'r2_residual_multicheckpoint_forward20_v0.json'}
SEED=20260822;N=32

def load(fn):
 d=json.load(open(D/fn,encoding='utf-8')); out=[]
 for r in d['rowsData']:
  z={'marketId':int(r['marketId']),'value':float(r['value']),'beneficial':int(r['value']>1e-9),'harmful':int(r['value']<-1e-9)};z.update(r['features']);z['candidateDelayMs']=r['candidateDelayMs'];out.append(z)
 return pd.DataFrame(out)

def metrics(df,preds):
 arr=np.asarray(preds); mean=arr.mean(0); lo=np.quantile(arr,.10,axis=0); gate=lo>0
 y=df.value.to_numpy(float); benef=y>1e-9; harm=y<-1e-9
 sel=np.where(gate)[0]
 return {'n':len(df),'baselineAlwaysInterveneBeneficialRate':float(benef.mean()),'gateCoverage':float(gate.mean()),'selectedN':int(gate.sum()),'selectedBeneficialRate':float(benef[sel].mean()) if len(sel) else None,'selectedHarmfulRate':float(harm[sel].mean()) if len(sel) else None,'selectedMeanTrueAdvantage':float(y[sel].mean()) if len(sel) else None,'abstainedBeneficialRate':float(benef[~gate].mean()) if (~gate).sum() else None,'meanPred':float(mean.mean()),'meanLower10':float(lo.mean())}

def main():
 tr=load(FILES['train']);va=load(FILES['validation']);fw=load(FILES['forward'])
 exclude={'marketId','value','beneficial','harmful'}; feats=[c for c in tr.columns if c not in exclude]
 rng=random.Random(SEED); mids=sorted(tr.marketId.unique()); models=[]
 for i in range(N):
  sample=[rng.choice(mids) for _ in mids]; parts=[tr[tr.marketId==m] for m in sample]; boot=pd.concat(parts,ignore_index=True)
  pre=ColumnTransformer([('num',Pipeline([('imp',SimpleImputer(strategy='median'))]),feats)])
  m=Pipeline([('pre',pre),('reg',HistGradientBoostingRegressor(max_depth=2,learning_rate=.05,max_iter=120,l2_regularization=8.0,min_samples_leaf=8,random_state=SEED+i))])
  m.fit(boot[feats],boot.value);models.append(m)
 def pred(df):return np.vstack([m.predict(df[feats]) for m in models])
 rep={'version':'R2_RESIDUAL_CONSERVATIVE_ADVANTAGE_V0','researchOnly':True,'method':'32 market-bootstrap shallow HGBR advantage models; intervene only if fixed 10th percentile predicted execution-local advantage > 0. No confidence threshold sweep.','trainMarkets':len(mids),'features':feats,'metrics':{'train':metrics(tr,pred(tr)),'validation':metrics(va,pred(va)),'forward':metrics(fw,pred(fw))},'guardrails':['Frozen R2 baseline','Only OPPOSITE_PRIORITY candidate action','Target is -deltaTargetErrorArea only','No winner/PnL/Target runtime input','Random16-30 and Random31-50 untouched by model fitting','Abstain means EXECUTE_AS_R2']}
 (D/'r2_residual_conservative_advantage_v0_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
