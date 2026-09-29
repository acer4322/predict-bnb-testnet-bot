from __future__ import annotations
import csv, hashlib, json, shutil, sqlite3, statistics, zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROV = ROOT / 'data/research/r4_v0/p0_provenance_v1'
OUT = ROOT / 'data/research/r4_v0/gpt6_target_our_system_synthesis_challenge_v1_20260906'
ZIP = ROOT / 'data/research/r4_v0/gpt6_target_our_system_synthesis_challenge_v1_20260906.zip'
OUT.mkdir(parents=True, exist_ok=True)

MIDS = [1945866,1945869,1945898,1945986,1946036,1946298,1946317,1946448,1946468,1946475,1946488,1946640,1946653,1946656,1946668,1946683,1946748,1946756,1946760,1946784,1946792,1946872,1946876,1946899]


def w(name: str, text: str):
    (OUT/name).write_text(text.strip()+"\n", encoding='utf-8')


def cp(src: Path, dst: str):
    if not src.exists():
        raise FileNotFoundError(src)
    shutil.copy2(src, OUT/dst)


def compact_row(r: dict):
    keep = ['marketId','cell','winnerPostHocOnly','pnlDiagnosticOnly','floor','best','fillEvents','filledQty','submits','roleSubmits','roleFills','roleFillQty','scopeRiskCreditTotal','scopeRiskCreditConsumed','scopeRiskCreditReserved','unauthorizedOverflowQty','repairQuotaExcessMax','r247ServiceCorrectnessPass','r255CorrectnessPass']
    return {k:r.get(k) for k in keep if k in r}

# ---------- Build modern OUR full24 merged evidence ----------
r264_sources = [
    PROV/'MS4_R264_STAGEA16_BATCH_A_RESULT_20260906.json',
    PROV/'MS4_R264_STAGEA16_BATCH_B_RESULT_20260906.json',
    PROV/'MS4_R264_HELDASIDE_REMAINDER8_RESULT_20260906.json',
]
all_rows = []
for p in r264_sources:
    d = json.loads(p.read_text(encoding='utf-8'))
    all_rows.extend(d['rows'])
# Last occurrence per (market,cell); stageA + remainder are disjoint except no intentional duplicates.
by = {}
for r in all_rows:
    by[(int(r['marketId']), str(r['cell']))] = r
modern_rows = []
for mid in MIDS:
    for cell in ['R257_CONTROL','R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND']:
        if (mid,cell) not in by:
            raise RuntimeError(f'missing {mid} {cell}')
        modern_rows.append(by[(mid,cell)])
modern = {'version':'MS4_R264_FULL24_CONSUMED_RESULT_V1','date':'2026-09-06','researchOnly':True,'markets':MIDS,'rows':modern_rows,'sources':[p.name for p in r264_sources]}
(PROV/'MS4_R264_FULL24_CONSUMED_RESULT_20260906.json').write_text(json.dumps(modern,indent=2),encoding='utf-8')

# R247 full24
r247d = json.loads((PROV/'MS4_R247_FULL24_CONSUMED_RESULT_20260906.json').read_text(encoding='utf-8'))
r247_rows = [r for r in r247d['rows'] if str(r.get('cell'))=='MS4_R247_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE']
r247_map = {int(r['marketId']):r for r in r247_rows}
r257_map = {int(r['marketId']):r for r in modern_rows if r['cell']=='R257_CONTROL'}
r264_map = {int(r['marketId']):r for r in modern_rows if r['cell']=='R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND'}

def agg(mp):
    rs=[mp[m] for m in MIDS]
    return {
        'markets':len(rs),
        'wins':sum(float(r['pnlDiagnosticOnly'])>0 for r in rs),
        'winRate':sum(float(r['pnlDiagnosticOnly'])>0 for r in rs)/len(rs),
        'totalPnl':sum(float(r['pnlDiagnosticOnly']) for r in rs),
        'avgPnl':statistics.mean(float(r['pnlDiagnosticOnly']) for r in rs),
        'aggregateFloor':sum(float(r['floor']) for r in rs),
        'avgFloor':statistics.mean(float(r['floor']) for r in rs),
        'aggregateBest':sum(float(r['best']) for r in rs),
        'fillEvents':sum(int(r['fillEvents']) for r in rs),
        'submits':sum(int(r['submits']) for r in rs),
        'worstPnl':min(float(r['pnlDiagnosticOnly']) for r in rs),
        'worstFloor':min(float(r['floor']) for r in rs),
    }
