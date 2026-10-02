"""V27 research-only market-disjoint route action-value representation pilot.

Input rows are preregistered exact Passive/Active native-HFT forks. Features are
strict-past only. Target actions are not model labels. This does not authorize
runtime routing, thresholds, sizing or promotion.
"""
from __future__ import annotations
import json, math, os, random, time
from pathlib import Path
import numpy as np

DATA=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V27_ROUTE_ACTION_VALUE_DATASET_20260912.json')
MARKETS=(2022527,2022538,2022602)
L2=1.0
SEED=270912
PERMUTATIONS=300

GROUPS={
 'EXEC_ONLY':['p_fill_passive','premium','passive_price','active_ask','pending_count','qty'],
 'RESPONSIBILITY':['atomic_need_per_qty','repair_qty','fresh_qty','live_side_per_qty','live_opp_per_qty','deficit_per_qty','frontier_REPAIR','material_BIRTH','material_COMPOSITE'],
 'PORTFOLIO':['wall_phase','log1p_own_gross','floor_per_gross','best_per_gross','side_aligned_own_net','side_aligned_thesis'],
 'FULL':['p_fill_passive','premium','passive_price','active_ask','pending_count','qty','atomic_need_per_qty','repair_qty','fresh_qty','live_side_per_qty','live_opp_per_qty','deficit_per_qty','frontier_REPAIR','material_BIRTH','material_COMPOSITE','wall_phase','log1p_own_gross','floor_per_gross','best_per_gross','side_aligned_own_net','side_aligned_thesis'],
}
REG_HEADS=('dFloor_per_qty','dBest_per_qty','dDebt_per_qty')
CLS_HEADS=('floor_positive','best_positive')

def row_features(r):
 f=dict(r['features'])
 f['log1p_own_gross']=math.log1p(max(0.0,float(f['own_gross'])))
 f['frontier_REPAIR']=1.0 if r['frontier_kind']=='REPAIR' else 0.0
 f['material_BIRTH']=1.0 if r['materialization']=='BIRTH_ONLY' else 0.0
 f['material_COMPOSITE']=1.0 if r['materialization']=='COMPOSITE' else 0.0
 return f

def matrix(rows,names): return np.asarray([[float(row_features(r)[k]) for k in names] for r in rows],float)
def standardize_fit(X):
 mu=X.mean(0);sd=X.std(0);sd=np.where(sd<1e-9,1.0,sd);return mu,sd
def add_intercept(X): return np.c_[np.ones(len(X)),X]
def ridge_fit(X,y,l2=L2):
 mu,sd=standardize_fit(X);A=add_intercept((X-mu)/sd);reg=np.eye(A.shape[1])*l2;reg[0,0]=0.0
 w=np.linalg.solve(A.T@A+reg,A.T@y);return mu,sd,w
def ridge_pred(X,m):
 mu,sd,w=m;return add_intercept((X-mu)/sd)@w
def sig(z): return 1/(1+np.exp(-np.clip(z,-30,30)))
def logit_fit(X,y,l2=L2,iters=60):
 mu,sd=standardize_fit(X);A=add_intercept((X-mu)/sd);w=np.zeros(A.shape[1]);reg=np.eye(A.shape[1])*l2;reg[0,0]=0
 # initialize intercept to training prevalence, preventing all-zero start bias
 p0=min(.99,max(.01,float(np.mean(y))));w[0]=math.log(p0/(1-p0))
 for _ in range(iters):
  p=sig(A@w);g=A.T@(p-y)+reg@w;v=np.maximum(p*(1-p),1e-5);H=(A.T*v)@A+reg
  try:step=np.linalg.solve(H,g)
  except np.linalg.LinAlgError:step=np.linalg.pinv(H)@g
  w-=step
  if np.max(np.abs(step))<1e-7:break
 return mu,sd,w
def logit_pred(X,m):
 mu,sd,w=m;return sig(add_intercept((X-mu)/sd)@w)
def auc(y,s):
 y=np.asarray(y,int);s=np.asarray(s,float);n1=int(y.sum());n0=len(y)-n1
 if n1==0 or n0==0:return None
 order=np.argsort(s);r=np.empty(len(s),float);i=0
 while i<len(s):
  j=i+1
  while j<len(s) and s[order[j]]==s[order[i]]:j+=1
  av=(i+1+j)/2.;r[order[i:j]]=av;i=j
 return float((r[y==1].sum()-n1*(n1+1)/2)/(n1*n0))
