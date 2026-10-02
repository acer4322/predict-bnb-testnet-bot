import json
from pathlib import Path

registry_path=Path('data/research/hourly_novel_test_registry_v1.json')
reg=json.loads(registry_path.read_text(encoding='utf-8'))
entry={
  'testId':'HFT_R2_SELF_TRADE_PREVENTION_REPAIR_RECONCILE_V1',
  'testedAt':'2026-08-25T06:58:00+08:00',
  'axis':'R2_AUTONOMOUS_REPAIR_SELF_TRADE_PREVENTION_RECONCILE',
  'semanticKeys':['self-trade prevention during repair','STP cancel newest repair child','STP cancel oldest conflicting own order','prevented self-match quantity is not fill','authoritative external fill only remainder'],
  'hypothesis':'When venue self-trade prevention intervenes during an R2 repair, prevented self-match quantity must never be counted as execution fill; reconcile which own order lost venue ownership, preserve exactly one economic repair obligation, and resume passive-first on the exact remainder from authoritative external fills only.',
  'cohort/source':'three deterministic opened structural execution-lifecycle scenarios; no sealed graduation data; no Target future action/winner/PnL',
  'primaryResult':{'scenarios':3,'passed':3,'preventedSelfMatchAppliedQty':0.0,'duplicateOwnerCount':0,'overRepairQty':0.0,'lifecycleViolationCount':0,'exactMixedCaseRemainderBeforeFreshPassive':7.0},
  'status':'TESTED_KEEP_SIGNAL',
  'artifact':'data/research/hourly_novel_tests/hft_r2_self_trade_prevention_repair_reconcile_v1_report.json',
  'preregisteredArtifact':'data/research/hourly_novel_tests/hft_r2_self_trade_prevention_repair_reconcile_v1_preregistered.json',
  'tool/command':'python tools/test_hft_r2_self_trade_prevention_repair_reconcile_v1.py',
  'conclusion':'Venue STP prevented quantity is non-fill evidence. R2 preserves/reconciles one economic repair obligation, distinguishes which own order lost venue ownership, sizes fresh passive repair only from authoritative external fills, and avoids duplicate ownership/over-repair. Structural/lifecycle evidence only.',
  'retestAllowed':False,
  'retestReason':'Only NEW_INDEPENDENT_CHRONOLOGY, MATERIAL_DATA_PIPELINE_FIX, or PREDECLARED_REPLICATION may reopen this axis.'
}
entries=reg.get('tests')
if entries is None:
    entries=reg.setdefault('entries',[])
if not any(x.get('testId')==entry['testId'] for x in entries):
    entries.append(entry)
