"""Diagnostic ablation of new directional Passive orders in the final third.

Uses only the public market clock and the existing selected OWN role. Does not
change demand, cash, pending reservations, cancellation, or repair authority.
Screening happens before reserve, so rejected proposals consume no owner key.
This is deliberately a zero-new boundary experiment, not a fitted schedule.
"""
from roles_runtime import roles


def blocked(t, start, end, side, strong, route):
    assert end > start
    return (start <= t < end and 3 * (t - start) >= 2 * (end - start)
            and strong in ('UP', 'DOWN') and side == strong and route == 'PASSIVE')


class TailNewStop:
    def __init__(self):
        self.rows = []

    def veto(self, frame, side, route, qty, price):
        stop = blocked(frame['t'], frame['start'], frame['end'], side, roles.side, route)
        self.rows.append(dict(t=int(frame['t']), index=int(frame['index']),
            start=int(frame['start']), end=int(frame['end']), strong=roles.side,
            side=side, route=route, qty=qty, price=price, blocked=stop))
        return stop


def instrument(source, replace):
    source = replace(source, 'self.growth_hold = _GrowthHold()',
        'self.growth_hold = _GrowthHold(); self.tail_new = _TailNewStop()')
    lines = source.splitlines(keepends=True)
    out = []; count = 0
    for line in lines:
        if 'draft.reserve(' in line:
            indent = line[:len(line) - len(line.lstrip())]
            if "pid[ss], 'PASSIVE'" in line:
                args = "ss, 'PASSIVE', qty, price"
            elif 'pid[ss], exec_route' in line:
                args = 'ss, exec_route, qty, exec_price'
            elif "pid[side], 'ACTIVE'" in line:
                args = "side, 'ACTIVE', qty, float(ask)"
            elif "pid[s], 'PASSIVE'" in line:
                args = "s, 'PASSIVE', qty, price"
            else:
                raise AssertionError(line)
            out.append(indent + f'if self.tail_new.veto(f, {args}): continue\n')
            count += 1
        out.append(line)
    assert count == 5, count
    return ''.join(out)


def self_test():
    count = 0
    for start, duration in ((0, 300000), (1788634800000, 300000), (10, 900)):
        end = start + duration; boundary = start + 2 * duration // 3
        for t in (start-1, start, boundary-1, boundary, boundary+1, end-1, end):
            for strong in ('UP', 'DOWN', None):
                for side in ('UP', 'DOWN'):
                    for route in ('PASSIVE', 'ACTIVE'):
                        expected = boundary <= t < end and side == strong and route == 'PASSIVE'
                        assert blocked(t, start, end, side, strong, route) == expected
                        count += 1
    return dict(status='PASS',boundary_symmetry_and_active_exemption_cases=count,
        pending_or_cash_mutation=False,local_native_jobs=0)
