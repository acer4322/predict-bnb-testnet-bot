"""V37 research-only Repair Passive wait-risk representation test.
Features are strict-past at V20 Repair frontier birth. Labels describe the common
book path until this Passive carrier first fills or terminalizes. No Target actions
or future Target state are used. Models are leave-one-market-out with fixed L2.
"""
from __future__ import annotations
import json,math,os,random,time
from pathlib import Path
import numpy as np
DATA=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V37_REPAIR_PASSIVE_WAIT_RISK_DATASET_20260912.json')
MARKETS=(2022527,2022538,2022602);L2=1.0;PERM=300;SEED=370912
BOOK=['book_spread','side_book_mid','side_book_micro_delta','side_book_imbalance1','side_book_imbalance5','log1p_bid_depth5','log1p_ask_depth5']
PUBLIC=['side_predictUpMid','side_spotMinusStrikeBps','side_chainlinkMinusStrikeBps','side_spotMinusChainlinkBps','side_spotReturn1sBps','side_spotReturn3sBps','side_futuresReturn1sBps','side_futuresReturn3sBps','side_perpSpotBasisBps','side_spotQueueImbalance','side_futuresQueueImbalance','side_spotTakerImbalance1s','side_futuresTakerImbalance1s']
EXEC=['wall_phase','progress','qty','passive_price','active_ask','premium','pending_count','live_side_per_qty','live_opp_per_qty','book_staleness_ms','public_staleness_ms']
ECON=['atomic_need_per_qty','repair_gap_per_qty','controller_deficit','floor_per_gross','best_per_gross','side_aligned_own_net','fresh_desire','fresh_gap']
GROUPS={'BOOK':BOOK,'PUBLIC_FLOW':PUBLIC,'EXECUTION_STATE':EXEC,'ECONOMIC':ECON,'BOOK_PLUS_EXEC':BOOK+EXEC,'WAIT_STATE':BOOK+PUBLIC+EXEC,'FULL':BOOK+PUBLIC+EXEC+ECON}
CLS=('ask_worse_at_response','any_adverse_ask_move','zero_fill')
REG=('max_adverse_ask_ticks','ask_change_response_ticks','log_response_ms')
def feat(r):
 f=dict(r['features']);return f
def matrix(rows,names):
 a=[]
 for r in rows:
  f=feat(r);a.append([np.nan if f.get(k) is None else float(f[k]) for k in names])
 return np.asarray(a,float)
def prep_fit(X):
 mu=np.nanmean(X,0);mu=np.where(np.isfinite(mu),mu,0.0);Xi=np.where(np.isnan(X),mu,X);sd=Xi.std(0);sd=np.where(sd<1e-9,1.,sd);return mu,sd
def prep(X,mu,sd):return (np.where(np.isnan(X),mu,X)-mu)/sd
def ai(X):return np.c_[np.ones(len(X)),X]
def ridge(X,y):
 mu,sd=prep_fit(X);A=ai(prep(X,mu,sd));reg=np.eye(A.shape[1])*L2;reg[0,0]=0.;w=np.linalg.solve(A.T@A+reg,A.T@y);return mu,sd,w
def rp(X,m):mu,sd,w=m;return ai(prep(X,mu,sd))@w
def sig(z):return 1/(1+np.exp(-np.clip(z,-30,30)))
def logit(X,y):
 mu,sd=prep_fit(X);A=ai(prep(X,mu,sd));w=np.zeros(A.shape[1]);p0=min(.99,max(.01,float(y.mean())));w[0]=math.log(p0/(1-p0));reg=np.eye(A.shape[1])*L2;reg[0,0]=0
 for _ in range(60):
  p=sig(A@w);v=np.maximum(p*(1-p),1e-5);g=A.T@(p-y)+reg@w;H=(A.T*v)@A+reg
  try:step=np.linalg.solve(H,g)
  except np.linalg.LinAlgError:step=np.linalg.pinv(H)@g
  w-=step
  if np.max(np.abs(step))<1e-7:break
 return mu,sd,w
def lp(X,m):mu,sd,w=m;return sig(ai(prep(X,mu,sd))@w)
def auc(y,s):
 y=np.asarray(y,int);s=np.asarray(s,float);n1=int(y.sum());n0=len(y)-n1
 if n1==0 or n0==0:return None
 o=np.argsort(s);rk=np.empty(len(s));i=0
 while i<len(s):
  j=i+1
  while j<len(s) and s[o[j]]==s[o[i]]:j+=1
  av=(i+1+j)/2.;rk[o[i:j]]=av;i=j
 return float((rk[y==1].sum()-n1*(n1+1)/2)/(n1*n0))
def ba(y,p):
 y=np.asarray(y,int);z=np.asarray(p)>=.5;pos=y==1;neg=y==0;return float(.5*((z[pos].mean() if pos.any() else 0)+((~z[neg]).mean() if neg.any() else 0)))
def pear(x,y):
 x=np.asarray(x,float);y=np.asarray(y,float);return None if x.std()<1e-12 or y.std()<1e-12 else float(np.corrcoef(x,y)[0,1])
def metrics(t,p):
 z={'classification':{},'regression':{}}
 for h in CLS:
  y=np.asarray(t[h]);q=np.asarray(p[h]);z['classification'][h]={'auc':auc(y,q),'balanced_accuracy':ba(y,q),'accuracy':float(np.mean((q>=.5)==y)),'brier':float(np.mean((q-y)**2))}
 for h in REG:
  y=np.asarray(t[h]);q=np.asarray(p[h]);z['regression'][h]={'mae':float(np.mean(abs(y-q))),'rmse':float(np.sqrt(np.mean((y-q)**2))),'pearson':pear(y,q),'sign_accuracy':float(np.mean((q>0)==(y>0)))}
 return z
