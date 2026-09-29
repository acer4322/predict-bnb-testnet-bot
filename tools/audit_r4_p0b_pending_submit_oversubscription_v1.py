from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_state_machine_late20e_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
def audit(events):
 by=defaultdict(list)
 for e in events:by[str(e.get('responsibility_id'))].append(e)
 roots=[]
 for rid,evs in by.items():
  evs=sorted(list(enumerate(evs)),key=lambda z:(int(z[1].get('received_at_ms') or 0),z[0])); opened=next((e for _,e in evs if e.get('event_type')=='RESPONSIBILITY_OPENED'),None)
  if not opened:continue
  req=float(opened.get('requested_qty') or 0.);fill=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.) for _,e in evs if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'});active={};seen_ack=set();pending=set();preack_multi=False;sibling_at_completion=0;late_fill=0;completed_at=None
  for _,e in evs:
   et=str(e.get('event_type') or '');iid=str(e.get('intent_id')) if e.get('intent_id') else None
   if et=='CARRIER_INTENT_CREATED' and iid:
    if pending:preack_multi=True
    pending.add(iid)
   elif et=='ACK_NEW' and iid:
    pending.discard(iid);seen_ack.add(iid);active[iid]=True
   elif et in {'FULL_FILL','ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:
    pending.discard(iid);active[iid]=False
    if completed_at is not None and et in {'FULL_FILL','PARTIAL_FILL'}:late_fill+=float((e.get('extras') or {}).get('fillDeltaQty') or 0.)
   if et=='RESPONSIBILITY_COMPLETED':
    completed_at=int(e.get('received_at_ms') or 0);sibling_at_completion=sum(1 for v in active.values() if v)
  over=max(0.,fill-req)
  roots.append({'responsibilityId':rid,'requestedQty':req,'fillSum':fill,'overfillQty':over,'intentCount':sum(1 for _,e in evs if e.get('event_type')=='CARRIER_INTENT_CREATED'),'preAckMultiCarrier':preack_multi,'activeSiblingAtCompletion':sibling_at_completion,'lateFillAfterCompletion':late_fill})
 return roots
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();pre=json.loads(PRE.read_text(encoding='utf-8'));ids=pre['cohort'][a.start:a.start+a.count];rows=[]
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));r=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True);rr=audit(r['provenanceJournal']);bad=[x for x in rr if x['overfillQty']>1e-9 or x['activeSiblingAtCompletion']>0 or x['preAckMultiCarrier']];rows.extend([{'marketId':mid,**x} for x in rr]);print(json.dumps({'marketId':mid,'roots':len(rr),'overfillRoots':sum(x['overfillQty']>1e-9 for x in rr),'preAckMultiRoots':sum(x['preAckMultiCarrier'] for x in rr),'siblingAtCompletionRoots':sum(x['activeSiblingAtCompletion']>0 for x in rr)},ensure_ascii=False),flush=True)
 out=ROOT/f'data/research/r4_v0/p0_provenance_v1/r4_p0b_pending_submit_oversubscription_chunk_{a.start}_{a.count}.json';out.write_text(json.dumps({'ids':ids,'roots':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids),'roots':len(rows)}))
if __name__=='__main__':main()