benchmark = {
    'version':'GPT6_TARGET_OUR_SYNTHESIS_CANONICAL_BENCHMARK_V1',
    'date':'2026-09-06','researchOnly':True,'consumedMarkets':MIDS,
    'R247_CONTROL':agg(r247_map),'R257_RISK_FIRST_INTERMEDIATE':agg(r257_map),'R264_HIGH_ACTIVITY_CONTROL':agg(r264_map),
    'challengeParetoIntent':{
        'economicsAnchor':'R247_CONTROL',
        'activityAndWinAnchor':'R264_HIGH_ACTIVITY_CONTROL',
        'goal':'candidate should combine R247 economics with R264 activity/win improvement, not merely trade less'
    }
}
(PROV/'GPT6_TARGET_OUR_SYNTHESIS_CANONICAL_BENCHMARK_V1_20260906.json').write_text(json.dumps(benchmark,indent=2),encoding='utf-8')

our_comp=[]
for mid in MIDS:
    a,b,c=r247_map[mid],r257_map[mid],r264_map[mid]
    our_comp.append({
        'marketId':mid,
        'winnerPostHocOnly':c.get('winnerPostHocOnly'),
        'R247':compact_row(a),'R257':compact_row(b),'R264':compact_row(c),
        'R264minusR247':{
            'pnl':float(c['pnlDiagnosticOnly'])-float(a['pnlDiagnosticOnly']),
            'floor':float(c['floor'])-float(a['floor']),
            'best':float(c['best'])-float(a['best']),
            'fills':int(c['fillEvents'])-int(a['fillEvents']),
            'submits':int(c['submits'])-int(a['submits']),
        }
    })
(OUT/'50_OUR_FULL24_R247_R257_R264_COMPARISON.json').write_text(json.dumps({'benchmark':benchmark,'rows':our_comp},indent=2),encoding='utf-8')

# ---------- Target raw / statistical slices ----------
compare_db = PROV/'target_eth_btc_strategy_compare_snapshot_v1.db'
fill_db = PROV/'target_eth_fill_legs_v3_snapshot_20260904.db'

con=sqlite3.connect(compare_db); con.row_factory=sqlite3.Row
latest500=list(con.execute("select * from target_market_results where asset='ETH' and net_pnl_usdt is not null order by resolved_at_ms desc limit 500"))
latest120=latest500[:120]

def target_resolved_stats(rs):
    pn=[float(r['net_pnl_usdt']) for r in rs]; fills=[int(r['fill_count']) for r in rs]; parents=[int(r['parent_count']) for r in rs]
    return {
        'markets':len(rs),'profitableMarkets':sum(x>0 for x in pn),'profitableRate':sum(x>0 for x in pn)/len(rs),
        'totalPnl':sum(pn),'avgPnl':statistics.mean(pn),'medianPnl':statistics.median(pn),
        'avgFillCount':statistics.mean(fills),'medianFillCount':statistics.median(fills),'avgParentCount':statistics.mean(parents),
        'makerNetPnlSum':sum(float(r['maker_net_pnl_usdt'] or 0) for r in rs),
        'takerNetPnlSum':sum(float(r['taker_net_pnl_usdt'] or 0) for r in rs),
        'snapshotCaveat':'resolved summary snapshot is older than OUR 2026-09-05 consumed24; use as architecture/performance context, not same-market ground truth'
    }
target_stats={'version':'TARGET_DATA_SLICE_STATS_V1','latest500ETH':target_resolved_stats(latest500),'latest120ETH':target_resolved_stats(latest120)}
(OUT/'20_TARGET_RESOLVED_STATS.json').write_text(json.dumps(target_stats,indent=2),encoding='utf-8')

# resolved120 market results CSV
cols=['market_id','asset','title','winner','resolved_at_ms','fill_count','parent_count','buy_notional_usdt','sell_proceeds_usdt','payout_usdt','net_pnl_usdt','net_roi','maker_net_pnl_usdt','taker_net_pnl_usdt','up_position_shares','down_position_shares','accounting_version']
with (OUT/'21_TARGET_RESOLVED_RECENT120_MARKET_RESULTS.csv').open('w',newline='',encoding='utf-8') as f:
    cw=csv.writer(f); cw.writerow(cols)
    for r in latest120: cw.writerow([r[c] for c in cols])
ids120=[int(r['market_id']) for r in latest120]; q=','.join('?'*len(ids120))
parent_cols=['parent_id','asset','market_id','role','side','first_event_ms','last_event_ms','average_price','shares','fill_legs']
parents=list(con.execute(f"select {','.join(parent_cols)} from target_parent_orders where market_id in ({q}) order by market_id,first_event_ms,parent_id",ids120))
with (OUT/'22_TARGET_RESOLVED_RECENT120_PARENT_ORDERS.csv').open('w',newline='',encoding='utf-8') as f:
    cw=csv.writer(f); cw.writerow(parent_cols)
    for r in parents: cw.writerow([r[c] for c in parent_cols])