def lomo(rows,names,override=None):
 t={h:[] for h in CLS+REG};p={h:[] for h in CLS+REG};meta=[]
 for hold in MARKETS:
  tr=[r for r in rows if r['market_id']!=hold];te=[r for r in rows if r['market_id']==hold];X=matrix(tr,names);Z=matrix(te,names)
  for h in CLS:
   y=np.asarray([override[(r['row_id'],h)] if override else r['labels'][h] for r in tr],float);p[h]+=lp(Z,logit(X,y)).tolist();t[h]+=[r['labels'][h] for r in te]
  for h in REG:
   y=np.asarray([math.log1p(r['labels']['response_ms']) if h=='log_response_ms' else r['labels'][h] for r in tr],float);p[h]+=rp(Z,ridge(X,y)).tolist();t[h]+=[math.log1p(r['labels']['response_ms']) if h=='log_response_ms' else r['labels'][h] for r in te]
  meta += [{'row_id':r['row_id'],'market_id':hold} for r in te]
 return t,p,meta
def mean_baseline(rows):
 t={h:[] for h in CLS+REG};p={h:[] for h in CLS+REG}
 for hold in MARKETS:
  tr=[r for r in rows if r['market_id']!=hold];te=[r for r in rows if r['market_id']==hold]
  for h in CLS:
   q=float(np.mean([r['labels'][h] for r in tr]));p[h]+=[q]*len(te);t[h]+=[r['labels'][h] for r in te]
  for h in REG:
   vals=[math.log1p(r['labels']['response_ms']) if h=='log_response_ms' else r['labels'][h] for r in tr];q=float(np.mean(vals));p[h]+=[q]*len(te);t[h]+=[math.log1p(r['labels']['response_ms']) if h=='log_response_ms' else r['labels'][h] for r in te]
 return t,p
def null(rows,names,obs):
 rng=random.Random(SEED+len(names));vals={h:[] for h in CLS}
 for _ in range(PERM):
  ov={}
  for h in CLS:
   for m in MARKETS:
    rr=[r for r in rows if r['market_id']==m];v=[r['labels'][h] for r in rr];rng.shuffle(v)
    for r,x in zip(rr,v):ov[(r['row_id'],h)]=x
  t,p,_=lomo(rows,names,ov);mm=metrics(t,p)
  for h in CLS:vals[h].append(mm['classification'][h]['balanced_accuracy'])
 out={'n':PERM}
 for h in CLS:
  o=obs['classification'][h]['balanced_accuracy'];out[h]={'null_mean':float(np.mean(vals[h])),'empirical_p':float((1+sum(x>=o for x in vals[h]))/(PERM+1))}
 return out
def main():
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);t0=time.perf_counter();rows=json.loads(DATA.read_text(encoding='utf-8'))['rows'];assert len(rows)==238
 bt,bp=mean_baseline(rows);res={'version':'V37_REPAIR_WAIT_RISK_MARKET_DISJOINT','rows':len(rows),'groups':{},'baseline':metrics(bt,bp),'feature_groups':GROUPS,'boundaries':['Strict-past frontier features only.','Labels are future common-book Passive wait outcomes, not runtime inputs.','No Target actions/outcomes.','Leave-one-market-out fixed L2=1.0, no threshold/hyperparameter tuning.','Research-only urgency representation, no Active routing authority.']}
 for g,names in GROUPS.items():
  t,p,meta=lomo(rows,names);m=metrics(t,p);res['groups'][g]={'metrics':m,'permutation_null':null(rows,names,m),'predictions':[{**meta[i],**{f'true_{h}':t[h][i] for h in CLS},**{f'pred_{h}':p[h][i] for h in CLS}} for i in range(len(meta))]}
 # primary research gate: WAIT_STATE must beat EXECUTION_STATE on ask-worse BA and max-adverse MAE, with ask-worse permutation p<=.10; FULL should not be required.
 w=res['groups']['WAIT_STATE']['metrics'];e=res['groups']['EXECUTION_STATE']['metrics'];wn=res['groups']['WAIT_STATE']['permutation_null'];res['research_gate']={'wait_state_ask_worse_ba_gt_execution':w['classification']['ask_worse_at_response']['balanced_accuracy']>e['classification']['ask_worse_at_response']['balanced_accuracy'],'wait_state_max_adverse_mae_lt_execution':w['regression']['max_adverse_ask_ticks']['mae']<e['regression']['max_adverse_ask_ticks']['mae'],'wait_state_ask_worse_p_le_010':wn['ask_worse_at_response']['empirical_p']<=.10,'pass':bool(w['classification']['ask_worse_at_response']['balanced_accuracy']>e['classification']['ask_worse_at_response']['balanced_accuracy'] and w['regression']['max_adverse_ask_ticks']['mae']<e['regression']['max_adverse_ask_ticks']['mae'] and wn['ask_worse_at_response']['empirical_p']<=.10)}
 res['elapsed_seconds']=time.perf_counter()-t0;(out/'result.json').write_text(json.dumps(res,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps({'baseline':res['baseline'],'groups':{g:res['groups'][g]['metrics'] for g in GROUPS},'gate':res['research_gate'],'elapsed':res['elapsed_seconds']}),flush=True)
if __name__=='__main__':main()