def balacc(y,p):
 y=np.asarray(y,int);z=np.asarray(p)>=.5;pos=y==1;neg=y==0
 return float(.5*((z[pos].mean() if pos.any() else 0)+(~z[neg]).mean() if neg.any() else 0))
def pear(x,y):
 x=np.asarray(x,float);y=np.asarray(y,float)
 if x.std()<1e-12 or y.std()<1e-12:return None
 return float(np.corrcoef(x,y)[0,1])
def mae(y,p): return float(np.mean(np.abs(np.asarray(y)-np.asarray(p))))
def rmse(y,p): return float(np.sqrt(np.mean((np.asarray(y)-np.asarray(p))**2)))

def lomo_predict(rows,group,override_cls=None):
 names=GROUPS[group];out={h:[] for h in REG_HEADS+CLS_HEADS};truth={h:[] for h in REG_HEADS+CLS_HEADS};meta=[]
 for held in MARKETS:
  tr=[r for r in rows if r['market_id']!=held];te=[r for r in rows if r['market_id']==held];Xt=matrix(tr,names);Xe=matrix(te,names)
  for h in REG_HEADS:
   y=np.array([float(r['outcomes'][h]) for r in tr]);m=ridge_fit(Xt,y);out[h].extend(ridge_pred(Xe,m).tolist());truth[h].extend(float(r['outcomes'][h]) for r in te)
  for h in CLS_HEADS:
   if override_cls is None:y=np.array([int(r['outcomes'][h]) for r in tr],float)
   else:y=np.array([int(override_cls[(r['row_id'],h)]) for r in tr],float)
   m=logit_fit(Xt,y);out[h].extend(logit_pred(Xe,m).tolist());truth[h].extend(int(r['outcomes'][h]) for r in te)
  meta.extend({'row_id':r['row_id'],'market_id':held} for r in te)
 return truth,out,meta

def baseline_predict(rows):
 out={h:[] for h in REG_HEADS+CLS_HEADS};truth={h:[] for h in REG_HEADS+CLS_HEADS}
 for held in MARKETS:
  tr=[r for r in rows if r['market_id']!=held];te=[r for r in rows if r['market_id']==held]
  for h in REG_HEADS:
   m=float(np.mean([float(r['outcomes'][h]) for r in tr]));out[h].extend([m]*len(te));truth[h].extend(float(r['outcomes'][h]) for r in te)
  for h in CLS_HEADS:
   p=float(np.mean([int(r['outcomes'][h]) for r in tr]));out[h].extend([p]*len(te));truth[h].extend(int(r['outcomes'][h]) for r in te)
 return truth,out

def metrics(truth,pred):
 z={'regression':{},'classification':{}}
 for h in REG_HEADS:
  z['regression'][h]={'mae':mae(truth[h],pred[h]),'rmse':rmse(truth[h],pred[h]),'pearson':pear(truth[h],pred[h]),'sign_accuracy':float(np.mean((np.asarray(pred[h])>0)==(np.asarray(truth[h])>0)))}
 for h in CLS_HEADS:
  y=np.array(truth[h]);p=np.array(pred[h]);z['classification'][h]={'auc':auc(y,p),'balanced_accuracy':balacc(y,p),'accuracy':float(np.mean((p>=.5)==y)),'brier':float(np.mean((p-y)**2))}
 # both economic signs correct using the two classifier heads
 z['classification']['joint_sign_accuracy']=float(np.mean(((np.asarray(pred['floor_positive'])>=.5)==np.asarray(truth['floor_positive'])) & ((np.asarray(pred['best_positive'])>=.5)==np.asarray(truth['best_positive']))))
 return z

