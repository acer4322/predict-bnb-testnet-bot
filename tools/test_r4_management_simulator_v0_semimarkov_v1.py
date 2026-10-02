from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('MKL_NUM_THREADS','1')
import json,time,psutil
from pathlib import Path
from collections import defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FEATURES=['seconds_left','abs_gap','coverage','floor_per_gross','checkpointUnresolvedQty','checkpointProgressRatio','checkpointOwnerCount','weakResponsibilityCount','dominantResponsibilityCount','events_15s','transitions_15s'];H=[5,15,30];K=15

def guard():
 vm=psutil.virtual_memory();r={'cpu':psutil.cpu_percent(.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2}
 if r['ram']>=86 or r['cpu']>=75:raise RuntimeError('RESOURCE_GUARD '+json.dumps(r))
 if r['cpu']>=60:time.sleep(.05)
 elif r['cpu']>=45:time.sleep(.02)
 return r

def load_rows():
 dev=json.loads((P/'r4_p0b_lifecycle_dataset_fresh21_v1.json').read_text(encoding='utf-8'))['rows']
 for i in range(3):dev+=json.loads((P/f'r4_management_simulator_multistep_unseen_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 val=[]
 for i in range(3):val+=json.loads((P/f'r4_management_simulator_multistep_rep22_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 return dev,val

def roots(rows):
 d=defaultdict(list)
 for r in rows:d[(int(r['marketId']),str(r['checkpointResponsibilityId']))].append(r)
 for k in d:d[k].sort(key=lambda x:int(x['t']))
 return d

def vec(r):return np.asarray([float(r.get(f) or 0) for f in FEATURES],dtype=float)
def active(r):return float(r.get('weakResponsibilityCount') or 0)+float(r.get('dominantResponsibilityCount') or 0)
def completed5(r):return str(r.get('rootLifecycleOutcome5s'))=='COMPLETED'
def at_h(seq,h):
 t0=int(seq[0]['t']);target=t0+h*1000
 for r in seq:
  if int(r['t'])>=target-250:return {'completed':0.,'live':1.,'unresolved':float(r.get('checkpointUnresolvedQty') or 0),'progress':float(r.get('checkpointProgressRatio') or 0),'active':active(r)}
 for r in reversed(seq):
  if int(r['t'])<=target and completed5(r) and int(r['t'])+5000<=target+500:return {'completed':1.,'live':0.,'unresolved':0.,'progress':1.,'active':0.}
 return None

def episode_library(rd):
 lib=[]
 for key,seq in rd.items():
  st={h:at_h(seq,h) for h in H}
  if all(st.values()):lib.append({'key':key,'v':vec(seq[0]),'init':{'unresolved':float(seq[0].get('checkpointUnresolvedQty') or 0),'progress':float(seq[0].get('checkpointProgressRatio') or 0),'active':active(seq[0])},'states':st})
 return lib

def metric(xs):
 n=len(xs);return {'n':n,'completion_rate':sum(x['completed'] for x in xs)/n,'live_rate':sum(x['live'] for x in xs)/n,'mean_unresolved_qty':sum(x['unresolved'] for x in xs)/n,'mean_progress_ratio':sum(x['progress'] for x in xs)/n,'mean_active_root_count':sum(x['active'] for x in xs)/n}
def main():
 start=time.perf_counter();pre=guard();dev,val=load_rows();dl=episode_library(roots(dev));vr=roots(val);A=np.vstack([x['v'] for x in dl]);q10=np.quantile(A,.1,axis=0);q90=np.quantile(A,.9,axis=0);sc=np.maximum(1e-6,q90-q10);AN=A/sc
 actual={h:[] for h in H};pred={h:[] for h in H};used=0;peak=dict(pre)
 for i,(key,seq) in enumerate(vr.items()):
  if i%5==0:
   rr=guard();peak={k:max(peak[k],rr[k]) for k in peak}
  ast={h:at_h(seq,h) for h in H}
  if not all(ast.values()):continue
  used+=1
  for h in H:actual[h].append(ast[h])
  v0=vec(seq[0]);dist=np.sum((AN-v0/sc)**2,axis=1);kk=min(K,len(dl));inds=np.argpartition(dist,kk-1)[:kk];init={'unresolved':float(seq[0].get('checkpointUnresolvedQty') or 0),'progress':float(seq[0].get('checkpointProgressRatio') or 0),'active':active(seq[0])}
  for h in H:
   preds=[]
   for di in inds:
    donor=dl[int(di)];ds=donor['states'][h]
    if ds['completed']>=.5:preds.append({'completed':1.,'live':0.,'unresolved':0.,'progress':1.,'active':0.})
    else:
     preds.append({'completed':0.,'live':1.,'unresolved':max(0.,init['unresolved']+(ds['unresolved']-donor['init']['unresolved'])),'progress':min(1.,max(0.,init['progress']+(ds['progress']-donor['init']['progress']))),'active':max(0.,init['active']+(ds['active']-donor['init']['active']))})
   m=metric(preds);pred[h].append({k:v for k,v in m.items() if k!='n'})
 repH={};rate_err=[];cont=[]
 for h in H:
  am=metric(actual[h]);n=len(pred[h]);pm={k:sum(x[k] for x in pred[h])/n for k in pred[h][0]};e={k:abs(pm[k]-am[k]) for k in pm};rate_err += [e['completion_rate'],e['live_rate']]
  for k in ['mean_unresolved_qty','mean_progress_ratio','mean_active_root_count']:cont.append(e[k]/max(1.,abs(am[k])))
  repH[str(h)]={'actual':am,'predicted':pm,'absoluteErrors':e}
 post=guard();peak={k:max(peak[k],post[k]) for k in peak};mr=sum(rate_err)/len(rate_err);xr=max(rate_err);mc=sum(cont)/len(cont);gate=mr<=.12 and xr<=.20 and mc<=.25
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_SEMIMARKOV_V1','researchOnly':True,'developmentRows':len(dev),'developmentEpisodes':len(dl),'validationRows':len(val),'validationRootsUsed':used,'kNearestEpisodes':K,'horizons':repH,'summary':{'meanAbsoluteRateError':mr,'maxAbsoluteRateError':xr,'meanNormalizedContinuousError':mc,'gatePass':gate},'resource':{'peak':peak,'end':post},'elapsedSec':time.perf_counter()-start,'interpretation':'Whole-episode context-conditioned nonparametric semi-Markov trajectory transfer. Validation cohort was untouched by method selection.'}
 (P/'r4_management_simulator_v0_semimarkov_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
