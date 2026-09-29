from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('MKL_NUM_THREADS','1')
import json,random,time,psutil
from pathlib import Path
from collections import defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FEATURES=['seconds_left','abs_gap','coverage','floor_per_gross','checkpointUnresolvedQty','checkpointProgressRatio','checkpointOwnerCount','weakResponsibilityCount','dominantResponsibilityCount','events_15s','transitions_15s']
H=[5,15,30];K=25;ROLLOUTS=40

def guard():
 vm=psutil.virtual_memory();r={'cpu':psutil.cpu_percent(.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2}
 if r['ram']>=86 or r['cpu']>=75: raise RuntimeError('RESOURCE_GUARD '+json.dumps(r))
 if r['cpu']>=60: time.sleep(.05)
 elif r['cpu']>=45: time.sleep(.02)
 return r

def load():
 cal=json.loads((P/'r4_p0b_lifecycle_dataset_fresh21_v1.json').read_text(encoding='utf-8'))['rows'];val=[]
 for i in range(3):val+=json.loads((P/f'r4_management_simulator_multistep_unseen_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 return cal,val

def roots(rows):
 d=defaultdict(list)
 for r in rows:d[(int(r['marketId']),str(r['checkpointResponsibilityId']))].append(r)
 for k in d:d[k].sort(key=lambda x:int(x['t']))
 return d

def vec(r):return np.asarray([float(r.get(f) or 0.0) for f in FEATURES],dtype=np.float64)
def active_count(r):return float(r.get('weakResponsibilityCount') or 0)+float(r.get('dominantResponsibilityCount') or 0)
def terminal_by(r):return str(r.get('rootLifecycleOutcome5s'))=='COMPLETED'
def actual_at(seq,t0,h):
 target=t0+h*1000
 for r in seq:
  if int(r['t'])>=target-250:return {'completed':0.,'live':1.,'unresolved':float(r.get('checkpointUnresolvedQty') or 0),'progress':float(r.get('checkpointProgressRatio') or 0),'active':active_count(r)}
 for r in reversed(seq):
  if int(r['t'])<=target and terminal_by(r) and int(r['t'])+5000<=target+500:return {'completed':1.,'live':0.,'unresolved':0.,'progress':1.,'active':0.}
 return None

def build_transitions(rd):
 trs=[]
 for seq in rd.values():
  for i,r in enumerate(seq):
   target=int(r['t'])+5000;nxt=None
   for z in seq[i+1:]:
    if int(z['t'])>=target-250:nxt=z;break
   if terminal_by(r):trs.append((r,None,True))
   elif nxt is not None:trs.append((r,nxt,False))
 return trs

def metric(xs):
 n=len(xs);return {'n':n,'completion_rate':sum(x['completed'] for x in xs)/n,'live_rate':sum(x['live'] for x in xs)/n,'mean_unresolved_qty':sum(x['unresolved'] for x in xs)/n,'mean_progress_ratio':sum(x['progress'] for x in xs)/n,'mean_active_root_count':sum(x['active'] for x in xs)/n}
def main():
 start=time.perf_counter();pre=guard();cal,val=load();cr,vr=roots(cal),roots(val);trs=build_transitions(cr)
 A=np.vstack([vec(a) for a,_,_ in trs]);p10=np.quantile(A,.1,axis=0);p90=np.quantile(A,.9,axis=0);sc=np.maximum(1e-6,p90-p10);AN=A/sc
 deltas=np.zeros_like(A);terms=np.zeros(len(trs),dtype=np.bool_)
 for i,(a,b,term) in enumerate(trs):
  terms[i]=term
  if b is not None:deltas[i]=vec(b)-vec(a)
 actual={h:[] for h in H};pred={h:[] for h in H};used=0;peak=dict(pre)
 for ri,(key,seq) in enumerate(vr.items()):
  if ri%4==0:
   rr=guard();peak={k:max(peak[k],rr[k]) for k in peak}
  r0=seq[0];t0=int(r0['t']);acts={h:actual_at(seq,t0,h) for h in H}
  if not all(acts.values()):continue
  used+=1
  for h in H:actual[h].append(acts[h])
  accum={h:[] for h in H}
  for q in range(ROLLOUTS):
   rng=random.Random(20260828+int(key[0])*101+q);v=vec(r0);completed=False;unres=float(r0.get('checkpointUnresolvedQty') or 0);prog=float(r0.get('checkpointProgressRatio') or 0);active=active_count(r0)
   for s in range(1,7):
    if not completed:
     vn=v/sc;dist=np.sum((AN-vn)**2,axis=1);kk=min(K,len(dist));inds=np.argpartition(dist,kk-1)[:kk];di=int(inds[rng.randrange(len(inds))])
     if terms[di]:completed=True;unres=0.;prog=1.;active=0.
     else:
      v=v+deltas[di];v[0]=max(0,v[0]);v[1]=max(0,v[1]);v[2]=min(1,max(0,v[2]));v[4]=max(0,v[4]);v[5]=min(1,max(0,v[5]));v[6:]=np.maximum(0,v[6:]);unres=float(v[4]);prog=float(v[5]);active=float(v[7]+v[8])
    if s in (1,3,6):
     h={1:5,3:15,6:30}[s];accum[h].append({'completed':float(completed),'live':float(not completed),'unresolved':unres,'progress':prog,'active':active})
  for h in H:
   m=metric(accum[h]);pred[h].append({k:v for k,v in m.items() if k!='n'})
 repH={};rate_err=[];cont_norm=[]
 for h in H:
  am=metric(actual[h]);n=len(pred[h]);pm={k:sum(x[k] for x in pred[h])/n for k in pred[h][0]};errs={k:abs(pm[k]-am[k]) for k in pm};rate_err += [errs['completion_rate'],errs['live_rate']]
  for k in ['mean_unresolved_qty','mean_progress_ratio','mean_active_root_count']:cont_norm.append(errs[k]/max(1.0,abs(am[k])))
  repH[str(h)]={'actual':am,'predicted':pm,'absoluteErrors':errs}
 post=guard();peak={k:max(peak[k],post[k]) for k in peak};meanrate=sum(rate_err)/len(rate_err);maxrate=max(rate_err);meancont=sum(cont_norm)/len(cont_norm);gate=meanrate<=.12 and maxrate<=.20 and meancont<=.25
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_MULTISTEP_V1','researchOnly':True,'calibrationRows':len(cal),'calibrationTransitions':len(trs),'validationRows':len(val),'validationRootsUsed':used,'rolloutsPerRoot':ROLLOUTS,'horizons':repH,'summary':{'meanAbsoluteRateError':meanrate,'maxAbsoluteRateError':maxrate,'meanNormalizedContinuousError':meancont,'gatePass':gate},'resource':{'peak':peak,'end':post},'elapsedSec':time.perf_counter()-start,'interpretation':'Single-process vectorized empirical strict-past context-conditioned 5s transition resampling rolled to 15s/30s; no role or manager label used.'}
 (P/'r4_management_simulator_v0_multistep_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
