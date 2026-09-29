from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MANAGEMENT_EARLY_GEOMETRY_COHERENCE_CONTEXT_V1_20260828_0334'
ART='data/research/r4_v0/hourly/r4_management_early_geometry_coherence_context_v1.json'
PRE='data/research/r4_v0/hourly/r4_management_early_geometry_coherence_context_v1_preregistered.json'
TOOL='tools/test_r4_management_early_geometry_coherence_context_v1.py'
res=json.loads((ROOT/ART).read_text(encoding='utf-8'))
s=res['summary']
entry={
 'testId':TEST_ID,
 'testedAt':res['createdAt'],
 'status':res['decision'],
 'domain':'R4',
 'semanticAxis':'MANAGEMENT / EARLY_GEOMETRY_TRAJECTORY_COHERENCE_CONTEXT',
 'semanticKeys':['early management 120-180s','pair balance trajectory','floor risk trajectory','cross-component geometry coherence','lifecycle quality consistency context','not action authority'],
 'hypothesis':'Within the already-kept EARLY 120-180s pair/balance + floor/risk trajectory context, test whether strict-past agreement/discordance between the two geometry groups adds incremental realistic-HFT lifecycle-quality calibration beyond the raw trajectory deltas themselves.',
 'artifact':ART,
 'preregistration':PRE,
 'tool':TOOL,
 'decision':res['decision'],
 'actionAuthority':False,
 'cohort':{'source':'existing disjoint realistic-HFT Management replications','phase':'120 < seconds_left <= 180','representedCohorts':s.get('representedCohorts',[]),'eligibleComparisons':s.get('eligibleComparisons',0),'special20260816Sealed':True,'echtgeldTraining':False},
 'primaryResult':{'meanDeltaAuc':s.get('meanDeltaAuc'),'meanDeltaAp':s.get('meanDeltaAp'),'meanLogLossImprovement':s.get('meanLogLossImprovement'),'worstDeltaAuc':s.get('worstDeltaAuc'),'nonNegativeAucFraction':s.get('nonNegativeAucFraction'),'seedNoRegression5of5':True,'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT'},
 'summary':'Cross-component coherence adds no stable incremental information beyond the raw pair/balance + floor/risk trajectories: mean dAUC about -0.0010, mean dAP about -0.0040, worst dAUC -0.0483. Reject coherence/discordance as an extra lifecycle-quality context; preserve the component trajectories themselves in their already-kept EARLY role only.',
 'retestAllowed':False,
 'nextDistinct':'Do not repackage pair/floor agreement as another uncertainty/coherence feature or rescue it with thresholds/models. Prefer genuinely new independent HFT chronology for the frozen EARLY pair/balance+floor/risk replication when available; otherwise move to a semantically different naturally-supported EARLY Management information source.'
}
# R4 registry: append to tests only, dedup by testId
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'; d=json.loads(p.read_text(encoding='utf-8'))
if not any(x.get('testId')==TEST_ID for x in d.get('tests',[])): d.setdefault('tests',[]).append(entry)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
# Novel ledger: compatible linked entry
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json'; n=json.loads(p2.read_text(encoding='utf-8'))
nov=dict(entry); nov['axis']='R4_MANAGEMENT_EARLY_GEOMETRY_TRAJECTORY_COHERENCE_CONTEXT'; nov['conclusion']=entry['summary']
if not any(x.get('testId')==TEST_ID for x in n.get('tests',[])): n.setdefault('tests',[]).append(nov)
p2.write_text(json.dumps(n,indent=2,ensure_ascii=False),encoding='utf-8')
# Handoff append
handoff=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
marker='<!-- R4_MANAGEMENT_EARLY_GEOMETRY_COHERENCE_CONTEXT_V1_20260828_0334 -->'
if marker not in handoff.read_text(encoding='utf-8'):
    text=f'''\n\n## 2026-08-28 03:34 — MANAGEMENT EARLY GEOMETRY-TRAJECTORY COHERENCE CONTEXT V1 — REJECTED\n- Exactly one novel bounded test completed: `{TEST_ID}`; preregistered before execution.\n- Semantic novelty: the 02:38 parent attribution showed pair/balance and floor/risk trajectory groups each carry EARLY 120–180s HFT lifecycle-quality information. This cycle does not retest their inclusion, phase, tempo, model, or thresholds; it asks whether the **relation between them**—agreement versus discordance in strict-past quality direction—adds information beyond the raw component deltas.\n- Layer assignment: portfolio/payoff geometry = LOGIC baseline; pair/balance + floor/risk deltas = LOGIC trajectory context; cross-component coherence = Management lifecycle-quality consistency context; output NOT_ACTION_AUTHORITY.\n- Realistic-HFT support: {s.get('eligibleComparisons')} eligible target×cohort comparisons across {len(s.get('representedCohorts',[]))} independent cohorts ({', '.join(s.get('representedCohorts',[]))}), EARLY 120–180s only; 2026-08-16 SEALED; no Echtgeld fit; future HFT outcomes scoring-only.\n- Result: mean dAUC {s.get('meanDeltaAuc'):.6f}, mean dAP {s.get('meanDeltaAp'):.6f}, mean log-loss improvement {s.get('meanLogLossImprovement'):.6f}, worst dAUC {s.get('worstDeltaAuc'):.6f}, non-negative AUC fraction {s.get('nonNegativeAucFraction'):.3f}. Fixed KEEP gate failed.\n- Decision: `TESTED_REJECTED`. Pair/balance and floor/risk raw trajectories remain retained in their prior EARLY context roles, but an additional coherence/discordance semantic does not improve stability. Do not threshold/model-sweep rescue or turn coherence into manager/action authority.\n- Seed/no-regression: 5/5 PASS. Target trainer refresh attempted once and timed out without a new artifact; latest valid snapshot remains 2026-08-27 19:43, Acquisition Spearman 0.7364 / Preservation 0.5879, frozen Echtgeld exactly 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifacts: `{ART}`; preregistration `{PRE}`; tool `{TOOL}`. Both registries updated.\n- Next distinct gap: do not create another agreement/uncertainty transform of the same trajectory components. Prefer a PREDECLARED_REPLICATION on genuinely new HFT chronology when available; otherwise move to a semantically different naturally-supported EARLY Management information source.\n{marker}\n'''
    with handoff.open('a',encoding='utf-8') as f: f.write(text)
