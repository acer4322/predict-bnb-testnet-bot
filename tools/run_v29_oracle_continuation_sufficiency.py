"""V29 research-only oracle continuation sufficiency audit.
Post-action transition-flow features are diagnostic only and can never be runtime inputs.
Goal: determine whether short event-distance continuation contains market-disjoint information
about terminal Passive-vs-Active route value beyond strict-past local state.
"""
from __future__ import annotations
import json,math,os,random,time
from pathlib import Path
import numpy as np
V27=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V27_ROUTE_ACTION_VALUE_DATASET_20260912.json')
V28=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/V28_EVENT_DISTANCE_CONTINUATION_KERNEL_20260912.json')
MARKETS=(2022527,2022538,2022602);L2=1.0;NS=(1,2,4,8);SEED=290912;PERM=200
LOCAL=['wall_phase','log1p_own_gross','floor_per_gross','best_per_gross','side_aligned_own_net','side_aligned_thesis','atomic_need_per_qty','repair_qty','fresh_qty','live_side_per_qty','live_opp_per_qty','deficit_per_qty','frontier_REPAIR','material_BIRTH','material_COMPOSITE','p_fill_passive','premium','passive_price','active_ask','pending_count','qty']
FLOW=['dBirthQty_per_qty','dPayQty_per_qty','dPairQty_per_qty','dGross_per_qty','dCost_per_qty','signed_log_event_time']
ECON=['early_dFloor_per_qty','early_dBest_per_qty','early_dDebt_per_qty']
HEADS=('dFloor_per_qty','dBest_per_qty','dDebt_per_qty');CLASSES=('floor_positive','best_positive')
def base_feat(r):
 f=dict(r['features']);f['log1p_own_gross']=math.log1p(max(0,float(f['own_gross'])));f['frontier_REPAIR']=float(r['frontier_kind']=='REPAIR');f['material_BIRTH']=float(r['materialization']=='BIRTH_ONLY');f['material_COMPOSITE']=float(r['materialization']=='COMPOSITE');return f
def fit_std(X):
 m=X.mean(0);s=X.std(0);s=np.where(s<1e-9,1.,s);return m,s
def ai(X):return np.c_[np.ones(len(X)),X]
def ridge(X,y):
 m,s=fit_std(X);A=ai((X-m)/s);reg=np.eye(A.shape[1])*L2;reg[0,0]=0;w=np.linalg.solve(A.T@A+reg,A.T@y);return m,s,w
def rpred(X,z):m,s,w=z;return ai((X-m)/s)@w
def sig(z):return 1/(1+np.exp(-np.clip(z,-30,30)))
def logit(X,y):
 m,s=fit_std(X);A=ai((X-m)/s);w=np.zeros(A.shape[1]);p0=min(.99,max(.01,float(y.mean())));w[0]=math.log(p0/(1-p0));reg=np.eye(A.shape[1])*L2;reg[0,0]=0
 for _ in range(60):
  p=sig(A@w);v=np.maximum(p*(1-p),1e-5);g=A.T@(p-y)+reg@w;H=(A.T*v)@A+reg
  try:step=np.linalg.solve(H,g)
  except np.linalg.LinAlgError:step=np.linalg.pinv(H)@g
  w-=step
  if np.max(np.abs(step))<1e-7:break
 return m,s,w
def lpred(X,z):m,s,w=z;return sig(ai((X-m)/s)@w)
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
 x=np.asarray(x,float);y=np.asarray(y,float)
 return None if x.std()<1e-12 or y.std()<1e-12 else float(np.corrcoef(x,y)[0,1])
def metr(t,p):
 out={'reg':{},'cls':{}}
 for h in HEADS:
  y=np.asarray(t[h]);q=np.asarray(p[h]);out['reg'][h]={'mae':float(np.mean(abs(y-q))),'pearson':pear(y,q),'sign_accuracy':float(np.mean((q>0)==(y>0)))}
 for h in CLASSES:
  y=np.asarray(t[h]);q=np.asarray(p[h]);out['cls'][h]={'auc':auc(y,q),'balanced_accuracy':ba(y,q),'brier':float(np.mean((q-y)**2))}
 out['cls']['joint_sign_accuracy']=float(np.mean(((np.asarray(p['floor_positive'])>=.5)==np.asarray(t['floor_positive'])) & ((np.asarray(p['best_positive'])>=.5)==np.asarray(t['best_positive']))))
 return out
