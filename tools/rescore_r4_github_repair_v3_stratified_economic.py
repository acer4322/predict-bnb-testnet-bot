import json,glob,sys
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.metrics import average_precision_score
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import evaluate_r4_github_repair_regime_economic_v3 as v3
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_github_repair_v3_stratified_economic_rescore_v1.json'
ROUTE={'FORMATION':'BASELINE','MGMT_NEG_FLOOR':'GLOBAL','MGMT_NONNEG_FLOOR':'REGIME_FALLBACK','PROTECTION':'BASELINE'}
def load_train():
 r=json.loads(v3.TRAIN0.read_text())['queryRows']
 for p in sorted(glob.glob(v3.TRAIN_GLOB)):r+=json.loads(Path(p).read_text())['queryRows']
 return r
def load_test():
 r=[]
 for p in v3.TESTS:r+=json.loads(Path(p).read_text())['queryRows']
 return r
def conc(p,g):
 s=n=0
 for i in range(len(g)):
  for j in range(i+1,len(g)):
   if abs(g[i]-g[j])<1e-12:continue
   n+=1;d=p[i]-p[j];truth=g[i]>g[j];s+=.5 if abs(d)<1e-12 else float((d>0)==truth)
 return s/n if n else None
def econ(rows,p,rg=None):
 by=defaultdict(list)
 for i,r in enumerate(rows):
  if rg and v3.regime(r)!=rg:continue
  if r.get('repairGain5s') is not None and r.get('repairEconomicEligible'):by[int(r['marketId'])].append(i)
 cs=[];aps=[];details=[]
 for mid,ix in by.items():
  g=np.array([rows[i]['repairGain5s'] for i in ix],float);q=np.asarray(p)[ix];c=conc(q,g);y=(g>1e-12).astype(int);ap=float(average_precision_score(y,q)) if len(np.unique(y))>1 else None
  if c is not None:cs.append(c)
  if ap is not None:aps.append(ap)
  details.append({'marketId':mid,'n':len(ix),'positiveGain':int(y.sum()),'concordance':c,'ap':ap})
 return {'markets':len(by),'concMarkets':len(cs),'meanConcordance':float(np.mean(cs)) if cs else None,'medianConcordance':float(np.median(cs)) if cs else None,'apMarkets':len(aps),'meanAP':float(np.mean(aps)) if aps else None,'medianAP':float(np.median(aps)) if aps else None,'details':details}
def main():
 tr=load_train();te=load_test();full=v3.expert_predictions(tr,te);b=full['BASELINE'];c=np.array([full[ROUTE[v3.regime(r)]][i] for i,r in enumerate(te)])
 out={}
 for rg in [None,'MGMT_NEG_FLOOR','MGMT_NONNEG_FLOOR']:
  ix=list(range(len(te))) if rg is None else [i for i,r in enumerate(te) if v3.regime(r)==rg];rr=[te[i] for i in ix];out[rg or 'ALL']={'baseline':econ(rr,b[ix],rg),'candidate':econ(rr,c[ix],rg)}
 rep={'version':'R4_GITHUB_REPAIR_V3_STRATIFIED_ECONOMIC_RESCORE_V1','posthocMetricCorrection':True,'noRetuning':True,'route':ROUTE,'testMarkets':len(set(r['marketId'] for r in te)),'testQueries':len(te),'result':out}
 OUT.write_text(json.dumps(rep,indent=2));print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
