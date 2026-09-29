from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
tid='R4_MANAGEMENT_EARLY_GEOMETRY_NEW_CHRONOLOGY_REPLICATION_V1_20260828_0738'
entry={
 'testId':tid,'testedAt':'2026-08-28T07:46:01.109321+08:00','status':'TESTED_INCONCLUSIVE','domain':'R4',
 'semanticAxis':'EARLY_MANAGEMENT_GEOMETRY_TRAJECTORY / NEW_INDEPENDENT_CHRONOLOGY_REPLICATION',
 'semanticKeys':['EARLY 120-180s Management','new independent receipt-clock HFT chronology','pair/balance geometry trajectory','floor/risk geometry trajectory','local 5s lifecycle quality','predeclared replication','context not action authority'],
 'hypothesis':'Replicate the frozen EARLY pair/balance + floor/risk geometry-trajectory information on genuinely newer independent realistic-HFT chronology without changing features, model, phase, labels, or gate.',
 'artifact':'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1.json',
 'preregistration':'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1_preregistered.json',
 'decision':'TESTED_INCONCLUSIVE','actionAuthority':False,
 'cohort':{'selectedMarkets':40,'allShadowExecutionExact':True,'newerARows':30,'newerAMarkets':10,'newerBRows':18,'newerBMarkets':6,'special20260816Sealed':True,'echtgeldTraining':False},
 'primaryResult':{'pairBalanceEligibleComparisons':0,'floorRiskEligibleComparisons':0,'newerAPositivesPerTarget':3,'newerANegativesPerTarget':27,'newerBPositivesPerTarget':7,'newerBNegativesPerTarget':11,'requiredPositivePerBlockTarget':8,'requiredNegativePerBlockTarget':8,'seedNoRegression5of5':True},
 'summary':'Forty genuinely newer independent HFT markets replayed with exact no-regression, but the frozen EARLY/current-BUILD filter yielded only 30 and 18 rows in the two chronological blocks. Positive local-quality transitions were 3 and 7 respectively, below the preregistered >=8 per block-target support gate, so no legal model comparison was made. Prior EARLY trajectory KEEP_SIGNAL is neither promoted nor downgraded.',
 'retestAllowed':'ONLY_PREDECLARED_REPLICATION_ON_GENUINELY_NEWER_INDEPENDENT_CHRONOLOGY_WITH_ADEQUATE_NATURAL_SUPPORT',
 'nextDistinct':'Do not expand/merge these same 40 markets or weaken the phase/support gate. Await genuinely newer independent chronology with adequate natural EARLY/current-BUILD quality-transition support; until then choose a semantically different naturally-supported R4 information gap.'
}
for rel,key in [('data/research/r4_v0/r4_hourly_experiment_registry_v1.json','tests'),('data/research/hourly_novel_test_registry_v1.json','tests')]:
 p=ROOT/rel;d=json.loads(p.read_text(encoding='utf-8'));arr=d[key]
 if not any(x.get('testId')==tid for x in arr):arr.append(entry)
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
hand=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
marker=f'<!-- {tid} -->'
if marker not in hand.read_text(encoding='utf-8'):
 with hand.open('a',encoding='utf-8') as f:
  f.write(f'''\n\n## 2026-08-28 07:46 — EARLY GEOMETRY TRAJECTORY NEW-CHRONOLOGY REPLICATION V1 — INCONCLUSIVE\n- Exactly one permitted replication: `{tid}`; retest basis `NEW_INDEPENDENT_CHRONOLOGY + PREDECLARED_REPLICATION`. No feature/model/threshold/phase/label change from the frozen EARLY pair/balance + floor/risk trajectory interpretation.\n- 40 newer non-live R2_RESIDUAL realistic-HFT markets with Execution Tape were fixed before scoring and split 20+20 chronologically. All 40 shadow replays were execution-exact no-regression. Frozen EARLY/current-BUILD support was NEWER_A 30 rows / 10 markets and NEWER_B 18 / 6.\n- All three local-quality labels had 3 positives / 27 negatives in NEWER_A and 7 / 11 in NEWER_B. The preregistered >=8 positive + >=8 negative block-target support rule therefore produced 0 eligible comparisons for both PAIR_BALANCE and FLOOR_RISK.\n- Decision: `TESTED_INCONCLUSIVE` due natural support/identifiability, not signal failure. Do not merge blocks, widen 120–180s, lower support gates, alter horizons/models, or mine more of the same cohort to rescue. Prior EARLY KEEP_SIGNAL remains frozen context-only / NOT_ACTION_AUTHORITY.\n- Seed/no-regression 5/5 PASS; R4 champion unchanged; live R3-S/R3.1/8781 untouched; no Echtgeld fit/ingestion; 2026-08-16 SEALED.\n- Artifact: `data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1.json`; preregistration: `data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1_preregistered.json`; progress V32.\n- Next: only repeat on genuinely newer independent chronology when natural EARLY/current-BUILD positive support is adequate under the frozen gate; otherwise move to a semantically different naturally-supported information gap.\n{marker}\n''')
print(json.dumps({'ok':True,'testId':tid,'registriesUpdated':True,'handoffUpdated':True}))
