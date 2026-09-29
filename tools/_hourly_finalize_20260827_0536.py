from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
TEST='R4_QUOTE_ECON_PREPARE_BUILD_ROUTE_ATTRIBUTION_V1_20260827_0536'
now=datetime.now(TZ).isoformat()
art_rel='data/research/r4_v0/hourly/r4_quote_econ_prepare_build_route_attribution_v1.json'
pre_rel='data/research/r4_v0/hourly/r4_quote_econ_prepare_build_route_attribution_v1_preregistered.json'
tool_rel='tools/test_r4_quote_econ_prepare_build_route_attribution_v1.py'
art=json.loads((ROOT/art_rel).read_text(encoding='utf-8'))
summary=art['routeSummary']
primary={
 'markets':art['coverage']['markets'],'rows':art['coverage']['rows'],'damageRate':art['coverage']['damageRate'],
 'medianPublicLagMs':art['coverage']['medianPublicLagMs'],'maxPublicLagMs':art['coverage']['maxPublicLagMs'],
 'prepareOnlyMeanDeltaAuc':summary['PREPARE_ONLY']['meanDeltaAuc'],'prepareOnlyMeanDeltaAp':summary['PREPARE_ONLY']['meanDeltaAp'],
 'prepareOnlyMeanLogLossImprovement':summary['PREPARE_ONLY']['meanLogLossImprovement'],'prepareOnlyFoldDeltaAuc':summary['PREPARE_ONLY']['foldDeltaAuc'],
 'buildOnlyMeanDeltaAuc':summary['BUILD_ONLY']['meanDeltaAuc'],'buildOnlyMeanDeltaAp':summary['BUILD_ONLY']['meanDeltaAp'],
 'buildOnlyMeanLogLossImprovement':summary['BUILD_ONLY']['meanLogLossImprovement'],'buildOnlyFoldDeltaAuc':summary['BUILD_ONLY']['foldDeltaAuc'],
 'bothHighMeanDeltaAuc':summary['BOTH_HIGH']['meanDeltaAuc'],'bothHighMeanDeltaAp':summary['BOTH_HIGH']['meanDeltaAp'],
 'bothHighMeanLogLossImprovement':summary['BOTH_HIGH']['meanLogLossImprovement'],'bothHighFoldDeltaAuc':summary['BOTH_HIGH']['foldDeltaAuc'],
 'qualifyingRoutes':art['qualifyingRoutes'],'attribution':art['attribution'],'seedNoRegression5of5':True,
 'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT','latestValidTrainerSnapshot':'2026-08-27T04:45:26.701297+08:00',
 'latestValidAcquisitionSpearman':0.6862917101519954,'latestValidPreservationSpearman':0.7986239271467139
}
sem=['quote economics route attribution','PREPARE-only belief regime','BUILD-only belief regime','parallel PREPARE BUILD interaction','15s Formation floor-tail damage','information not action authority']
conclusion=('TESTED_REJECTED. The prior HIGH-PREPARE quote-economics KEEP_SIGNAL does not admit a stable finer attribution to PREPARE-only, BUILD-only, or BOTH-high parallel-belief states. PREPARE-only has positive mean lift but one negative chronological fold; BUILD-only is near-zero/negative on average; BOTH-high is weak and worsens mean log-loss. Preserve quote economics as conditional INFORMATION only; do not inject it into a belief head or grant action authority.')
nextd=('Move to a semantically separate final-60s crossing/protection information-routing study. Test strict-past queue opportunity / quote economics only as protection context against a local late floor-tail/crossing-quality target; do not extend Formation PREPARE authority into 0-60s and do not create direct actions.')
# R4 registry
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'; reg=json.loads(p.read_text(encoding='utf-8'))
if not any(x.get('experimentId')==TEST or x.get('cycleId')==TEST for x in reg.get('experiments',[])):
 reg['experiments'].append({'experimentId':TEST,'testedAt':now,'type':'INFORMATION_SOURCE_ROUTE_ATTRIBUTION_PREPARE_BUILD','status':'TESTED_REJECTED','semanticKeys':sem,'candidate':'Attribute strict-past weak-side quote-economics tail information among mutually exclusive PREPARE-only, BUILD-only, and BOTH-high Formation belief regimes; information routing only.','artifact':art_rel,'preregisteredArtifact':pre_rel,'candidateTool':tool_rel,'primaryResult':primary,'conclusion':conclusion,'promotion':'NONE_NOT_ACTION_AUTHORITY','retestAllowed':False,'nextDistinct':nextd})
 p.write_text(json.dumps(reg,ensure_ascii=False,indent=2),encoding='utf-8')
# global novelty ledger
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json'; nr=json.loads(p2.read_text(encoding='utf-8'))
if not any(x.get('testId')==TEST for x in nr.get('tests',[])):
 nr['tests'].append({'testId':TEST,'testedAt':now,'axis':'R4_INFORMATION_SOURCE_ROUTE_ATTRIBUTION_PREPARE_BUILD','semanticKeys':sem,'hypothesis':'The prior HIGH-PREPARE quote-economics tail signal may route specifically to PREPARE-only, BUILD-only, or their BOTH-high interaction rather than acting as a universal quote-price belief.','cohort':{'source':'ordinary Target BTC Formation + strict-past public_research_archive_v1','markets':art['coverage']['markets'],'rows':art['coverage']['rows'],'phase':'60-300s','sealed20260816':True,'publicQuoteMaxLagMs':1500},'primaryResult':primary,'status':'TESTED_REJECTED','artifact':art_rel,'preregisteredArtifact':pre_rel,'tool':tool_rel,'conclusion':conclusion,'retestAllowed':False})
 p2.write_text(json.dumps(nr,ensure_ascii=False,indent=2),encoding='utf-8')
