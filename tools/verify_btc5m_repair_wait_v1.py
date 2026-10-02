"""Verify the frozen wait panel without native execution or FIFO payment labels."""
import json
from audit_btc5m_repair_wait_and_dust_v1 import PACKAGE, R, RET, STEM, get, read, sha


def main():
    manifest = read(PACKAGE/'manifest.json')
    hashes = {name: sha(PACKAGE/name) == digest for name, digest in manifest['files'].items()}
    assert all(hashes.values())
    assert manifest == read(R/'BTC5M_REPAIR_WAIT_PREREGISTERED_20260913.json')
    aggregate = read(R/(STEM+'_RESULT.json'))
    evidence = []
    for tag in ('control', 'progress', 'existing'):
        folder = RET/f'repair-wait-2026085-{tag}-20260913-v1'
        d, tr = get(folder)
        assert d['target_runtime_access'] and d['target_direction_input'] and d['oracle']
        assert not d['runtime_eligible']
        start = next(e for e in tr['wait_events'] if e['event'] == 'START')
        assert len(start['atomic_queues']['UP']) == 1 and not start['atomic_queues']['DOWN']
        # At this prefix, later DOWN acquisitions first pay the sole old UP lot.
        # Reconstruct its progress from cumulative inventory, not atomic payment labels.
        cut = start['t']; end = 1788758400000; initial = start['remaining']
        remaining = initial; prev = cut; area = 0.; first = exact = substep = None
        previous_down = start['inventory']['DOWN']
        for row in tr['states']:
            if row['t'] <= cut:
                continue
            if row['t'] > end:
                # Native terminal drain can continue after market end. Confirm it
                # adds no repair fills, and keep the declared area horizon fixed.
                assert abs(row['inv']['DOWN']-previous_down) < 1e-7
                continue
            assert prev <= row['t'] <= end
            down = row['inv']['DOWN']
            assert down >= previous_down - 1e-7, 'BID-only cumulative reconstruction invalid'
            previous_down = down
            next_remaining = max(0., initial-(down-start['inventory']['DOWN']))
            area += remaining*(row['t']-prev)/1000
            if next_remaining < remaining-1e-9 and first is None:
                first = row['t']-cut
            remaining = next_remaining; prev = row['t']
            if remaining < start['quantity_step'] and substep is None:
                substep = row['t']-cut
            if remaining <= 1e-9 and exact is None:
                exact = row['t']-cut
        area += remaining*(end-prev)/1000
        expected = aggregate['arms'][tag]['cohort']
        actual = dict(remaining=remaining, debt_area_share_seconds=area, first_payment_ms=first,
                      exact_completion_ms=exact, substep_completion_ms=substep)
        for key, value in actual.items():
            assert value == expected[key] if value is None else abs(value-expected[key]) < 1e-7, (tag, key, value, expected[key])
        evidence.append(dict(arm=tag, independent_inventory_reconstruction=actual,
                             result_sha256=sha(folder/'result.json'), trace_file_sha256=sha(folder/'clock_trace.json.gz')))
    control = aggregate['arms']['control']; progress = aggregate['arms']['progress']
    assert control['cohort'] == progress['cohort']
    same_metrics = ('score', 'path_mse', 'events', 'floor', 'best', 'debt', 'cost',
                    'inventory_up', 'inventory_down', 'births', 'completed')
    equality = {key: control['metrics'][key] == progress['metrics'][key] for key in same_metrics}
    assert all(equality.values())
    out = dict(status='PASS', package_hashes=hashes, inventory_audit=evidence,
               progress_vs_control_equal_metrics=equality,
               progress_vs_control_submit_delta=progress['metrics']['submits']-control['metrics']['submits'],
               progress_vs_control_cancel_request_delta=progress['metrics']['cancels']-control['metrics']['cancels'],
               limitation='Single old lot in a BID-only shared prefix; not general FIFO identification or a Target strategy claim.')
    (R/'BTC5M_REPAIR_WAIT_VERIFICATION_20260913.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps(out))


if __name__ == '__main__':
    main()