con.close()

# latest80 strict event slice; no winner column in this DB
con=sqlite3.connect(fill_db); con.row_factory=sqlite3.Row
latest80_ids=[int(r['market_id']) for r in con.execute('select market_id,max(event_ms) mx from eth_events group by market_id order by mx desc limit 80')]
q=','.join('?'*len(latest80_ids))
events=list(con.execute(f'select id,leg_id,market_id,role,side,order_hash,transaction_hash,settlement_id,event_ms,observed_at_ms,price,shares from eth_events where market_id in ({q}) order by event_ms,id',latest80_ids))
with (OUT/'23_TARGET_RAW_LATEST80_FILL_EVENTS.jsonl').open('w',encoding='utf-8') as f:
    for r in events: f.write(json.dumps(dict(r),separators=(',',':'))+'\n')
market_end={int(r['market_id']):int(r['window_end_ms']) for r in con.execute(f'select market_id,window_end_ms from eth_markets where market_id in ({q})',latest80_ids)}
per=defaultdict(lambda:{'events':0,'maker':0,'taker':0,'up':0,'down':0,'shares':0.0,'notional':0.0,'uniquePrices':set(),'firstEventMs':None,'lastEventMs':None})
for r in events:
    d=per[int(r['market_id'])]; d['events']+=1; d[str(r['role']).lower()]+=1; d[str(r['side']).lower()]+=1; d['shares']+=float(r['shares']); d['notional']+=float(r['shares'])*float(r['price']); d['uniquePrices'].add(float(r['price']))
    t=int(r['event_ms']); d['firstEventMs']=t if d['firstEventMs'] is None else min(d['firstEventMs'],t); d['lastEventMs']=t if d['lastEventMs'] is None else max(d['lastEventMs'],t)
summary=[]
for mid in latest80_ids:
    d=per[mid]; summary.append({'marketId':mid,'windowEndMs':market_end.get(mid),'eventCount':d['events'],'makerEvents':d['maker'],'takerEvents':d['taker'],'upEvents':d['up'],'downEvents':d['down'],'shares':d['shares'],'notional':d['notional'],'uniquePriceCount':len(d['uniquePrices']),'firstEventMs':d['firstEventMs'],'lastEventMs':d['lastEventMs']})
role_counts=Counter(str(r['role']) for r in events); side_counts=Counter(str(r['side']) for r in events)
raw80_stats={'markets':80,'events':len(events),'roleCounts':dict(role_counts),'sideCounts':dict(side_counts),'avgEventsPerMarket':statistics.mean(x['eventCount'] for x in summary),'medianEventsPerMarket':statistics.median(x['eventCount'] for x in summary),'marketIds':latest80_ids,'sourceDb':fill_db.name,'note':'raw confirmed Target fill legs; no outcome field is present in this event slice'}
(OUT/'24_TARGET_RAW_LATEST80_MARKET_SUMMARY.json').write_text(json.dumps({'aggregate':raw80_stats,'markets':summary},indent=2),encoding='utf-8')
con.close()

