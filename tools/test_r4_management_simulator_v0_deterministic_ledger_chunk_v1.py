from __future__ import annotations
import argparse,json,lzma,sys,psutil,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1';FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json';SRC=ROOT/'data/hft_forward_paper_v1/markets'
def core(x): return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def takers(d):
 out=[]
 for x in d.get('takerEvents') or []:
  q=float(x.get('shares') or x.get('filledShares') or x.get('deltaShares') or x.get('qty') or 0);px=float(x.get('price') or x.get('fillPrice') or x.get('avgPrice') or 0);side=str(x.get('side') or '').upper();t=int(x.get('observedAtMs') or x.get('atMs') or x.get('fillMs') or 0)
  if q>0 and side in {'UP','DOWN'} and t:out.append((t,1,'TAKER',side,q,px))
 return out
def makers(r):
 out=[];seen=set();orph=0
 for e in r.get('provenanceJournal') or []:
  if e.get('event_type') not in {'PARTIAL_FILL','FULL_FILL'}:continue
  xid=e.get('execution_id')
  if not xid:orph+=1;continue
  if xid in seen:continue
  seen.add(xid);q=float((e.get('extras') or {}).get('fillDeltaQty') or 0);px=float(e.get('price') or 0);side=str(e.get('side') or '').upper();t=int(e.get('received_at_ms') or 0)
  if q>0 and side in {'UP','DOWN'} and t:out.append((t,0,'MAKER',side,q,px))
 return out,len(seen),orph
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();fc=json.loads(FIX.read_text(encoding='utf-8'));ids=[int(x) for x in fc['managementHftReplication']['markets']][a.start:a.start+a.count]
 rows=[];start=time.perf_counter();peakcpu=peakram=0.;
 for mid in ids:
  vm=psutil.virtual_memory();cpu=psutil.cpu_percent(.05);peakcpu=max(peakcpu,cpu);peakram=max(peakram,vm.percent)
  if cpu>=75 or vm.percent>=86: raise SystemExit(f'RESOURCE_GUARD cpu={cpu} ram={vm.percent}')
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b0=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
  me,nexec,orph=makers(r);ev=sorted(me+takers(d),key=lambda z:(z[0],z[1]));s=(0.,0.,0.,0.,0.);j=0;mx={'floor':0.,'absNet':0.,'coverage':0.};n=0
  for row in sorted(r.get('managementLifecycleRows') or [],key=lambda x:int(x['t'])):
   t=int(row['t'])
   while j<len(ev) and ev[j][0]<=t:
    _,_,role,side,q,px=ev[j];s=v2.apply(s,side,px,q,role=='TAKER');j+=1
   g=v2.geom(s)
   for k in mx:mx[k]=max(mx[k],abs(float(g[k])-float(row[k])))
   n+=1
  z={'marketId':mid,'executionCoreExact':core(b0)==core(r),'checkpoints':n,'makerExecIds':nexec,'orphanFill':orph,'maxAbsError':mx};rows.append(z);print(json.dumps(z),flush=True)
 out=P/f'r4_management_simulator_v0_deterministic_ledger_chunk_{a.start}_{len(ids)}_v1.json';out.write_text(json.dumps({'start':a.start,'count':len(ids),'rows':rows,'resource':{'peakCpu':peakcpu,'peakRam':peakram},'elapsedSec':time.perf_counter()-start},indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'rows':len(rows)},indent=2))
if __name__=='__main__':main()
