import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT=Path('.')
TEST='R4_PREPARE_BUILD_INTERACTION_LOCAL_QUALITY_V1_20260828_1434'
ART='data/research/r4_v0/hourly/r4_prepare_build_interaction_local_quality_v1.json'
PRE='data/research/r4_v0/hourly/r4_prepare_build_interaction_local_quality_v1_preregistered.json'
TOOL='tools/test_r4_prepare_build_interaction_local_quality_v1.py'
PROG='data/research/r4_v0/hourly/r4_information_layer_progress_20260828_v39.md'
HAND='data/research/r4_v0/r4_hourly_research_handoff_v1.md'
RREG='data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
NREG='data/research/hourly_novel_test_registry_v1.json'

a=json.loads(Path(ART).read_text(encoding='utf-8'))
p=a['primaryResult']; c=a['cohort']
now='2026-08-28T14:34:00+08:00'
entry={
 'testId':TEST,'testedAt':now,'status':'TESTED_REJECTED','domain':'R4',
 'semanticAxis':'PARALLEL_BELIEF_INTERACTION / PREPARE_X_BUILD_INCREMENTAL_LOCAL_FORMATION_QUALITY',
 'semanticKeys':['p_build','p_prepare_role_routed','multiplicative PREPARE x BUILD interaction','local 5s floor improvement','local 5s abs-net reduction','joint local Formation quality','belief interaction context only'],
 'hypothesis':'After conditioning on portfolio/payoff geometry plus additive BUILD and PREPARE beliefs, test whether their strict-past multiplicative interaction adds reproducible local Formation-quality information.',
 'artifact':ART,'preregistration':PRE,'tool':TOOL,'decision':'TESTED_REJECTED','actionAuthority':False,
 'cohort':{
   'train':c['TRAIN_FRESH24'],'unseen24':c['TEST_UNSEEN24'],'replication3':c['TEST_REPLICATION3'],
   'phase':'60-300s Formation','special20260816Sealed':True,'echtgeldTraining':False
 },
 'primaryResult':{
   'eligibleComparisons':p['eligibleComparisons'],'meanDeltaAuc':p['meanDeltaAuc'],'meanDeltaAp':p['meanDeltaAp'],
   'meanLogLossImprovement':p['meanLogLossImprovement'],'worstDeltaAuc':p['worstDeltaAuc'],
   'nonnegativeAucComparisons':p['nonnegativeAucComparisons'],
   'seedNoRegression5of5':'UNCHANGED_LAST_VERIFIED_5_OF_5_NO_ACTION_CODE_CHANGE',
   'trainerRefresh':'FAILED_NO_NEW_ARTIFACT_EMPTY_FROZEN_MATCHED_GAP_ARRAY'
 },
 'summary':'The BUILDxPREPARE interaction gave small positive AUC lift on all three UNSEEN24 local-quality targets, but all three AUC deltas turned slightly negative on the newer independent REPLICATION3 holdout; mean AP also worsened. The preregistered stability/calibration gate fails. Reject this multiplicative synergy as incremental Formation-quality context; preserve the underlying BUILD and PREPARE beliefs in their existing roles.',
 'retestAllowed':False,
 'nextDistinct':'Do not rescue with centered/residualized products, belief bins, interaction thresholds or nonlinear model sweeps. Prefer a semantically different naturally-supported late protection/execution-lifecycle information route, or a genuinely new independent chronology for an already frozen replication.'
}
# enrich artifact with cycle status
obj=json.loads(Path(ART).read_text(encoding='utf-8'))
obj['broadStability']={
 'seedNoRegression':'UNCHANGED_LAST_VERIFIED_5_OF_5_NO_ACTION_CODE_CHANGE',
 'trainerRefresh':'FAILED_NO_NEW_ARTIFACT_EMPTY_FROZEN_MATCHED_GAP_ARRAY',
 'latestValidTrainingSnapshot':'2026-08-27T19:43:32.325987+08:00',
 'latestValidAcquisitionSpearman':0.7364208101958059,
 'latestValidPreservationSpearman':0.5878522049742544,
 'frozenEchtgeldMarkets':8,
 'r4ChampionChanged':False,'liveR3Changed':False,'echtgeld8781Changed':False
}
obj['registryEntry']=entry
Path(ART).write_text(json.dumps(obj,indent=2),encoding='utf-8')

for rp,key in [(RREG,'tests'),(NREG,'tests')]:
 d=json.loads(Path(rp).read_text(encoding='utf-8'))
 arr=d[key]
 arr=[x for x in arr if x.get('testId')!=TEST]
 arr.append(entry)
 d[key]=arr
 Path(rp).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')

