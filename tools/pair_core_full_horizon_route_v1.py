"""Research-only composition of full-horizon continuation and frozen handoff.

No shared module is patched. All arms use one passive4/Active1 factory; only X
can actually create an Active owner. This is an action-family probe, not a value
selector, and does not represent repeated or composite Target responsibilities.
"""
from types import SimpleNamespace
from .hft244_pair_active_pool_v1 import make_sim as pool_factory


def full_horizon_view(minimal):
    # PoolMinimal and handoff read this view, not the global inherited constant.
    values = dict(vars(minimal.v2))
    values['NO_NEW_EXPOSURE_MS'] = 0
    return SimpleNamespace(MinimalPairRoleSim=minimal.MinimalPairRoleSim,
                           v2=SimpleNamespace(**values))


def make_sim(minimal):
    Parent = pool_factory(full_horizon_view(minimal))

    class FullHorizonRoute(Parent):
        def __init__(self, tape, arm):
            if arm not in ('A', 'C', 'T'):
                raise ValueError('unregistered arm')
            super().__init__(tape, arm)
            self._probe_boundary = None
            self._probe_late_admissions = 0

        def _open_one_option(self, t, qv, end):
            late = int(end) - int(t) <= minimal.v2.NO_NEW_EXPOSURE_MS
            if late and self._probe_boundary is None:
                self._probe_boundary = dict(t=int(t), prefix=self._probe_prefix(),
                                            inventory=dict(self.inv), cost=self.cost)
            if int(t) >= int(end):
                # Stop admission, not ownership: no synthetic cancel/settlement.
                self.veto['MARKET_ENDED_NO_NEW_REQUEST'] += 1
                if self._probe_stage == 'WAIT_ACK':
                    self._probe_stage = 'ABORT_MARKET_ENDED'
                    self._event(t, self._probe_stage)
                return
            before = self.submits
            result = super()._open_one_option(t, qv, end)
            if late:
                self._probe_late_admissions += self.submits - before
            return result

    return FullHorizonRoute
