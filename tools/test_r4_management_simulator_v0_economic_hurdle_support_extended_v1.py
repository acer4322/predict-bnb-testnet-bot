from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('MKL_NUM_THREADS','1')
import json,time,psutil
from pathlib import Path
from collections import defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';H=[5,15,30];K=30
BF=['seconds_left','abs_gap','floor','absNet','coverage','floor_per_gross','risk_deficit','weak_active_owners','dominant_active_owners','weak_unresolved_shares','dominant_unresolved_shares','current_mode_age_s','events_15s','transitions_15s','requested_px']
def load():
 dev=json.loads((P/'r4_p0b_lifecycle_dataset_fresh21_v1.json').read_text(encoding='utf-8'))['rows']
 for prefix,n in [('r4_management_simulator_multistep_unseen_chunk',3),('r4_management_simulator_multistep_rep22_chunk',3),('r4_management_simulator_economic_late20d_chunk',4),('r4_management_simulator_economic_late20e_chunk',4)]:
  for i in range(n):dev+=json.loads((P/f'{prefix}{i}_v1.json').read_text(encoding='utf-8'))['rows']
 val=[]
 for i in range(4):val+=json.loads((P/f'r4_management_simulator_economic_late20f_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 for i in range(6):val+=json.loads((P/f'r4_management_simulator_economic_late60h_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 return dev,val
def group(rows,keyfn):
 d=defaultdict(list)
 for r in rows:d[keyfn(r)].append(r)
 for k in d:d[k].sort(key=lambda x:int(x['t']))
 return d
def nearest(seq,target,tol=1750):
 if not seq:return None
 z=min(seq,key=lambda r:abs(int(r['t'])-target));return z if abs(int(z['t'])-target)<=tol else None
def f(r,k):
 try:return float(r.get(k) or 0)
 except:return 0.0
def lib(rows):
 rr=group(rows,lambda r:(int(r['marketId']),str(r['checkpointResponsibilityId'])));mm=group(rows,lambda r:int(r['marketId']));out=[]
 for _,seq in rr.items():
  r0=seq[0];ms=mm[int(r0['marketId'])];bfloor=f(r0,'floor');babs=f(r0,'absNet');hs={};ok=True
  for h in H:
   rt=nearest(seq,int(r0['t'])+h*1000);mt=nearest(ms,int(r0['t'])+h*1000)
   if rt is None or mt is None:ok=False;break
   completed=1.0 if str(rt.get('rootLifecycleOutcome5s') or '').upper()=='COMPLETED' or f(rt,'checkpointUnresolvedQty')<=1e-9 else 0.0
   prog=max(f(rt,'checkpointProgressRatio'),1.0-f(rt,'checkpointUnresolvedQty')/max(1e-9,f(rt,'checkpointRequestedQty') or 18.0));active=f(rt,'checkpointOwnerCount')
   hs[h]={'hv':np.asarray([completed,prog,f(rt,'checkpointUnresolvedQty'),active]),'floor':f(mt,'floor')-bfloor,'abs':f(mt,'absNet')-babs}
  if ok:out.append({'bv':np.asarray([f(r0,k) for k in BF]),'h':hs})
 return out
def pred(vals):
 nz=[v for v in vals if abs(v)>1e-9]
 if not nz:return 0.0
 return (len(nz)/len(vals))*float(np.mean(nz))
def dacc(a,p):return 1.0 if (abs(a)<1e-9 and abs(p)<1e-9) or ((a>=0)==(p>=0)) else 0.0
def main():
 start=time.perf_counter();vm=psutil.virtual_memory();cpu=psutil.cpu_percent(.2)
 if vm.percent>=86 or cpu>=75:raise SystemExit('RESOURCE_GUARD '+json.dumps({'cpu':cpu,'ram':vm.percent}))
 devrows,valrows=load();dl,vl=lib(devrows),lib(valrows)
 if len(vl)<30:raise SystemExit('INSUFFICIENT_VALIDATION_ROOTS '+str(len(vl)))
 B=np.vstack([x['bv'] for x in dl]);bs=np.maximum(1e-6,np.quantile(B,.9,axis=0)-np.quantile(B,.1,axis=0));BN=B/bs;out={};passes=0
 for h in H:
  HV=np.vstack([x['h'][h]['hv'] for x in dl]);hs=np.maximum(1e-6,np.quantile(HV,.9,axis=0)-np.quantile(HV,.1,axis=0));HN=HV/hs;rows=[]
  for x in vl:
   dist=np.sum((BN-x['bv']/bs)**2,axis=1)+1.5*np.sum((HN-x['h'][h]['hv']/hs)**2,axis=1);kk=min(K,len(dl));idx=np.argpartition(dist,kk-1)[:kk]
   pf=pred([dl[int(i)]['h'][h]['floor'] for i in idx]);pa=pred([dl[int(i)]['h'][h]['abs'] for i in idx]);rows.append((x['h'][h]['floor'],pf,x['h'][h]['abs'],pa))
  fmae=np.mean([abs(a-p) for a,p,_,__ in rows]);amae=np.mean([abs(a-p) for _,__,a,p in rows]);fs=max(1.,np.mean([abs(a) for a,_,__,___ in rows]));ass=max(1.,np.mean([abs(a) for _,__,a,___ in rows]));fd=np.mean([dacc(a,p) for a,p,_,__ in rows]);ad=np.mean([dacc(a,p) for _,__,a,p in rows]);fn=fmae/fs;an=amae/ass;hp=bool(fd>=.60 and ad>=.60 and fn<=.85 and an<=.85);passes+=int(hp)
  out[str(h)]={'n':len(rows),'floorDirectionAccuracy':float(fd),'absNetDirectionAccuracy':float(ad),'floorNormalizedMAE':float(fn),'absNetNormalizedMAE':float(an),'floorMeanActual':float(np.mean([a for a,_,__,___ in rows])),'floorMeanPred':float(np.mean([p for _,p,__,___ in rows])),'absNetMeanActual':float(np.mean([a for _,__,a,___ in rows])),'absNetMeanPred':float(np.mean([p for _,__,__,p in rows])),'horizonPass':hp}
 vm=psutil.virtual_memory();rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_ECONOMIC_HURDLE_SUPPORT_EXTENDED_V1','researchOnly':True,'developmentRoots':len(dl),'validationRoots':len(vl),'k':K,'horizons':out,'summary':{'horizonsPassingAll':passes,'gatePass':passes>=2},'resource':{'cpu':psutil.cpu_percent(.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2},'elapsedSec':time.perf_counter()-start,'interpretation':'Fixed hurdle kernel: expected economic delta = neighbor nonzero frequency * conditional mean nonzero delta, conditioned on strict-past base context plus oracle lifecycle state.'}
 (P/'r4_management_simulator_v0_economic_hurdle_support_extended_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
