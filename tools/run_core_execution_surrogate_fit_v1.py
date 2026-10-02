"""Fit a research-only OUR-execution surrogate from native-HFT traces.

Target actions are never read. Train markets: 2022527/2022538 OUR traces.
Held-out execution validation market: 2022602 OUR traces.
Purpose: decide whether a fast structural micro-world may approximate execution
for discovery. Native HFT remains mandatory for every promoted mechanism candidate.
"""
from __future__ import annotations
import gzip,json,math,os,re,statistics,time
from pathlib import Path
import numpy as np

ROOT=Path(r'C:/BTC5M-worker/.lan_worker_v1/results/open-funding-recovery-train-20260911-v3')
FEATURES=['phase','log1p_quote_depth_ticks','spread_ticks','side_depth_imbalance','side_own_net','log1p_own_gross','log_qty','price','pending_count']

def sig(z):
 z=np.clip(z,-30,30);return 1/(1+np.exp(-z))

def parse_trace(path,market,variant,split):
 submits={};terms={}
 with gzip.open(path,'rt',encoding='utf-8') as f:
  for line in f:
   z=json.loads(line);k=z['kind'];d=z['data']
   if k=='complete_policy_step':
    ft=d.get('features') or {};pending=len(d.get('pending_carriers') or {});t=int(d['t'])
    if not ft:continue
    for op in d.get('operations') or []:
     if op.get('kind')!='NEW':continue
     side=op['side'];price=float(op['price']);qty=float(op['qty'])
     if side=='UP':bb=float(ft['up_bid']);ba=float(ft['up_ask']);imb=float(ft['depth_imbalance']);onet=float(ft['own_net'])
     else:bb=1-float(ft['up_ask']);ba=1-float(ft['up_bid']);imb=-float(ft['depth_imbalance']);onet=-float(ft['own_net'])
     depth=max(0.,(bb-price)/.01);spread=max(0.,(ba-bb)/.01)
     x=[float(ft['phase']),math.log1p(depth),spread,imb,onet,math.log1p(max(0.,float(ft['own_gross']))),math.log(max(qty,1e-9)),price,float(pending)]
     submits[op['key']]=dict(key=op['key'],market=market,variant=variant,split=split,t=t,side=side,qty=qty,x=x,quote_depth_ticks=depth,phase=float(ft['phase']))
   elif k=='terminal_owner_once':terms[d['key']]=dict(owner=d['owner'],t=int(d['t']))
 rows=[]
 for key,s in submits.items():
  if key not in terms:continue
  q=float(s['qty']);o=terms[key]['owner'];filled=float(o['filled']);frac=max(0.,min(1.,filled/max(q,1e-12)));lat=max(0,int(terms[key]['t'])-int(s['t']))
  r=dict(s,filled=filled,fill_fraction=frac,fill_any=int(filled>1e-12),full_fill=int(frac>=1-1e-9),terminal_latency_ms=lat)
  rows.append(r)
 return rows

def auc(y,score):
 y=np.asarray(y,int);score=np.asarray(score,float);order=np.argsort(score);r=np.empty(len(order),float);i=0
 while i<len(order):
  j=i+1
  while j<len(order) and score[order[j]]==score[order[i]]:j+=1
  avg=(i+1+j)/2.;r[order[i:j]]=avg;i=j
 n1=y.sum();n0=len(y)-n1
 if n1==0 or n0==0:return None
 return float((r[y==1].sum()-n1*(n1+1)/2)/(n1*n0))

def balacc(y,p,thr=.5):
 y=np.asarray(y,int);z=np.asarray(p)>=thr;pos=y==1;neg=~pos
 return float(.5*((z[pos].mean() if pos.any() else 0)+(~z[neg]).mean() if neg.any() else 0))

