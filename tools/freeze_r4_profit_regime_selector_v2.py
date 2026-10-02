from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, joblib
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_a2b_v1.json'
OUTJ=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_v2_frozen.joblib'
OUTC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_v2_frozen_contract.json'
FEATURES=['pMakerSum','pResidualWake','orderAgeS']
SEED=20260831

d=json.loads(SRC.read_text(encoding='utf-8'))
rows=d['rows']
X=np.array([[float(r['features'].get(f,math.nan)) for f in FEATURES] for r in rows],float)
y=np.array([int(r['labelH2Better']) for r in rows],int)
model=Pipeline([
 ('imp',SimpleImputer(strategy='median',add_indicator=True)),
 ('scale',StandardScaler()),
 ('lr',LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=SEED))
])
model.fit(X,y)
joblib.dump({'model':model,'features':FEATURES,'threshold':.5,'seed':SEED},OUTJ)
contract={
 'version':'R4_PROFIT_REGIME_SELECTOR_V2_FROZEN',
 'researchOnly':True,
 'frozenBeforeChallengeC':True,
 'developmentMarkets':len(rows),
 'features':FEATURES,
 'featureRationale':{
   'pMakerSum':'higher passive Maker formation pressure had directionally consistent H2-benefit tendency across prior A and B development cohorts',
   'pResidualWake':'lower residual/active-repair pressure had directionally consistent H2-benefit tendency across prior A and B development cohorts',
   'orderAgeS':'younger first-pull carrier had directionally consistent H2-benefit tendency across prior A and B development cohorts'
 },
 'model':'median+indicator -> StandardScaler -> LogisticRegression(C=0.5,class_weight=balanced,max_iter=2000,seed=20260831)',
 'threshold':0.5,
 'decisionSeam':'first candidate CONT_STATE_H2 negative-marginal pull; decide H2-mode vs FLEX-mode before executing the first H2 pull',
 'runtimeBoundary':'strict-past only; no winner, terminal PnL, settlement, future Target action or future HFT fill labels',
 'challengeBoundary':'Challenge C must be market-disjoint and selected before reading its winner/PnL; no threshold/C/feature changes after Challenge C begins',
 'actionAuthority':'research replay only; no live R3/R3.1/8781 changes',
 'noTaker':True,
 'authoritySafeRequired':True
}
OUTC.write_text(json.dumps(contract,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(contract,indent=2,ensure_ascii=False))