# progress V28
prog=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260828_v28.md'
prog.write_text(f'''# R4 Information Layer Progress V28 — 2026-08-28 03:34\n\n- Exactly one novel test: `{TEST_ID}`.\n- Semantic novelty: prior component attribution asked which EARLY trajectory groups carry signal; this cycle asks whether cross-component agreement/discordance itself adds lifecycle-quality information beyond the already-kept raw pair/balance + floor/risk deltas.\n- Layers: portfolio/payoff = LOGIC baseline; pair/balance + floor/risk = LOGIC trajectory context; coherence = Management lifecycle-quality consistency context; NOT_ACTION_AUTHORITY.\n- HFT support: {s.get('eligibleComparisons')} eligible comparisons across {len(s.get('representedCohorts',[]))} independent cohorts ({', '.join(s.get('representedCohorts',[]))}), 120–180s EARLY Management.\n- Result: mean dAUC {s.get('meanDeltaAuc'):.6f}; mean dAP {s.get('meanDeltaAp'):.6f}; mean log-loss improvement {s.get('meanLogLossImprovement'):.6f}; worst dAUC {s.get('worstDeltaAuc'):.6f}; non-negative AUC fraction {s.get('nonNegativeAucFraction'):.3f}.\n- Decision: `TESTED_REJECTED`. Raw pair/balance and floor/risk trajectories remain prior KEEP contexts; their extra coherence transform is not supported. No threshold/model rescue and no action authority.\n- Seed/no-regression 5/5 PASS. Trainer refresh timed out once with no new artifact; latest valid snapshot remains Acquisition 0.7364 / Preservation 0.5879, frozen Echtgeld 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Next: wait for genuinely new independent HFT chronology for frozen EARLY interpretation replication, or move to a semantically different naturally-supported EARLY Management information source. Do not repackage coherence/discordance.\n''',encoding='utf-8')
print(json.dumps({'ok':True,'testId':TEST_ID,'registryTests':len(d.get('tests',[])),'novelTests':len(n.get('tests',[])),'progress':str(prog.relative_to(ROOT)).replace('\\','/')}))
