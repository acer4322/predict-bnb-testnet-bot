"""Conditional settlement geometry, not execution completeness or realized PnL."""
import math


def geometry(inv, cost):
    up, down = inv['UP'] - cost, inv['DOWN'] - cost
    best, floor = max(up, down), min(up, down)
    loss = max(0., -floor)
    if not sum(inv.values()) and not cost:
        regime = 'NO_POSITION'
    elif best <= 0:
        regime = 'NO_POSITIVE_BRANCH'
    elif floor > 0:
        regime = 'BOTH_POSITIVE'
    elif floor == 0:
        regime = 'NONNEGATIVE_BOUNDARY'
    else:
        regime = 'ONE_PROFIT_ONE_LOSS'
    return dict(up=up, down=down, best=best, floor=floor, loss=loss,
                gain_to_loss=best / loss if best > 0 and loss > 0 else None,
                loss_to_gain=loss / best if best > 0 else None,
                regime=regime, cost=cost, inventory_up=inv['UP'], inventory_down=inv['DOWN'],
                up_net=inv['UP'] - inv['DOWN'],
                fixed_up_direction_retained=inv['UP'] > inv['DOWN'],
                up_per_cost=up / cost if cost > 0 else None,
                down_per_cost=down / cost if cost > 0 else None)


def path_summary(initial, states, cut, end):
    assert end > cut
    previous, t0 = initial, cut
    floor_area = down_area = both_time = reverse_time = 0.
    changes = []
    first = second = 0
    minimum_floor = geometry(initial['inv'], initial['cost'])['floor']
    last_input = -math.inf

    def contribution(state, duration):
        g = geometry(state['inv'], state['cost'])
        return (max(0., -g['floor']) * duration, max(0., -g['down']) * duration,
                duration if g['floor'] > 0 else 0., duration if g['up_net'] < 0 else 0.)

    for row in states:
        assert row['t'] >= last_input
        last_input = row['t']
        if row['t'] <= cut or row['t'] > end:
            continue
        a, b, c, d = contribution(previous, (row['t'] - t0) / 1000.)
        floor_area += a; down_area += b; both_time += c; reverse_time += d
        if row['inv'] != previous['inv'] or abs(row['cost'] - previous['cost']) > 1e-9:
            delta = {s: row['inv'][s] - previous['inv'][s] for s in ('UP', 'DOWN')}
            assert min(delta.values()) >= -1e-7, delta
            changes.append(dict(t=row['t'], delta=delta, cost_delta=row['cost'] - previous['cost']))
            if row['t'] < (cut + end) / 2: first += 1
            else: second += 1
        previous, t0 = row, row['t']
        minimum_floor = min(minimum_floor, geometry(row['inv'], row['cost'])['floor'])
    a, b, c, d = contribution(previous, (end - t0) / 1000.)
    return dict(horizon_seconds=(end - cut) / 1000., negative_floor_area_currency_seconds=floor_area + a,
                fixed_down_loss_area_currency_seconds=down_area + b, both_positive_seconds=both_time + c,
                reversed_net_seconds=reverse_time + d, minimum_floor=minimum_floor,
                changed_states=len(changes), changes_first_half=first, changes_second_half=second,
                first_change_ms=changes[0]['t'] - cut if changes else None,
                last_change_ms=changes[-1]['t'] - cut if changes else None,
                acquired={s: previous['inv'][s] - initial['inv'][s] for s in ('UP', 'DOWN')},
                spent=previous['cost'] - initial['cost'], market_end=geometry(previous['inv'], previous['cost']),
                changed_rows=changes)


def self_test():
    base = geometry({'UP':200., 'DOWN':80.}, 100.)
    # Cheap strong-side addition improves the ratio while increasing absolute loss, with zero repair.
    add = geometry({'UP':210., 'DOWN':80.}, 101.)
    assert base['gain_to_loss'] == 5. and add['loss'] == 21.
    assert add['loss_to_gain'] < base['loss_to_gain'] and add['down'] < base['down']
    # Expensive weak-side repair reduces loss but worsens the ratio by consuming most favorable payoff.
    before = geometry({'UP':110., 'DOWN':50.}, 100.)
    repair = geometry({'UP':110., 'DOWN':60.}, 109.)
    assert repair['loss'] < before['loss'] and repair['loss_to_gain'] > before['loss_to_gain']
    assert geometry({'UP':100., 'DOWN':90.}, 80.)['gain_to_loss'] is None
    assert geometry({'UP':0., 'DOWN':0.}, 0.)['regime'] == 'NO_POSITION'
    assert geometry({'UP':10., 'DOWN':10.}, 20.)['loss_to_gain'] is None
    for factor in (.01, 10.):
        scaled = geometry({'UP':200. * factor, 'DOWN':80. * factor}, 100. * factor)
        assert abs(scaled['gain_to_loss'] - base['gain_to_loss']) < 1e-12
    swapped = geometry({'UP':80., 'DOWN':200.}, 100.)
    assert swapped['gain_to_loss'] == base['gain_to_loss'] and not swapped['fixed_up_direction_retained']
    return dict(cheap_add_without_repair=dict(before=base, after=add), expensive_repair=dict(before=before, after=repair))


if __name__ == '__main__':
    self_test()
    print('PASS')