# ---------- Curated source evidence ----------
source_map = {
'30_TARGET_SYSTEM_REGULARITIES.md':PROV/'TARGET_SYSTEM_REGULARITIES_RESEARCH_HANDOFF_V1_20260903.md',
'31_TARGET_CROSS_TIMEFRAME_ARCHITECTURE.json':PROV/'TARGET_CROSS_TIMEFRAME_SYSTEM_ARCHITECTURE_SYNTHESIS_V21_20260903.json',
'32_TARGET_REEXPAND_BEFORE_REPAIR_PROGRESS.json':PROV/'TARGET_ETH_REEXPAND_BEFORE_REPAIR_PROGRESS_SYNTHESIS_V1_20260904.json',
'33_TARGET_EARLY_RISK_INITIATION.json':PROV/'TARGET_ETH5M_EARLY_RISK_INITIATION_ANATOMY_V1_20260905.json',
'34_TARGET_RISK_BUDGET_ANATOMY.json':PROV/'TARGET_ETH_RISK_BUDGET_ANATOMY_V1_20260904.json',
'35_TARGET_PARALLEL_ACTIVE_PASSIVE_REPAIR.json':PROV/'TARGET_ETH_PARALLEL_ACTIVE_PASSIVE_REPAIR_SYNTHESIS_20260902.json',
'36_TARGET_FIFO_SERVICE_URGENCY.json':PROV/'TARGET_ETH_ATOMIC_FIFO_SERVICE_URGENCY_V1_20260906.json',
'37_TARGET_COMPOSITE_BOUNDARY_CROSSING.json':PROV/'TARGET_ETH_COMPOSITE_BOUNDARY_CROSSING_V1_20260906.json',
'38_TARGET_POST_COMPOSITE_CONTINUATION.json':PROV/'TARGET_ETH_POST_COMPOSITE_CHILD_CONTINUATION_V1_20260906.json',
'39_TARGET_COSTLY_REPAIR_CONTINUATION.json':PROV/'TARGET_COSTLY_REPAIR_CONTINUATION_V1_20260906.json',
'40_TARGET_SERVICE_SCHEDULER_STATE.json':PROV/'TARGET_ETH_SERVICE_SCHEDULER_STATE_V2_20260906.json',
'41_TARGET_PARALLEL_CYCLE_PHASE_FLOW.json':PROV/'TARGET_ETH_PARALLEL_CYCLE_PHASE_FLOW_COMPACT_V1_20260905.json',
'60_OUR_R247_FULL24.json':PROV/'MS4_R247_FULL24_CONSUMED_RESULT_20260906.json',
'61_OUR_R264_FULL24.json':PROV/'MS4_R264_FULL24_CONSUMED_RESULT_20260906.json',
'62_OUR_FAILURE_CAUSALITY_SYNTHESIS.md':PROV/'MS4_R238_R245_FAILURE_CAUSALITY_AND_LIABILITY_ARCHITECTURE_SYNTHESIS_20260906.md',
'63_OUR_FAVORABLE_SPREAD_HANDOFF.md':PROV/'MS4_R223_FAVORABLE_SPREAD_RESEARCH_HANDOFF_20260906.md',
'64_OUR_FAILURE_CAUSAL_LEDGER.md':PROV/'RESEARCH_FAILURE_CAUSAL_LEDGER_V1_20260906.md',
'65_PRIOR_ART_SAFETY_POLLUTION_AUDIT.md':PROV/'TARGET_REGULARITY_PRIOR_ART_SAFETY_POLLUTION_AUDIT_V1_20260906.md',
'66_PRETEST_POLLUTION_REGISTRY.json':PROV/'PRETEST_TARGET_REGULARITY_AND_POLLUTION_REGISTRY_V1_20260906.json',
'67_GPT6_RESOURCE_SERVICE_STAGEA16_RESULT.json':ROOT/'data/research/r4_v0/gpt6_resource_service_round3_b_policy_challenge_v1_20260906/82_STAGEA16_RESULT.json',
'68_GPT6_RESOURCE_SERVICE_HANDOFF.md':ROOT/'data/research/r4_v0/GPT6_RESOURCE_SERVICE_V1_EXTERNAL_HANDOFF_20260906.md',
'69_GPT6_RESOURCE_SERVICE_PREREG.json':ROOT/'data/research/r4_v0/GPT6_RESOURCE_SERVICE_V1_PREREGISTERED_20260906.json',
'70_CODE_R247.py':ROOT/'tools/run_eth_ms4_r2_47_bounded_core_service_favorable_recycle.py',
'71_CODE_R257.py':ROOT/'tools/run_eth_ms4_r2_57_risk_fill_passive_repair_obligation.py',
'72_CODE_R264.py':ROOT/'tools/run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand.py',
'73_CODE_R240.py':ROOT/'tools/run_eth_ms4_r2_40_handoff_repair_credit_quarantine.py',
'74_CODE_RESOURCE_SERVICE_V1.py':ROOT/'tools/gpt6_three_failure_resource_service_v1.py',
'75_CODE_CAP1.py':ROOT/'tools/run_eth_ms4_r2_8_fanout_role_capacity_ablation.py',
'76_CODE_EXTERNAL_SCORER.py':ROOT/'tools/score_gpt6_target_our_system_synthesis_challenge_v1.py',
}
for dst,src in source_map.items(): cp(src,dst)
# Optional exact consumed bundle, for Lab/GPT-6 inspection only; never run by GPT-6.
cp(PROV/'eth_latest_settled24_20260905_bundle.zip','95_OUR_CONSUMED24_HFT_BUNDLE_DO_NOT_EXECUTE.zip')

