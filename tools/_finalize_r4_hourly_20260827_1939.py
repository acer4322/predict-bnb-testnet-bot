from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
now=datetime.now(TZ).isoformat()
tid='R4_ORIGINAL_SIMULATION_LINEAGE_SUFFICIENCY_V1_20260827_1939'
art='data/research/r4_v0/hourly/r4_original_simulation_lineage_sufficiency_v1.json'
pre='data/research/r4_v0/hourly/r4_original_simulation_lineage_sufficiency_v1_preregistered.json'
entry={
 'testId':tid,'testedAt':now,'status':'TESTED_REJECTED',
 'semanticAxis':'EXECUTION_DATA_PROVENANCE / ORIGINAL_SIMULATION_IMMUTABLE_LINEAGE',
 'semanticKeys':['original simulation-time candidate lineage','stable responsibility identity','source-only provenance sufficiency','future confirmed-fill evidence','no replay rediscovery'],
 'candidate':'Audit whether the original realistic-HFT queue-option source artifact itself preserves immutable candidate/responsibility identity plus direct per-order future confirmed-fill timeline, without any current replay.',
 'artifact':art,'preregistration':pre,'decision':'TESTED_REJECTED','actionAuthority':False,
 'primaryResult':{'eligibleRows':32,'economicCheckpointCompleteRows':32,'economicCheckpointCompletenessRate':1.0,'explicitStableIdentityRows':0,'explicitStableIdentityRate':0.0,'directPerOrderFillTimelineRows':0,'directPerOrderFillTimelineRate':0.0,'fullImmutableLineageRows':0,'fullImmutableLineageRate':0.0,'transientOrderNumRows':29,'terminalCumOnlyRows':29,'seedNoRegression5of5':True,'trainerAcquisitionSpearman':0.7364208101958059,'trainerPreservationSpearman':0.5878522049742544,'frozenEchtgeldMarkets':8},
 'summary':'Source-only provenance audit passed support and economic-checkpoint completeness (32/32) but found 0/32 explicit stable responsibility/client-intent ids and 0/32 direct per-order fill-event timelines; full immutable lineage is 0%. The historical artifact cannot support deterministic fill-frontier reconstruction across replay revisions by itself.',
 'retestAllowed':False,
 'nextDistinct':'Instrument future non-live receipt-clock simulations at creation time with immutable responsibility/client-intent id and append-only per-order fill events; then run a new independent provenance-contract validation before reopening continuous remaining-option belief.'
}
# R4 registry
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'; d=json.loads(p.read_text(encoding='utf-8'))
xs=d.setdefault('experiments',[])
if not any((x.get('testId')==tid or x.get('experimentId')==tid or x.get('cycleId')==tid) for x in xs): xs.append(entry)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
# Novel registry
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json'; d2=json.loads(p2.read_text(encoding='utf-8'))
tests=d2.setdefault('tests',[])
nov={
 'testId':tid,'testedAt':now,'status':'TESTED_REJECTED','domain':'R4','semanticAxis':entry['semanticAxis'],'semanticKeys':entry['semanticKeys'],
 'hypothesis':entry['candidate'],'artifact':art,'preregistration':pre,'resultSummary':entry['summary'],'actionAuthority':False,
 'retestAllowed':False,'nextDistinct':entry['nextDistinct']
}
if not any(x.get('testId')==tid for x in tests): tests.append(nov)
p2.write_text(json.dumps(d2,indent=2,ensure_ascii=False),encoding='utf-8')
# Handoff append
handoff=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
text='''\n\n## 2026-08-27 19:43 — ORIGINAL SIMULATION LINEAGE SUFFICIENCY V1 — REJECTED\n- Exactly one novel bounded test completed: `R4_ORIGINAL_SIMULATION_LINEAGE_SUFFICIENCY_V1_20260827_1939`, preregistered before execution.\n- Semantic novelty: the 17:46 frozen-identity and 18:40 stable-economic-intent tests both still depended on a later replay revision to materialize or reconstruct a candidate counterpart. This test uses the original source artifact only and asks whether immutable candidate/responsibility lineage plus direct future confirmed-fill evidence was already persisted at simulation time. No current replay and no model fit.\n- Layer assignment: source order lifecycle = EXECUTION INFORMATION; immutable candidate/responsibility lineage = EXECUTION DATA PROVENANCE; future confirmed-fill evidence = OFFLINE LABEL PROVENANCE; NOT_ACTION_AUTHORITY.\n- Cohort: all 32 eligible rows in `data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json`; 2026-08-16 SEALED; no Echtgeld training and no dream fill.\n- Economic checkpoint provenance is complete: 32/32 rows contain candidate time, side, order age/price, cumulative executed qty and remaining qty, so the source can reconstruct the economic state. But explicit stable responsibility/client-intent identity is 0/32, direct per-order fill-event timeline is 0/32, and full immutable lineage is 0/32. 29 rows preserve transient `oldOrderNum` and terminal cumulative quantity only, which is insufficient for cross-revision deterministic 1s/3s/5s fill frontiers.\n- Decision: `TESTED_REJECTED`. This is a source-schema/provenance deficiency, not model or support failure. Do not infer a stable identity from numeric orderNum, terminal cumulative fill, or relaxed timestamp/price/qty tolerance. Continuous remaining-option belief stays closed.\n- Trainer refresh succeeded: 500 ordinary Target markets, Acquisition Spearman 0.7364, Preservation Spearman 0.5879; frozen Echtgeld remains exactly 8 markets. Seed/no-regression 5/5 PASS. R4 champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `data/research/r4_v0/hourly/r4_original_simulation_lineage_sufficiency_v1.json`; preregistration: `data/research/r4_v0/hourly/r4_original_simulation_lineage_sufficiency_v1_preregistered.json`; tool: `tools/test_r4_original_simulation_lineage_sufficiency_v1.py`. Both registries updated.\n- Next distinct gap: instrument future non-live receipt-clock simulation at creation time with immutable responsibility/client-intent id plus append-only per-order fill events. Then run a new independent provenance-contract validation; only if it produces reproducible non-zero frontier support may the preregistered continuous remaining-executable-option belief reopen.\n<!-- R4_ORIGINAL_SIMULATION_LINEAGE_SUFFICIENCY_V1_20260827_1939 -->\n'''
old=handoff.read_text(encoding='utf-8')
if tid not in old: handoff.write_text(old+text,encoding='utf-8')
# progress v20
progress=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v20.md'
progress.write_text('# R4 Information / Belief Layer Progress V20 — 2026-08-27\n'+text,encoding='utf-8')
print(json.dumps({'ok':True,'r4RegistryCount':len(xs),'novelRegistryCount':len(tests),'progress':str(progress.relative_to(ROOT)).replace('\\\\','/')}))
