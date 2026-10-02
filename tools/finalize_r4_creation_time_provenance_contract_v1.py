from pathlib import Path
import json
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]; TZ=ZoneInfo('Asia/Taipei')
TEST='R4_CREATION_TIME_PROVENANCE_CONTRACT_V1_20260827_2035'
ART=ROOT/'data/research/r4_v0/hourly/r4_creation_time_provenance_contract_v1.json'
PRE='data/research/r4_v0/hourly/r4_creation_time_provenance_contract_v1_preregistered.json'
ARTR='data/research/r4_v0/hourly/r4_creation_time_provenance_contract_v1.json'
rep=json.loads(ART.read_text(encoding='utf-8'))
rep['seedNoRegression5of5']=True
rep['trainerRefresh']='TIMEOUT_NO_NEW_ARTIFACT'
rep['latestValidTrainerSnapshot']='2026-08-27T19:43:32.325987+08:00'
rep['latestValidAcquisitionSpearman']=0.7364208101958059
rep['latestValidPreservationSpearman']=0.5878522049742544
rep['decision']='TESTED_INCONCLUSIVE'; rep['status']='TESTED_INCONCLUSIVE'
rep['conclusion']='Creation-time instrumentation successfully attached stable responsibility/client-intent IDs to Maker execution lineage on all 12 new receipt-clock markets, and all observed Maker fill timelines were identity-complete and append-only monotonic. However none of the 12 markets entered the preregistered queue-option candidate checkpoint, so no 1s/3s/5s candidate frontier could be validated and no continuous remaining-option belief may reopen.'
rep['nextDistinct']='Do not mine additional markets in this cycle. Future independent non-live simulations should retain this creation-time provenance contract by default; only after a naturally occurring cohort contains >=5 candidate checkpoints with >=1 non-zero 5s frontier should a PREDECLARED_REPLICATION validate frontier reproducibility. Until then move to a different R4 semantic gap rather than proxying queue option.'
ART.write_text(json.dumps(rep,indent=2),encoding='utf-8')
entry={
 'testId':TEST,'testedAt':datetime.now(TZ).isoformat(),'status':'TESTED_INCONCLUSIVE','domain':'R4',
 'semanticAxis':'EXECUTION_DATA_PROVENANCE / CREATION_TIME_IMMUTABLE_INTENT_AND_FILL_LINEAGE',
 'semanticKeys':['creation-time immutable responsibility id','creation-time immutable client intent id','append-only per-order confirmed fill timeline','deterministic 1s 3s 5s fill frontier','new independent receipt-clock chronology'],
 'hypothesis':'Persist stable responsibility/client-intent identity and append-only per-order confirmed fill evidence at original simulation time so later frontier labels do not depend on replay identity reconstruction.',
 'artifact':ARTR,'preregistration':PRE,'decision':'TESTED_INCONCLUSIVE','actionAuthority':False,
 'cohort':{'markets':12,'newIndependentChronology':True,'special20260816Sealed':True,'echtgeldTraining':False},
 'primaryResult':rep['primaryResult'],'seedNoRegression5of5':True,'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT',
 'summary':'12/12 new receipt-clock markets carried identity-complete Maker fill lineage and 12/12 append-only monotonic timelines, but 0/12 entered the preregistered queue-option candidate checkpoint. Provenance attachment works at simulation time; candidate frontier support remains absent, so the result is INCONCLUSIVE and continuous remaining-option belief stays closed.',
 'retestAllowed':'ONLY_PREDECLARED_REPLICATION_ON_NEW_INDEPENDENT_CHRONOLOGY_WITH_NATURAL_CANDIDATE_SUPPORT',
 'nextDistinct':rep['nextDistinct']}
for rel,key in [('data/research/r4_v0/r4_hourly_experiment_registry_v1.json','experiments'),('data/research/hourly_novel_test_registry_v1.json','tests')]:
 p=ROOT/rel; d=json.loads(p.read_text(encoding='utf-8')); arr=d[key]
 arr[:]=[x for x in arr if x.get('testId')!=TEST]; arr.append(entry); p.write_text(json.dumps(d,indent=2),encoding='utf-8')
section=f'''\n\n## 2026-08-27 20:xx — CREATION-TIME PROVENANCE CONTRACT V1 — INCONCLUSIVE\n- Exactly one novel bounded R4 test completed: `{TEST}`; preregistered before execution.\n- Semantic novelty: prior provenance tests either froze transient historical `orderNum`, reconstructed economic identity across replay revisions, or audited an old source schema. This cycle instruments stable lineage **at creation time** on a new independent receipt-clock chronology, so no later replay rediscovery is required.\n- Layer assignment: receipt-clock orders/fills = EXECUTION INFORMATION; immutable `responsibilityId` + `clientIntentId` = EXECUTION DATA PROVENANCE; future 1s/3s/5s fill frontier = OFFLINE LABEL PROVENANCE; NOT_ACTION_AUTHORITY.\n- Cohort: 12 newest completed non-live R2 realistic-HFT markets excluded from the old 32-market queue-option source cohort; 2026-08-16 SEALED; no Echtgeld training/dream fill.\n- Result: all 12 markets' observed Maker fill events carried stable responsibility/client-intent identity; identity completeness 12/12 markets and append-only cumulative fill monotonicity 12/12. But candidate checkpoints = **0/12**, so there was no legal candidate 1s/3s/5s frontier and positive 5s frontier support = 0.\n- Decision: `TESTED_INCONCLUSIVE` by the fixed support rule. This is not a schema failure: creation-time provenance successfully attaches to actual fill lifecycle, but the new chronology did not naturally enter the old queue-option candidate state. Do not add more markets this cycle, change the candidate definition, or reopen the continuous remaining-option belief.\n- Seed/no-regression: 5/5 PASS. Trainer refresh was attempted once and timed out without a new artifact; latest valid snapshot remains 19:43, Acquisition Spearman 0.7364 / Preservation 0.5879, frozen Echtgeld exactly 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `{ARTR}`; preregistration: `{PRE}`; tool: `tools/test_r4_creation_time_provenance_contract_v1.py`. Both registries updated.\n- Next distinct gap: keep this creation-time provenance schema for future independent non-live simulations. Revalidate it only as a predeclared replication once natural support reaches >=5 candidate checkpoints with >=1 non-zero 5s frontier; meanwhile choose a different semantic R4 research gap rather than manufacturing a queue-option proxy.\n<!-- R4_CREATION_TIME_PROVENANCE_CONTRACT_V1_20260827_2035 -->\n'''
h=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'; h.write_text(h.read_text(encoding='utf-8')+section,encoding='utf-8')
prog=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v21.md'; prog.write_text('# R4 Information / Belief Layer Progress V21 — 2026-08-27\n'+section,encoding='utf-8')
print(json.dumps({'ok':True,'testId':TEST,'decision':'TESTED_INCONCLUSIVE','registryUpdated':True,'handoffUpdated':True,'progress':str(prog.relative_to(ROOT)).replace('\\','/')}))