# ---------- Human-facing contract ----------
w('00_READ_ME_FIRST.md', '''
# GPT-6 Target + OUR System Synthesis Challenge V1

This is a **research-system synthesis challenge**, not a request for another local patch.

GPT-6 receives: (1) curated Target research regularities and raw/statistical data, (2) current OUR systems and full24 evidence, (3) failed/prior mechanisms including safety-polluted results, and (4) a strict target specification.

GPT-6 may design a materially new research-only controller by recombining or replacing Management/authority/scheduler logic. It must NOT run HFT/replay/batch/LAN/training, must NOT inspect fresh holdout outcomes, and must NOT tune against external test results. BTC 5M Lab owns all execution and scoring.

Mandatory first read order:
1. 01_CHALLENGE_MISSION.md
2. 02_DETAILED_GOAL_REQUIREMENTS.md
3. 03_NONNEGOTIABLE_RUNTIME_CONTRACT.md
4. 04_TARGET_EVIDENCE_GUIDE.md
5. 05_OUR_SYSTEM_EVIDENCE_GUIDE.md
6. 06_ALREADY_TRIED_AND_POLLUTION_RULES.md
7. 07_ARCHITECTURE_FREEDOM_AND_OUTPUT_CONTRACT.md
8. 08_EXTERNAL_TEST_PROTOCOL.md
9. 09_SCORING_AND_GRADUATION.md
10. 10_GPT6_ENTRY_PROMPT.md

Do not begin with a repository-wide search. The package is intentionally curated. Up to 3 narrow additional evidence requests are permitted only if a concrete design decision is blocked.
''')

w('01_CHALLENGE_MISSION.md', '''
# Mission

Build a new **research-only Target-inspired but OUR-owned system** using Target evidence and OUR proven execution/accounting substrate as foundations.

The goal is not exact behavioral cloning. Target data is a teacher for architecture, cadence, responsibility topology, risk/repair/expand coexistence, and execution routing. The resulting runtime must depend only on OUR strict-past observable market/execution/inventory state.

The candidate should solve the central conflict now visible in OUR evidence:
- R2.47 has better economics but insufficient activity/upside density.
- R2.64 has more fills and one more winning market, and moves toward Target-like risk-first cycling, but still trails R2.47 on aggregate PnL/Floor.
- GPT6 Resource Service V1 improved several failure families but failed Stage-A because winners and activity were damaged.

Your job is to design a more complete system, not another scalar gate on one symptom.

You may reuse valid primitives (exact FIFO, separate liability/authority, multi-slot, passive/active shared responsibility, explicit risk obligation, execution representation) but you must state what you keep, delete, and redesign.
''')

w('02_DETAILED_GOAL_REQUIREMENTS.md', f'''
# Detailed OUR Goal / Requirement Specification

## Formal graduation target (chronology-fresh realistic HFT)
- 24 fresh markets.
- Win rate >= 50% (longer-term aspiration: ~60% once stable).
- Average PnL > +2 USDT per market; equivalent total > +48 over 24.
- All correctness/accounting invariants pass.
- No activity-collapse cheat: profitability/win rate cannot be obtained by refusing to trade or drastically reducing lifecycle density.
- No dream fill as formal evidence.

## Behavioral target
We want an OUR-owned system with Target-like structural abilities:
- enter risk early enough to complete cycles;
- repeatedly create small, recoverable responsibility cycles rather than one monolithic bet;
- Repair and Expand may coexist; do not require Repair-to-zero before all continuation;
- passive and active execution are routes under responsibility, not mutually exclusive global modes;
- multiple physical carriers/prices may coexist within max4 capacity;
- favorable direction should be able to amplify winners substantially;
- wrong direction should be repaired/contained so losses remain bounded rather than catastrophic;
- do not equate share balance with economic safety;
- do not require instantaneous Floor non-worsening for every action;
- do not treat pending/live Repair as realized payment or free credit.

## Current consumed24 Pareto anchors
R2.47 economics anchor:
{json.dumps(benchmark['R247_CONTROL'],indent=2)}

R2.64 activity/win anchor:
{json.dumps(benchmark['R264_HIGH_ACTIVITY_CONTROL'],indent=2)}

Candidate challenge objective: combine or exceed these properties without market-ID specialization. Full24 is consumed mechanism evidence only; even a win here must be followed by a preregistered small fresh test.
''')

