"""Small worker load-only checks; synthetic fixtures, native execution = zero."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from engine import Study, read_json, digest, validate_parameters
from attempt_memory import AttemptMemory, extra_cost


def run():
    checked = []
    m = AttemptMemory(lambda q, p: 0.)
    opening = dict(side='UP', route='PASSIVE', qty=15., price=.4)
    m.register('1', opening, {'now_ms': 0})
    receipt = dict(sequence=1, order_id=1, side=1, qty=15., price=.4, maker=True, receive_ts=1_000_000_000)
    m.consume([receipt], 1000)
    m.consume([receipt], 1000)
    o = dict(now_ms=3000, remaining_seconds=297.)
    a = m.snapshot(o)
    assert a['sides']['UP']['open_net_qty'] == 15.
    assert a['sides']['UP']['weighted_fill_age_seconds'] == 2.
    checked += ['duplicate_receipt_idempotent', 'age_is_fill_age_not_order_age']
    active = dict(side='DOWN', route='ACTIVE', qty=15., price=.55)
    m.register('2', active, {'now_ms': 2000})
    assert not m.terminal('2', 'UNKNOWN', 0., 2500)
    assert m.snapshot(o)['confirmed_zero_attempts'] == 0
    assert m.terminal('2', 'EXPIRED', 0., 2500)
    assert not m.terminal('2', 'EXPIRED', 0., 2600)
    b = m.snapshot(o)
    assert b['sides']['UP']['failed_coverage_per_open_share'] == 1.
    checked += ['unknown_not_zero', 'canonical_zero_only', 'terminal_idempotent']
    config = dict(history='attempts', curve='linear', failure_weight=1., age_weight=1.)
    assert extra_cost(config, a['sides']['UP'], .02, 15.) == 0.
    assert abs(extra_cost(config, b['sides']['UP'], .02, 15.)-.3) < 1e-12
    checked.append('same_current_state_different_confirmed_history_changes_cost')
    polluted = deepcopy(o)
    polluted.update(winner='DOWN', target_action='BUY', future_books=['unknown'])
    assert m.snapshot(polluted) == b
    checked.append('unrelated_future_metadata_ignored')
    m.register('3', active, {'now_ms': 3000})
    m.consume([dict(sequence=2, order_id=3, side=-1, qty=15., price=.45, maker=False, receive_ts=4_000_000_000)], 4000)
    closed = m.snapshot(dict(now_ms=4000, remaining_seconds=296.))
    assert closed['sides']['UP']['failed_coverage_per_open_share'] == 0.
    assert closed['sides']['UP']['open_net_qty'] == 0.
    checked.append('repaired_lots_release_soft_signal_without_cooldown')
    spec = dict(seed=7, trials=12, startup=2, exploration_probability=.2,
        objectives=['a','b'], space=dict(x=dict(type='log_float',low=.25,high=4.),
                                        family=dict(type='choice',values=['a','b'])))
    with TemporaryDirectory() as directory:
        p = Path(directory)/'STUDY.json'
        study = Study(p,spec)
        for i in range(12):
            row=study.ask(); validate_parameters(spec['space'],row['params'])
            assert row['number']==i
            study.begin(i);study.tell(i,[-row['params']['x'],row['params']['x']],dict(synthetic=True))
        assert len(study.trials)==12 and len({r['parameter_hash'] for r in study.trials})==12
        assert all(r['parent_trial'] is None or r['parent_trial']<r['number'] for r in study.trials)
        reloaded=Study(p,spec)
        assert reloaded.data==study.data
        assert len(reloaded.shortlist(3))>=2
    checked += ['sampler_within_declared_ranges','unique_configs','parents_only_from_completed_past',
                'persistent_reload_exact','pareto_extremes_preserved']
    return dict(status='PASS', checks=checked, count=len(checked), native_executed=0,
                market_tests=0, neural_updates=0, synthetic_only=True)
