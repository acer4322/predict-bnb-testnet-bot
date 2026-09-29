from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HP=ROOT/'data'/'research'/'flash_sandbox_hourly_handoff_v1.json'
LP=ROOT/'data'/'research'/'flash_sandbox_research_log_20260819_coordination_fresh.json'
h=json.loads(HP.read_text(encoding='utf-8'))
now=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
h['updatedAt']=now
h['hourlyDecision']='FRESH_COORDINATION_19_MARKETS_HANDOFF_SAME_BIAS_PERSISTS_STABLE_R1_TAIL_FAILS'
for x in h.get('items',[]):
    if x.get('experimentId')=='STABLE_DIRECTIONAL_TOLERANCE_V1':
        x['state']='OBSERVE'
        x['settledMarkets']=31
        x['lastResult']={
          'completeSettledMarkets':31,'flashPnlUsdt':-677.3159340547957,'basePnlSameMarketsUsdt':-186.1081214312248,
          'deltaVsBaseUsdt':-491.2078126235708,'positiveMarkets':2,'positiveRate':0.06451612903225806,
          'betterVsBase':11,'worseVsBase':20,'medianDeltaUsdt':-3.4003921568627575,
          'worstMarketPnlUsdt':-210.871094513071,'makerPairedEdgePerShare':0.0180701754385965,
          'makerAbsNetMedian':18.0,'makerAbsNetMax':324.0,'classification':'OBSERVE_FAILS_GRADUATION_TAIL_BLOWUP_REPEATED',
          'interpretation':'Exact frozen R1 remains OBSERVE. Fresh complete cohort expanded to 31 markets; positive rate 6.45%, aggregate delta vs Base -491.21 USDT. Pair edge remains positive aggregate, but repeated residual/tail losses persist including market 1484861 -152.66 PnL with 306-share Maker residual. No tuning/rescue.'}
        x['nextHourDecision']='Keep exact R1 frozen OBSERVE; continue forward tail audit only. Do not tune or promote.'
