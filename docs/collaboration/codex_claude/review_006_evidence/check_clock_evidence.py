"""Bounded, read-only review of frozen JSON/trace evidence. No native imports."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RET = ROOT / 'data/research/lan_worker_returns'
PKG = ROOT / 'data/research/v12g_strict_clock_deadline_20260927_v32'
ARM = RET / 'btc5m-v12g-strict-clock-deadline-20260927-v32/arms/v12g32_MARGIN_ONLY_STRICT_2629019'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


cap = read(ARM / 'strict_clock_capture.json')
pin = read(PKG / 'SOURCE_PIN_V32_POST.json')
calls = cap['strict']['calls']
last = calls[-1]
assert len(calls) == cap['strict']['n'] == 1481
assert all(c['bound_is_observer'] for c in calls)
assert last['elapse_rc'] == 1 and last['returned'] is False
assert last['cur_before_ns'] == last['cur_after_ns'] == 1790440800255000000
assert last['target_ns'] == 1790440800535000000
assert last['elapse_arg_ns'] == 280000000
traces = [
    RET / 'btc5m-v12g-admission-components-20260927-v30/arms/v12g30_MARGIN_ONLY_2629019/failure_trace.json.gz',
    RET / 'btc5m-v12g-receipt-clock-diagnostic-20260927-v31/arms/v12g31_MARGIN_ONLY_DIAG_2629019/failure_trace.json.gz',
    ARM / 'failure_trace.json.gz',
]
trace_hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in traces}
assert len(set(trace_hashes.values())) == 1
comparisons = {}
for group in ('tools', 'src', 'tapes'):
    reference = pin['files'][f'scratch_v30:{group}']
    for label in ('scratch_v31', 'scratch_v32', 'template'):
        candidate = pin['files'][f'{label}:{group}']
        keys = sorted(set(reference) | set(candidate))
        diffs = {k: {'v30': reference.get(k), label: candidate.get(k)}
                 for k in keys if reference.get(k) != candidate.get(k)}
        comparisons[f'v30_vs_{label}:{group}'] = {'reference_files': len(reference), 'differences': diffs}
        if label != 'template':
            assert not diffs
violation = cap['violations'][0]
rows = violation['rows']
assert [r['sequence'] for r in rows] == [73, 74]
assert all(r['receive_ts'] - last['cur_after_ns'] == 95000000 for r in rows)
old_probe = RET / 'open-funding-native-eof-probe-20260911-v3/COMPACT.json'
native_manifest = RET / 'hft244-receipts-v4-20260910-v1/SOURCE_MANIFEST.json'
local_native = ROOT / '.tmp/hft244_accounting_source_v1/hftbacktest/src/backtest/mod.rs'
native_key = 'hftbacktest\\src\\backtest\\mod.rs'
output = {
    'scope': 'Read-only frozen artifacts; no native execution, receipt acceptance, or worker dispatch.',
    'strict_call_count': len(calls),
    'all_bound_is_observer': True,
    'last_call': last,
    'binding': cap['strict']['binding'],
    'feed_tail': calls[0]['feed_tail'],
    'raw_receipts': rows,
    'responsibility': violation['responsibility'],
    'trace_byte_identical': True,
    'trace_hashes': trace_hashes,
    'source_pin_comparisons': comparisons,
    'native_pyd_record': pin.get('native_pyd'),
    'historical_eof_probe': read(old_probe),
    'local_native_source_is_reference_only': {
        'local_sha256': sha(local_native),
        'v4_manifest_sha256': read(native_manifest)['files'][native_key],
        'matches_v4': sha(local_native) == read(native_manifest)['files'][native_key],
        'limitation': 'Local source is not the pinned v4 source; verify worker v4 source before patching.',
    },
    'margin_formula_correction': {
        'K': 4.15, 'positive_for_q_gt_zero_only_when_p_below': 4.15 / 5.15,
        'example_p': 0.31, 'example_q': 10, 'delta': 10 * (4.15 - 5.15 * 0.31),
        'not_always_positive': True,
    },
    'artifact_sha256': {p.relative_to(ROOT).as_posix(): sha(p) for p in
                       (ARM / 'strict_clock_capture.json', PKG / 'SOURCE_PIN_V32_POST.json', old_probe, native_manifest, local_native)},
}
dest = Path(__file__).with_name('CLOCK_CHECK.json')
dest.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'saved': str(dest), 'calls': len(calls), 'trace_byte_identical': True,
                  'local_native_matches_v4': output['local_native_source_is_reference_only']['matches_v4']}))