def fit_logistic(X,y,l2=1e-3,iters=40):
 mu=X.mean(0);sd=X.std(0);sd=np.where(sd<1e-8,1.,sd);Z=(X-mu)/sd;A=np.c_[np.ones(len(Z)),Z];w=np.zeros(A.shape[1]);reg=np.eye(A.shape[1])*l2;reg[0,0]=0
 for _ in range(iters):
  p=sig(A@w);g=A.T@(p-y)/len(y)+reg@w;v=np.maximum(p*(1-p),1e-5);H=(A.T*v)@A/len(y)+reg
  try:step=np.linalg.solve(H,g)
  except np.linalg.LinAlgError:step=np.linalg.pinv(H)@g
  w-=step
  if np.max(np.abs(step))<1e-7:break
 return mu,sd,w

def predict_logistic(X,mu,sd,w):return sig(np.c_[np.ones(len(X)),(X-mu)/sd]@w)

def fit_ridge(X,y,l2=1e-2):
 mu=X.mean(0);sd=X.std(0);sd=np.where(sd<1e-8,1.,sd);A=np.c_[np.ones(len(X)),(X-mu)/sd];reg=np.eye(A.shape[1])*l2;reg[0,0]=0
 w=np.linalg.solve(A.T@A+reg,A.T@y);return mu,sd,w

def pred_ridge(X,mu,sd,w):return np.c_[np.ones(len(X)),(X-mu)/sd]@w

def metrics(rows,prob,frac_pred,lat_pred):
 y=np.array([r['fill_any'] for r in rows]);ff=np.array([r['fill_fraction'] for r in rows]);lat=np.array([r['terminal_latency_ms'] for r in rows],float)
 return dict(n=len(rows),fill_rate=float(y.mean()),auc_fill=auc(y,prob),balanced_accuracy=balacc(y,prob),brier=float(np.mean((prob-y)**2)),fill_fraction_mae=float(np.mean(np.abs(np.clip(frac_pred,0,1)-ff))),latency_log_mae=float(np.mean(np.abs(np.log1p(np.maximum(0,lat_pred))-np.log1p(lat)))))

def depth_bin(x):
 if x<.5:return 0
 if x<1.5:return 1
 if x<2.5:return 2
 if x<3.5:return 3
 if x<5.5:return 4
 return 5

def cell_key(r):return (min(5,int(max(0.,min(.999999,r['phase']))*6)),depth_bin(r['quote_depth_ticks']),r['side'])

def build_cells(rows):
 by={}
 for r in rows:by.setdefault(cell_key(r),[]).append(r)
 out={}
 for k,a in by.items():
  l=sorted(r['terminal_latency_ms'] for r in a);q=lambda f:l[min(len(l)-1,int(round(f*(len(l)-1))))]
  out[k]=dict(n=len(a),fill_rate=sum(r['fill_any'] for r in a)/len(a),mean_fill_fraction=sum(r['fill_fraction'] for r in a)/len(a),latency_p25_ms=q(.25),latency_median_ms=q(.5),latency_p75_ms=q(.75))
 return out

def cell_predict(rows,cells,min_n=20):
 global_fill=sum(c['fill_rate']*c['n'] for c in cells.values())/max(1,sum(c['n'] for c in cells.values()));global_frac=sum(c['mean_fill_fraction']*c['n'] for c in cells.values())/max(1,sum(c['n'] for c in cells.values()));alllat=[]
 for c in cells.values():alllat += [c['latency_median_ms']]*c['n']
 glat=statistics.median(alllat) if alllat else 0.;p=[];ff=[];lat=[];covered=0
 for r in rows:
  c=cells.get(cell_key(r));ok=c is not None and c['n']>=min_n;covered+=ok
  if not ok:p.append(global_fill);ff.append(global_frac);lat.append(glat)
  else:p.append(c['fill_rate']);ff.append(c['mean_fill_fraction']);lat.append(c['latency_median_ms'])
 return np.array(p),np.array(ff),np.array(lat),covered/max(1,len(rows))

