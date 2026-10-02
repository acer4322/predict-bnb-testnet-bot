from __future__ import annotations
import json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
SRC=ROOT/'data/hft_forward_paper_v1/markets'
IDS=[1712834,1712121,1712118,1712117,1712104,1712067,1712061,1714959,1714949,1714879,1714765,1714507,1714373,1714361,1714351]
out=[]
for mid in IDS:
 d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
 r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
 first={}
 for x in r.get('managementLifecycleRows') or []:
  rid=str(x['checkpointResponsibilityId']); first.setdefault(rid,x)
 j=r.get('provenanceJournal') or []
 for rid,x in first.items():
  t0=int(x['t']); ev=[e for e in j if str(e.get('responsibility_id'))==rid and t0<int(e.get('received_at_ms') or 0)<=t0+5000]
  fills=[e for e in ev if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}]
  qty=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0) for e in fills)
  if qty<=1e-9: continue
  rec={'marketId':mid,'responsibilityId':rid,'t':t0,'requestedQty':float(x.get('checkpointRequestedQty') or 0),'fillQty5s':qty,'fills':[],'carrierEvents':[]}
  for e in fills:
   z=e.get('extras') or {}; rec['fills'].append({'event':e.get('event_type'),'t':e.get('received_at_ms'),'intent':e.get('intent_id'),'execution':e.get('execution_id'),'qty':z.get('fillDeltaQty'),'px':z.get('fillPrice'),'carrierRequestedQty':z.get('requestedQty'),'leavesQty':z.get('leavesQty')})
  for e in ev:
   if e.get('event_type') in {'CARRIER_INTENT_CREATED','SUBMIT_SENT','ACK_NEW','CANCEL_REQUESTED','ACK_CANCELED','REPLACE_REQUESTED','ACK_REPLACED','RESPONSIBILITY_COMPLETED'}:
    z=e.get('extras') or {}; rec['carrierEvents'].append({'event':e.get('event_type'),'t':e.get('received_at_ms'),'intent':e.get('intent_id'),'parent':e.get('parent_intent_id'),'requestedQty':z.get('requestedQty'),'confirmedFillQty':z.get('confirmedFillQty'),'leavesQty':z.get('leavesQty')})
  out.append(rec)
p=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'fill_size_lineage_audit.json'; p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps({'version':'R4_EXECUTION_FILL_SIZE_LINEAGE_AUDIT_V1','rows':out},indent=2),encoding='utf-8');print(json.dumps({'rows':len(out),'overRequested':sum(x['fillQty5s']>x['requestedQty']+1e-9 for x in out),'artifact':str(p)}))
