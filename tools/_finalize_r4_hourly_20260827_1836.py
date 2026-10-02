from pathlib import Path
import json
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
now=datetime.now(TZ).isoformat()
tid='R4_STABLE_ECONOMIC_INTENT_IDENTITY_V1_20260827_1836'
art='data/research/r4_v0/hourly/r4_stable_economic_intent_identity_v1.json'
pre='data/research/r4_v0/hourly/r4_stable_economic_intent_identity_v1_preregistered.json'
rep=json.loads((ROOT/art).read_text(encoding='utf-8'))
entry={
 'testId':tid,'testedAt':rep.get('createdAt',now),'status':rep['status'],
 'semanticAxis':'EXECUTION_DATA_PROVENANCE / STABLE_ECONOMIC_INTENT_IDENTITY',
 'semanticKeys':['stable economic intent identity','resting responsibility identity','orderNum independent identity','source artifact to current replay identity','receipt-clock lifecycle provenance'],
 'candidate':'Reconstruct an orderNum-independent canonical resting-responsibility identity from market, side, original submit time, passive price and original requested quantity, then compare historical source artifact against current receipt-clock replay.',
 'artifact':art,'preregistration':pre,'decision':rep['status'],'actionAuthority':False,
 'primaryResult':rep['primaryResult'],
 'summary':'Historical source rows had complete canonical economic keys, but the current replay reproduced zero comparable candidate/resting-child checkpoints for the same cohort, so cross-revision identity reconstruction is not yet identifiable. This is a replay-provenance failure before order identity, not evidence that the economic key is wrong.',
 'retestAllowed':'ONLY_MATERIAL_DATA_PIPELINE_FIX_OR_NEW_INDEPENDENT_CHRONOLOGY',
 'nextDistinct':'Persist original simulation-time candidate/responsibility checkpoint lineage itself (stable client/economic intent id + source lifecycle snapshot) so later replay revisions do not need to rediscover the candidate. Only after that provenance layer exists may deterministic fill-frontier reconstruction be retried.'
}
# R4 registry
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'; d=json.loads(p.read_text(encoding='utf-8')); arr=d.setdefault('experiments',[])
if not any((x.get('testId')==tid or x.get('experimentId')==tid or x.get('cycleId')==tid) for x in arr): arr.append(entry)
p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
# global novelty ledger
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json'; d2=json.loads(p2.read_text(encoding='utf-8')); arr2=d2.setdefault('tests',[])
if not any(x.get('testId')==tid for x in arr2):
    arr2.append({
      'testId':tid,'testedAt':entry['testedAt'],'axis':entry['semanticAxis'],'semanticKeys':entry['semanticKeys'],
      'hypothesis':'A stable orderNum-independent economic intent key should reconstruct the same resting responsibility across historical artifact and current receipt-clock replay revisions.',
      'cohort':rep['cohort'],'primaryResult':rep['primaryResult'],'status':rep['status'],'artifact':art,'preregisteredArtifact':pre,
      'tool':'tools/test_r4_stable_economic_intent_identity_v1.py','conclusion':entry['summary'],'retestAllowed':False,
      'retestReason':'Only MATERIAL_DATA_PIPELINE_FIX or NEW_INDEPENDENT_CHRONOLOGY may reopen; do not change key tolerances to rescue.'
    })
p2.write_text(json.dumps(d2,ensure_ascii=False,indent=2),encoding='utf-8')
# handoff append
handoff=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
text=f'''\n\n## 2026-08-27 18:40 — STABLE ECONOMIC INTENT IDENTITY V1 — INCONCLUSIVE\n- Exactly one novel bounded R4 test completed: `{tid}`. It was preregistered before execution and is a data-provenance test, not a continuous fill model or execution action test.\n- Semantic novelty: the prior frozen-identity frontier froze transient `candidateAtMs + orderNum` and obtained 29/29 materialized but all-zero future-fill frontiers. This cycle instead attempted to reconstruct the same resting responsibility without `orderNum`, using canonical economic identity `(marketId, side, original submittedAtMs, passive orderPrice, originalRequestedQty)`.\n- Layer assignment: source/current order lifecycle = EXECUTION INFORMATION; canonical economic-intent identity = EXECUTION DATA PROVENANCE; output = IDENTITY_RECONSTRUCTABILITY_DIAGNOSTIC; NOT_ACTION_AUTHORITY.\n- Source support was strong: 32/32 eligible historical rows had a complete canonical key. However the current replay revision produced 0/32 comparable candidate/resting-child checkpoints, so current replay coverage was 0% and no valid source-vs-current key comparison could be made. Numeric orderNum also matched 0/32, consistent with the previous revision-drift finding.\n- Decision: `TESTED_INCONCLUSIVE`. This is not evidence that the canonical economic key is wrong; the provenance break occurs earlier because the current controller replay no longer rediscovers the historical responsibility checkpoint. Do not relax key tolerances or reopen continuous remaining-option prediction.\n- Broad stability: seed/no-regression 5/5 PASS. Trainer refresh succeeded on 500 ordinary Target markets with 2026-08-16 SEALED; Acquisition Spearman 0.7301, Preservation Spearman 0.6428; frozen Echtgeld remains exactly 8 markets. R4 champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `{art}`; preregistration: `{pre}`; tool: `tools/test_r4_stable_economic_intent_identity_v1.py`. Both registries updated.\n- Next highest-value distinct gap: persist the original simulation-time candidate/responsibility checkpoint lineage itself (stable economic/client intent id plus lifecycle snapshot) rather than asking a later replay revision to rediscover the candidate. Only after that provenance layer is materialized should deterministic fill-frontier reconstruction be retried as a separate MATERIAL_DATA_PIPELINE_FIX.\n<!-- {tid} -->\n'''
s=handoff.read_text(encoding='utf-8')
if tid not in s: handoff.write_text(s+text,encoding='utf-8')
# progress V19
prog=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v19.md'
prog.write_text('# R4 Information / Belief Layer Progress V19 — 2026-08-27\n'+text,encoding='utf-8')
print(json.dumps({'ok':True,'status':rep['status'],'r4RegistryCount':len(arr),'novelRegistryCount':len(arr2),'progress':str(prog.relative_to(ROOT)).replace('\\','/')},ensure_ascii=False))
