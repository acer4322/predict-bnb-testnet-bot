"""Metadata/source preflight, NOT a trading gate or a Target-rule oracle.

Run before a new research job. This checks declared scope and exact staged source
hashes; it does not prove what a later Python process actually imports. The runner
must separately save loaded module paths/hashes and execution coverage.
No HFT, network, database, strategy import, source rewrite or dispatch occurs here.
"""
from pathlib import Path
import argparse
import hashlib
import json

VERSION='PAIR_CORE_RESEARCH_SCOPE_PREFLIGHT_V1'
NATIVE='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
COMMON={'PAIR_AVERAGE_GATE','FIXED_NOTIONAL_QTY12_PRICE_CENSOR','BOOK_IMBALANCE_PROXY',
        'TTL5_VISIBLE_LEVEL_REANCHOR','NO_FULL_SEMANTIC_OBJECTIVE_MANAGER',
        'EXECUTION_MODEL_NOT_TARGET','FULL_NET_COST_UNKNOWN','SOURCE_SELECTION_LIMITS'}
PROFILE_DEVIATIONS={
 'S_ASSET_SIZING_PASSIVE':(COMMON-{'FIXED_NOTIONAL_QTY12_PRICE_CENSOR'})|{
     'USER_ASSET_PASSIVE_FIXED1_ETH12_BTC18','ALL_ROLE_180_CUTOFF',
     'NO_ACTIVE_CHANNELS','PASSIVE4_ASSUMPTION','TERMINAL_CLOSURE_UNRESOLVED'},
 'A_CUTOFF_PASSIVE':COMMON|{'ALL_ROLE_180_CUTOFF','NO_ACTIVE_CHANNELS','PASSIVE4_ASSUMPTION'},
 'C_CUTOFF_HANDOFF_CONTROL':COMMON|{'ALL_ROLE_180_CUTOFF','NO_ACTIVE_CHANNELS','PASSIVE4_ASSUMPTION','HANDOFF_POLICY_COST'},
 'T_CUTOFF_ONE_SHOT':COMMON|{'ALL_ROLE_180_CUTOFF','ONE_SHOT_REPAIR_ONLY','NO_ACTIVE_EXPAND','PASSIVE4_SHARED_ACTIVE','HANDOFF_POLICY_COST'},
 'X_CUTOFF_SEPARATE_POOL':COMMON|{'ALL_ROLE_180_CUTOFF','ONE_SHOT_REPAIR_ONLY','NO_ACTIVE_EXPAND','PASSIVE4_ACTIVE1_TOTAL5','HANDOFF_POLICY_COST'},
 'F_FULL_HORIZON_PASSIVE':COMMON|{'NO_ACTIVE_CHANNELS','PASSIVE4_ASSUMPTION','TERMINAL_CLOSURE_UNRESOLVED'},
}
ALLOWED_CLAIMS={'MECHANISM_ONLY','DESCRIPTIVE_TARGET_COMPARISON','FULL_TARGET_RULE_EQUIVALENCE',
                'WHOLE_LIFECYCLE_READINESS','NET_PROFITABILITY_PROMOTION'}
RUNTIME_CHECKS={'loaded_source_paths_and_hashes','loaded_native_path_and_hash',
 'declared_effective_rules','per_phase_per_role_proposed_admitted_rejected_filled',
 'native_receipt_cash_inventory','pending_cancel_terminal_owners',
 'immutable_cohort_source_cost_and_outcome_clock','mechanism_exercise_and_claim_scope'}


def validate(declaration, snapshot_root=None):
    errors=[]
    if not isinstance(declaration,dict):return ['declaration must be a mapping']
    profile=declaration.get('profile')
    if profile not in PROFILE_DEVIATIONS:return ['UNREGISTERED_PROFILE_REQUIRES_NEW_RULE_AUDIT']
    if profile=='S_ASSET_SIZING_PASSIVE':
        if declaration.get('asset_route_sizing')!={'ETH':{'PASSIVE':[1,12]},'BTC':{'PASSIVE':[1,18]}}:
            errors.append('ASSET_ROUTE_SIZING_CONTRACT_MISMATCH')
        if declaration.get('scope_audit')!='PAIR_CORE_ASSET_SIZING_SMOKE4_PREREG_20260910.md':
            errors.append('NEW_PROFILE_REQUIRES_NAMED_RULE_AUDIT')
    acknowledged=set(declaration.get('acknowledged_deviations',[]))
    missing=PROFILE_DEVIATIONS[profile]-acknowledged
    if missing:errors.append('UNDECLARED_RULE_DEVIATIONS: '+','.join(sorted(missing)))
    claims=set(declaration.get('claims',[]))
    if not claims or not claims<=ALLOWED_CLAIMS:errors.append('MISSING_OR_UNKNOWN_CLAIM_SCOPE')
    if 'FULL_TARGET_RULE_EQUIVALENCE' in claims:errors.append('TARGET_PRIVATE_RULES_UNKNOWN_AND_REGISTERED_MISMATCHES')
    if 'WHOLE_LIFECYCLE_READINESS' in claims:errors.append('PROFILE_DOES_NOT_ESTABLISH_COMPLETE_EXECUTABLE_LIFECYCLE')
    if 'NET_PROFITABILITY_PROMOTION' in claims:errors.append('FULL_NET_COST_AND_INDEPENDENT_PROMOTION_NOT_ESTABLISHED')
    if declaration.get('native_sha256')!=NATIVE:errors.append('UNREGISTERED_NATIVE_REQUIRES_REVALIDATION')
    if not RUNTIME_CHECKS<=set(declaration.get('required_runtime_checks',[])):
        errors.append('INCOMPLETE_RUNTIME_ATTESTATION_PLAN')
    if declaration.get('target_runtime_inputs') is not False:errors.append('TARGET_RUNTIME_AUTHORITY_NOT_ALLOWED')
    if declaration.get('preserve_original_artifacts') is not True:errors.append('HISTORICAL_RESULTS_MUST_REMAIN_IMMUTABLE')
    frozen=declaration.get('frozen_sources')
    if not isinstance(frozen,dict) or not frozen:errors.append('MISSING_SOURCE_FREEZE')
    elif snapshot_root is not None:
        root=Path(snapshot_root).resolve()
        for name,wanted in frozen.items():
            p=(root/name).resolve()
            try:p.relative_to(root)
            except ValueError:errors.append('SOURCE_PATH_ESCAPES_SNAPSHOT: '+name);continue
            if not p.is_file() or p.stat().st_size>1024*1024:
                errors.append('MISSING_OR_OVERSIZED_FROZEN_SOURCE: '+name);continue
            actual=hashlib.sha256(p.read_bytes()).hexdigest()
            if actual!=wanted:errors.append('SOURCE_HASH_DRIFT: '+name)
    return errors


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('declaration');ap.add_argument('--snapshot-root',required=True)
    args=ap.parse_args();p=Path(args.declaration)
    if p.stat().st_size>65536:raise ValueError('declaration exceeds64KiB')
    d=json.loads(p.read_text(encoding='utf-8-sig'));errors=validate(d,args.snapshot_root)
    print(json.dumps({'version':VERSION,'verdict':'BLOCK_UNDECLARED_OR_UNSUPPORTED_SCOPE' if errors else 'PASS_DECLARED_DIAGNOSTIC_SCOPE_ONLY',
      'errors':errors,'profile':d.get('profile'),'limitations':'Not semantic equivalence, not automatic installation in legacy runners. Runtime attestations remain required.'},ensure_ascii=False))
    raise SystemExit(2 if errors else 0)


if __name__=='__main__':main()
