"""V30B research-only strict-past Logic representation screen.
Semantic feature groups are fixed by source/domain, not by outcome threshold fitting.
Leave-one-market-out only; no runtime route authority.
"""
from __future__ import annotations
import json,math,os,random,time
from pathlib import Path
import numpy as np
DATA=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V30_ROUTE_LOGIC_ENRICHED_DATASET_20260912.json')
MARKETS=(2022527,2022538,2022602);L2=1.0;PERM=300;SEED=300912
PRICE=['side_predictUpMid','side_spotMinusStrikeBps','side_chainlinkMinusStrikeBps','side_spotMinusChainlinkBps','side_book_mid','side_book_micro_delta','book_spread']
FLOW=['side_spotReturn1sBps','side_spotReturn3sBps','side_futuresReturn1sBps','side_futuresReturn3sBps','side_perpSpotBasisBps','side_spotQueueImbalance','side_futuresQueueImbalance','side_spotTakerImbalance1s','side_futuresTakerImbalance1s']
BOOK=['side_book_imbalance1','side_book_imbalance5','log1p_bid_depth5','log1p_ask_depth5','book_spread','side_book_micro_delta']
PORT=['wall_phase','log1p_own_gross','floor_per_gross','best_per_gross','side_aligned_own_net','side_aligned_thesis','atomic_need_per_qty','repair_qty','fresh_qty','live_side_per_qty','live_opp_per_qty','deficit_per_qty','frontier_REPAIR','material_BIRTH','material_COMPOSITE']
GROUPS={'PRICE':PRICE,'FLOW':FLOW,'BOOK':BOOK,'LOGIC_ALL':PRICE+FLOW+[x for x in BOOK if x not in PRICE+FLOW],'PORTFOLIO':PORT,'PORT_PLUS_LOGIC':PORT+PRICE+FLOW+[x for x in BOOK if x not in PRICE+FLOW]}
REG=('dFloor_per_qty','dBest_per_qty','dDebt_per_qty');CLS=('floor_positive','best_positive')
def feat(r):
 f=dict(r['base_features']);l=dict(r['logic_features']);f.update(l);f['log1p_own_gross']=math.log1p(max(0,float(f['own_gross'])));f['frontier_REPAIR']=float(r['frontier_kind']=='REPAIR');f['material_BIRTH']=float(r['materialization']=='BIRTH_ONLY');f['material_COMPOSITE']=float(r['materialization']=='COMPOSITE');return f
# Matrix keeps nan for strict train-only imputation.
def matrix(rows,names):
 a=[]
 for r in rows:
  q=[];f=feat(r)
  for k in names:
   v=f.get(k);q.append(np.nan if v is None else float(v))
  a.append(q)
 return np.asarray(a,float)
def prep_fit(X):
 mu=np.nanmean(X,0);mu=np.where(np.isfinite(mu),mu,0.0);Xi=np.where(np.isnan(X),mu,X);sd=Xi.std(0);sd=np.where(sd<1e-9,1.,sd);return mu,sd
def prep(X,mu,sd):return (np.where(np.isnan(X),mu,X)-mu)/sd
def ai(X):return np.c_[np.ones(len(X)),X]
def ridge(X,y):
 mu,sd=prep_fit(X);A=ai(prep(X,mu,sd));reg=np.eye(A.shape[1])*L2;reg[0,0]=0;w=np.linalg.solve(A.T@A+reg,A.T@y);return mu,sd,w
def rp(X,m):mu,sd,w=m;return ai(prep(X,mu,sd))@w
def sig(z):return 1/(1+np.exp(-np.clip(z,-30,30)))
def logit(X,y):
 mu,sd=prep_fit(X);A=ai(prep(X,mu,sd));w=np.zeros(A.shape[1]);p0=min(.99,max(.01,float(y.mean())));w[0]=math.log(p0/(1-p0));reg=np.eye(A.shape[1])*L2;reg[0,0]=0
 for _ in range(60):
  p=sig(A@w);v=np.maximum(p*(1-p),1e-5);g=A.T@(p-y)+reg@w;H=(A.T*v)@A+reg
  try:step=np.linalg.solve(H,g)
  except np.linalg.LinAlgError:step=np.linalg.pinv(H)@g
  w-=step
  if np.max(abs(step))<1e-7:break
 return mu,sd,w
def lp(X,m):mu,sd,w=m;return sig(ai(prep(X,mu,sd))@w)
def auc(y,s):
 y=np.asarray(y,int);s=np.asarray(s,float);n1=y.sum();n0=len(y)-n1
 if n1==0 or n0==0:return None
 o=np.argsort(s);rk=np.empty(len(s));i=0
 while i<len(s):
  j=i+1
  while j<len(s) and s[o[j]]==s[o[i]]:j+=1
  av=(i+1+j)/2;rk[o[i:j]]=av;i=j
 return float((rk[y==1].sum()-n1*(n1+1)/2)/(n1*n0))
def ba(y,p):
 y=np.asarray(y,int);z=np.asarray(p)>=.5;pos=y==1;neg=y==0;return float(.5*((z[pos].mean() if pos.any() else 0)+((~z[neg]).mean() if neg.any() else 0)))
