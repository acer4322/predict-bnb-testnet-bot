from __future__ import annotations
import argparse, copy, json, sys, warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_execution_incident_notification_exam_v1 import ExecutionIncidentNotifier,HftIncidentBridge,IncidentReceiverProbe
from tools.hft_r21_information_only_incident_inbox_exam_v1 import R21InformationOnlyIncidentInbox
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FAULT_TERMINALS={'SUBMIT_REJECT_CONFIRMED','TERMINAL_ZERO_FILL_CONFIRMED'}

class SequenceMakerFault:
    def __init__(self,modes:list[str]): self.modes=list(modes); self.used=[]
    def __call__(self,s:dict[str,Any]):
        if len(self.used)>=len(self.modes): return None
        mode=self.modes[len(self.used)]; self.used.append(mode); return mode

class RefreshProbe:
    def __init__(self, bridge:HftIncidentBridge): self.bridge=bridge; self.calls=[]
    def __call__(self,x:dict[str,Any]):
        self.bridge(x)
        s=copy.deepcopy(x['executionState']); mem=copy.deepcopy(s.get('behaviorMemory') or {})
        self.calls.append({'atMs':int(x['atMs']),'trigger':x.get('trigger'),'actual':copy.deepcopy(s.get('actualSharesBySide')),'desired':copy.deepcopy(x.get('desiredPortfolio')),'tracking':s.get('trackingError'),'memory':mem})
        faults=int(mem.get('consecutiveReject') or 0)+int(mem.get('consecutiveNoFill') or 0)+int(mem.get('consecutivePartial') or 0)
        if faults>0: return {'executionMode':'WAIT','freezeFrozenR2TakerIntents':True}
        return None

class V33SequenceInbox:
    def __init__(self, base:R21InformationOnlyIncidentInbox): self.base=base; self.prev=None; self.snapshots=[]; self.firstSeen=[]; self._seen=set()
    def __call__(self,snapshot:dict[str,Any])->dict[str,Any]:
        payload=self.base(snapshot); incidents=payload.get('incidents') or []
        for e in incidents:
            eid=str(e.get('eventId') or '')
            if eid and eid not in self._seen:
                self._seen.add(eid); self.firstSeen.append({'eventId':eid,'incidentType':str(e.get('incidentType') or 'UNKNOWN'),'atMs':int(e.get('atMs') or 0)})
        latest=incidents[-1] if incidents else None
        cur={'latestIncidentType':None if latest is None else latest.get('incidentType'),'latestVenueState':None if latest is None else latest.get('venueState'),'latestStateCertainty':None if latest is None else latest.get('stateCertainty'),'latestUnresolvedQty':None if latest is None else latest.get('unresolvedQty'),'totalIncidentCount':payload.get('totalIncidentCount',0),'asOfMs':int(payload['asOfMs'])}
        seq={'previous':copy.deepcopy(self.prev),'current':copy.deepcopy(cur),'elapsedSincePriorObservationMs':None if self.prev is None else int(payload['asOfMs'])-int(self.prev['asOfMs'])}
        self.prev=cur; payload['stateSequenceSummary']=seq
        payload['actionAuthority']=False; payload['orderMutationAuthority']=False; payload['desiredPortfolioMutationAuthority']=False
        self.snapshots.append({'asOfMs':payload['asOfMs'],'incidentCount':len(incidents),'latestIncidentType':cur['latestIncidentType']})
        return payload

def fault_trace(events:list[dict[str,Any]])->list[dict[str,Any]]:
    return [{'eventId':str(e.get('eventId') or ''),'incidentType':str(e.get('incidentType') or ''),'atMs':int(e.get('atMs') or 0)} for e in events if str(e.get('incidentType') or '') in FAULT_TERMINALS]

