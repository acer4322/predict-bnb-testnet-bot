from __future__ import annotations
import json,lzma,math,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_management_provenance_bridge_v1 as br
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_provenance_bridge_smoke_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_provenance_bridge_smoke_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
EPS=1e-9

def strip_mgmt(rows):
    out=[]
    for r in rows:
        q=dict(r)
        for k in ('submittedResponsibilityId','submittedIntentId','parentIntentId'):q.pop(k,None)
        out.append(q)
    return out

def core(x):
    return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}

def audit_prov(r):
    evs=r.get('provenanceJournal') or []; st=r.get('provenanceResponsibilityState') or {}; by=defaultdict(list)
    for e in evs:by[str(e.get('responsibility_id'))].append(e)
    fail=defaultdict(int); execids=[]; multi=0; cancel_checks=0; cancel_fail=0
    for e in evs:
        if str(e.get('responsibility_id')) not in st:fail['orphanEvents']+=1
        if e.get('execution_id'):execids.append(str(e['execution_id']))
    for rid,xs in by.items():
        seq=[int(e.get('event_seq') or -1) for e in xs]
        if seq!=list(range(1,len(xs)+1)):fail['sequenceFailures']+=1
        rt=[int(e.get('received_at_ms') or 0) for e in xs]
        if any(b<a for a,b in zip(rt,rt[1:])):fail['receivedTimeFailures']+=1
        opens=[e for e in xs if e.get('event_type')=='RESPONSIBILITY_OPENED']
        if len(opens)!=1 or xs[0].get('event_type')!='RESPONSIBILITY_OPENED':fail['rootOpenFailures']+=1
        intents=[e for e in xs if e.get('event_type')=='CARRIER_INTENT_CREATED']
        if len(intents)>1:multi+=1
        known=[]; pok=True
        for i,e in enumerate(intents):
            iid=e.get('intent_id');par=e.get('parent_intent_id')
            if i==0 and par not in (None,''):pok=False
            if i>0 and par not in known:pok=False
            known.append(iid)
        if not pok:fail['parentChainFailures']+=1
        compidx=next((i for i,e in enumerate(xs) if e.get('event_type')=='RESPONSIBILITY_COMPLETED'),None)
        if compidx is not None:fail['postCompletionCarrierCount']+=sum(1 for e in xs[compidx+1:] if e.get('event_type')=='CARRIER_INTENT_CREATED')
        req=float(opens[0].get('requested_qty') or 0.) if opens else float(st.get(rid,{}).get('requested_qty') or 0.)
        fill_sum=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.) for e in xs if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'})
        expected=min(req,fill_sum); ss=st.get(rid,{})
        if abs(float(ss.get('confirmed_qty') or 0.)-expected)>1e-7:fail['stateReconstructionFailures']+=1
        terminal=[e for e in xs if e.get('event_type') in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}]
        if len(terminal)!=1:fail['terminalEventFailures']+=1
        for ce in [e for e in xs if e.get('event_type')=='CANCEL_REQUESTED']:
            cancel_checks+=1;iid=ce.get('intent_id');cseq=int(ce.get('event_seq') or 0); fut=[e for e in xs if int(e.get('event_seq') or 0)>cseq]
            resolved=any(e.get('event_type')=='ACK_CANCELED' and e.get('intent_id')==iid for e in fut) or any(e.get('event_type')=='RESPONSIBILITY_COMPLETED' for e in fut) or any(e.get('event_type')=='RESPONSIBILITY_TERMINATED' for e in fut)
            if not resolved:cancel_fail+=1
    fail['duplicateExecutionIds']=len(execids)-len(set(execids));fail['cancelResolutionFailures']=cancel_fail
    # every management row submission id must be present in provenance carrier events
    carrier_pairs={(e.get('responsibility_id'),e.get('intent_id')) for e in evs if e.get('event_type')=='CARRIER_INTENT_CREATED'}
    mr=r.get('managementShadowRows') or [];mid_ok=0
    for x in mr:
        pair=(x.get('submittedResponsibilityId'),x.get('submittedIntentId'))
        if pair in carrier_pairs:mid_ok+=1
    return {'responsibilities':len(by),'events':len(evs),'multiIntentRoots':multi,'cancelChecks':cancel_checks,'managementRows':len(mr),'managementIdMatches':mid_ok,'managementIdCoverage':mid_ok/max(1,len(mr)),**dict(fail)}

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8')); ids=[int(x) for x in pre['markets']]; rows=[]; totals=defaultdict(int); exact_core=exact_shadow=exact_mgmt=0
    for mid in ids:
        p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz';d=json.load(lzma.open(p,'rt',encoding='utf-8'))
        a=base.simulate(d,pre['policy'],collect_shadow=True)
        b=br.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True)
        ce=(core(a)==core(b));se=(a.get('shadowRows')==b.get('shadowRows'));me=(a.get('managementShadowRows')==strip_mgmt(b.get('managementShadowRows') or []))
        exact_core+=int(ce);exact_shadow+=int(se);exact_mgmt+=int(me)
        pa=audit_prov(b)
        for k,v in pa.items():
            if isinstance(v,int):totals[k]+=v
        rows.append({'marketId':mid,'executionCoreExact':ce,'shadowRowsExact':se,'managementRowsExactAfterIdStrip':me,'provenance':pa})
        print(json.dumps({'marketId':mid,'core':ce,'shadow':se,'mgmt':me,'prov':pa},ensure_ascii=False),flush=True)
    mgmt_rows=sum(r['provenance']['managementRows'] for r in rows);mgmt_match=sum(r['provenance']['managementIdMatches'] for r in rows);coverage=mgmt_match/max(1,mgmt_rows)
    failures=sum(totals.get(k,0) for k in ['sequenceFailures','receivedTimeFailures','rootOpenFailures','parentChainFailures','stateReconstructionFailures','duplicateExecutionIds','orphanEvents','postCompletionCarrierCount','cancelResolutionFailures','terminalEventFailures'])
    passed=(len(rows)==5 and exact_core==5 and exact_shadow==5 and exact_mgmt==5 and abs(coverage-1.)<=EPS and failures==0 and totals.get('multiIntentRoots',0)>0)
    rep={'version':'R4_MANAGEMENT_PROVENANCE_BRIDGE_SMOKE_V1','status':'BRIDGE_SMOKE_PASS' if passed else 'BRIDGE_SMOKE_FAIL','preregistered':str(PRE.relative_to(ROOT)).replace('\\','/'),
         'summary':{'markets':len(rows),'executionCoreExact':f'{exact_core}/{len(rows)}','shadowRowsExact':f'{exact_shadow}/{len(rows)}','managementRowsExactAfterIdStrip':f'{exact_mgmt}/{len(rows)}','managementIdCoverage':coverage,'managementRows':mgmt_rows,'managementIdMatches':mgmt_match,'multiIntentRoots':totals.get('multiIntentRoots',0),'cancelChecks':totals.get('cancelChecks',0),'totalInvariantFailures':failures,**{k:totals.get(k,0) for k in ['sequenceFailures','receivedTimeFailures','rootOpenFailures','parentChainFailures','stateReconstructionFailures','duplicateExecutionIds','orphanEvents','postCompletionCarrierCount','cancelResolutionFailures','terminalEventFailures']}},
         'rows':rows,'interpretation':'Pass means durable logical->responsibility and carrier->intent lineage can be attached to the existing Management HFT simulator without changing execution or shadow semantics. Research-only; no action authority.',
         'guards':pre['guards']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':rep['status'],'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':rep['summary']},ensure_ascii=False))
if __name__=='__main__':main()
