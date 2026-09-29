"""Isolated research ablation: full passive lifecycle, no global180s cutoff."""
from .hft244_pair_paid_probe_v1 import make_sim as observer_base


def make_sim(minimal):
    Parent=observer_base(minimal)
    class Horizon(Parent):
        def __init__(self,tape,full=False):
            self._probe_full=full;self._probe_boundary=None
            self._probe_late_admissions=0;self._probe_closed_admissions=0
            super().__init__(tape,'A')

        def _open_one_option(self,t,qv,end):
            late=int(end)-int(t)<=minimal.v2.NO_NEW_EXPOSURE_MS
            if late and self._probe_boundary is None:
                self._probe_boundary=dict(t=int(t),prefix=self._probe_prefix(),inventory=dict(self.inv),cost=self.cost)
            if not self._probe_full or not late:
                return minimal.MinimalPairRoleSim._open_one_option(self,t,qv,end)
            if int(t)>=int(end):
                self.veto['MARKET_ENDED_NO_NEW_REQUEST']+=1;return
            # Exact original opener after removing only its early horizon return.
            side,role,require_pair,require_budget=self._role_decision(qv)
            if len(self._live_role_rows(side=side))>=self.max_slots:
                self.veto['SIDE_SLOT_CAP_FULL']+=1;return
            cand=self._candidate_from_levels(side,require_pair,require_budget)
            if cand is None:
                self.role_budget_blocks[role]+=1;return
            p,q,proj=cand
            before=self.submits
            self._submit_role(t,side,role,p,q,proj,'CURRENT_STRICT_PAST_LIVE_BOOK_ROLE_SEPARATED')
            self._probe_late_admissions+=self.submits-before
    return Horizon