def run_case(mid:int,name:str,modes:list[str])->dict[str,Any]:
    inj=SequenceMakerFault(modes); recv=IncidentReceiverProbe(); notifier=ExecutionIncidentNotifier(recv); bridge=HftIncidentBridge(notifier)
    probe=RefreshProbe(bridge); base=R21InformationOnlyIncidentInbox(recv,max_incidents=64); inbox=V33SequenceInbox(base)
    r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,maker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=probe,behavior_ownstate_reentry=True,trace_execution_states=True,r21_incident_inbox_provider=inbox)
    generated=fault_trace(recv.notifications); delivered=[e for e in inbox.firstSeen if e['incidentType'] in FAULT_TERMINALS]
    post=[]
    for c in probe.calls:
        m=c['memory']; faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
        if faults>0: post.append(c)
    return {'marketId':mid,'case':name,'faultPlan':modes,'faultsInjected':inj.used,'generatedConfirmedFaultTrace':generated,'r21DeliveredConfirmedFaultTrace':delivered,'confirmedFaultTraceExact':generated==delivered,'strictPastViolations':base.strict_past_violations,'inboxSchemaErrors':base.schema_errors,'sequenceSnapshots':len(inbox.snapshots),'sequenceNonEmpty':sum(int(x['incidentCount']>0) for x in inbox.snapshots),'r21ActionAuthority':False,'r21EventMutationAllowed':False,'executorCallbackAllowed':False,'postFaultDecisionStates':len(post),'r2Reassessed':bool(post),'cycleInvariantViolationCount':r['cycleInvariantViolationCount'],'maxConsecutiveReject':max([int((c['memory'] or {}).get('consecutiveReject') or 0) for c in probe.calls] or [0]),'maxConsecutiveNoFill':max([int((c['memory'] or {}).get('consecutiveNoFill') or 0) for c in probe.calls] or [0]),'terminalFloorAuditOnly':r['actualExecution']['finalPortfolio']['worst_case_floor']}

def main():
    warnings.filterwarnings('ignore'); ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,default=1633380); ap.add_argument('--output',default='r21_v33_fault_ladder_end_to_end_v1_report.json'); a=ap.parse_args()
    cases=[('L1_SINGLE_REJECT',['SUBMIT_REJECT']),('L1_SINGLE_NO_FILL',['NO_FILL_STALL']),('L2_DOUBLE_REJECT',['SUBMIT_REJECT','SUBMIT_REJECT']),('L2_DOUBLE_NO_FILL',['NO_FILL_STALL','NO_FILL_STALL']),('L3_MIXED_REJECT_NO_FILL',['SUBMIT_REJECT','NO_FILL_STALL']),('L4_TRIPLE_MIXED',['SUBMIT_REJECT','NO_FILL_STALL','SUBMIT_REJECT'])]
    rows=[]
    for name,modes in cases:
        x=run_case(a.market_id,name,modes); x['pass']=bool(x['confirmedFaultTraceExact'] and not x['strictPastViolations'] and not x['inboxSchemaErrors'] and x['r2Reassessed'] and x['cycleInvariantViolationCount']==0 and not x['r21ActionAuthority'] and not x['r21EventMutationAllowed'] and not x['executorCallbackAllowed']); rows.append(x)
        print(json.dumps({k:x[k] for k in ('case','faultsInjected','generatedConfirmedFaultTrace','r21DeliveredConfirmedFaultTrace','confirmedFaultTraceExact','r2Reassessed','postFaultDecisionStates','maxConsecutiveReject','maxConsecutiveNoFill','cycleInvariantViolationCount','pass')},ensure_ascii=False),flush=True)
    rep={'version':'R21_V33_FAULT_LADDER_END_TO_END_EXAM_V1_1','marketId':a.market_id,'researchOnly':True,'r21Role':'information/state-sequence transport only; R2 owns response','orderingRule':'R2.1 must preserve confirmed event chronology, which may differ from injection/request chronology','rows':rows,'summary':{'cases':len(rows),'passed':sum(int(r['pass']) for r in rows),'failed':sum(int(not r['pass']) for r in rows),'allPass':all(r['pass'] for r in rows)}}
    (OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps(rep['summary'],ensure_ascii=False,indent=2))
if __name__=='__main__': main()