w('03_NONNEGOTIABLE_RUNTIME_CONTRACT.md', '''
# Non-negotiable runtime / correctness contract

Frozen or semantically authoritative:
- realistic HFT execution substrate and order lifecycle;
- exact-FIFO responsibility allocation: physical fill may pay old Repair first and overflow becomes a new responsibility;
- physical cost/inventory accounting and terminal scoring;
- max physical structural slots = 4 for this challenge;
- <=180 seconds remaining: no new exposure (Repair is still allowed according to frozen semantics);
- venue legality/minimum quantities;
- no 8781/live runtime changes;
- no future book, winner, settlement, future Target action, or post-hoc PnL as runtime features;
- no market-ID branches or memorized exception IDs;
- no hidden/bogus risk credit, double-spend, debt erasure, cross-generation funding, or pending-fill-as-payment;
- no correctness gain via activity collapse.

Target files may be used offline to infer architecture. They are forbidden as live runtime inputs.

You may redesign Management state, objective/thesis persistence, responsibility service priority, continuation authority, active/passive routing, risk-tranche lifecycle, or role scheduler, as long as the above substrate and conservation laws remain intact.
''')

w('04_TARGET_EVIDENCE_GUIDE.md', f'''
# Target Evidence Guide

Read 30_TARGET_SYSTEM_REGULARITIES.md first; it is the consolidated handoff. The numbered Target artifacts then provide independent detail.

Key established observations to preserve as evidence, not literal code rules:
- Repair and Expand are concurrent responsibilities, not strictly serial stages.
- Re-expansion commonly occurs before full old debt clearance; first re-expand may occur with observable Repair progress = 0.
- Active and Passive often coexist around the same responsibility; Taker is not simply a terminal takeover.
- Target uses many small responsibility/payment clocks and multi-price carriers.
- carrier quantity != manager debt; overflow can create a new responsibility.
- Target frequently spends beyond immediately realized Floor reserve, so Floor-nonworse is not a universal admission rule.
- expensive Repair can be rational insurance; pairSum<=1 is not a universal Repair veto.
- event/progress/state transitions matter more than arbitrary fixed delays.
- high activity is a capability, not an error.

Data files:
- 20_TARGET_RESOLVED_STATS.json: resolved ETH summary context. latest500 snapshot: profitable rate {target_stats['latest500ETH']['profitableRate']:.3f}, avg PnL {target_stats['latest500ETH']['avgPnl']:.3f}, avg fills {target_stats['latest500ETH']['avgFillCount']:.1f}.
- 21/22: recent120 resolved markets + parent orders (offline research only; winner/PnL forbidden at runtime).
- 23/24: latest80 raw confirmed Target fill legs and market summary. This newer event slice has no winner field and is useful for cadence/role/side/price geometry.

Important provenance caveat: the Target snapshots are not same-market twins of OUR consumed24. Use them for architecture/statistics, not for claiming direct same-cell superiority.
''')

w('05_OUR_SYSTEM_EVIDENCE_GUIDE.md', '''
# OUR System Evidence Guide

## R2.47 — current economics anchor
Bounded Core Service + Favorable Recycle. It retains exact responsibility accounting and has the best current consumed24 aggregate PnL/Floor among the modern candidates in this package, but lifecycle density/upside is still far below graduation needs.

## R2.57 — intermediate risk-first system
Confirmed risk fills create explicit Repair obligations and get passive service priority. It materially increases activity but costs too much economically.

## R2.64 — execution-represented pre-Repair re-expand
Adds a single bounded pre-Repair re-expand only after the existing risk obligation is fully represented by live passive Repair execution capacity. Live Repair is not counted as paid protection/credit. This improves R2.57 strongly, producing 11/24 wins and 177 fills, but still trails R2.47 by about 0.266 total PnL and 0.408 aggregate Floor.

## GPT6 Resource Service V1 prior candidate
Do not repeat it blindly. Stage-A16 correctness passed and total PnL improved vs R240, but it failed because original winner aggregate PnL/Floor regressed and activity fell materially. Read 67-69.

## Failure families / economics
Read 62/63/64. Current research repeatedly shows:
- unrecovered exposure is the dominant loss family;
- expensive Active/Passive Repair is a large but not universal problem;
- initial-exposure liveness failures exist;
- Core favorable economics are already reasonably good;
- hypothetical recoverability and pending Repair are not equivalent to realized repayment;
- repayment/liability and recyclable continuation authority must be distinct resources;
- upside amplification is mandatory: loss reduction alone cannot reach avg PnL > +2.

50_OUR_FULL24_R247_R257_R264_COMPARISON.json gives compact per-market comparison.
''')