# refresh flash usage audit
fu=h.setdefault('flashUsageAudit',{})
fu['newStrategyVariantsCreatedThisCycle']=[]
fu['existingStrategyVariantsModifiedThisCycle']=[]
fu['collectionOnlyWorkThisCycle']=[
  'Canonical/frozen coordination contract freshness audit',
  'Frozen Maker-Taker coordination prospective evaluation through market_end_ms 1787108100000',
  'Fresh HANDOFF error-cluster descriptive V0 without retraining/tuning',
  'Frozen STABLE_DIRECTIONAL_TOLERANCE_V1:R1 forward/tail audit'
]
fu['FLASH_STRATEGY_RESEARCH_THIS_CYCLE']='NONE; no new 8785 strategy/revision created; work was frozen prospective evaluation and descriptive hidden-state diagnosis only'
fu['NO_NEW_FLASH_STRATEGY_REASON']='Fresh coordination confirms a broad HANDOFF SAME-bias but does not yet identify a deployable strict-past pending/resting-quote state. Same-cohort medians are descriptive and time-confounded; Stable R1 also continues failing graduation/tail gates.'
# new coordination section
h['freshMakerTakerCoordination']={
  'state':'PROSPECTIVE_FRESH_FORWARD_ACTIVE',
  'contract':'data/research/target_maker_taker_coordination_big_v1/forward_contract_v1.json',
  'freezeCutoffMarketEndMs':1787102400000,
  'evaluatedThroughMarketEndMs':1787108100000,
  'coverage':{'markets':19,'hazardStates':3028,'takerParents':114,'handoffStates':114,'handoffMarketsWithRows':10},
  'hazard':{
    '1s':{'auc':0.8018229817163064,'ap':0.1338556126539364,'logLoss':0.09347669454150104},
    '3s':{'auc':0.7895331416282048,'ap':0.26361841185500007,'logLoss':0.18501821712758434},
    '5s':{'auc':0.7675657835931216,'ap':0.3158256812065403,'logLoss':0.2537264786790029}},
  'side':{'balancedAccuracy':0.760625,'macroF1':0.7600374210649412,'truth':{'UP':64,'DOWN':50},'predicted':{'UP':63,'DOWN':51}},
  'effect':{'balancedAccuracy':0.7342229199372057,'macroF1':0.736678614097969,'truth':{'REPAIR_EFFECT':65,'ADD_EFFECT':49},'predicted':{'REPAIR_EFFECT':70,'ADD_EFFECT':44}},
  'handoff':{
    'balancedAccuracy':0.33108766233766235,'macroF1':0.252341539873079,
    'truth':{'PAUSE':35,'SAME':32,'OPP':25,'BOTH':22},'predicted':{'SAME':84,'BOTH':19,'PAUSE':9,'OPP':2},
    'perClassRecall':{'BOTH':0.36363636363636365,'OPP':0.0,'PAUSE':0.08571428571428572,'SAME':0.875},
    'nonSameMissedAsSame':56,'nonSameCorrect':11,
    'diagnosis':'HANDOFF remains the clear gap. Frozen student recognizes SAME well but maps 56 non-SAME states to SAME; OPP recall is 0 and PAUSE recall ~8.6%. Correct non-SAME examples are strongly late/extreme-state skewed, so same-cohort medians must not become thresholds.'},
  'hiddenStateV0':{
    'report':'data/research/target_maker_taker_coordination_big_v1/forward_handoff_error_clusters_v0.json',
    'retrained':False,'thresholdSweep':False,
    'candidateMissingState':'side-specific pending/resting Maker quote ownership, placement/refill/reprice age, queue/front-depth and partial-fill path around Taker completion',
    'guard':'Candidate only. Current fresh CSV observes fills/book and cannot prove private pending-order ownership; do not infer ground truth or tune thresholds from this cohort.'},
  'reports':['data/research/target_maker_taker_coordination_big_v1/forward_evaluation_v1.json','data/research/target_maker_taker_coordination_big_v1/forward_handoff_error_clusters_v0.json']
}
h['nextCyclePriority']='Continue frozen Maker-Taker coordination forward evaluation on only new finalized markets. For HANDOFF, prioritize a short strict-past descriptive join to inferred side-specific resting/placement/refill/reprice lifecycle around Taker completion; do not retrain/tune on this cohort. Keep STABLE_DIRECTIONAL_TOLERANCE_V1:R1 exact frozen OBSERVE.'
HP.write_text(json.dumps(h,ensure_ascii=False,indent=2),encoding='utf-8')
log={
 'timestamp':now,'researchOnly':True,
 'PRE_EXISTING_KNOWLEDGE':['Frozen Maker-Taker coordination cutoff is market_end_ms 1787102400000; artifacts may not be retrained/tuned hourly.','HANDOFF was already the weakest historical coordination task.','STABLE_DIRECTIONAL_TOLERANCE_V1:R1 is frozen OBSERVE and must meet >=35% positive settled plus nonnegative pair edge and controlled tail to graduate.'],
 'NEW_THIS_HOUR':['Fresh coordination expanded from 13 to 19 markets and from 37 to 114 Taker/handoff states, evaluated through market_end_ms 1787108100000.','Hazard transfer remains useful: AUC 1s/3s/5s = 0.8018/0.7895/0.7676. SIDE balanced accuracy=0.7606; EFFECT=0.7342.','HANDOFF remains poor: balanced accuracy=0.3311, macro-F1=0.2523, predictions SAME 84/114. Per-class recall SAME=.875, BOTH=.364, PAUSE=.086, OPP=0.','Descriptive error audit found 56 non-SAME states misclassified SAME versus only 11 correctly classified non-SAME. Correct non-SAME cases are heavily late/extreme-state skewed, so no threshold is justified.','Stable R1 complete cohort reached 31 markets: 2 positive (6.45%), PnL -677.32 vs Base -186.11, delta -491.21; aggregate pair edge remains +0.0181/share but residual/tail gate repeatedly fails.'],
 'INFERENCE_PROXY':['Side-specific pending/resting quote ownership and queue/partial-fill path remain candidate hidden states; current evidence does not directly observe private pending Target orders.'],
 'NO_STRATEGY_CHANGE':True,'NO_RETRAIN_OR_TUNE':True,'NO_LIVE_OR_8784_CHANGE':True
}
LP.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'updatedHandoff':str(HP),'log':str(LP),'updatedAt':now},ensure_ascii=False,indent=2))
