from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as base
from tools import test_r4_p0b_legitimate_successor_simulator_v1 as var
from tools.audit_r4_p0b_pending_submit_oversubscription_v1 import audit
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_legitimate_successor_late60h_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
def summarize_audit(rr):
 return {'roots':len(rr),'preAckMultiCarrierRoots':sum(x['preAckMultiCarrier'] for x in rr),'activeSiblingAtCompletionRoots':sum(x['activeSiblingAtCompletion']>0 for x in rr),'overfillRoots':sum(x['overfillQty']>1e-9 for x in rr),'totalOverfillQty':sum(x['overfillQty'] for x in rr),'lateFillAfterCompletionQty':sum(x['lateFillAfterCompletion'] for x in rr)}
def successor_roots(events):
 r=set()
 for e in events:
  if (e.get('extras') or {}).get('kind')=='SUCCESSOR_OPTION':r.add(str(e.get('responsibility_id')))
 return len(r)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();pre=json.loads(PRE.read_text(encoding='utf-8'));ids=pre['cohort'][a.start:a.start+a.count];rows=[]
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  b=base.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True);v=var.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True)
  ab=summarize_audit(audit(b['provenanceJournal']));av=summarize_audit(audit(v['provenanceJournal']))
  row={'marketId':mid,'baselineAudit':ab,'variantAudit':av,'baseline':{'makerFilledShares':b['makerFilledShares'],'durableBase':b['durableBase'],'finalFloor':b['final']['floor'],'finalAbsNet':b['final']['absNet']},'variant':{'makerFilledShares':v['makerFilledShares'],'durableBase':v['durableBase'],'finalFloor':v['final']['floor'],'finalAbsNet':v['final']['absNet'],'successorSubmits':v['counts'].get('legitimateSuccessorSubmits',0),'successorRoots':successor_roots(v['provenanceJournal'])}}
  rows.append(row);print(json.dumps({'marketId':mid,'successors':row['variant']['successorSubmits'],'baseOverfill':ab['totalOverfillQty'],'varOverfill':av['totalOverfillQty'],'floorDeltaVsReservation':row['variant']['finalFloor']-row['baseline']['finalFloor'],'absNetDeltaVsReservation':row['variant']['finalAbsNet']-row['baseline']['finalAbsNet']},ensure_ascii=False),flush=True)
 out=ROOT/f'data/research/r4_v0/p0_provenance_v1/r4_p0b_legitimate_successor_late60h_chunk_{a.start}_{a.count}.json';out.write_text(json.dumps({'ids':ids,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids)}))
if __name__=='__main__':main()
