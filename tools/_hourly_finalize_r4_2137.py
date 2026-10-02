from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
HO=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
exp_id='R4_MARGINAL_RESERVE_SPEND_EFFICIENCY_V1_20260826_2137'
reg=json.loads(REG.read_text(encoding='utf-8'))
if not any(x.get('experimentId')==exp_id for x in reg.get('experiments',[])):
    reg.setdefault('experiments',[]).append({
      'experimentId':exp_id,
      'testedAt':'2026-08-26T21:37:47+08:00',
      'type':'MARGINAL_RESERVE_SPEND_EFFICIENCY',
      'status':'REJECTED',
      'semanticKeys':['marginal reserve spend efficiency','post-durable reserve economics','upside per reserve dollar','economic re-expansion gate'],
      'candidate':'Keep frozen relation-aware floor=0 tranche. After a 15s durable base is strictly observable, allow favorable/surplus-side reserve-spending fills only when immediate marginal favorable-upside gain / floor reserve spent >= 1.0.',
      'artifact':'data/research/r4_v0/hourly/r4_marginal_reserve_spend_efficiency_v1_20260826_213747.json',
      'candidateTool':'tools/test_r4_marginal_reserve_spend_efficiency_v1.py',
      'primaryResult':{
        'normalSupportHistories':23,'normalSupportEvents':60,'normalBlockedEvents':25,
        'normalDeltaMedianFinalFloor':0.0,'normalDeltaMedianPositiveDurationSec':8.533,
        'normalPeakUpsideRetention':1.003472222222222,'normalTerminalUpsideRetention':1.1520000000000015,
        'normalRelapseTranche':1.0,'normalRelapseEfficiency':0.9565217391304348,
        'normalMedianReserveSpendTranche':56.52,'normalMedianReserveSpendEfficiency':48.78,
        'weakPartialDeltaMedianFinalFloor':-1.763907894736846,
        'makerDropTerminalUpsideRetention':0.7944379629378413,
        'normalFractionEfficiencyBelow1':0.23333333333333334,
        'makerDropFractionEfficiencyBelow1':0.32,
        'seedNoRegression5of5':True
      },
      'conclusion':'REJECTED under the fixed execution-robustness gate. Dollar efficiency >=1 is more economically grounded than 1:1 share credit and improves normal-path duration/relapse without sacrificing upside, but it worsens median final floor under weak-partial execution and maker-drop terminal upside retention remains 79.44% (<80%). Do not tune the 1.0 economic boundary or retest this exact gate.',
      'retestAllowed':False,
      'nextDistinct':'Move from immediate action efficiency to reserve-spend sequencing / budget amortization: test whether several individually efficient favorable fills can cumulatively erode a durable base when weak-side realization lags. Use a strict-past rolling reserve-debt state (cumulative reserve spent since last confirmed weak-side base-deepening) with a natural reset on realized weak-side floor improvement, rather than another per-fill threshold.'
    })
REG.write_text(json.dumps(reg,indent=2,ensure_ascii=False),encoding='utf-8')
marker='R4_MARGINAL_RESERVE_SPEND_EFFICIENCY_V1_20260826_2137'
text=HO.read_text(encoding='utf-8')
if marker not in text:
    text += f'''\n\n## 2026-08-26 21:37 — MARGINAL RESERVE SPEND EFFICIENCY V1 — REJECTED\n- Refreshed 500 ordinary Target markets with 2026-08-16 SEALED: acquisition Spearman 0.7208, preservation 0.7622. Frozen Echtgeld calibration remained exactly the accepted 8 markets; no new live scan/ingestion.\n- Execution-environment finding: maker-drop stress raised the share of post-durable favorable reserve-spend events with immediate upside/reserve efficiency <1 from 23.33% normal to 32.00%, consistent with execution degradation making otherwise attractive re-expansion sequences less economically reliable. Frozen live data was used only to preserve stress dimensions, not to tune the boundary.\n- Candidate ({marker}): keep the frozen relation-aware `floor=0` tranche. After a strictly observable 15s durable base, favorable/surplus-side fills that spend reserve are permitted only when immediate `delta_upside / reserve_spent >= 1.0`. The 1.0 boundary is a natural dollar-for-dollar economic boundary; no sweep.\n- Normal genuine-HFT support: 23 histories / 60 events; 25 events blocked. Median final-floor delta 0.00; positive-floor duration +8.533s; peak/terminal upside retention 100.35% / 115.20%; relapse 100% -> 95.65%; reserve-spend median 56.52 -> 48.78; PASS.\n- WEAK_PARTIAL_HALF_ALTERNATE failed because median final floor worsened by -1.764 despite better duration/relapse and >100% upside retention. MAKER_DROP_EVERY5 failed because terminal upside retention was 79.44%, below the fixed 80% gate. WEAK_DROP_ALTERNATE had only 7 support histories and was ineligible.\n- Decision: `REJECTED`. Do not lower the 1.0 efficiency boundary or tune around these cohorts. Seed/no-regression 5/5 PASS; R4 champion unchanged; live R3-S/R3.1/8781 untouched.\n- Artifact: `data/research/r4_v0/hourly/r4_marginal_reserve_spend_efficiency_v1_20260826_213747.json`. Tool: `tools/test_r4_marginal_reserve_spend_efficiency_v1.py`.\n- Next distinct hypothesis: test **rolling reserve debt / spend sequencing** rather than another per-fill threshold. Several individually efficient favorable fills may cumulatively consume the durable base before weak-side realization catches up. Track strict-past cumulative reserve spent since the last confirmed weak-side floor improvement and reset debt only on realized base-deepening; evaluate whether this explains weak-partial/maker-drop failures without tuning to Echtgeld.\n'''
    HO.write_text(text,encoding='utf-8')
print(json.dumps({'ok':True,'registryCount':len(reg.get('experiments',[])),'handoffUpdated':marker in HO.read_text(encoding='utf-8')}))