progress=f'''# R4 Information Layer Progress V39 — 2026-08-28 14:34\n\n- Exactly one novel bounded test: `{TEST}` -> `TESTED_REJECTED`; preregistered before execution after both-registry and semantic duplicate audit.\n- Semantic novelty: prior BUILD×PREPARE work was descriptive regime/cell mapping, while prior BOTH_HIGH work attributed a separate quote-information increment. This cycle first tests whether the strict-past **nonlinear interaction itself** adds local Formation-quality information after conditioning on the additive BUILD and PREPARE beliefs plus portfolio/payoff geometry.\n- Layers: portfolio/payoff geometry = `LOGIC_CONTEXT`; `p_build` = `BUILD_BELIEF`; `p_prepare_role_routed` = `PREPARE_BELIEF`; their product = `PARALLEL_BELIEF_INTERACTION_CONTEXT_CANDIDATE`; output `NOT_ACTION_AUTHORITY`.\n- Realistic-HFT cohort after 60–300s filter: FRESH24 train {c['TRAIN_FRESH24']['rows']} rows / {c['TRAIN_FRESH24']['markets']} markets; independent UNSEEN24 {c['TEST_UNSEEN24']['rows']} / {c['TEST_UNSEEN24']['markets']}; REPLICATION3 {c['TEST_REPLICATION3']['rows']} / {c['TEST_REPLICATION3']['markets']}. All 6 target×holdout comparisons passed the fixed support gate.\n- Result: mean dAUC {p['meanDeltaAuc']:+.5f}; mean dAP {p['meanDeltaAp']:+.5f}; mean log-loss improvement {p['meanLogLossImprovement']:+.6f}; worst dAUC {p['worstDeltaAuc']:+.5f}; only {p['nonnegativeAucComparisons']}/6 AUC deltas non-negative. UNSEEN24 improved on floor/abs-net/joint quality, but all three REPLICATION3 AUC deltas were slightly negative and AP/calibration also reversed. Fixed gate fails -> `TESTED_REJECTED`.\n- Do not rescue with centered/residualized products, belief bins/thresholds, or nonlinear model sweeps. Existing PREPARE and BUILD beliefs retain their prior roles; only the multiplicative local-quality synergy is rejected. No order/size/owner/MPQ/KEEP/PULL/REPRICE authority.\n- Trainer refresh attempted once and failed at the known empty frozen matched-gap training array before writing a new snapshot. Latest valid snapshot remains 2026-08-27 19:43: Acquisition 0.7364 / Preservation 0.5879; frozen Echtgeld remains exactly 8. Last verified seed no-regression remains 5/5 because no action/champion code changed. R4 champion/live R3-S/R3.1/8781 unchanged.\n- Next: move away from BUILD×PREPARE algebraic interaction variants. Prefer a semantically different naturally-supported late protection/execution-lifecycle information route, or a genuinely new independent chronology for a frozen predeclared replication.\n'''
Path(PROG).write_text(progress,encoding='utf-8')
block=f'''\n\n## 2026-08-28 14:34 — PREPARE × BUILD NONLINEAR LOCAL-QUALITY INTERACTION V1 — REJECTED\n- Exactly one novel bounded test: `{TEST}` -> `TESTED_REJECTED`; preregistered before execution.\n- Semantic novelty: prior parallel-belief maps were descriptive regime/cell analyses and prior BOTH_HIGH attribution measured the increment of separate quote INFORMATION. This cycle compares additive BUILD+PREPARE beliefs against the identical strict-past model plus only `p_build * p_prepare_role_routed`, targeting local 5s Formation quality.\n- Layer assignment: geometry = LOGIC context; p_build = BUILD BELIEF; p_prepare = PREPARE BELIEF; interaction = PARALLEL_BELIEF_INTERACTION context candidate; `NOT_ACTION_AUTHORITY`.\n- Realistic-HFT: FRESH24 train {c['TRAIN_FRESH24']['rows']} rows/24 markets; UNSEEN24 {c['TEST_UNSEEN24']['rows']}/24 and REPLICATION3 {c['TEST_REPLICATION3']['rows']}/24 independent holdouts. Six comparisons all supported.\n- Result: mean dAUC {p['meanDeltaAuc']:+.5f}, mean dAP {p['meanDeltaAp']:+.5f}, mean log-loss improvement {p['meanLogLossImprovement']:+.6f}, worst dAUC {p['worstDeltaAuc']:+.5f}, non-negative dAUC {p['nonnegativeAucComparisons']}/6. UNSEEN24 improved on all three targets; REPLICATION3 slightly reversed all three. Fixed chronological/calibration gate fails -> `TESTED_REJECTED`.\n- Do not rescue by interaction bins/thresholds, centering/residualization, polynomial/nonlinear model sweeps. Existing BUILD and PREPARE belief evidence is unchanged; this rejects only extra multiplicative synergy as local-quality context.\n- Trainer refresh attempted once and hit the known empty frozen matched-gap array; no new artifact. Latest valid training snapshot remains Acquisition 0.7364 / Preservation 0.5879, frozen Echtgeld 8. Last verified seed no-regression remains 5/5. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifacts: `{ART}`; preregistration `{PRE}`; progress V39. Both registries deduplicated and updated.\n- Next distinct gap: do not repackage BUILD×PREPARE algebra. Prefer a semantically different naturally-supported late protection/execution-lifecycle information route, or wait for genuinely new independent chronology for frozen replication.\n<!-- R4_PREPARE_BUILD_INTERACTION_LOCAL_QUALITY_V1_20260828_1434 -->\n'''
h=Path(HAND).read_text(encoding='utf-8')
marker='<!-- R4_PREPARE_BUILD_INTERACTION_LOCAL_QUALITY_V1_20260828_1434 -->'
if marker not in h:
 h+=block
 Path(HAND).write_text(h,encoding='utf-8')
print(json.dumps({'ok':True,'testId':TEST,'decision':'TESTED_REJECTED','progress':PROG,'artifact':ART},ensure_ascii=False))