def build_rows(n):
 a=json.loads(V27.read_text(encoding='utf-8'));b=json.loads(V28.read_text(encoding='utf-8'));bm={x['row_id']:x for x in b['rows']};rows=[]
 for r in a['rows']:
  z=bm[r['row_id']];h=z['horizons'].get(str(n))
  if not h:continue
  f=base_feat(r);q=max(float(f['qty']),1e-12);f.update(dBirthQty_per_qty=float(h['dBirthQty'])/q,dPayQty_per_qty=float(h['dPayQty'])/q,dPairQty_per_qty=float(h['dPairQty'])/q,dGross_per_qty=float(h['dGross'])/q,dCost_per_qty=float(h['dCost'])/q,early_dFloor_per_qty=float(h['dFloor'])/q,early_dBest_per_qty=float(h['dBest'])/q,early_dDebt_per_qty=float(h['dDebt'])/q,signed_log_event_time=math.copysign(math.log1p(abs(float(h['d_event_time_ms']))),float(h['d_event_time_ms'])) if float(h['d_event_time_ms'])!=0 else 0.0)
  rows.append({'row_id':r['row_id'],'market_id':r['market_id'],'f':f,'o':r['outcomes']})
 return rows
def pred_lomo(rows,names,override=None):
 truth={h:[] for h in HEADS+CLASSES};pred={h:[] for h in HEADS+CLASSES}
 for hold in MARKETS:
  tr=[r for r in rows if r['market_id']!=hold];te=[r for r in rows if r['market_id']==hold];X=np.asarray([[x['f'][k] for k in names] for x in tr],float);Z=np.asarray([[x['f'][k] for k in names] for x in te],float)
  for h in HEADS:
   y=np.asarray([x['o'][h] for x in tr],float);pred[h]+=rpred(Z,ridge(X,y)).tolist();truth[h]+=[x['o'][h] for x in te]
  for h in CLASSES:
   y=np.asarray([override[(x['row_id'],h)] if override else x['o'][h] for x in tr],float);pred[h]+=lpred(Z,logit(X,y)).tolist();truth[h]+=[x['o'][h] for x in te]
 return truth,pred
def null(rows,names,obs):
 rng=random.Random(SEED+len(names));dist=[]
 for _ in range(PERM):
  ov={}
  for h in CLASSES:
   for m in MARKETS:
    rr=[r for r in rows if r['market_id']==m];v=[r['o'][h] for r in rr];rng.shuffle(v)
    for r,x in zip(rr,v):ov[(r['row_id'],h)]=x
  t,p=pred_lomo(rows,names,ov);dist.append(metr(t,p)['cls']['joint_sign_accuracy'])
 return {'n':PERM,'mean_joint':float(np.mean(dist)),'joint_empirical_p':float((1+sum(x>=obs['cls']['joint_sign_accuracy'] for x in dist))/(1+len(dist)))}
def main():
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);t0=time.perf_counter();res={'version':'V29_ORACLE_CONTINUATION_SUFFICIENCY','horizons':{},'boundary':['Post-action continuation features are oracle diagnostics only and are forbidden as runtime inputs.','Leave-one-market-out fixed regularization; no threshold/hyperparameter selection.','Purpose is horizon/representation discovery, not route promotion.']}
 for n in NS:
  rows=build_rows(n);groups={'LOCAL':LOCAL,'FLOW_ONLY':FLOW,'FLOW_PLUS_ECON':FLOW+ECON,'LOCAL_PLUS_FLOW':LOCAL+FLOW,'LOCAL_PLUS_FLOW_ECON':LOCAL+FLOW+ECON};hz={'rows':len(rows),'groups':{}}
  for g,names in groups.items():
   t,p=pred_lomo(rows,names);m=metr(t,p);hz['groups'][g]={'metrics':m,'permutation_null':null(rows,names,m) if g in ('FLOW_ONLY','LOCAL_PLUS_FLOW','LOCAL_PLUS_FLOW_ECON') else None}
  # research comparison: does flow improve over local on both sign heads and joint?
  l=hz['groups']['LOCAL']['metrics']['cls'];f=hz['groups']['LOCAL_PLUS_FLOW']['metrics']['cls'];hz['flow_increment_gate']={'floor_ba_up':f['floor_positive']['balanced_accuracy']>l['floor_positive']['balanced_accuracy'],'best_ba_up':f['best_positive']['balanced_accuracy']>l['best_positive']['balanced_accuracy'],'joint_up':f['joint_sign_accuracy']>l['joint_sign_accuracy'],'pass':bool(f['floor_positive']['balanced_accuracy']>l['floor_positive']['balanced_accuracy'] and f['best_positive']['balanced_accuracy']>l['best_positive']['balanced_accuracy'] and f['joint_sign_accuracy']>l['joint_sign_accuracy'])}
  res['horizons'][str(n)]=hz
 res['elapsed_seconds']=time.perf_counter()-t0;(out/'result.json').write_text(json.dumps(res,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps({'horizons':{n:{'rows':z['rows'],'gate':z['flow_increment_gate'],'local':z['groups']['LOCAL']['metrics']['cls'],'flow':z['groups']['FLOW_ONLY']['metrics']['cls'],'local_flow':z['groups']['LOCAL_PLUS_FLOW']['metrics']['cls']} for n,z in res['horizons'].items()},'elapsed':res['elapsed_seconds']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