w('06_ALREADY_TRIED_AND_POLLUTION_RULES.md', '''
# Already tried / prior-art / safety-pollution rules

Before proposing a mechanism, read 65_PRIOR_ART_SAFETY_POLLUTION_AUDIT.md and 66_PRETEST_POLLUTION_REGISTRY.json.

Do NOT waste tokens re-proposing these as if untested:
- universal pairSum<=1 trade/Repair veto;
- Floor-nonworse hard admission;
- full-payoff-before-expand serial lifecycle;
- whole-portfolio hypothetical recoverability as realized protection;
- pending/live Repair as spendable credit;
- first zero-fill -> Active;
- N zero-fills -> Active;
- fixed timeout/stale-tick rule as main authority;
- instant book imbalance/midpoint direction as thesis authority;
- local small classifier on the 19 consumed causal states;
- continuous unbounded recoverable Expand (V42 runaway);
- global abstention / fewer fills as a safety solution;
- market-ID or winner-conditioned routing.

Historical REJECT is not portable negative evidence if the mechanism never materialized or was blocked by old safety/serialization/accounting. Examples in the audit include V37/V38 recycle not exercising, V83 changing after representation fixes, and old composite tests blocked by deprecated whole-portfolio safety.

Classify any predecessor you reuse as one of:
1. true mechanism failure,
2. valid primitive,
3. safety/serialization polluted,
4. not sufficiently exercised,
5. mixed/needs decontaminated replication.

Your final design must include a table mapping its major ideas to prior art and explaining why each is genuinely new, decontaminated, or intentionally reused.
''')

w('07_ARCHITECTURE_FREEDOM_AND_OUTPUT_CONTRACT.md', '''
# Architecture freedom + required GPT-6 deliverables

You are allowed to build a new Management system rather than patch R2.64. Prefer a coherent state/resource machine over a stack of gates.

Potential design surfaces (not mandates):
- persistent objective/thesis state;
- responsibility stack / generations;
- physical liability vs execution reservation vs realized repayment vs recyclable continuation authority;
- risk initiation and small risk tranches;
- Repair/Expand concurrency and cycle rearm;
- active/passive shared quotas and opportunity cost;
- service urgency / execution fillability;
- multi-price/multi-carrier scheduling within max4;
- bounded option/sequence value rather than one-step PnL gates.

Required files to return:
1. `GPT6_SYNTHESIS_ARCHITECTURE_V1.md` — full architecture, state transitions, invariants, prior-art mapping, expected advantages and self-critique.
2. `tools/gpt6_target_our_synthesis_v1.py` — research-only candidate controller/module.
3. `tools/run_gpt6_target_our_synthesis_v1.py` — single-market/single-cell runner + merge support. You must write it but NOT execute it.
4. `GPT6_TARGET_OUR_SYNTHESIS_V1_PREREGISTERED.json` — frozen hypotheses, exact behavior changes, invariants, no-tuning claims, expected intervention telemetry.
5. `GPT6_SYNTHESIS_ASSUMPTIONS_AND_FAILURES_V1.json` — assumptions, strongest failure modes, what evidence would falsify each mechanism.
6. static syntax/AST/import-boundary check report only; no HFT execution.

Code should reuse the existing HFT simulator/base rather than inventing a new fill model. No modifications to live files or frozen baselines. If an implementation dependency is missing, request at most 3 narrowly specified files; do not browse the repository broadly.

Final line must be exactly:
`STATUS: READY_FOR_EXTERNAL_EXECUTION`
''')

w('08_EXTERNAL_TEST_PROTOCOL.md', '''
# External testing — owned by BTC 5M Lab, never GPT-6

GPT-6 must not execute HFT/replay/batch/LAN/training or inspect fresh test outcomes.

After return, Lab performs:
1. static + semantic review for leakage, hidden authority, prior-art duplication, accounting violations;
2. 1-3 consumed smoke markets selected structurally, not by outcome;
3. if correct and exercised, preregistered Stage-A <=16;
4. exact consumed24 comparison;
5. only if consumed evidence beats the current Pareto frontier, a new chronology-fresh small realistic-HFT validation;
6. graduation only on fresh24 under the formal target.

Consumed24 comparison cells:
- R247_CONTROL
- R264_CONTROL
- GPT6_SYNTHESIS_CANDIDATE

All cells use identical tape, latency/execution model, winner scoring, max4 and <=180s boundary. External Lab writes/checks controls; candidate cannot replace the scorer or baseline.

Mandatory counterexample audit includes at least: 1945866, 1945898, 1945986, 1946468, 1946475, 1946656, 1946683, 1946756, 1946784, 1946792, 1946899. These IDs are for post-hoc Lab audit only and are forbidden as runtime conditions.
''')

