from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,EXPECTED_OVERFLOW,near,EPS

class Gen3LifetimeAudit(R239ConfirmedPaymentBindingFork):
 def __init__(self,tape,mid):
  super().__init__(tape,mid,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP');self.o2BirthStats=None;self.o2BirthState=None
 def _register_overflow(self,t,ev):
  before=int(getattr(self,'_nextObligationId',0));super()._register_overflow(t,ev)
  if self.o2BirthStats is None and before==2 and int(getattr(self,'_nextObligationId',0))==3:
   ob=next((dict(x) for x in self.obligations if int(x.get('id') or -1)==2),None)
   if ob:
    self.o2BirthStats=Counter(self.r239);self.o2BirthState={'t':int(t),'obligation':ob,'scopeSide':self.scopeSide,'scopeGeneration':int(self.scopeGeneration),'slots':len(self.slot_key),'active':len(self.activeKeys)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_gen3_life_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  s=Gen3LifetimeAudit(tmp/f'{MID}.json.xz',MID)
  try:
   r=s.run_payment(co[MID]['winner']);end=Counter(s.r239);birth=Counter(s.o2BirthStats or {});delta=end-birth;obs=[dict(x) for x in s.obligations];events=list(s.r239events)
  finally:s.close()
  o2=next((x for x in obs if int(x.get('id') or -1)==2),None);life=[e for e in events if int(e.get('obligationId') or -1)==2]
  d={k:int(v) for k,v in delta.items() if int(v)!=0 and (k.startswith('HANDOFF_') or k.startswith('OBLIGATION_'))}
  if int(d.get('HANDOFF_NO_PURE_REPAIR_CANDIDATE',0))>0 and int(d.get('HANDOFF_LIABILITY_BELOW_VENUE_MIN',0))>0:cls='GEN3_BLOCKED_VENUE_MIN_NO_PURE_REPAIR'
  elif any(e.get('event')=='R239_OVERFLOW_HANDOFF_SUBMIT' for e in life):cls='GEN3_SERVICE_MATERIALIZED'
  else:cls='GEN3_OTHER_OR_UNRESOLVED_BLOCKER'
  correct=bool(s.o2BirthState and o2 and near(float(o2.get('originOverflowQty') or 0.0),EXPECTED_OVERFLOW,1e-12) and r['paymentEvent'] and not r['paymentErrors'])
  out={'version':'LANE_G_POST_BINDING_GEN3_OWNER_LIFETIME_BLOCKER_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'marketId':MID,'birthState':s.o2BirthState,'owner2Final':o2,'owner2Events':life,'ownerLifetimeR239CounterDelta':d,'classification':cls,'correct':correct,'boundary':['counter baseline taken immediately after owner2 birth','consumed 1946468 only','payment-binding candidate semantics only','no new behavior','realistic HFT','fresh untouched','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'classification':cls,'birth':s.o2BirthState,'owner2Final':o2,'delta':d,'correct':correct},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
