from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,EXPECTED_OVERFLOW,near,EPS

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_postbind_gen3_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  s=R239ConfirmedPaymentBindingFork(tmp/f'{MID}.json.xz',MID,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP')
  try:
   r=s.run_payment(co[MID]['winner']);stats=dict(s.r239);events=list(s.r239events);obs=[dict(x) for x in s.obligations]
  finally:s.close()
  o2=next((x for x in obs if int(x.get('id') or -1)==2),None)
  born=next((e for e in events if e.get('event')=='R239_OVERFLOW_OBLIGATION_BORN' and int(e.get('obligationId') or -1)==2),None)
  life=[e for e in events if int(e.get('obligationId') or -1)==2]
  service=[e for e in life if e.get('event') in {'R239_OVERFLOW_HANDOFF_SUBMIT','R239_HANDOFF_REPAIR_FILL','R239_NATIVE_CORE_REPAIR_PAYMENT_BIND'}]
  counters={k:int(v) for k,v in stats.items() if k.startswith('HANDOFF_') or k.startswith('OBLIGATION_CLOSED_')}
  correct=bool(r['paymentEvent'] and o2 and near(float(o2.get('originOverflowQty') or 0.0),EXPECTED_OVERFLOW,1e-12) and not r['paymentErrors'] and float(r.get('combinedAuthorityExcessMax') or 0.0)<=EPS)
  if not o2:cls='GEN3_OWNER_MISSING'
  elif float(o2.get('outstanding') or 0.0)<=EPS:cls='GEN3_REPAID'
  elif service:cls='GEN3_SERVICE_EXERCISED'
  else:cls='GEN3_OWNED_OUTSTANDING_NO_SERVICE_EXERCISE'
  out={'version':'LANE_G_POST_BINDING_GEN3_SUCCESSOR_REACHABILITY_AUDIT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'marketId':MID,
       'owner2':o2,'birth':born,'owner2Events':life,'serviceEvents':service,'r239RelevantCounters':counters,'classification':cls,'correct':correct,
       'interpretation':'Read-only downstream audit after the confirmed-payment binding fork; counts are whole replay and are not attributed to owner2 unless represented in owner2Events.',
       'boundary':['same consumed 1946468 realistic-HFT replay','candidate payment binding only','no additional behavior','fresh untouched','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'classification':cls,'owner2':o2,'serviceEvents':service,'counters':counters,'correct':correct},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