w('09_SCORING_AND_GRADUATION.md', f'''
# Challenge scoring vs formal graduation

## Consumed24 synthesis challenge (mechanism evidence only)
Canonical anchors are in 50_OUR_FULL24_R247_R257_R264_COMPARISON.json.

Machine challenge aims to reach the current Pareto envelope simultaneously:
- correctness/invariants PASS;
- complete 24/24;
- total PnL >= R2.47 ({benchmark['R247_CONTROL']['totalPnl']:.6f});
- aggregate Floor >= R2.47 ({benchmark['R247_CONTROL']['aggregateFloor']:.6f});
- wins >= R2.64 ({benchmark['R264_HIGH_ACTIVITY_CONTROL']['wins']}/24);
- fillEvents >= 90% of R2.64 ({0.9*benchmark['R264_HIGH_ACTIVITY_CONTROL']['fillEvents']:.1f}); this is a gross anti-collapse floor, not permission to suppress activity;
- no control parity drift;
- no hidden funding, future leakage or market-specific rules.

Manual review additionally checks role-level activity, winner collateral, repeated mechanism benefit, exception markets, and whether apparent improvement comes from lifecycle path suppression.

## Formal graduation remains much harder
Even a consumed24 challenge PASS is NOT graduation and NOT live promotion. Graduation still requires fresh24 realistic HFT, WR >=50%, average PnL > +2, total >+48, correctness zero and no low-frequency cheat.
''')

w('10_GPT6_ENTRY_PROMPT.md', '''
You are the independent senior Quant / Strategy Architecture challenger for BTC 5M Lab.

Read this package in the mandatory order from 00_READ_ME_FIRST.md. Your task is to **build a new research-only system**, not merely critique OUR current controller and not merely patch one failure family.

Use Target evidence as an architecture teacher and OUR system as the executable/accounting substrate. The runtime you design must use only OUR strict-past observable market/execution/inventory state. Never use Target actions, winner, settlement or future data at runtime.

First response/architecture report must contain these headings:
- CURRENT SYSTEM VERDICT
- TARGET REGULARITIES YOU TREAT AS HARD EVIDENCE
- TARGET OBSERVATIONS YOU TREAT ONLY AS HYPOTHESES
- OUR PRIMITIVES YOU KEEP
- OUR COMPONENTS YOU DELETE OR REPLACE
- NEW SYSTEM STATE MACHINE
- RISK INITIATION
- REPAIR / EXPAND CONCURRENCY
- ACTIVE / PASSIVE EXECUTION
- RESPONSIBILITY / RESOURCE SEMANTICS
- MULTI-SLOT / MULTI-CARRIER SCHEDULER
- PRIOR-ART AND SAFETY-POLLUTION MAP
- WHY THIS CAN OUTPERFORM R2.47 AND R2.64
- STRONGEST FAILURE MODES OF YOUR OWN DESIGN
- SMALLEST EXTERNAL FALSIFICATION
- ADDITIONAL_EVIDENCE_REQUESTS (0-3)

Then create the required research files in 07_ARCHITECTURE_FREEDOM_AND_OUTPUT_CONTRACT.md. Static checks are allowed. Do not run HFT/replay/batch/LAN/training. Do not modify frozen baselines or live runtime.

Do not optimize for one consumed market or invent a selector from outcome labels. The Lab will execute and score your system after you finish.

End the final handoff with exactly:
STATUS: READY_FOR_EXTERNAL_EXECUTION
''')

# evidence manifest
manifest={
    'version':'GPT6_TARGET_OUR_SYSTEM_SYNTHESIS_CHALLENGE_V1_MANIFEST','date':'2026-09-06',
    'purpose':'Target+OUR data/evidence driven research-system synthesis; all market execution remains external Lab-owned',
    'consumed24':MIDS,
    'targetData':{'resolvedSummarySnapshot':compare_db.name,'rawFillSnapshot':fill_db.name,'rawLatest80MarketIds':latest80_ids},
    'ourBenchmark':benchmark,
    'testingOwner':'BTC 5M Lab','gpt6MayRunMarketTests':False,
}
(OUT/'11_EVIDENCE_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

# SHA manifest for everything except itself
sha={}
for p in sorted(OUT.iterdir()):
    if p.is_file() and p.name!='SHA256_MANIFEST.json': sha[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'SHA256_MANIFEST.json').write_text(json.dumps(sha,indent=2,sort_keys=True),encoding='utf-8')

if ZIP.exists(): ZIP.unlink()
with zipfile.ZipFile(ZIP,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(OUT.iterdir()):
        if p.is_file(): z.write(p,arcname=p.name)

print(json.dumps({'ok':True,'dir':str(OUT.relative_to(ROOT)),'zip':str(ZIP.relative_to(ROOT)),'files':len(list(OUT.iterdir())),'zipBytes':ZIP.stat().st_size,'benchmark':benchmark,'targetRaw80':raw80_stats},ensure_ascii=False))
