from __future__ import annotations
import json,random,time,psutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_management_simulator_v0_feasibility_v1 as ms
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
REF={'weak_fill_5s':0.4166666667,'completed_5s':0.35,'persists_5s':0.65}
N=5000

def resource():
 vm=psutil.virtual_memory();return {'cpu':psutil.cpu_percent(interval=.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2}

def main():
 seeds=ms.load_seed_rows(); counts={'weak_fill_5s':0,'completed_5s':0,'persists_5s':0}; start=time.perf_counter();peak={'cpu':0.,'ram':0.,'rssMB':0.}
 for i in range(N):
  if i%100==0:
   r=resource();
   for k in peak: peak[k]=max(peak[k],r[k])
   if r['ram']>=86 or r['cpu']>=75: raise SystemExit('RESOURCE_GUARD '+json.dumps(r))
   if r['cpu']>=60: time.sleep(.05)
   elif r['cpu']>=45: time.sleep(.02)
  out=ms.episode(seeds[i%len(seeds)],i,random.Random(20260828+i)); s=out['final']
  weak=s['confirmed']>0
  completed=(s['confirmed']>=18-1e-9 or s['residual']<=1e-9)
  persists=(s['active']>0 and not completed)
  counts['weak_fill_5s']+=int(weak);counts['completed_5s']+=int(completed);counts['persists_5s']+=int(persists)
 rates={k:counts[k]/N for k in counts};err={k:abs(rates[k]-REF[k]) for k in rates};passes={k:err[k]<=.10 for k in err}
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_TRANSITION_FIDELITY_V1','researchOnly':True,'episodes':N,'referenceRates':REF,'simulatedRates':rates,'absoluteErrors':err,'passes':passes,'allPass':all(passes.values()),'peakResource':peak,'elapsedSec':time.perf_counter()-start,'interpretation':'This is a pre-calibration fidelity gate. Failure means the arbitrary V0 transition kernel must not be used for curriculum training.'}
 out=P/'r4_management_simulator_v0_transition_fidelity_v1.json';out.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
