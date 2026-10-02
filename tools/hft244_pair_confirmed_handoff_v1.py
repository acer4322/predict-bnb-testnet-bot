"""Research-only cancel/ack/single-route contract, no economic selector."""
from .hft244_pair_paid_probe_v1 import make_sim as probe_class
from .hft244_pair_route_legality_v1 import crossing_owners

EPS=1e-9

def ready_reason(*,remaining_conflicts,ask,limit,available,qty,occupied,late):
    if late:return 'ABORT_INHERITED_BOUNDARY'
    if remaining_conflicts:return 'WAIT_TERMINAL_ACK'
    if ask is None or ask>limit+EPS:return 'ABORT_NO_CHASE_PRICE'
    if available+EPS<qty:return 'ABORT_LIABILITY_OR_RESERVATION_CHANGED'
    if occupied>=4:return 'ABORT_CAPACITY_CHANGED'
    return 'READY'

def make_sim(minimal):
    Parent=probe_class(minimal)
    class Handoff(Parent):
        def __init__(self,tape,arm):
            super().__init__(tape,arm)
            self._probe_stage='UNSELECTED';self._probe_conflicts=[]
            self._probe_events=[];self._probe_hold_clocks=0;self._probe_cross_blocks=0
            self._probe_ready=None;self._probe_cross_seen=0

        def _reservations(self):
            return [dict(key=k,side=self.orders[k]['side'],price=self.orders[k]['price'],
                         remaining=self._remaining(k),status=self.snap(self.orders[k])['status'],
                         cancelRequested=bool(self.orders[k].get('cancelRequested')))
                    for k in self.slot_key.values()]

        def _event(self,t,event,**data):
            assert len(self._probe_events)<100
            self._probe_events.append(dict(t=int(t),event=event,**data))

        def _submit_role(self,t,side,role,p,q,proj,source):
            # A proposed price is not a physically admissible request when the
            # original capacity check already rejects it. Preserve its counters.
            if len(self.slot_key)>=self.max_slots:
                return super()._submit_role(t,side,role,p,q,proj,source)
            hits=crossing_owners(side,p,self._reservations())
            if hits:
                self._probe_cross_seen+=1
                # Local cancel-pending can clear before a request reaches venue.
                # Potential cross is NOT proof of an exchange-time self-match.
                # A and every pre-selection prefix retain native baseline truth.
                if self._probe_arm!='A' and self._probe_stage!='UNSELECTED':
                    self._probe_cross_blocks+=1
                    return False
            return super()._submit_role(t,side,role,p,q,proj,source)

        def _open_one_option(self,t,qv,end):
            if self._probe_stage=='UNSELECTED' and end-t>minimal.v2.NO_NEW_EXPOSURE_MS:
                option=self._probe_select(qv)
                if option:
                    reserved=self._reservations();hits=crossing_owners(option['side'],option['price'],reserved)
                    # Exercise the conflict-handoff mechanism, not a search over
                    # later profitable clocks. First prefix-qualified conflict.
                    if hits:
                        self._probe_mark=dict(t=int(t),option=option,prefix=self._probe_prefix(),
                                              reserved=reserved,conflicts=hits,inventory=dict(self.inv),cost=self.cost)
                        self._probe_conflicts=list(hits)
                        self._probe_stage='A_OBSERVED' if self._probe_arm=='A' else 'WAIT_ACK'
                        self._event(t,'INTENT_SELECTED',conflicts=hits,option=option)
            if self._probe_stage=='WAIT_ACK':
                self._probe_hold_clocks+=1
                option=self._probe_mark['option'];side=option['side'];opp='DOWN' if side=='UP' else 'UP'
                reserved=self._reservations();live_keys={r['key'] for r in reserved}
                remaining=[k for k in self._probe_conflicts if k in live_keys]
                pending=sum(r['remaining'] for r in reserved if r['side']==side)
                available=sum(q for q,p in self.un[opp])-pending
                reason=ready_reason(remaining_conflicts=remaining,ask=(qv.get(side) or {}).get('ask'),
                    limit=option['price'],available=available,qty=option['qty'],occupied=len(reserved),
                    late=end-t<=minimal.v2.NO_NEW_EXPOSURE_MS)
                if reason=='WAIT_TERMINAL_ACK':
                    for k in remaining:
                        sid=next(s for s,key in self.slot_key.items() if key==k)
                        if self._request_cancel(t,sid,'PAID_HANDOFF_SELF_CROSS'):
                            self._event(t,'CANCEL_REQUESTED',key=k)
                    return
                if reason!='READY':
                    self._probe_stage=reason;self._event(t,reason)
                    return minimal.MinimalPairRoleSim._open_one_option(self,t,qv,end)
                # A slot disappears only after inherited explicit-terminal
                # refresh. Validate every requested conflict's authoritative ACK.
                terminals={k:self.snap(self.orders[k])['status'] for k in self._probe_conflicts}
                assert all(s in minimal.v2.TERMINAL_STATUSES for s in terminals.values())
                assert not crossing_owners(side,option['price'],reserved),'new cross after acknowledged clearance'
                self._probe_ready=dict(t=int(t),prefix=self._probe_prefix(),terminals=terminals,
                                       option=option,currentAsk=qv[side]['ask'],available=available)
                self._event(t,'TERMINAL_ACK_READY',terminals=terminals)
                if self._probe_arm=='T':
                    self._probe_submit(t,option)
                    self._probe_stage='ACTIVE_OWNER_PENDING';self._event(t,'ACTIVE_SUBMITTED',key=self._probe_key)
                else:
                    self._probe_stage='CANCEL_ONLY_COMPLETE';self._event(t,'NO_ACTIVE_CONTROL')
                return  # Both C/T consume this protocol admission clock.
            if self._probe_stage=='ACTIVE_OWNER_PENDING' and self._probe_key not in self.slot_key.values():
                status=self.snap(self.orders[self._probe_key])['status']
                assert status in minimal.v2.TERMINAL_STATUSES
                self._probe_stage='ACTIVE_TERMINAL';self._event(t,'ACTIVE_TERMINAL',key=self._probe_key,status=status)
            return minimal.MinimalPairRoleSim._open_one_option(self,t,qv,end)
    return Handoff
