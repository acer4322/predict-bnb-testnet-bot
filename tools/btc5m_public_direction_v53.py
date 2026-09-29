"""Current public-book direction; physical inventory never grants direction.

This is a transparent research proxy, not a fitted Target belief estimator.
Use the pre-existing V52 coefficients, without a first-fill amplitude latch.
"""
import math


def observe_public(frame, theta, previous=None):
    book = frame['book']
    if not book['bids'] or not book['asks']:
        return dict(side=previous, score=None, amplitude=0., reason='NO_PUBLIC_BOOK')
    bid, ask = max(book['bids']), min(book['asks'])
    if not (0 < bid < ask < 1):
        return dict(side=previous, score=None, amplitude=0., reason='INVALID_PUBLIC_BOOK')
    bq, aq = float(book['bids'][bid]), float(book['asks'][ask])
    mid = (bid + ask) / 2.
    imbalance = (bq - aq) / max(1., bq + aq)
    score = theta[1] * (2. * mid - 1.) + theta[2] * imbalance
    assert math.isfinite(score)
    side = 'UP' if score > 1e-12 else 'DOWN' if score < -1e-12 else previous
    return dict(side=side, score=score, amplitude=math.tanh(abs(score)),
                reason='CURRENT_PUBLIC_BOOK' if abs(score) > 1e-12 else 'PUBLIC_TIE_RETAIN',
                mid=mid, depth_imbalance=imbalance)


class PublicExposureIntent:
    """Expose positive role-relative magnitude; the bridge owns the sign.

    Physical signed exposure is side_sign * applied_exposure. The downstream
    share map already swaps UP/DOWN, so signing again would invert it twice.
    Existing addition_growth/growth_hold still apply confirmed-own risk control.
    """
    def __init__(self, mode):
        assert mode == 'NO_DIRECTION'
        self.mode = mode
        self.sign = 0
        self.birth = None
        self.rows = []
        self.amplitude_rows = []
        # Compatibility fields explicitly empty: there is no held amplitude.
        self.held_amplitude = None
        self.amplitude_birth = None
        self.signal = None

    def apply(self, legacy, own_net, frame):
        from roles_runtime import roles
        signal = roles.public_signal
        self.signal = dict(signal)
        self.sign = 1 if roles.side == 'UP' else -1 if roles.side == 'DOWN' else 0
        self.birth = roles.birth
        applied = signal['amplitude'] if self.sign else 0.
        self.amplitude_rows.append(dict(t=int(frame['t']), index=int(frame['index']),
            inv=dict(frame['own_view']['inv']), current_amplitude=applied, held_amplitude=None,
            applied_exposure=applied, physical_signed_exposure=self.sign * applied,
            public_signal=dict(signal), amplitude_birth=None))
        return applied

    def capture(self, frame, producer, ledger, legacy, applied, desired):
        import collections
        accounts = {s: ledger.account(pid) for pid, s in ((1, 'UP'), (2, 'DOWN'))}
        self.rows.append(dict(t=int(frame['t']), index=int(frame['index']),
            gateway_state_id=frame['gateway_state_id'], inv=dict(frame['own_view']['inv']),
            cost=float(frame['own_view']['cost']), legacy_exposure=legacy,
            applied_exposure=applied, physical_signed_exposure=self.sign * applied,
            retained_direction_sign=self.sign, public_signal=dict(self.signal),
            desired=dict(desired), atomic_outstanding=producer.atomic.totals(),
            atomic_completed=producer.atomic.completed,
            reserved_qty={s: a['reserved_qty'] for s, a in accounts.items()},
            reserved_cash={s: a['reserved_cash'] for s, a in accounts.items()},
            carrier_states=dict(collections.Counter(c.state for c in ledger.carriers.values())),
            terminal_total=sum(c.state == 'TERMINAL' for c in ledger.carriers.values()),
            first_direction_birth=self.birth))
