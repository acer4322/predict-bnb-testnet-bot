from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_satellite_overflow_r239_ownership_handoff_exact_v1 import SatelliteOverflowR239OwnershipHandoff
from tools.run_lane_g_shared_parent_parallel_residual_exact_fork_v1 import EPS

MID=1946468
OWNER={'t':1788534920674,'sourceKey':'DOWN_13','side':'DOWN','generation':2,'qty':1.5071397734278216}

class OwnedSuccessorServiceAudit(SatelliteOverflowR239OwnershipHandoff):
 def run_service(self,winner):
  r=self.run_audit(winner)
  events=list(getattr(self,'r239events',[]) or [])
  birth=next((e for e in events if e.get('event')=='R239_OVERFLOW_OBLIGATION_BORN' and str(e.get('sourceKey'))==OWNER['sourceKey'] and int(e.get('generation') or -1)==OWNER['generation']),None)
  oid=int(birth['obligationId']) if birth else None
  life=[]
  if oid is not None:
   for e in events:
    if int(e.get('t') or -1)<int(birth['t']):continue
    if int(e.get('obligationId') or -999)==oid:life.append(e)
  submits=[e for e in life if e.get('event')=='R239_OVERFLOW_HANDOFF_SUBMIT']
  fills=[e for e in life if e.get('event')=='R239_HANDOFF_REPAIR_FILL']
  closes=[e for e in life if e.get('event')=='R239_OVERFLOW_OBLIGATION_CLOSED']
  ob=next((x for x in getattr(self,'obligations',[]) if oid is not None and int(x.get('id'))==oid),None)
  stats=dict(getattr(self,'r239',{}) or {})
  if fills and ob and float(ob.get('outstanding') or 0)<=EPS:cls='OWNED_SUCCESSOR_REPAID'
  elif fills:cls='OWNED_SUCCESSOR_PASSIVELY_SERVICED'
  elif closes and closes[-1].get('reason')=='SCOPE_LEFT_OVERFLOW_SIDE':cls='OWNED_SUCCESSOR_SUPERSEDED_UNPAID'
  else:cls='OWNED_SUCCESSOR_SERVICE_UNRESOLVED'
  correct=bool(birth and int(birth['t'])==OWNER['t'] and birth.get('side')==OWNER['side'] and int(birth.get('generation'))==OWNER['generation'] and abs(float(birth.get('overflowQty') or 0)-OWNER['qty'])<=2e-7 and bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS)
  return {'marketId':MID,'ownerBirth':birth,'obligationId':oid,'ownerLifecycleEvents':life,'handoffSubmits':submits,'handoffFills':fills,'closes':closes,'obligationFinal':ob,'r239Stats':stats,'classification':cls,'underlyingCorrect':bool(r.get('r264CorrectnessPass')),'nativeUnauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),'nativeRepairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'correct':correct}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_owned_successor_service_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  sim=OwnedSuccessorServiceAudit(tmp/f'{MID}.json.xz',MID)
  try:r=sim.run_service(co[MID]['winner'])
  finally:sim.close()
  out={'version':'LANE_G_OWNED_SUCCESSOR_SERVICE_REACHABILITY_AUDIT_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'row':r,'gates':{'ownerBirthParity':bool(r['ownerBirth']) and r['correct'],'serviceLifecycleObserved':bool(r['closes'] or r['handoffSubmits'] or r['handoffFills']),'correctnessPass':r['correct']},'boundary':['telemetry only','no behavior mutation','realistic HFT','structural lifecycle classification','consumed only','fresh untouched','no dream fill','no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'classification':r['classification'],'submits':len(r['handoffSubmits']),'fills':len(r['handoffFills']),'closes':r['closes'],'stats':r['r239Stats']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