def main():
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);t0=time.perf_counter()
 train=[];test=[];files=[]
 for p in sorted(ROOT.glob('TRAIN_*.jsonl.gz')):
  m=re.search(r'_(2022527|2022538)\.jsonl\.gz$',p.name)
  if not m:continue
  mid=int(m.group(1));variant=p.name[len('TRAIN_'):-(len(str(mid))+10)];rows=parse_trace(p,mid,variant,'TRAIN');train+=rows;files.append(dict(path=p.name,split='TRAIN',market=mid,rows=len(rows)))
 for p in sorted(ROOT.glob('PIPELINE_CHECK_*.jsonl.gz')):
  if not p.name.endswith('_2022602.jsonl.gz'):continue
  variant=p.name[len('PIPELINE_CHECK_'):-len('_2022602.jsonl.gz')];rows=parse_trace(p,2022602,variant,'HELDOUT_2022602');test+=rows;files.append(dict(path=p.name,split='HELDOUT_2022602',market=2022602,rows=len(rows)))
 if not train or not test:raise RuntimeError('missing train/test causal OUR traces')
 X=np.array([r['x'] for r in train],float);y=np.array([r['fill_any'] for r in train],float);Xt=np.array([r['x'] for r in test],float)
 mu,sd,w=fit_logistic(X,y);ptr=predict_logistic(X,mu,sd,w);pte=predict_logistic(Xt,mu,sd,w)
 # Fill fraction ridge is deliberately simple; native validation determines whether this is adequate.
 mf,sf,wf=fit_ridge(X,np.array([r['fill_fraction'] for r in train],float));ftr=pred_ridge(X,mf,sf,wf);fte=pred_ridge(Xt,mf,sf,wf)
 ml,sl,wl=fit_ridge(X,np.log1p(np.array([r['terminal_latency_ms'] for r in train],float)));ltr=np.expm1(pred_ridge(X,ml,sl,wl));lte=np.expm1(pred_ridge(Xt,ml,sl,wl))
 cells=build_cells(train);cp,cf,cl,cov=cell_predict(test,cells)
 result=dict(version='CORE_EXECUTION_SURROGATE_FIT_V1',researchOnly=True,purpose='OUR_NATIVE_EXECUTION_SURROGATE_FOR_FAST_STRUCTURAL_DISCOVERY',target_data_used=False,train_markets=[2022527,2022538],heldout_market=2022602,files=files,features=FEATURES,
  train_metrics=metrics(train,ptr,ftr,ltr),heldout_metrics=metrics(test,pte,fte,lte),cell_heldout_metrics=metrics(test,cp,cf,cl),cell_coverage_min20=cov,
  model=dict(feature_mean=mu.tolist(),feature_sd=sd.tolist(),fill_logistic_weights=w.tolist(),fill_fraction_ridge_mean=mf.tolist(),fill_fraction_ridge_sd=sf.tolist(),fill_fraction_ridge_weights=wf.tolist(),latency_ridge_mean=ml.tolist(),latency_ridge_sd=sl.tolist(),latency_log_ridge_weights=wl.tolist()),
  cells=[dict(phase_bin=k[0],depth_bin=k[1],side=k[2],**v) for k,v in sorted(cells.items())],
  outcome_counts=dict(train_orders=len(train),test_orders=len(test),train_fill=sum(r['fill_any'] for r in train),test_fill=sum(r['fill_any'] for r in test)),
  elapsedSec=time.perf_counter()-t0,
  boundary=['OUR native-HFT causal traces only; Target actions/labels are never read.','Held-out market 2022602 is excluded from fit.','This surrogate can support structural discovery only, never promotion or execution certification.','Every fast-world winner must return unchanged to native HFT.'])
 hm=result['heldout_metrics'];cm=result['cell_heldout_metrics'];result['pilot_gate']=dict(fill_auc_at_least_065=hm['auc_fill'] is not None and hm['auc_fill']>=.65,brier_below_025=hm['brier']<.25,cell_coverage_at_least_075=cov>=.75,pass_for_fast_structural_pilot=bool(hm['auc_fill'] is not None and hm['auc_fill']>=.65 and hm['brier']<.25 and cov>=.75))
 (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
 print(json.dumps({'train_n':len(train),'test_n':len(test),'train':result['train_metrics'],'heldout':hm,'cell':cm,'coverage':cov,'gate':result['pilot_gate'],'elapsedSec':result['elapsedSec']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