def permutation_null(rows,group,observed):
 rng=random.Random(SEED+sum(ord(c) for c in group));vals={h:[int(r['outcomes'][h]) for r in rows] for h in CLS_HEADS};ids=[r['row_id'] for r in rows];null={h:[] for h in CLS_HEADS};joint=[]
 # shuffle labels within each market, preserving each market's class prevalence
 for _ in range(PERMUTATIONS):
  ov={}
  for h in CLS_HEADS:
   for m in MARKETS:
    idx=[i for i,r in enumerate(rows) if r['market_id']==m];lab=[vals[h][i] for i in idx];rng.shuffle(lab)
    for i,v in zip(idx,lab):ov[(ids[i],h)]=v
  truth,pred,_=lomo_predict(rows,group,override_cls=ov)
  mm=metrics(truth,pred);joint.append(mm['classification']['joint_sign_accuracy'])
  for h in CLS_HEADS:null[h].append(mm['classification'][h]['balanced_accuracy'])
 ret={'permutations':PERMUTATIONS,'joint_sign_accuracy_null_mean':float(np.mean(joint)),'joint_sign_accuracy_empirical_p':float((1+sum(x>=observed['classification']['joint_sign_accuracy'] for x in joint))/(1+len(joint)))}
 for h in CLS_HEADS:
  obs=observed['classification'][h]['balanced_accuracy'];ret[h]={'null_mean':float(np.mean(null[h])),'empirical_p':float((1+sum(x>=obs for x in null[h]))/(1+len(null[h])))}
 return ret

def main():
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);t0=time.perf_counter();d=json.loads(DATA.read_text(encoding='utf-8'));rows=d['rows'];assert len(rows)==29;assert set(r['market_id'] for r in rows)==set(MARKETS)
 btruth,bpred=baseline_predict(rows);res={'version':'V27_ROUTE_ACTION_VALUE_MARKET_DISJOINT_PILOT','rows':len(rows),'markets':list(MARKETS),'l2':L2,'feature_groups':GROUPS,'heads':{'regression':list(REG_HEADS),'classification':list(CLS_HEADS)},'baseline':metrics(btruth,bpred),'groups':{},'boundaries':['Research-only representation test; no runtime route authority.','All predictors are strict-past. Passive/Active realized fills and Target future actions are excluded from features.','Each prediction is from a model fit on the other two markets only.','Regularization is fixed before evaluation; no hyperparameter selection.','dScore/pathImprove remain diagnostics and are not trained heads.']}
 for g in GROUPS:
  truth,pred,meta=lomo_predict(rows,g);m=metrics(truth,pred);res['groups'][g]={'metrics':m,'predictions':[{**meta[i],**{f'true_{h}':truth[h][i] for h in REG_HEADS+CLS_HEADS},**{f'pred_{h}':pred[h][i] for h in REG_HEADS+CLS_HEADS}} for i in range(len(meta))]}
 # permutation only FULL and compact groups to keep runtime bounded
 for g in ('EXEC_ONLY','RESPONSIBILITY','PORTFOLIO','FULL'):
  res['groups'][g]['permutation_null']=permutation_null(rows,g,res['groups'][g]['metrics'])
 # fixed promotion-style research gate: FULL must improve both sign BAs and joint sign vs execution baseline, and at least 2/3 regression MAEs vs train-mean baseline.
 ex=res['groups']['EXEC_ONLY']['metrics'];fu=res['groups']['FULL']['metrics'];bl=res['baseline'];regwins=sum(fu['regression'][h]['mae']<bl['regression'][h]['mae'] for h in REG_HEADS)
 res['research_gate']={'full_floor_ba_gt_exec':fu['classification']['floor_positive']['balanced_accuracy']>ex['classification']['floor_positive']['balanced_accuracy'],'full_best_ba_gt_exec':fu['classification']['best_positive']['balanced_accuracy']>ex['classification']['best_positive']['balanced_accuracy'],'full_joint_gt_exec':fu['classification']['joint_sign_accuracy']>ex['classification']['joint_sign_accuracy'],'full_regression_mae_wins_vs_mean_baseline':regwins,'pass_for_next_representation_iteration':bool(fu['classification']['floor_positive']['balanced_accuracy']>ex['classification']['floor_positive']['balanced_accuracy'] and fu['classification']['best_positive']['balanced_accuracy']>ex['classification']['best_positive']['balanced_accuracy'] and fu['classification']['joint_sign_accuracy']>ex['classification']['joint_sign_accuracy'] and regwins>=2)}
 res['elapsed_seconds']=time.perf_counter()-t0;(out/'result.json').write_text(json.dumps(res,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8');print(json.dumps({'rows':len(rows),'baseline':res['baseline'],'groups':{g:res['groups'][g]['metrics'] for g in GROUPS},'gate':res['research_gate'],'elapsed':res['elapsed_seconds']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
