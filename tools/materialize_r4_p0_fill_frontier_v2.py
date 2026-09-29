from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_instrumented_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_fill_frontier_v2.json'
H=(1000,3000,5000); EPS=1e-9

def fill_deltas(events:list[dict[str,Any]], rid:str):
    prior=0.0; out=[]
    for e in sorted([x for x in events if str(x.get('responsibility_id'))==rid], key=lambda x:int(x.get('event_seq') or 0)):
        if e.get('event_type') not in {'PARTIAL_FILL','FULL_FILL'}: continue
        cum=float(e.get('cum_confirmed_fill_qty') or 0.0); d=max(0.0,cum-prior); prior=max(prior,cum)
        out.append({'intentId':str(e.get('intent_id')),'receivedAtMs':int(e.get('received_at_ms') or 0),'eventAtMs':int(e.get('event_at_ms') or 0),'shares':d,'executionId':e.get('execution_id')})
    return out

def main():
    d=json.loads(SRC.read_text(encoding='utf-8')); rows=[]; miss=mono_fail=nonzero=0
    for r in d.get('rows',[]):
        stress=list(r.get('provenanceStressEvents') or [])
        if not stress:
            miss+=1; rows.append({'marketId':int(r['marketId']),'materialized':False,'reason':'no_stress_checkpoint'}); continue
        # Only the FIRST forced lifecycle intervention is guaranteed to be pre-divergence.
        se=min(enumerate(stress), key=lambda z:(int(z[1].get('cancelRequestedAtMs') or 0),z[0]))[1]
        mid=int(r['marketId']); baseline=r.get('keepProvenanceJournal') or []
        rid=str(se.get('responsibilityId')); iid=str(se.get('oldIntentId')); cp=int(se.get('cancelRequestedAtMs') or 0); side=str(se.get('side'))
        carriers=[e for e in baseline if str(e.get('responsibility_id'))==rid and e.get('event_type')=='CARRIER_INTENT_CREATED' and str(e.get('intent_id'))==iid]
        if len(carriers)!=1:
            miss+=1; rows.append({'marketId':mid,'side':side,'checkpointMs':cp,'responsibilityId':rid,'intentId':iid,'materialized':False,'reason':f'carrier_count_{len(carriers)}'}); continue
        requested=float(carriers[0].get('requested_qty') or 0.0); ds=fill_deltas(baseline,rid)
        before=sum(x['shares'] for x in ds if x['intentId']==iid and x['receivedAtMs']<=cp); remaining=max(0.0,requested-before)
        fut={h:sum(x['shares'] for x in ds if x['intentId']==iid and cp<x['receivedAtMs']<=cp+h) for h in H}
        mono=fut[1000]<=fut[3000]+EPS and fut[3000]<=fut[5000]+EPS and fut[5000]<=remaining+EPS
        mono_fail += int(not mono); nonzero += int(fut[5000]>EPS)
        raw=f'{mid}|{rid}|{iid}|{cp}'
        rows.append({'marketId':mid,'side':side,'checkpointMs':cp,'responsibilityId':rid,'intentId':iid,'immutableFrontierKey':hashlib.sha256(raw.encode()).hexdigest()[:24],
                     'materialized':True,'requestedQty':requested,'confirmedBeforeCheckpoint':before,'remainingQtyAtCheckpoint':remaining,
                     'fillShares1s':fut[1000],'fillShares3s':fut[3000],'fillShares5s':fut[5000],
                     'fillFraction1s':fut[1000]/remaining if remaining>EPS else 0.0,'fillFraction3s':fut[3000]/remaining if remaining>EPS else 0.0,'fillFraction5s':fut[5000]/remaining if remaining>EPS else 0.0,
                     'monotonicFrontier':mono,'futureExecutionIds5s':[x['executionId'] for x in ds if x['intentId']==iid and cp<x['receivedAtMs']<=cp+5000],
                     'checkpointRule':'first deterministic lifecycle-exam checkpoint per market; guaranteed pre-divergence by prefix audit','clockSemantics':'receipt-clock received_at_ms'})
    n=len(rows); ok=n-miss; coverage=ok/max(1,n)
    status='MATERIAL_PIPELINE_FIX_PASS' if n>=20 and coverage==1.0 and mono_fail==0 and nonzero>0 else 'MATERIAL_PIPELINE_FIX_FAIL'
    rep={'version':'R4_P0_FILL_FRONTIER_V2','status':status,'source':str(SRC.relative_to(ROOT)).replace('\\','/'),
         'semanticChangeFromV1':'V1 incorrectly attempted both stress checkpoints even when the second could occur after branch divergence. V2 uses exactly the first pre-divergence checkpoint per market; no selection by fill outcome.',
         'summary':{'rows':n,'materializedRows':ok,'coverage':coverage,'missingRows':miss,'monotonicFailureCount':mono_fail,'nonZeroFill5sRows':nonzero,'nonZeroFill5sRate':nonzero/max(1,ok)},
         'gate':{'minimumRows':20,'coverage':1.0,'monotonicFailureCount':0,'requireNonZeroFill5sRows':'>0'},'rows':rows,
         'interpretation':'Pass proves deterministic per-order receipt-clock future-fill labels can now be materialized from immutable journal identity without later strategy replay rediscovery. Checkpoints are lifecycle-exam checkpoints, not Target recovery labels; prediction/action authority remains closed.',
         'guards':{'researchOnly':True,'no8781Change':True,'noLiveR3Change':True,'noEchtgeld':True,'special20260816Sealed':True,'notActionAuthority':True}}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':rep['summary']},ensure_ascii=False))
if __name__=='__main__': main()
