from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
HAND=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
ART='data/research/r4_v0/hourly/r4_upside_retention_after_durable_base_v1_20260826_183728.json'
EID='R4_UPSIDE_RETENTION_AFTER_DURABLE_BASE_V1_20260826_1837'
r=json.loads(REG.read_text(encoding='utf-8'))
if not any((x.get('experimentId') or x.get('cycleId'))==EID for x in r.get('experiments',[])):
 r['experiments'].append({
  'experimentId':EID,'testedAt':'2026-08-26T18:37:28+08:00','type':'UPSIDE_RETENTION_AFTER_DURABLE_BASE','status':'REJECTED',
  'semanticKeys':['durable-base upside retention','post-base favorable surplus','relation-aware tranche upside noninferiority','execution-stress payoff asymmetry'],
  'candidate':'Evaluate whether the frozen relation-aware reserve-capped tranche retains favorable payoff asymmetry after 15s durable-base formation on genuine non-live HFT BASE_BREAK histories. No action semantics changed.',
  'artifact':ART,'candidateTool':'tools/test_r4_upside_retention_after_durable_base_v1.py',
  'primaryResult':{'normalBothDurable15Histories':52,'normalPeakUpsideRetention':1.1550925925925926,'normalTerminalUpsideRetention':1.232841064341943,'normalRelapseBaseline':1.0,'normalRelapseOverlay':0.8846153846153846,'weakDropPeakUpsideRetention':0.7870658970658972,'weakDropTerminalUpsideRetention':0.8477454780361755,'weakPartialPeakUpsideRetention':1.3070593330446088,'weakPartialRelapseBaseline':0.9411764705882353,'weakPartialRelapseOverlay':1.0,'makerDropPeakUpsideRetention':0.953757225433526,'makerDropTerminalUpsideRetention':1.2373623057190293,'seedNoRegression5of5':True},
  'conclusion':'Normal HFT and maker-drop stress retain or improve post-durable upside, so the tranche does not generally erase favorable asymmetry. However the preregistered >=80% upside-retention/no-relapse gate failed under weak-side drop and weak-side partial execution stress. Reject this evaluation hypothesis as execution-robust evidence; do not tune the floor boundary or noninferiority margin around these cohorts.',
  'retestAllowed':False,
  'nextDistinct':'Model a weak-side-realization-conditioned phase transition between LOCK_BASE and RE-EXPAND_SURPLUS using strict-past confirmed realization state, without changing the frozen floor=0 tranche boundary. First test whether delayed re-expansion after durable base restores upside specifically under weak-side execution degradation while preserving floor persistence.'})
 REG.write_text(json.dumps(r,indent=2),encoding='utf-8')
append='''\n\n## 2026-08-26 18:37 — UPSIDE RETENTION AFTER DURABLE BASE V1 — REJECTED\n- Canonical/registry duplicate audit completed first. This is distinct from the 17:42 durable-base lifecycle KEEP_SIGNAL and 18:27 weak-side build-dominance teacher: no control semantics or model threshold changed; this cycle evaluates post-durable favorable payoff asymmetry only.\n- Target refresh command was blocked by the upstream safety layer, so the cycle used the latest already-written ordinary Target snapshot (500 markets, 2026-08-16 SEALED) plus the previously audited genuine non-live HFT BASE_BREAK support cohort. Frozen Echtgeld calibration remained exactly the accepted 8 markets; no new Echtgeld scan/ingestion.\n- Fixed preregistered non-inferiority gate: median post-durable peak and terminal upside retention >=80%, no increase in post-durable floor-relapse rate; normal plus at least two eligible frozen-live-derived execution stresses must pass. No sweep.\n- Normal genuine-HFT: 79 active histories / 52 both-durable15. Peak upside 38.88 -> 44.91 (115.5% retention), terminal upside 27.67 -> 34.11 (123.3%), relapse 100% -> 88.46%; PASS.\n- WEAK_DROP_ALTERNATE: peak retention 78.71%, terminal 84.77%, relapse unchanged 100%; FAIL on peak retention.\n- WEAK_PARTIAL_HALF_ALTERNATE: peak retention 130.71%, terminal 114.04%, but relapse 94.12% -> 100%; FAIL on relapse.\n- MAKER_DROP_EVERY5: peak retention 95.38%, terminal 123.74%, relapse 100% -> 92%; PASS.\n- Decision: REJECTED under the fixed execution-robustness gate. The tranche does not generally kill upside; the remaining failure is specifically the weak-side-realization-degraded regime, where floor protection and favorable surplus retention become path-dependent. Do not tune the floor=0 boundary or the 80% margin to rescue.\n- Seed/no-regression: 5/5 PASS. Champion/live R3/R3.1/8781 unchanged.\n- Artifact: `data/research/r4_v0/hourly/r4_upside_retention_after_durable_base_v1_20260826_183728.json`.\n- Next distinct hypothesis: weak-side-realization-conditioned phase transition `LOCK_BASE -> RE_EXPAND_SURPLUS`. After a 15s durable base is formed, delay favorable-side re-expansion until strict-past confirmed weak-side realization is healthy; evaluate whether this restores upside under weak-side execution degradation without increasing floor relapse. Keep the frozen floor=0 tranche unchanged.\n'''
h=HAND.read_text(encoding='utf-8')
if EID not in h:
 HAND.write_text(h+append+f'\n<!-- {EID} -->\n',encoding='utf-8')
print(json.dumps({'ok':True,'experimentId':EID,'registryCount':len(r.get('experiments',[])),'handoffUpdated':True}))