def pear(x,y):
 x=np.asarray(x,float);y=np.asarray(y,float);return None if x.std()<1e-12 or y.std()<1e-12 else float(np.corrcoef(x,y)[0,1])
def metrics(t,p):
 z={'reg':{},'cls':{}}
 for h in REG:
  y=np.asarray(t[h]);q=np.asarray(p[h]);z['reg'][h]={'mae':float(np.mean(abs(y-q))),'pearson':pear(y,q),'sign_accuracy':float(np.mean((q>0)==(y>0)))}
 for h in CLS:
  y=np.asarray(t[h]);q=np.asarray(p[h]);z['cls'][h]={'auc':auc(y,q),'balanced_accuracy':ba(y,q),'brier':float(np.mean((q-y)**2))}
 z['cls']['joint_sign_accuracy']=float(np.mean(((np.asarray(p['floor_positive'])>=.5)==np.asarray(t['floor_positive'])) & ((np.asarray(p['best_positive'])>=.5)==np.asarray(t['best_positive']))))
 return z
def lomo(rows,names,override=None):
 t={h:[] for h in REG+CLS};p={h:[] for h in REG+CLS};meta=[]
 for hold in MARKETS:
  tr=[r for r in rows if r['market_id']!=hold];te=[r for r in rows if r['market_id']==hold];X=matrix(tr,names);Z=matrix(te,names)
  for h in REG:
   y=np.asarray([r['outcomes'][h] for r in tr],float);p[h]+=rp(Z,ridge(X,y)).tolist();t[h]+=[r['outcomes'][h] for r in te]
  for h in CLS:
   y=np.asarray([override[(r['row_id'],h)] if override else r['outcomes'][h] for r in tr],float);p[h]+=lp(Z,logit(X,y)).tolist();t[h]+=[r['outcomes'][h] for r in te]
  meta += [{'row_id':r['row_id'],'market_id':hold} for r in te]
 return t,p,meta
def null(rows,names,obs):
 rng=random.Random(SEED+len(names));dist={h:[] for h in CLS};joint=[]
 for _ in range(PERM):
  ov={}
  for h in CLS:
   for m in MARKETS:
    rr=[r for r in rows if r['market_id']==m];vv=[r['outcomes'][h] for r in rr];rng.shuffle(vv)
    for r,v in zip(rr,vv):ov[(r['row_id'],h)]=v
  t,p,_=lomo(rows,names,ov);mm=metrics(t,p);joint.append(mm['cls']['joint_sign_accuracy'])
  for h in CLS:dist[h].append(mm['cls'][h]['balanced_accuracy'])
 out={'n':PERM,'joint_null_mean':float(np.mean(joint)),'joint_p':float((1+sum(x>=obs['cls']['joint_sign_accuracy'] for x in joint))/(PERM+1))}
 for h in CLS:
  val=obs['cls'][h]['balanced_accuracy'];out[h]={'null_mean':float(np.mean(dist[h])),'p':float((1+sum(x>=val for x in dist[h]))/(PERM+1))}
 return out
def main():
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);t0=time.perf_counter();rows=json.loads(DATA.read_text(encoding='utf-8'))['rows'];assert len(rows)==29
 res={'version':'V30B_LOGIC_REPRESENTATION_MARKET_DISJOINT','rows':29,'feature_groups':GROUPS,'groups':{},'boundary':['Strict-past public/book state only; no Target action labels.','Semantic feature groups fixed before fit; no threshold/hyperparameter selection.','Leave-one-market-out fixed regularization.','Research-only representation screen; no route authority.']}
 for g,names in GROUPS.items():
  t,p,meta=lomo(rows,names);m=metrics(t,p);res['groups'][g]={'metrics':m,'permutation_null':null(rows,names,m),'predictions':[{**meta[i],**{f'true_{h}':t[h][i] for h in CLS},**{f'pred_{h}':p[h][i] for h in CLS}} for i in range(len(meta))]}
 port=res['groups']['PORTFOLIO']['metrics']['cls'];pl=res['groups']['PORT_PLUS_LOGIC']['metrics']['cls'];res['research_gate']={'floor_ba_up':pl['floor_positive']['balanced_accuracy']>port['floor_positive']['balanced_accuracy'],'best_ba_up':pl['best_positive']['balanced_accuracy']>port['best_positive']['balanced_accuracy'],'joint_up':pl['joint_sign_accuracy']>port['joint_sign_accuracy'],'pass':bool(pl['floor_positive']['balanced_accuracy']>port['floor_positive']['balanced_accuracy'] and pl['best_positive']['balanced_accuracy']>port['best_positive']['balanced_accuracy'] and pl['joint_sign_accuracy']>port['joint_sign_accuracy'])}
 res['elapsed_seconds']=time.perf_counter()-t0;(out/'result.json').write_text(json.dumps(res,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps({'groups':{g:res['groups'][g]['metrics'] for g in GROUPS},'gate':res['research_gate'],'elapsed':res['elapsed_seconds']}),flush=True)
if __name__=='__main__':main()
