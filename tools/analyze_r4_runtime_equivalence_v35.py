import json
from pathlib import Path
B=Path('data/research/r4_v0/p0_provenance_v1')
v25=json.loads((B/'r4_exact_first_late_v25_summary.json').read_text(encoding='utf-8'))
v32=json.loads((B/'r4_carrier_queuepath_v32_3_features_unlabeled.json').read_text(encoding='utf-8'))
v34=json.loads((B/'r4_two_axis_selective_completion_v34_event_map.json').read_text(encoding='utf-8'))
q={int(r['marketId']):r for r in v32['rows']}
expected=set(map(int,v34.get('eligibleMarketIds',[])))
rows=[]; got=set(); missing=[]
for r in v25['rows']:
    mid=int(r['marketId']); f=r.get('features') or {}
    qr=q.get(mid)
    if qr is None:
        missing.append({'marketId':mid,'reason':'missing_queue_row'}); continue
    vals=[f.get('predictAligned'),f.get('spotTaker1sAligned'),f.get('futuresTaker1sAligned')]
    qn=(((qr.get('queuePath') or {}).get('original') or {}).get('1000') or {}).get('queueChangeNorm')
    if any(x is None for x in vals) or qn is None:
        decision=False; reason='strict_past_axis_undefined'
        adverse=None if any(x is None for x in vals) else 0.4*vals[0]+0.3*vals[1]+0.3*vals[2]
        decay=None if qn is None else -qn
    else:
        adverse=0.4*vals[0]+0.3*vals[1]+0.3*vals[2]
        decay=-qn
        decision=(adverse<=0.0 and decay>=0.0)
        reason='eligible' if decision else 'ineligible'
    if decision: got.add(mid)
    rows.append({'marketId':mid,'cohort':r.get('cohort'),'adverseRuntime':adverse,'queueDecay1sRuntime':decay,'decisionRuntime':decision,'decisionReason':reason,'expectedV34':mid in expected,'match':decision==(mid in expected)})
extra=sorted(got-expected); missed=sorted(expected-got); mism=[x for x in rows if not x['match']]
out={'version':'R4_RUNTIME_EQUIVALENCE_V35','date':'2026-08-31','researchOnly':True,'strictPast':True,'runtimeFormula':{'adverse':'0.40*predictAligned + 0.30*spotTaker1sAligned + 0.30*futuresTaker1sAligned','queueDecay1s':'- original carrier queueChangeNorm over strict-past 1000ms','decision':'adverse<=0 AND queueDecay1s>=0; undefined axis => no reactivation'},'sources':{'rawSeamFeatures':'r4_exact_first_late_v25_summary.json features only','rawQueuePath':'r4_carrier_queuepath_v32_3_features_unlabeled.json queuePath.original.1000 only','comparisonOnly':'r4_two_axis_selective_completion_v34_event_map.json eligibleMarketIds'},'nRows':len(rows),'expectedEligible':sorted(expected),'runtimeEligible':sorted(got),'decisionMatches':sum(x['match'] for x in rows),'decisionMismatches':len(mism),'extraRuntimeEligible':extra,'missedExpectedEligible':missed,'undefinedAxisCount':sum(x['decisionReason']=='strict_past_axis_undefined' for x in rows),'equivalencePassed':len(mism)==0 and not extra and not missed,'rows':rows,'scientificBoundary':'Consumed A/B/C equivalence audit only; no fresh cohort, no outcome label used to compute runtime decision, no dream fill, no live authority.'}
(B/'r4_runtime_equivalence_v35.json').write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:out[k] for k in ['nRows','expectedEligible','runtimeEligible','decisionMatches','decisionMismatches','undefinedAxisCount','equivalencePassed']},ensure_ascii=False))
