from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_satellite_overflow_r239_ownership_handoff_exact_v1 import SatelliteOverflowR239OwnershipHandoff
from tools.run_lane_g_shared_parent_parallel_residual_exact_fork_v1 import EPS
MID=1946468; SOURCE='DOWN_13'; GEN=2

class BlockerLifetimeAudit(SatelliteOverflowR239OwnershipHandoff):
 def __init__(self,tape,mid):
  super().__init__(tape,mid);self.ownerOid=None;self.statsAtBirth=None;self.statsAtClose=None;self.birthT=None;self.closeT=None
 def process(self,t):
  super().process(t)
  if self.ownerOid is None:
   for e in getattr(self,'r239events',[]) or []:
    if e.get('event')=='R239_OVERFLOW_OBLIGATION_BORN' and str(e.get('sourceKey'))==SOURCE and int(e.get('generation') or -1)==GEN:
     self.ownerOid=int(e['obligationId']);self.birthT=int(e['t']);self.statsAtBirth=dict(getattr(self,'r239',{}) or {});break
  if self.ownerOid is not None and self.statsAtClose is None:
   for e in reversed(getattr(self,'r239events',[]) or []):
    if e.get('event')=='R239_OVERFLOW_OBLIGATION_CLOSED' and int(e.get('obligationId') or -1)==self.ownerOid:
     self.closeT=int(e['t']);self.statsAtClose=dict(getattr(self,'r239',{}) or {});break
 def run_blocker(self,winner):
  r=self.run_audit(winner);a=Counter(self.statsAtBirth or {});b=Counter(self.statsAtClose or dict(getattr(self,'r239',{}) or {}));delta={k:int(b[k]-a[k]) for k in sorted(set(a)|set(b)) if int(b[k]-a[k])!=0}
  ev=[e for e in getattr(self,'r239events',[]) or [] if self.ownerOid is not None and int(e.get('obligationId') or -1)==self.ownerOid]
  attempts={k:v for k,v in delta.items() if k.startswith('HANDOFF_')}
  if attempts.get('HANDOFF_SUBMIT',0)>0:cls='SERVICE_SUBMIT_REACHED'
  elif attempts.get('HANDOFF_FANOUT_CAP_OCCUPIED',0)>0:cls='BLOCKED_FANOUT_CAP'
  elif attempts.get('HANDOFF_SLOT_CAP_FULL',0)>0:cls='BLOCKED_SLOT_CAP'
  elif attempts.get('HANDOFF_LIABILITY_BELOW_VENUE_MIN',0)>0 and attempts.get('HANDOFF_NO_PURE_REPAIR_CANDIDATE',0)>0:cls='BLOCKED_VENUE_MIN_NO_PURE_REPAIR'
  elif attempts.get('HANDOFF_NO_PURE_REPAIR_CANDIDATE',0)>0:cls='BLOCKED_NO_PURE_REPAIR_CANDIDATE'
  else:cls='SERVICE_BLOCKER_UNRESOLVED'
  correct=bool(self.ownerOid is not None and self.birthT==1788534920674 and self.closeT==1788534947089 and bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS)
  return {'marketId':MID,'obligationId':self.ownerOid,'birthT':self.birthT,'closeT':self.closeT,'ownerLifecycleEvents':ev,'counterAtBirth':self.statsAtBirth,'counterAtClose':self.statsAtClose,'lifetimeCounterDelta':delta,'lifetimeHandoffBlockers':attempts,'classification':cls,'correct':correct}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_owner_blocker_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  sim=BlockerLifetimeAudit(tmp/f'{MID}.json.xz',MID)
  try:r=sim.run_blocker(co[MID]['winner'])
  finally:sim.close()
  out={'version':'LANE_G_OWNED_SUCCESSOR_SERVICE_BLOCKER_LIFETIME_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'row':r,'gates':{'ownerLifetimeExact':r['correct'],'blockerLocalized':r['classification']!='SERVICE_BLOCKER_UNRESOLVED','correctnessPass':r['correct']},'boundary':['telemetry only','owner-lifetime counter delta','no behavior mutation','no fixed-time gate','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'classification':r['classification'],'blockers':r['lifetimeHandoffBlockers']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
