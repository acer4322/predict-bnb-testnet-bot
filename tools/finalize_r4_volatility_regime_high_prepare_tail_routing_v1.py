from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
TEST='R4_VOLATILITY_REGIME_HIGH_PREPARE_TAIL_ROUTING_V1_20260828_0937'
ART='data/research/r4_v0/hourly/r4_volatility_regime_high_prepare_tail_routing_v1.json'
PRE='data/research/r4_v0/hourly/r4_volatility_regime_high_prepare_tail_routing_v1_preregistered.json'
REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
NOV=ROOT/'data/research/hourly_novel_test_registry_v1.json'
HAND=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
PROG=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260828_v34.md'

def dump(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    a=json.loads((ROOT/ART).read_text(encoding='utf-8')); s=a['primaryHighPrepareSummary']; c=a['coverage']; now=datetime.now(TZ).isoformat()
    entry={
      'testId':TEST,'testedAt':now,'status':a['status'],'domain':'R4',
      'semanticAxis':'INFORMATION_ROUTING / PUBLIC_VOLATILITY_REGIME_IN_HIGH_PREPARE_TAIL_CONTEXT',
      'semanticKeys':['public volatilityAlert','HIGH PREPARE Formation','retained weak-side quote economics','15s floor-tail damage','information regime context','context not action authority'],
      'hypothesis':'Inside HIGH PREPARE Formation, test whether the published strict-past volatilityAlert adds local 15s floor-tail information beyond retained geometry + Predict/strike + placement-readiness + weak-side quote economics.',
      'artifact':ART,'preregistration':PRE,'decision':a['status'],'actionAuthority':False,
      'cohort':{'markets':c['markets'],'rows':c['rows'],'medianPublicLagMs':c['medianPublicLagMs'],'maxPublicLagMs':c['maxPublicLagMs'],'volatilityDistribution':c['volatilityDistribution'],'special20260816Sealed':True,'echtgeldTraining':False},
      'primaryResult':{'eligibleFolds':s['eligibleFolds'],'meanDeltaAuc':s['meanDeltaAuc'],'meanDeltaAp':s['meanDeltaAp'],'meanLogLossImprovement':s['meanLogLossImprovement'],'worstDeltaAuc':s['worstDeltaAuc'],'allFoldAucNonnegative':s['allFoldAucNonnegative'],'seedNoRegression5of5':True,'trainerRefresh':'FAILED_NO_NEW_ARTIFACT_EMPTY_MATCHED_GAP_ARRAY'},
      'summary':'The published volatilityAlert was constant at 1.0 in all 2,699 eligible rows and every HIGH-PREPARE fold. Adding it to the retained quote-tail stack changed AUC, AP and log-loss by exactly 0 in all three folds. The fixed KEEP gate therefore fails. This rejects the current published volatilityAlert field as useful routing information in this chronology; it does not authorize rebuilding/tuning a volatility threshold in this cycle.',
      'retestAllowed':False,
      'nextDistinct':'Do not rescue volatilityAlert with thresholds or derived-volatility variants on the same cohort. Choose a semantically different naturally-supported information/routing source, or use a genuinely new independent chronology for a predeclared replication.'
    }
    r=json.loads(REG.read_text(encoding='utf-8')); arr=r.setdefault('experiments',[])
    if not any(x.get('testId')==TEST for x in arr): arr.append(entry)
    dump(REG,r)
    n=json.loads(NOV.read_text(encoding='utf-8')); arr2=n.setdefault('tests',[])
    if not any(x.get('testId')==TEST for x in arr2): arr2.append(entry)
    dump(NOV,n)
    progress=f'''# R4 Information Layer Progress V34 — 2026-08-28 09:xx\n\n- Exactly one novel bounded test completed: `{TEST}` -> `{a['status']}`.\n- Semantic novelty: prior Formation work tested raw public feature soup, HIGH-PREPARE weak quote economics, secondary basis/Chainlink context, and Predict-confidence routing. This cycle isolates the already-published `volatilityAlert` as a single semantic `INFORMATION / REGIME CONTEXT` channel on top of the retained HIGH-PREPARE quote-tail stack. No volatility threshold or engineered-volatility reconstruction was allowed.\n- Layers: `volatilityAlert` = INFORMATION_REGIME_CONTEXT; weak-side quote economics = retained INFORMATION; placement readiness = PREPARE BELIEF; Predict/strike = FORMATION BELIEF; portfolio/payoff geometry = LOGIC. Output is `NOT_ACTION_AUTHORITY`.\n- Cohort: {c['markets']} ordinary Target BTC markets / {c['rows']} 60-300s REPAIR rows; strict-past public lag median {c['medianPublicLagMs']:.0f}ms, max {c['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED; no Echtgeld fit/ingest.\n- Result: the published `volatilityAlert` had zero variation: {c['volatilityDistribution']}. In all 3 eligible HIGH-PREPARE folds, adding it produced exactly dAUC=0, dAP=0 and log-loss improvement=0. Fixed KEEP gate required mean dAUC>=0.01 plus positive AP/calibration and therefore fails -> `TESTED_REJECTED`.\n- Interpretation: the current published alert cannot improve this routing context in the observed chronology. Do not rescue it by changing alert thresholds or synthesizing a new volatility feature under the same hypothesis. This result does not claim all volatility information is useless; it only rejects this field/formulation.\n- Seed/no-regression: 5/5 PASS. Trainer refresh was attempted once and failed before writing a new artifact because its frozen matched-gap training array was empty (`Expected 2D array, got 1D array []`); latest valid trainer snapshot therefore remains 2026-08-27 19:43, Acquisition Spearman 0.7364 / Preservation 0.5879, frozen Echtgeld exactly 8. No retry or live-data expansion was performed.\n- R4 champion unchanged; live R3-S/R3.1 and 8781 unchanged.\n- Artifact: `{ART}`; preregistration: `{PRE}`; both registries updated.\n- Next distinct gap: choose a semantically different naturally-supported information/routing source, or await genuinely new independent chronology for a frozen predeclared replication.\n'''
    PROG.write_text(progress,encoding='utf-8')
    block=f'''\n\n## 2026-08-28 09:xx — PUBLIC VOLATILITY REGIME × HIGH-PREPARE TAIL ROUTING V1 — REJECTED\n- Exactly one novel bounded test: `{TEST}`. Preregistered before execution after both-registry + semantic duplicate audit.\n- Semantic novelty: isolated the existing published `volatilityAlert` as a single regime-information source above the already-kept HIGH-PREPARE weak-quote tail stack; unlike prior raw-public soup, basis/reference routing, or Predict-confidence routing.\n- Layer assignment: volatilityAlert = INFORMATION/REGIME CONTEXT; weak quote = retained INFORMATION; placement readiness = PREPARE BELIEF; Predict/strike = FORMATION BELIEF; portfolio/payoff = LOGIC. `NOT_ACTION_AUTHORITY`.\n- Cohort: {c['markets']} ordinary Target markets / {c['rows']} Formation REPAIR rows (60-300s), strict-past public lag median {c['medianPublicLagMs']:.0f}ms / max {c['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED; no Echtgeld fit/ingest.\n- `volatilityAlert` was constant in the entire eligible cohort: {c['volatilityDistribution']}. Across all 3 HIGH-PREPARE folds, dAUC=dAP=log-loss improvement=0. The preregistered mean dAUC>=0.01 + positive AP/calibration KEEP rule fails -> `TESTED_REJECTED`.\n- Do not change alert thresholds or invent a derived-volatility variant to rescue this test. This only rejects the current published alert/formulation.\n- Seed/no-regression 5/5 PASS. Trainer refresh attempted once but failed with an empty frozen matched-gap training array before any new artifact; latest valid snapshot remains 2026-08-27 19:43 (Acq 0.7364 / Pres 0.5879, frozen Echtgeld exactly 8). No retry/new Echtgeld scan.\n- R4 champion unchanged; live R3-S/R3.1 and 8781 untouched.\n- Artifact `{ART}`; prereg `{PRE}`; information progress V34; both registries updated.\n- Next: semantically different naturally-supported information/routing gap, or genuinely new independent chronology for a predeclared replication.\n'''
    with HAND.open('a',encoding='utf-8') as f: f.write(block)
    print(json.dumps({'ok':True,'testId':TEST,'status':a['status'],'registryExperimentCount':sum(x.get('testId')==TEST for x in json.loads(REG.read_text(encoding='utf-8')).get('experiments',[])),'novelCount':sum(x.get('testId')==TEST for x in json.loads(NOV.read_text(encoding='utf-8')).get('tests',[])),'progress':str(PROG.relative_to(ROOT)).replace('\\','/')},ensure_ascii=False))

if __name__=='__main__': main()
