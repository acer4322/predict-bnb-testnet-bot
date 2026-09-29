from __future__ import annotations
import json,lzma,sys,psutil,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1';FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json';SRC=ROOT/'data/hft_forward_paper_v1/markets'
EPS=1e-9

def core(x): return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def taker_events(d):
 out=[]
 for x in d.get('takerEvents') or []:
  q=float(x.get('shares') or x.get('filledShares') or x.get('deltaShares') or x.get('qty') or 0);px=float(x.get('price') or x.get('fillPrice') or x.get('avgPrice') or 0);side=str(x.get('side') or '').upper();t=int(x.get('observedAtMs') or x.get('atMs') or x.get('fillMs') or 0)
  if q>0 and side in {'UP','DOWN'} and t: out.append((t,1,'TAKER',side,q,px,None))
 return out

def maker_events(r):
 out=[];seen=set();orph=0
 for e in r.get('provenanceJournal') or []:
  if e.get('event_type') not in {'PARTIAL_FILL','FULL_FILL'}: continue
  xid=e.get('execution_id')
  if not xid: orph+=1;continue
  if xid in seen: continue
  seen.add(xid);q=float((e.get('extras') or {}).get('fillDeltaQty') or 0);px=float(e.get('price') or 0);side=str(e.get('side') or '').upper();t=int(e.get('received_at_ms') or 0)
  if q>0 and side in {'UP','DOWN'} and t: out.append((t,0,'MAKER',side,q,px,xid))
 return out,len(seen),orph

def main():
 fc=json.loads(FIX.read_text(encoding='utf-8'));ids=[int(x) for x in fc['managementHftReplication']['markets']]
 errors=[];checkpoint_count=0;maxerr={'floor':0.0,'absNet':0.0,'coverage':0.0};exact=0;dup_exec=0;orphan=0;market_rows=[];start=time.perf_counter();peakcpu=0.;peakram=0.
 for mid in ids:
  rs=psutil.virtual_memory();cpu=psutil.cpu_percent(.05);peakcpu=max(peakcpu,cpu);peakram=max(peakram,rs.percent)
  if cpu>=75 or rs.percent>=86: raise SystemExit(f'RESOURCE_GUARD cpu={cpu} ram={rs.percent}')
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  b0=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(b0)==core(r);exact+=int(ce)
  me,nexec,orph=maker_events(r);orphan+=orph; te=taker_events(d);ev=sorted(me+te,key=lambda z:(z[0],z[1]))
  # simulator harvests maker fills before same-time action list, then takers before LIFE checkpoint; ordering above matches this.
  s=(0.,0.,0.,0.,0.);j=0;rows=r.get('managementLifecycleRows') or [];localmax={'floor':0.,'absNet':0.,'coverage':0.}
  for row in sorted(rows,key=lambda x:int(x['t'])):
   t=int(row['t'])
   while j<len(ev) and ev[j][0]<=t:
    _,_,role,side,q,px,_=ev[j];s=v2.apply(s,side,px,q,role=='TAKER');j+=1
   g=v2.geom(s)
   for k in ('floor','absNet','coverage'):
    er=abs(float(g[k])-float(row[k]));maxerr[k]=max(maxerr[k],er);localmax[k]=max(localmax[k],er)
   checkpoint_count+=1
  market_rows.append({'marketId':mid,'executionCoreExact':ce,'checkpoints':len(rows),'makerExecIds':nexec,'maxAbsError':localmax})
  print(json.dumps(market_rows[-1]),flush=True)
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_DETERMINISTIC_LEDGER_V1','researchOnly':True,'markets':len(ids),'executionCoreExact':f'{exact}/{len(ids)}','checkpoints':checkpoint_count,'maxAbsError':maxerr,'duplicateExecApplication':dup_exec,'orphanFill':orphan,'gatePass':bool(exact==len(ids) and checkpoint_count>=1000 and all(v<=1e-9 for v in maxerr.values()) and dup_exec==0 and orphan==0),'marketRows':market_rows,'resource':{'peakCpu':peakcpu,'peakRam':peakram},'elapsedSec':time.perf_counter()-start,'interpretation':'Portfolio economics reconstructed only from maker fill provenance + source taker execution primitives, then deterministic ledger formulas.'}
 (P/'r4_management_simulator_v0_deterministic_ledger_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
