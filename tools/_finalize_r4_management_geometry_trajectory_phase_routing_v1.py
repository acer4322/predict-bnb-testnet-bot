from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
test_id='R4_MANAGEMENT_GEOMETRY_TRAJECTORY_PHASE_ROUTING_V1_20260828_0038'
entry={
  'testId':test_id,
  'testedAt':'2026-08-28T00:39:00+08:00',
  'status':'TESTED_KEEP_SIGNAL',
  'domain':'R4',
  'semanticAxis':'MANAGEMENT / GEOMETRY_TRAJECTORY_PHASE_ROUTING',
  'semanticKeys':['realized geometry trajectory','early management 120-180s','late management 60-120s','realistic-HFT phase routing','local lifecycle quality','not action authority'],
  'hypothesis':'Test whether the already-defined strict-past geometry-trajectory context has phase-specific incremental value for local Management lifecycle quality across independent realistic-HFT chronologies, without retraining/normalizing the signal or changing manager actions.',
  'artifact':'data/research/r4_v0/hourly/r4_management_geometry_trajectory_phase_routing_v1.json',
  'preregistration':'data/research/r4_v0/hourly/r4_management_geometry_trajectory_phase_routing_v1_preregistered.json',
  'tool':'tools/test_r4_management_geometry_trajectory_phase_routing_v1.py',
  'decision':'TESTED_KEEP_SIGNAL',
  'actionAuthority':False,
  'primaryResult':{
    'earlyEligibleComparisons':6,'earlyRepresentedCohorts':2,'earlyMeanDeltaAuc':0.08506007564370577,'earlyMeanDeltaAp':0.036119851716540856,'earlyMeanLogLossImprovement':0.05932880085081155,'earlyWorstDeltaAuc':-0.0006435006435006052,'earlyNonNegativeAucFraction':0.8333333333333334,
    'lateEligibleComparisons':6,'lateRepresentedCohorts':2,'lateMeanDeltaAuc':-0.04599734566213113,'lateMeanDeltaAp':-0.15149842842074132,'lateMeanLogLossImprovement':0.030679301541548836,'lateWorstDeltaAuc':-0.1293103448275862,'lateNonNegativeAucFraction':0.3333333333333333,
    'seedNoRegression5of5':True,'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT'
  },
  'summary':'Phase-routing resolves the prior broad HFT instability without changing the trajectory signal: EARLY_MANAGEMENT 120-180s passes the fixed routing gate (mean dAUC +0.0851, dAP +0.0361, log-loss improvement +0.0593; worst dAUC -0.00064), while LATE_MANAGEMENT 60-120s is clearly negative (mean dAUC -0.0460, dAP -0.1515). Keep raw geometry trajectory only as early-Management lifecycle-quality context; no action authority.',
  'retestAllowed':False,
  'nextDistinct':'Do not normalize or threshold the trajectory signal to rescue late Management. Highest-value next gap is to test whether the early-only trajectory context remains stable on a new independent realistic-HFT chronology or whether a different role-separated early Management information source adds broad calibration/tail value; action authority remains separate.'
}
for rel in ['data/research/r4_v0/r4_hourly_experiment_registry_v1.json','data/research/hourly_novel_test_registry_v1.json']:
    p=ROOT/rel; d=json.loads(p.read_text(encoding='utf-8')); arr=d.setdefault('tests',[])
    if not any((x.get('testId')==test_id) for x in arr if isinstance(x,dict)):
        arr.append(entry)
        p.write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'ok':True,'testId':test_id},ensure_ascii=False))
