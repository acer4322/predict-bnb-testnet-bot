from __future__ import annotations
import json,random,time,psutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
N=5000
DEV={'COMPLETED':21/60,'LIVE_STALLED':38/60,'OPEN_UNOWNED':1/60}
VAL={'completed_5s':8/35,'live_stalled_5s':26/35,'leaves_live_state_5s':8/35}

def res():
 vm=psutil.virtual_memory();return {'cpu':psutil.cpu_percent(interval=.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2}

def sample(rng):
 u=rng.random()
 if u<DEV['COMPLETED']: return 'COMPLETED'
 if u<DEV['COMPLETED']+DEV['LIVE_STALLED']: return 'LIVE_STALLED'
 return 'OPEN_UNOWNED'

def main():
 c={'COMPLETED':0,'LIVE_STALLED':0,'OPEN_UNOWNED':0};peak={'cpu':0.,'ram':0.,'rssMB':0.};start=time.perf_counter()
 for i in range(N):
  if i%100==0:
   r=res();
   for k in peak:peak[k]=max(peak[k],r[k])
   if r['ram']>=86 or r['cpu']>=75: raise SystemExit('RESOURCE_GUARD '+json.dumps(r))
   if r['cpu']>=60:time.sleep(.05)
   elif r['cpu']>=45:time.sleep(.02)
  c[sample(random.Random(20260828+i))]+=1
 rates={'completed_5s':c['COMPLETED']/N,'live_stalled_5s':c['LIVE_STALLED']/N,'leaves_live_state_5s':(c['COMPLETED'])/N}
 err={k:abs(rates[k]-VAL[k]) for k in rates};passes={k:err[k]<=.10 for k in err}
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_EMPIRICAL_KERNEL_V1','researchOnly':True,'developmentMarginals':DEV,'validationTargets':VAL,'generatedRates':rates,'absoluteErrors':err,'passes':passes,'allPass':all(passes.values()),'counts':c,'peakResource':peak,'elapsedSec':time.perf_counter()-start,'interpretation':'Marginal-only calibration is validated only if it transfers to the untouched Late20 lifecycle distribution. Failure implies context-conditioned kernels are required.'}
 out=P/'r4_management_simulator_v0_empirical_kernel_v1.json';out.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
