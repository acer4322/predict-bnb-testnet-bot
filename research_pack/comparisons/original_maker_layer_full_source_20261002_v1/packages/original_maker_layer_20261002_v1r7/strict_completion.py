"""Pure output validation; no engine import, outcome input or strategy change."""
GATES = (
    'unresolved_zero', 'accounting_valid',
    'active_responsibility_overfill_zero', 'epoch_residual_overfill_zero',
    'active_matches_opportunity', 'atomic_responsibility_conservation',
    'exact_frontier_seen', 'pass',
)


def completion_errors(raw, clock, rc):
    errors = []
    gate = raw.get('safety_gate', {})
    if raw.get('status') != 'COMPLETE':
        errors.append('RAW_STATUS_NOT_COMPLETE')
    if set(gate) != set(GATES) or any(type(gate.get(k)) is not bool for k in GATES):
        errors.append('EXACT_EIGHT_BOOLEAN_GATES_REQUIRED')
    else:
        ordinary = all(gate[k] for k in GATES if k != 'pass')
        legacy_only = (gate['active_matches_opportunity'] is False
                       and all(gate[k] for k in GATES
                               if k not in ('pass', 'active_matches_opportunity')))
        if gate['pass'] != ordinary:
            errors.append('RAW_PASS_NOT_CONJUNCTION')
        if not ((rc == 0 and ordinary) or (rc == 2 and legacy_only and not gate['pass'])):
            errors.append('RETURN_CODE_OR_NONLEGACY_GATE_FAILURE')
    if raw.get('unresolved_owners') != 0:
        errors.append('RAW_UNRESOLVED_OWNERS')
    owners = clock.get('carriers', {})
    if not isinstance(owners, dict) or any(c.get('state') != 'TERMINAL' for c in owners.values()):
        errors.append('CLOCK_NONTERMINAL_OWNERS')
    if clock.get('capture_error') is not None or clock.get('invalid') is not False:
        errors.append('CLOCK_CAPTURE_ERROR_OR_INVALID')
    counts = [clock.get(k) for k in ('frames', 'source_updates', 'producer_calls')]
    if any(type(n) is not int or n <= 0 for n in counts) or len(set(counts)) != 1:
        errors.append('SOURCE_FRAME_PRODUCER_COUNT_MISMATCH')
    if raw.get('target_runtime_access') is not False or raw.get('target_direction_input') is not False:
        errors.append('TARGET_CAUSALITY_CONTRACT')
    if raw.get('coefficient_tuning') is not False or raw.get('live_changes') != 0:
        errors.append('FROZEN_OR_LIVE_CONTRACT')
    return errors