registry_path.write_text(json.dumps(reg,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

cycle={
  'cycleAt':'2026-08-25T06:58:00+08:00','researchOnly':True,
  'officialHft':{
    'matched':566,
    'R2':{'pnlUsdt':-3254.8429540000006,'positive':198,'positiveRate':198/566,'meanCapitalUsdt':124.20671899999999,'maxCapitalUsdt':402.84},
    'CAP100':{'pnlUsdt':-1355.8266000000003,'positive':222,'positiveRate':222/566,'meanCapitalUsdt':72.80972897526502,'maxCapitalUsdt':112.4532},
    'relativeCap100MinusR2':1899.0163540000003,
    'freshSince555':{'markets':11,'r2PnlUsdt':-195.17400000000043,'r2Positive':2,'cap100PnlUsdt':-65.95920000000021,'cap100Positive':3,'relativeDeltaUsdt':129.21480000000022},
    'capitalSafety':'CAP100 hard-$100 disproven; max $112.4532; known breaches remain material.'
  },
  'runtime':{
    '8783':'ONLINE/READY',
    '8778':'LIVE/fresh; websocket LIVE; durable lastReceivedMs advancing; no STORAGE_STALLED evidence',
    '8784':'ACTIVE/sourceReady/bookReady; legacy diagnostic only',
    '8786':'ACTIVE/sourceReady/bookReady; legacy diagnostic only',
    '8788':'ACTIVE/ok; current HFT forward job running; no lastError'
  },
  'novelTest':entry,
  'supervisor':'No new independent supervisor_r2_75+ ordinary OUR-state chronology found; STUDENT_STATE_ACT_ADAPTER remains OBSERVE; starts0-74 not recycled; final75-99 and 2026-08-16 remain SEALED.'
}
Path('data/research/hourly_supervisor_hft_forward_20260825_0658.json').write_text(json.dumps(cycle,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

handoff=Path('BTC5M_PROJECT_HANDOFF.md')
text='''\n\n## 2026-08-25 06:58 TST — Official HFT matched566 + self-trade-prevention autonomous repair KEEP\n- Research-only; R2 autonomous self-repair remains primary objective. Official HFT Forward PAPER advanced 555 -> **566** matched settled markets. R2 **-$3,254.8430**, 198/566=**34.98%** positive, mean/max capital **$124.2067/$402.84**. CAP100 **-$1,355.8266**, 222/566=**39.22%** positive, mean/max capital **$72.8097/$112.4532**. CAP100-R2 cumulative relative PnL **+$1,899.0164**. Fresh11: R2 **-$195.1740** (2/11 positive), CAP100 **-$65.9592** (3/11), relative **+$129.2148**. PnL remains background monitoring, not autonomous-repair graduation.\n- Runtime read-only: 8778 LIVE/fresh with websocket LIVE and durable lastReceivedMs advancing; 8783 ONLINE/READY; 8784/8786 ACTIVE + sourceReady/bookReady but remain legacy diagnostic only; 8788 ACTIVE/ok with a current HFT forward job and no lastError. No STORAGE_STALLED evidence; Echtgeld untouched.\n- Novel test `HFT_R2_SELF_TRADE_PREVENTION_REPAIR_RECONCILE_V1` was preregistered after semantic de-dup. Registry/canonical/research searches found no autonomous-repair test for venue self-trade prevention (STP) cancel/reduce semantics during repair. Distinct from generic repair submit reject, cancel reject/timeout, active IOC partial recovery, and opposing repair-obligation netting because STP prevented quantity is matching-engine non-fill evidence caused by conflict with our own resting liquidity.\n- Method: STP-prevented quantity never updates confirmed execution inventory; reconcile which own order the venue canceled/reduced; preserve exactly one economic repair obligation; compute unresolved quantity from authoritative external fills only; fresh passive repair regains first authority before any bounded active fallback.\n- Structural result: **3/3 scenarios passed -> TESTED_KEEP_SIGNAL**. preventedSelfMatchAppliedQty=0; duplicateOwnerCount=0; overRepairQty=0; lifecycleViolationCount=0. In the mixed case a true external +5 fill followed by STP preventing 7 shares left an exact **7-share remainder**; the prevented 7 were not miscounted as fills, and fresh passive repaired exactly that remainder. Structural/lifecycle evidence only; no PnL/promotion claim and no frozen/live policy change. Artifacts: `data/research/hourly_novel_tests/hft_r2_self_trade_prevention_repair_reconcile_v1_preregistered.json`, `data/research/hourly_novel_tests/hft_r2_self_trade_prevention_repair_reconcile_v1_report.json`; tool `tools/test_hft_r2_self_trade_prevention_repair_reconcile_v1.py`; registry updated.\n- Supervisor: fresh search found no new independent `supervisor_r2_75+` ordinary OUR-state chronology. `STUDENT_STATE_ACT_ADAPTER` remains OBSERVE; starts0-74 not recycled; final75-99 and 2026-08-16 remain SEALED.\n- Cycle artifact: `data/research/hourly_supervisor_hft_forward_20260825_0658.json`.\n'''
with handoff.open('a',encoding='utf-8') as f:f.write(text)
print('finalized')
