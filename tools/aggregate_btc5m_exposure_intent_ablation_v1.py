"""Control parity plus economic/topology/state audits; no fits or native runs."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS, compact, first_difference
from audit_btc5m_target_core_loop_topology_v1 import topology, target_rows, own_rows, batches, orientation, read, sha

ROOT = Path(__file__).resolve().parents[1]; R = ROOT/'data/research'; RET = R/'lan_worker_returns'
BASE = RET/'causal-clock-2026085-fixed-train-20260912-v1'
STEM = 'BTC5M_EXPOSURE_INTENT_ABLATION_RESULT_V1_20260913'
STREAMS = ('states', 'plans', 'native_actions', 'observations')


def get(folder):
    result = read(folder/'result.json'); assert result['status'] == 'COMPLETE', result.get('error')
    assert result['safety_gate']['pass'] and result['unresolved_owners'] == 0
    with gzip.open(folder/'clock_trace.json.gz', 'rb') as f: raw = f.read(32*1024*1024+1)
    assert len(raw) <= 32*1024*1024
    assert hashlib.sha256(raw).hexdigest() == result['clock_smoke']['trace_payload_sha256']
    return result, json.loads(raw)


def check_intent(result, trace):
    rows = trace['intent']; mode = result['clock_smoke']['intent_mode']; birth = result['clock_smoke']['first_direction_birth']
    reversed_against_intent = 0; first_sign = 0
    for r in rows:
        inv = r['inv']; net = (inv['UP']-inv['DOWN'])/(1+sum(inv.values()))
        legacy = math.tanh(result['theta'][3]*net)
        assert legacy == r['legacy_exposure']
        current = 1 if net>0 else -1 if net<0 else 0
        if not first_sign and current: first_sign = current
        assert r['retained_direction_sign'] == first_sign
        expected = legacy if mode=='CONTROL' else (current if mode=='FOLLOW_CURRENT' else first_sign)*abs(legacy)
        assert expected == r['applied_exposure']
        implied = (r['desired']['UP']-r['desired']['DOWN'])/sum(r['desired'].values())
        assert abs(implied-expected) < 1e-12
        assert abs(sum(r['atomic_outstanding'].values())-abs(inv['UP']-inv['DOWN'])) < 1e-7
        assert all(v>=-1e-7 for v in r['reserved_qty'].values())
        reversed_against_intent += bool(current and current != first_sign)
    return dict(rows=len(rows), first_direction_birth=birth, rows_current_opposes_first=reversed_against_intent,
        max_terminal_count=max(r['terminal_total'] for r in rows),
        max_completed_atomic_lots=max(r['atomic_completed'] for r in rows),
        frames_with_cancel_pending=sum(r['carrier_states'].get('CANCEL_PENDING',0)>0 for r in rows),
        reservation_and_atomic_state_audit_pass=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--control-only', action='store_true'); args = ap.parse_args()
    baseline, bt = get(BASE)
    control, ct = get(RET/'exposure-intent-2026085-control-20260913-v1')
    parity = {k: baseline[k] == control[k] for k in PARITY_FIELDS}
    parity.update({k: bt[k] == ct[k] for k in STREAMS}); assert all(parity.values()), parity
    ca = check_intent(control, ct)
    out = dict(version=STEM, control_parity=parity, control_parity_pass=True, control_state_audit=ca,
        oracle=False, target_runtime_direction=False, numeric_parameters_frozen=True)
    if args.control_only:
        (R/(STEM+'_CONTROL.json')).write_text(json.dumps(out,indent=2)+'\n')
        print(json.dumps(out)); return
    source_path = ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    target = target_rows(read(source_path)); target_top = topology(target)
    out['target'] = target_top['metrics']; out['arms'] = {}; traces = {}; out['provenance'] = []
    for tag in ('control', 'follow', 'latch'):
        folder = RET/('exposure-intent-2026085-'+tag+'-20260913-v1'); result, trace = get(folder)
        audit = check_intent(result, trace); traces[tag] = trace
        assert result['theta'] == control['theta']
        assert result['clock_smoke']['actual_replay_frames'] == control['clock_smoke']['actual_replay_frames']
        assert result['clock_smoke']['runtime_clock_total_frames'] == 1487
        birth = result['clock_smoke']['first_direction_birth']
        prefix = {k: [r for r in trace[k] if r['t'] < birth['t']] == [r for r in ct[k] if r['t'] < birth['t']] for k in STREAMS}
        assert all(prefix.values()), (tag,prefix)
        own = batches(own_rows(result),1000); top = topology(own)
        out['arms'][tag] = dict(metrics=compact(result), topology=top['metrics'],
            target_orientation=orientation(target,own), controller_state=audit, prefix_before_first_own_inventory=prefix,
            actual_replay_frames=result['clock_smoke']['actual_replay_frames'])
        out['provenance'].append(dict(job=folder.name,result_sha256=sha(folder/'result.json'),trace_file_sha256=sha(folder/'clock_trace.json.gz')))
    out['follow_vs_latch_first_difference'] = {k:first_difference(traces['follow'][k],traces['latch'][k]) for k in STREAMS}
    for difference in out['follow_vs_latch_first_difference'].values():
        if difference:
            difference['follow'] = difference.pop('legacy')
            difference['latch'] = difference.pop('fixed_train')
    out['memory_binding_observed'] = any(v is not None for v in out['follow_vs_latch_first_difference'].values())
    out['limitations'] = ['Single consumed market; passive-only sign mechanism, no Active arbitration validation.',
        'First confirmed own inventory is an experimental initial condition, not inferred Target alpha.',
        'Magnitude still depends on current inventory; not an independent target exposure level.',
        'Direction retention alone is insufficient: activity, debt, floor/best and mismatch all reported.',
        'FIFO responsibilities are an accounting convention, not Target private identities.',
        'Carrier SUBMITTED is not proof of exchange accepted; snapshot records local ledger states.']
    (R/(STEM+'.json')).write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(json.dumps(out,allow_nan=False))


if __name__=='__main__': main()
