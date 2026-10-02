from __future__ import annotations
import json,math,psutil,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
FRESH=json.loads((P/'r4_management_simulator_execution_primitives_dev7_v1.json').read_text());UN=json.loads((P/'r4_management_simulator_execution_primitives_val8_v1.json').read_text())
dev=FRESH['rows']+UN['rows']
valrows=[]
for i in range(4):
 d=json.loads((P/f'r4_management_simulator_economic_late20d_chunk{i}_v1.json').read_text())
 first={}
 for r in d['rows']:
  k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
  if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
 valrows+=list(first.values())

def qfeat(rows):
 by=defaultdict(list)
 for r in rows:by[int(r['marketId'])].append(r)
 out=[]
 for mid,rr in by.items():
  cpu=psutil.cpu_percent(.2);ram=psutil.virtual_memory().percent
  if cpu>=75 or ram>=86:raise SystemExit(f'RESOURCE_GUARD cpu={cpu} ram={ram}')
  br=sim.book_rows(mid)
  for r in rr:
   bs=sim.state_at(br,int(r['t']))
   if bs is None: off=999.;top5=0.;churn=0.
   else:
    side=str(r.get('side') or r.get('checkpointSide') or '')
    px=float(r.get('price') if 'price' in r else r.get('requested_px') or 0)
    off=sim.offset_ticks(side,px,bs);off=999. if off is None else float(off);top5=float(bs[3]);churn=float(bs[4])
   z=dict(r);z['quoteOffsetTicks']=off;z['top5Depth']=top5;z['churn1s']=churn;out.append(z)
 return out
D=qfeat(dev);V=qfeat(valrows)

def target(r,k):
 if k=='anyFill5s':return int(float(r.get('fillQty5s',r.get('rootFillShares5s',0)) or 0)>1e-9)
 if k=='completed5s':return int(r.get('completed5s',r.get('rootCompleted5s',0)) or 0)
 return int(float(r.get('cancelRequest5s',r.get('rootCancelRequests5s',0)) or 0)>0)
BASE=['price','secondsLeft','ownerCount','weakResponsibilityCount','events15s','oldestOwnerAge'];QUEUE=BASE+['quoteOffsetTicks','top5Depth','churn1s']
def gv(r,f):
 mp={'price':r.get('price',r.get('requested_px',0)),'secondsLeft':r.get('secondsLeft',r.get('seconds_left',0)),'ownerCount':r.get('ownerCount',r.get('checkpointOwnerCount',0)),'weakResponsibilityCount':r.get('weakResponsibilityCount',0),'events15s':r.get('events15s',r.get('events_15s',0)),'oldestOwnerAge':r.get('oldestOwnerAge',r.get('checkpointOldestOwnerAgeS',0))}
 return float(mp.get(f,r.get(f,0)) or 0)
def scales(fs):
 s={}
 for f in fs:
  xs=sorted(gv(r,f) for r in D);lo=xs[int(.1*(len(xs)-1))];hi=xs[int(.9*(len(xs)-1))];s[f]=max(1e-6,hi-lo)
 return s
def predict(fs,k):
 sc=scales(fs);out=[]
 for v in V:
  ds=[]
  for d in D:
   dist=sum(((gv(v,f)-gv(d,f))/sc[f])**2 for f in fs);ds.append((dist,d))
  nn=[x[1] for x in sorted(ds,key=lambda x:x[0])[:min(15,len(ds))]];out.append(sum(target(x,k) for x in nn)/len(nn))
 return out
def metrics(fs):
 o={}
 for k in ['anyFill5s','completed5s','cancelRequest5s']:
  y=[target(r,k) for r in V];p=predict(fs,k);ar=sum(y)/len(y);pr=sum(p)/len(p);mae=abs(pr-ar);brier=sum((a-b)**2 for a,b in zip(y,p))/len(y);o[k]={'actualRate':ar,'predRate':pr,'rateAbsError':mae,'brier':brier}
 vals=[x for x in o.values() if isinstance(x,dict)];o['meanRateAbsError']=sum(x['rateAbsError'] for x in vals)/3;o['meanBrier']=sum(x['brier'] for x in vals)/3;return o
base=metrics(BASE);queue=metrics(QUEUE);rep={'version':'R4_MANAGEMENT_SIMULATOR_QUEUE_AWARE_EXECUTION_RATES_DIAGNOSTIC_V1','researchOnly':True,'developmentRoots':len(D),'replicationRoots':len(V),'baselineNoQueue':base,'queueAware':queue,'improvement':{'meanRateAbsError':base['meanRateAbsError']-queue['meanRateAbsError'],'meanBrier':base['meanBrier']-queue['meanBrier']},'keepQueueContext':queue['meanRateAbsError']<base['meanRateAbsError'] and queue['meanBrier']<base['meanBrier'],'interpretation':'Reused Late20d diagnostic only, not untouched graduation. Receipt-clock L2 augments management context without full HFT replay.'};(P/'r4_management_simulator_queue_aware_execution_rates_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
