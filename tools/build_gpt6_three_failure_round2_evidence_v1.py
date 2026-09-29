from __future__ import annotations
import ast, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/gpt6_three_failure_system_challenge_v1_20260906/evidence_round2'
OUT.mkdir(parents=True,exist_ok=True)

SPEC={
 'tools/run_eth_ms4_r2_1_fillability_active_repair.py':['__init__','_active_reserved_repair','_reserved_repair_quota','_has_live_active','_submit_active','_manage_active','process'],
 'tools/run_eth_ms4_r2_2_failure_evidence_active_drain.py':['__init__','_submit_role_v8','_refresh_slots','_try_active_drain'],
 'tools/run_eth_ms4_r2_6_parallel_passive_coverage.py':['__init__','_open_one_option'],
 'tools/run_eth_ms4_r2_8_fanout_role_capacity_ablation.py':['__init__','_live_fanout_count','_parallel_repair_fill'],
 'tools/run_eth_role_separated_multislot_v7_monetary_credit_smoke.py':['__init__','_sync_scope','process','_candidate_alone_floor','_repair_budget_ok','_reserved_current_expand_risk','_available_expand_risk_credit','_submit_role'],
 'tools/run_eth_role_separated_multislot_v8_repair_overflow_split_smoke.py':['__init__','_scope_debt_qty','_repair_side','_reserved_repair_quota','_reserved_repair_overflow_risk','_reserved_current_expand_risk','_audit_reservation','_repair_split','_submit_role_v8','process'],
 'tools/run_eth_ms4_r2_39_overflow_responsibility_handoff.py':['__init__','_register_overflow','process','_try_overflow_handoff','_open_one_option'],
 'tools/run_eth_ms4_r2_40_handoff_repair_credit_quarantine.py':['__init__','process'],
 'tools/run_eth_target_grounded_distinct_multislot_v2_smoke.py':['process','run_v2'],
}

def sha(p:Path):return hashlib.sha256(p.read_bytes()).hexdigest()
def extract_methods(path:Path,names):
 txt=path.read_text(encoding='utf-8'); tree=ast.parse(txt); rows=[]
 for n in ast.walk(tree):
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names:
   rows.append((n.lineno,n.end_lineno,n.name,ast.get_source_segment(txt,n)))
 rows.sort()
 return rows

lines=['# GPT-6 Round-2 Bounded Evidence Request 1 — Native Active / Credit Authority','',
'Generated 2026-09-06 from current project source. This is evidence only; no HFT/replay/training was run to build it.','',
'## Resolved inheritance / authority chain','',
'```text',
'R2.40 HandoffRepairCreditQuarantineSim',
'  -> R2.39 OverflowResponsibilityHandoffSim',
'  -> R2.8 FanoutRoleCapacitySim',
'  -> R2.6 ParallelPassiveCoverageSim',
'  -> R2.2 FailureEvidenceActiveDrainSim',
'  -> R2.1 FillabilityActiveRepairSim',
'  -> R1 QueueAwareRepairRoutingSim',
'  -> V82 SafeParallelCapacitySim',
'  -> V8 RepairOverflowSplitSim',
'  -> V7 RealizedCreditMultiSlotSim / lower physical HFT base',
'```','',
'## Direct semantic findings','',
'- R2.6 fixes `sourceRoles` to `SATELLITE_REPAIR`; therefore CAP1/R2.40 already retains R2.2 failure-evidence Active for Satellite Repair, while ECONOMIC_CORE is normally excluded.',
'- R2.2 genuine failure evidence requires terminal status, zero cumulative fill, and `cancelRequested == false`; own/reanchor zero-fill cancellations are ignored.',
'- R2.2 Active drain uses current ask and venue-min quantity `q = 1 / ask`; non-finite/zero or `q > 12` is rejected. It requires unreserved authoritative Repair debt and candidate-alone physical Floor improvement.',
'- `_submit_active` creates a pure Repair order: Repair quota = q, Overflow quota = 0. It does not create new debt or Expand authority.',
'- Active reservation is tracked separately through `activeKeys` + `keyRepairQuotaRemaining`. `_reserved_repair_quota()` explicitly adds `_active_reserved_repair()`. Active keys are not inserted into passive `slot_key`; this is the observed implementation, not a claim that such separation is economically harmless.',
'- Active remainder is cancel-requested after `ACTIVE_WINDOW_MS = 500`; terminal reconciliation zeroes remaining Repair/Overflow quota and removes the key from `activeKeys`.',
'- V8 physical fill accounting sees Active orders through the common `orders`/`key_role`/quota maps. Confirmed Repair allocation reduces key Repair quota. If old scope remains unchanged, credit minted is `repairAllocated * (1-price)` and is added to `scopeRiskCreditTotal`.',
'- V7 scope birth/flip resets total/consumed Repair credit to a newly computed birth credit; scope completion resets both to zero. Therefore same-generation credit and post-flip birth credit are distinct paths.',
'- Spendable continuation credit is `scopeRiskCreditTotal - scopeRiskCreditConsumed - reserved_current_expand_risk`, floored at zero; stale-scope reservation forces availability to zero.',
'- R2.40 removes only credit attributable to confirmed fills of R2.39 `handoffKeys`, and only when scope side/generation are unchanged across that process clock. It does not quarantine ordinary or Active Repair credit.',
'- In this research branch, terminal PnL/Floor/Best are computed from inventory minus `buyNotional`; no explicit Taker fee term appears in `run_v2`. Thus the supplied Active mechanism should be interpreted under this simulator cost convention, not assumed to include a separate taker-fee debit.','',
'## Exact source slices','']
manifest=[]
for rel,names in SPEC.items():
 p=ROOT/rel
 if not p.exists():
  manifest.append({'path':rel,'missing':True});continue
 h=sha(p); manifest.append({'path':rel,'sha256':h,'bytes':p.stat().st_size})
 lines += ['',f'### `{rel}`',f'SHA256: `{h}`','']
 for a,b,name,src in extract_methods(p,names):
  lines += [f'#### `{name}` lines {a}-{b}','```python',src,'```','']

lines += ['## Evidence boundary','',
'- No winner, settlement, Target future action, or future book state is an input to any quoted runtime decision.',
'- This packet does not decide whether separate Active-vs-passive occupancy is the right final architecture; it only resolves current source semantics.',
'- This packet does not claim R2.40+Core Active is safe. That is Request 2 / causal ablation.',
'- No new strategy authority is granted by this document.']
md='\n'.join(lines)+'\n'
(OUT/'10_REQUEST1_NATIVE_ACTIVE_CREDIT_AUTHORITY.md').write_text(md,encoding='utf-8')
(OUT/'11_REQUEST1_SOURCE_MANIFEST.json').write_text(json.dumps({'version':'GPT6_THREE_FAILURE_ROUND2_REQUEST1_V1','files':manifest},indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'out':str(OUT),'mdBytes':len(md.encode()),'files':len(manifest),'missing':[x['path'] for x in manifest if x.get('missing')]},ensure_ascii=False))
