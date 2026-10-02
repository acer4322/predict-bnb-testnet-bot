from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,EXPECTED_OVERFLOW,near
class Gen3ActiveAudit(R239ConfirmedPaymentBindingFork):
 def __init__(self,tape,mid):
  super().__init__(tape,mid,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP');self.cap=None
 def _register_overflow(self,t,ev):
  before=int(getattr(self,'_nextObligationId',0));super()._register_overflow(t,ev)
  if self.cap is None and before==2 and int(getattr(self,'_nextObligationId',0))==3:
   ob=next((dict(x) for x in self.obligations if int(x.get('id') or -1)==2),None)
   if ob:self.cap={'t':int(t),'obligation':ob,'drainStats':dict(self.drainStats),'activeStats':dict(self.activeStats),'activeFillQty':float(self.activeFillQty),'slotHistoryN':len(self.slot_history),'drainEventsN':len(self.drainEvents),'pendingFailureN':len(self.pendingFailure),'usedEpochs':[list(x) for x in self.usedEpochs]}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_gen3_active_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  s=Gen3ActiveAudit(tmp/f'{MID}.json.xz',MID)
  try:
   r=s.run_payment(co[MID]['winner']);cap=s.cap or {};dd=Counter(s.drainStats)-Counter(cap.get('drainStats',{}));ad=Counter(s.activeStats)-Counter(cap.get('activeStats',{}));sh=list(s.slot_history)[int(cap.get('slotHistoryN',0)):];de=list(s.drainEvents)[int(cap.get('drainEventsN',0)):]
   active_life=[dict(e) for e in sh if 'ACTIVE' in str(e.get('event','')).upper()];gen3_fail=[dict(e) for e in de if int(e.get('generation') or -1)==3]
   out={'version':'LANE_G_POST_BINDING_GEN3_INHERITED_ACTIVE_REACHABILITY_AUDIT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'marketId':MID,'owner2Birth':cap,'owner2Final':r.get('owner2Final'),'drainCounterDelta':{k:int(v) for k,v in dd.items() if v},'activeCounterDelta':{k:int(v) for k,v in ad.items() if v},'activeFillQtyDelta':float(s.activeFillQty)-float(cap.get('activeFillQty',0.0)),'gen3FailureEvidenceEvents':gen3_fail,'activeEventsLifetime':active_life,'pendingFailureFinal':len(s.pendingFailure),'usedEpochsFinal':[list(x) for x in s.usedEpochs]}
   reachable=bool(out['activeCounterDelta'].get('SUBMIT',0) or out['activeCounterDelta'].get('FILL_EVENT',0) or active_life)
   evidence=bool(gen3_fail)
   if reachable:cls='GEN3_INHERITED_ACTIVE_REACHABLE'
   elif not evidence:cls='GEN3_ACTIVE_UNREACHABLE_NO_FAILURE_EVIDENCE_SOURCE'
   else:cls='GEN3_ACTIVE_EVIDENCE_PRESENT_BUT_ADMISSION_BLOCKED'
   out['classification']=cls;out['correct']=bool(cap and near(float((cap.get('obligation') or {}).get('originOverflowQty') or 0.0),EXPECTED_OVERFLOW,1e-12) and r.get('paymentEvent') and not r.get('paymentErrors'))
   out['boundary']=['read-only owner2 lifetime audit','existing inherited Active logic only','no new trigger/authority/credit','consumed 1946468','realistic HFT','fresh untouched','no dream fill','no 8781','no fixed-time/rank-age Manager gate']
   Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'classification':cls,'drainDelta':out['drainCounterDelta'],'activeDelta':out['activeCounterDelta'],'activeFillQtyDelta':out['activeFillQtyDelta'],'gen3FailureEvidenceN':len(gen3_fail),'activeEventsN':len(active_life),'correct':out['correct']},ensure_ascii=False),flush=True)
  finally:s.close()
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
