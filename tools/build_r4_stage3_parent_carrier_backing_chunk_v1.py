from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
from collections import defaultdict
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';DATA=P/'r4_p0b_stage3_expanded48_dataset_v2.csv';EPS=1e-9
ROLES={'PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}
TERM={'ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL','FULL_FILL'}
ACK={'ACK_NEW','PARTIAL_FILL'}
PEND={'CARRIER_INTENT_CREATED','SUBMIT_SENT'}

def classify_intent(es):
 es=sorted(es,key=lambda e:(int(e.get('received_at_ms') or 0),str(e.get('event_type') or '')));last=es[-1];et=str(last.get('event_type') or '')
 if et in TERM:return 'TERMINAL',last
 if et=='CANCEL_REQUESTED':return 'CANCEL_PENDING',last
 if et in ACK:return 'ACK_BACKED',last
 if et in PEND:return 'PENDING_BACKED',last
 # walk backwards for latest known state if a non-state bookkeeping event is last
 for e in reversed(es):
  et=str(e.get('event_type') or '')
  if et in TERM:return 'TERMINAL',e
  if et=='CANCEL_REQUESTED':return 'CANCEL_PENDING',e
  if et in ACK:return 'ACK_BACKED',e
  if et in PEND:return 'PENDING_BACKED',e
 return 'UNKNOWN',last

def qty(e):
 v=e.get('leaves_qty')
 if v is None:v=e.get('requested_qty')
 try:return max(0.,float(v or 0.))
 except:return 0.

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args()
 d=pd.read_csv(DATA);d=d[d.knownRole.isin(ROLES)].reset_index(drop=True);sub=d.iloc[a.start:a.start+a.count];out=[]
 for _,r in sub.iterrows():
  mid=int(r.marketId);key=str(r.candidateKey);t=int(r.candidateT);parent=str(r.parentLogical);poid=led.oid(mid,parent)
  try:
   z=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));rr=sim.simulate(z,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key);prov=[e for e in (rr.get('provenanceJournal') or []) if int(e.get('received_at_ms') or 0)<t]
   _,st,resp_to_obj=led.materialize(mid,prov);po=st.get(poid) or {};parent_rids={str(rid) for rid,oid in resp_to_obj.items() if oid==poid};ies=defaultdict(list)
   for e in prov:
    if str(e.get('responsibility_id') or '') not in parent_rids:continue
    iid=e.get('intent_id')
    if iid:ies[str(iid)].append(e)
   states=[]
   for iid,es in ies.items():
    s,last=classify_intent(es);states.append((iid,s,last))
   sums={'ACK_BACKED':0.,'PENDING_BACKED':0.,'CANCEL_PENDING':0.}
   for _,s,e in states:
    if s in sums:sums[s]+=qty(e)
   reserved=max(0.,float(po.get('reserved_same_objective_commitment_qty') or 0.));den=reserved if reserved>EPS else None
   recent_term=sum(1 for e in prov if str(e.get('responsibility_id') or '') in parent_rids and str(e.get('event_type') or '') in TERM and int(e.get('received_at_ms') or 0)>=t-15000)
   active=sum(1 for _,s,_ in states if s in {'ACK_BACKED','PENDING_BACKED','CANCEL_PENDING'})
   x={'marketId':mid,'candidateKey':key,'knownRole':str(r.knownRole),'parentObjectiveId':poid,'parentResponsibilityCount':len(parent_rids),'parentReservedQty':reserved,'parentResidualQty':float(po.get('residual_objective_deficit_qty') or 0.),'parent_ack_backing_ratio':(sums['ACK_BACKED']/den if den else 0.0),'parent_pending_backing_ratio':(sums['PENDING_BACKED']/den if den else 0.0),'parent_cancel_pending_backing_ratio':(sums['CANCEL_PENDING']/den if den else 0.0),'parent_unbacked_reserved_ratio':(max(0.,reserved-sums['ACK_BACKED']-sums['PENDING_BACKED']-sums['CANCEL_PENDING'])/den if den else 0.0),'parent_active_intent_count':active,'parent_recent_terminal_intent_count_15s':recent_term,'parentAckQty':sums['ACK_BACKED'],'parentPendingQty':sums['PENDING_BACKED'],'parentCancelPendingQty':sums['CANCEL_PENDING'],'intentStateCounts':{s:sum(1 for _,ss,_ in states if ss==s) for s in ['ACK_BACKED','PENDING_BACKED','CANCEL_PENDING','TERMINAL','UNKNOWN']}}
   out.append(x);print(json.dumps({'marketId':mid,'role':x['knownRole'],'ack':x['parent_ack_backing_ratio'],'pending':x['parent_pending_backing_ratio'],'cancel':x['parent_cancel_pending_backing_ratio'],'active':active,'recentTerm':recent_term}),flush=True)
  except Exception as ex:
   x={'marketId':mid,'candidateKey':key,'knownRole':str(r.knownRole),'error':f'{type(ex).__name__}:{ex}'};out.append(x);print(json.dumps(x),flush=True)
 path=P/f'r4_stage3_parent_carrier_backing_chunk_{a.start}_{a.count}_v1.json';path.write_text(json.dumps({'version':'R4_STAGE3_PARENT_CARRIER_BACKING_CHUNK_V1','rows':out},indent=2),encoding='utf-8');print(json.dumps({'artifact':str(path.relative_to(ROOT)),'rows':len(out),'errors':sum('error' in x for x in out)}))
if __name__=='__main__':main()
