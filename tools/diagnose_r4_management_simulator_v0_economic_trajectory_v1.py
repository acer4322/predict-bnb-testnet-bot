from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('MKL_NUM_THREADS','1')
import json,time,psutil
from pathlib import Path
from collections import defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FEATURES=['seconds_left','abs_gap','coverage','floor_per_gross','checkpointUnresolvedQty','checkpointProgressRatio','checkpointOwnerCount','weakResponsibilityCount','dominantResponsibilityCount','events_15s','transitions_15s'];H=[5,15,30];K=15

def load():
 dev=json.loads((P/'r4_p0b_lifecycle_dataset_fresh21_v1.json').read_text(encoding='utf-8'))['rows']
 for i in range(3):dev+=json.loads((P/f'r4_management_simulator_multistep_unseen_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 val=[]
 for i in range(3):val+=json.loads((P/f'r4_management_simulator_multistep_rep22_chunk{i}_v1.json').read_text(encoding='utf-8'))['rows']
 return dev,val

def group(rows,keyfn):
 d=defaultdict(list)
 for r in rows:d[keyfn(r)].append(r)
 for k in d:d[k].sort(key=lambda x:int(x['t']))
 return d

def vec(r):return np.asarray([float(r.get(f) or 0) for f in FEATURES],dtype=float)
def nearest_market_state(seq,target):
 if not seq:return None
 return min(seq,key=lambda r:abs(int(r['t'])-target))
def lib(rows):
 rr=group(rows,lambda r:(int(r['marketId']),str(r['checkpointResponsibilityId'])));mm=group(rows,lambda r:int(r['marketId']));out=[]
 for key,seq in rr.items():
  r0=seq[0];ms=mm[int(r0['marketId'])];base={'floor':float(r0.get('floor') or 0),'absNet':float(r0.get('absNet') or 0)};ds={}
  ok=True
  for h in H:
   z=nearest_market_state(ms,int(r0['t'])+h*1000)
   if z is None or abs(int(z['t'])-(int(r0['t'])+h*1000))>1250:ok=False;break
   ds[h]={'floorDelta':float(z.get('floor') or 0)-base['floor'],'absNetDelta':float(z.get('absNet') or 0)-base['absNet']}
  if ok:out.append({'v':vec(r0),'base':base,'delta':ds,'marketId':int(r0['marketId']),'root':str(r0['checkpointResponsibilityId'])})
 return out

def signacc(a,b):return float((a>=0)==(b>=0))
def main():
 start=time.perf_counter();dev,val=load();dl=lib(dev);vl=lib(val);A=np.vstack([x['v'] for x in dl]);sc=np.maximum(1e-6,np.quantile(A,.9,axis=0)-np.quantile(A,.1,axis=0));AN=A/sc
 out={};
 for h in H:
  rows=[]
  for x in vl:
   dist=np.sum((AN-x['v']/sc)**2,axis=1);kk=min(K,len(dl));inds=np.argpartition(dist,kk-1)[:kk]
   pf=sum(dl[int(i)]['delta'][h]['floorDelta'] for i in inds)/kk;pa=sum(dl[int(i)]['delta'][h]['absNetDelta'] for i in inds)/kk;af=x['delta'][h]['floorDelta'];aa=x['delta'][h]['absNetDelta']
   rows.append({'actualFloorDelta':af,'predFloorDelta':pf,'actualAbsNetDelta':aa,'predAbsNetDelta':pa})
  floor_mae=sum(abs(r['predFloorDelta']-r['actualFloorDelta']) for r in rows)/len(rows);abs_mae=sum(abs(r['predAbsNetDelta']-r['actualAbsNetDelta']) for r in rows)/len(rows);floor_scale=max(1.,sum(abs(r['actualFloorDelta']) for r in rows)/len(rows));abs_scale=max(1.,sum(abs(r['actualAbsNetDelta']) for r in rows)/len(rows));
  out[str(h)]={'n':len(rows),'floorDeltaMeanActual':sum(r['actualFloorDelta'] for r in rows)/len(rows),'floorDeltaMeanPred':sum(r['predFloorDelta'] for r in rows)/len(rows),'floorMAE':floor_mae,'floorNormalizedMAE':floor_mae/floor_scale,'floorDirectionAccuracy':sum(signacc(r['predFloorDelta'],r['actualFloorDelta']) for r in rows)/len(rows),'absNetDeltaMeanActual':sum(r['actualAbsNetDelta'] for r in rows)/len(rows),'absNetDeltaMeanPred':sum(r['predAbsNetDelta'] for r in rows)/len(rows),'absNetMAE':abs_mae,'absNetNormalizedMAE':abs_mae/abs_scale,'absNetDirectionAccuracy':sum(signacc(r['predAbsNetDelta'],r['actualAbsNetDelta']) for r in rows)/len(rows)}
 vm=psutil.virtual_memory();rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_ECONOMIC_TRAJECTORY_DIAGNOSTIC_V1','researchOnly':True,'developmentEpisodes':len(dl),'validationEpisodes':len(vl),'horizons':out,'resource':{'cpu':psutil.cpu_percent(.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2},'elapsedSec':time.perf_counter()-start,'interpretation':'Diagnostic only. Same semi-Markov context metric, no refit. Portfolio floor/absNet deltas are transferred from nearest development trajectories and compared on Replication22.'}
 (P/'r4_management_simulator_v0_economic_trajectory_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
