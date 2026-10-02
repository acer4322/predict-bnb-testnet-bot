import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

REG=Path('data/research/hourly_novel_test_registry_v1.json')
REPORT=Path('data/research/hourly_novel_tests/hft_r2_opposing_repair_obligation_netting_v1_report.json')
HANDOFF=Path('BTC5M_PROJECT_HANDOFF.md')
CYCLE=Path('data/research/hourly_supervisor_hft_forward_20260824_0500.json')

r=json.loads(REG.read_text(encoding='utf-8'))
report=json.loads(REPORT.read_text(encoding='utf-8'))
test_id=report['testId']
if not any(x.get('testId')==test_id for x in r['tests']):
    r['tests'].append({
      'testId': test_id,
      'testedAt': '2026-08-24T05:00:00+08:00',
      'axis': report['axis'],
      'semanticKeys': ['opposing repair obligations','repair ownership conflict','obligation netting','late fill opposite repair','single economic repair owner'],
      'hypothesis': 'When opposite-direction repair obligations coexist, reconcile and algebraically net them before issuing a new child; preserve at most one economic repair owner and execute only the latest net residual.',
      'cohort/source': 'opened structural execution-lifecycle fault scenarios; no sealed outcomes; no PnL claim',
      'primaryResult': report['primaryResult'],
      'status': report['status'],
      'artifact': str(REPORT).replace('\\','/'),
      'preregisteredArtifact': 'data/research/hourly_novel_tests/hft_r2_opposing_repair_obligation_netting_v1_preregistered.json',
      'tool/command': 'python tools/test_hft_r2_opposing_repair_obligation_netting_v1.py',
      'conclusion': report['conclusion'],
      'retestAllowed': False,
      'retestReason': 'Only NEW_INDEPENDENT_CHRONOLOGY, MATERIAL_DATA_PIPELINE_FIX, or PREDECLARED_REPLICATION may reopen this axis.'
    })
REG.write_text(json.dumps(r,indent=2,ensure_ascii=False),encoding='utf-8')

cycle={
  'timestamp':'2026-08-24T05:00:00+08:00',
  'officialHftForward':{
    'matchedSettledMarkets':344,
    'r2Pnl':-2142.2053540000006,
    'r2PositiveMarkets':120,
    'r2PositiveRate':0.3488372093023256,
    'cap100Pnl':-982.5989999999999,
    'cap100PositiveMarkets':135,
    'cap100PositiveRate':0.39244186046511625,
    'capMinusR2':1159.6063540000007,
    'cap100MeanCapital':71.34961918604651,
    'cap100MaxCapital':103.0284,
    'performanceRole':'BACKGROUND_CONTEXT_NOT_AUTONOMOUS_REPAIR_GRADUATION'
  },
  'novelTest':{
    'testId':test_id,
    'status':report['status'],
    'primaryResult':report['primaryResult'],
    'artifact':str(REPORT).replace('\\','/'),
    'preregisteredArtifact':'data/research/hourly_novel_tests/hft_r2_opposing_repair_obligation_netting_v1_preregistered.json',
    'novelty':'No dedicated prior autonomous-repair test of opposing repair-obligation conflict netting was found; prior work covered remainder ownership/target revision/late-fill reconciliation separately.'
  },
  'runtime':'No new STORAGE_STALLED evidence established in this cycle; official HFT ledger/analyzer advanced to matched344. Direct multi-port health probe was unavailable, so no stronger runtime-freshness claim is made.',
  'supervisor':'No new independent supervisor_r2_75+ ordinary OUR-state chronology found; STUDENT_STATE_ACT_ADAPTER remains OBSERVE; sealed cohorts untouched.'
}
CYCLE.write_text(json.dumps(cycle,indent=2,ensure_ascii=False),encoding='utf-8')

append='''\n\n### 2026-08-24 05:00 — Autonomous repair KEEP: opposing repair-obligation netting\n- Current research priority remains R2 autonomous recovery after execution faults; positive PnL is background context rather than the main graduation criterion. Official HFT Forward PAPER advanced to **344** matched settled markets: R2 **-$2,142.205354**, 120/344=34.88% positive; CAP100 **-$982.5990**, 135/344=39.24%; CAP100-R2 **+$1,159.606354**. CAP100 mean/max capital ~$71.35/$103.0284; the hard-$100 claim remains disproven.\n- Semantic de-dup: prior work separately covered remainder ownership, target-revision release, uncertain-order quarantine, and late-fill reconciliation, but no dedicated Novel Test was found for **simultaneous/opposing repair-obligation conflict netting**. A candidate `BOUNDED_ACTIVE_THEN_PASSIVE` test exists and was rejected previously, so this cycle deliberately did not repeat that axis.\n- New preregistered test `HFT_R2_OPPOSING_REPAIR_OBLIGATION_NETTING_V1`: when an opposite-direction confirmed repair obligation appears while another economic repair owner is unresolved, quarantine creation of a second child, wait for terminal evidence on the current owner, reconcile confirmed obligations, algebraically net UP vs DOWN responsibility, and execute only the latest net residual.\n- Structural result: **3/3 scenarios passed**; exact net residual 3/3; reconcile-before-new-child 3/3; stale gross repair quantity suppressed 3/3; **dualEconomicOwnerCount=0**; terminal unresolved residual max=0; lifecycle violations=0. Examples include DOWN12 + opposite UP7 -> single DOWN5 obligation, and an active DOWN10 owner receiving UP6 before terminal ACK -> no dual child, then single DOWN4 after terminal/reconcile. Status `TESTED_KEEP_SIGNAL`.\n- Interpretation: this adds a distinct autonomous-repair primitive: **repair responsibilities can reconcile/net against each other instead of spawning mutually-cancelling repair children**. This is structural/lifecycle evidence only; no PnL or promotion claim and no frozen/live policy was modified. Artifact: `data/research/hourly_novel_tests/hft_r2_opposing_repair_obligation_netting_v1_report.json`; preregistration adjacent; registry updated.\n- Runtime: official HFT ledger/analyzer advanced to matched344, so no STORAGE_STALLED evidence was established this cycle. A direct multi-port health snapshot was not available; no stronger runtime-freshness claim is made. Echtgeld untouched.\n- Supervisor search still finds no new independent `supervisor_r2_75+` ordinary OUR-state chronology; STUDENT_STATE_ACT_ADAPTER remains OBSERVE; final75-99 and 2026-08-16 remain SEALED.\n'''
text=HANDOFF.read_text(encoding='utf-8')
if '### 2026-08-24 05:00 — Autonomous repair KEEP: opposing repair-obligation netting' not in text:
    HANDOFF.write_text(text+append,encoding='utf-8')
print(json.dumps({'registryUpdated':True,'cycle':str(CYCLE),'handoffUpdated':True,'testId':test_id,'status':report['status']}))
