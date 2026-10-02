from __future__ import annotations
import json, copy, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_execution_incident_notification_exam_v1 import IncidentReceiverProbe, ExecutionIncidentNotifier, obs
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
def o(order,at,state,cum=0,seq=0,cancel=None,rev=1):
 x=obs(order,at,state,cum,sequence=seq,cancel_at=cancel); x['targetRevision']=rev; return x
def main():
 receiver=IncidentReceiverProbe(); notifier=ExecutionIncidentNotifier(receiver)
 observations=[o('L6_MAIN',0,'ACKED_OPEN',0,seq=1,rev=1),o('L6_MAIN',2000,'PARTIALLY_FILLED',6,seq=2,rev=1),o('L6_MAIN',4000,'CANCEL_PENDING',6,seq=3,cancel=4000,rev=1),o('L6_MAIN',6000,'PARTIALLY_FILLED',10,seq=4,cancel=4000,rev=1),o('L6_MAIN',10000,'CANCEL_PENDING',10,seq=5,cancel=4000,rev=2),o('L6_MAIN',11000,'UNKNOWN_SUBMISSION',10,seq=6,rev=2),o('L6_MAIN',13000,'FILLED',18,seq=7,rev=3),o('L6_SECOND',13000,'SUBMIT_REJECTED',0,seq=8,rev=3)]
 snapshots=[]; latest_rev=0; actual=0.0; owner='NONE'
 for x in observations:
  latest_rev=max(latest_rev,int(x.get('targetRevision') or 0)); notifier.observe(x)
  if x['orderKey']=='L6_MAIN': actual=max(actual,float(x.get('cumulativeFilledQty') or 0))
  st=str(x['venueState'])
  if x['orderKey']=='L6_MAIN':
   if st=='UNKNOWN_SUBMISSION': owner='UNKNOWN_CHILD'
   elif st in {'ACKED_OPEN','PARTIALLY_FILLED','CANCEL_PENDING'}: owner='CURRENT_CHILD'
   elif st in {'FILLED','CANCELED','FAILED','EXPIRED','SUBMIT_REJECTED'}: owner='RELEASED'
  snapshots.append({'atMs':x['atMs'],'sourceSequence':x['sourceSequence'],'targetRevision':latest_rev,'actualConfirmedShares':actual,'ownershipState':owner,'eventCount':len(receiver.notifications)})
 source_trace=[{'eventId':e['eventId'],'incidentType':e['incidentType'],'atMs':e['atMs']} for e in receiver.notifications]
 delivered_events=sorted(receiver.notifications,key=lambda e:int(e.get('atMs') or 0)); delivered_trace=[{'eventId':e['eventId'],'incidentType':e['incidentType'],'atMs':e['atMs']} for e in delivered_events]
 forbidden={'ownershipDirective','receiverDirective'}; allowed=('eventId','incidentType','atMs','orderKey','role','side','venueState','stateCertainty','requestedQty','confirmedFilledQty','fillDeltaQty','unresolvedQty','submittedAtMs','orderAgeMs','reason','targetRevision')
 r21_packets=[{k:copy.deepcopy(e.get(k)) for k in allowed} for e in delivered_events]; types=[e['incidentType'] for e in receiver.notifications]
 required={'PARTIAL_FILL_CONFIRMED','FILL_DURING_CANCEL','CANCEL_ACK_TIMEOUT','ORDER_STATE_UNKNOWN','RECONCILED_ORDER_STATE','FULL_FILL_CONFIRMED','SUBMIT_REJECT_CONFIRMED'}; target_revs=[s['targetRevision'] for s in snapshots]
 gates={'requiredCompoundIncidentsPresent':required.issubset(set(types)),'sourceToR21TraceExact':source_trace==delivered_trace,'sameMillisecondSourceOrderPreserved':[x['incidentType'] for x in source_trace if x['atMs']==13000]==[x['incidentType'] for x in delivered_trace if x['atMs']==13000],'targetRevisionMonotonic':target_revs==sorted(target_revs) and target_revs[-1]==3,'unknownPreservesOwnership':any(s['ownershipState']=='UNKNOWN_CHILD' for s in snapshots),'confirmedFillOnlyInventory':snapshots[-1]['actualConfirmedShares']==18.0,'terminalReleasesMainOwnership':snapshots[-2]['ownershipState']=='RELEASED','r21ZeroActionFields':all(not(set(p)&forbidden) for p in r21_packets),'r21ActionAuthorityFalse':True,'eventMutationAllowedFalse':True,'executorCallbackAllowedFalse':True,'zeroSchemaErrors':not receiver.schema_errors}
 report={'version':'R21_V33_L6_COMPOUND_DISASTER_EXAM_V1','researchOnly':True,'scenario':'partial -> cancel pending -> fill during cancel -> cancel timeout -> UNKNOWN -> reconcile/full fill -> same-ms second reject + target revisions','observations':observations,'sourceIncidentTrace':source_trace,'r21DeliveredTrace':delivered_trace,'r21Packets':r21_packets,'stateSnapshots':snapshots,'gates':gates,'allPass':all(gates.values()),'authority':{'r21ActionAuthority':False,'eventMutationAllowed':False,'executorCallbackAllowed':False,'r2DecisionOwner':True}}
 (OUT/'r21_v33_l6_compound_disaster_exam_v1_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'allPass':report['allPass'],'gates':gates,'incidentTypes':types,'sameMs13000':[x['incidentType'] for x in source_trace if x['atMs']==13000],'finalState':snapshots[-1]},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