# handoff append
hp=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'; h=hp.read_text(encoding='utf-8')
marker='<!-- R4_QUOTE_ECON_PREPARE_BUILD_ROUTE_ATTRIBUTION_V1_20260827_0536 -->'
section=f'''\n\n## 2026-08-27 05:xx — QUOTE ECONOMICS PREPARE/BUILD ROUTE ATTRIBUTION V1 — REJECTED\n- Semantic novelty: parent test only established quote-economics usefulness inside HIGH PREPARE. This test made PREPARE-only, BUILD-only, and BOTH-high belief regimes mutually exclusive and asked where the INFORMATION increment belongs; no action coupling.\n- Layer assignment: weak-side public quote economics = INFORMATION; placement readiness = PREPARE BELIEF; portable build score = BUILD BELIEF; portfolio/Predict/strike = LOGIC context. Output remains NOT_ACTION_AUTHORITY. Final 0-60s excluded.\n- Cohort: {art['coverage']['markets']} ordinary Target BTC markets / {art['coverage']['rows']} 60-300s Formation REPAIR rows; strict-past public quote median lag {art['coverage']['medianPublicLagMs']:.0f}ms, max {art['coverage']['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED.\n- PREPARE_ONLY: mean dAUC {summary['PREPARE_ONLY']['meanDeltaAuc']:+.4f}, mean dAP {summary['PREPARE_ONLY']['meanDeltaAp']:+.4f}, mean log-loss improvement {summary['PREPARE_ONLY']['meanLogLossImprovement']:+.4f}; fold dAUC {[round(x,4) for x in summary['PREPARE_ONLY']['foldDeltaAuc']]}. Fails fixed all-fold-nonnegative rule.\n- BUILD_ONLY: mean dAUC {summary['BUILD_ONLY']['meanDeltaAuc']:+.4f}; fold dAUC {[round(x,4) for x in summary['BUILD_ONLY']['foldDeltaAuc']]}; fails.\n- BOTH_HIGH: mean dAUC {summary['BOTH_HIGH']['meanDeltaAuc']:+.4f}, mean log-loss improvement {summary['BOTH_HIGH']['meanLogLossImprovement']:+.4f}; fold dAUC {[round(x,4) for x in summary['BOTH_HIGH']['foldDeltaAuc']]}; fails.\n- Decision: TESTED_REJECTED. No route qualifies. Preserve the prior HIGH-PREPARE result only as coarse conditional INFORMATION routing; do not attach quote economics to PREPARE/BUILD heads, threshold it, or grant order/size/owner/MPQ authority.\n- Trainer refresh was attempted once but timed out without a new artifact; per timeout policy it was not relaunched. Latest valid 04:45 snapshot remains acquisition Spearman 0.6863 / preservation 0.7986, frozen Echtgeld exactly 8.\n- Seed/no-regression: 5/5 PASS. Champion unchanged; live R3-S/R3.1/8781 untouched.\n- Artifact: `{art_rel}`; preregistration: `{pre_rel}`; tool: `{tool_rel}`. Both registries updated.\n- Next distinct gap: final-60s crossing/protection information routing. Treat queue opportunity / quote economics as protection context only and use a local late tail/crossing-quality target; do not carry Formation PREPARE authority into 0-60s.\n{marker}\n'''
if marker not in h: hp.write_text(h+section,encoding='utf-8')
# progress V6 from V5
v5=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v5.md';v6=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v6.md'
base=v5.read_text(encoding='utf-8')
prog='''\n\n## Quote economics PREPARE vs BUILD route attribution — REJECTED\nA finer routing test split the prior HIGH PREPARE support into mutually exclusive PREPARE-only, BUILD-only, and BOTH-high parallel-belief regimes on the same local 15s Formation floor-tail target. Quote economics stayed INFORMATION; both heads stayed BELIEF only.\n\nPREPARE-only had positive mean lift (dAUC about +0.0296, dAP +0.0284, mean log-loss improvement +0.0460) but one of three chronological folds was negative in AUC, so it failed the fixed stability rule. BUILD-only was near zero/negative on average (mean dAUC about -0.0024). BOTH-high was weak (mean dAUC about +0.0042) and worsened mean log-loss. No route qualified.\n\nInterpretation: retain the prior HIGH-PREPARE result only as coarse conditional INFORMATION routing. There is not enough stable evidence to inject quote economics specifically into PREPARE, BUILD, or their interaction. No action authority.\n\nNext: move to a separate final-60s crossing/protection routing study; placement-readiness Formation authority remains withheld there.\n'''
v6.write_text(base+prog,encoding='utf-8')
print(json.dumps({'ok':True,'testId':TEST,'r4RegistryEntries':sum(1 for x in reg['experiments'] if x.get('experimentId')==TEST),'novelRegistryEntries':sum(1 for x in nr['tests'] if x.get('testId')==TEST),'handoffUpdated':marker in hp.read_text(encoding='utf-8'),'progressV6':v6.exists(),'status':art['status'],'seedNoRegression5of5':True},ensure_ascii=False))
