"""A confirmed completed monetary work may be followed by a new bounded work.

No timer, zero-payoff objective, rolling high-water mark, or Target observation.
This component supplies at most ONE additional Active service in the experiment.
The frozen coordinator still handles the original services unchanged.
"""
from copy import deepcopy
from roles_runtime import roles

EPS = 1e-8


class WorkMemory:
    def __init__(self):
        self.retired = False
        self.previous = None
        self.work = None
        self.next_id = 1
        self.events = []

    def advance(self, state, t, old_episode, old_services, detect):
        # Missing or fully filled nonterminal owners are not terminal receipts.
        terminal = len(old_services) == 2 and all(x == 'TERMINAL' for x in old_services)
        if not self.retired:
            if not terminal or old_episode is None or state['payoff'][roles.weak] < old_episode['anchor_floor']-EPS:
                return dict(retired=False, work=None, reason='WAIT_FOR_CONFIRMED_OLD_WORK_COMPLETION')
            self.retired = True
            self.previous = deepcopy({k: state[k] for k in ('inv','cost','payoff')})
            self.events.append(dict(kind='OLD_WORK_CONFIRMED_COMPLETE', t=t,
                anchor=old_episode['anchor_floor'], confirmed_payoff=state['payoff'][roles.weak]))
            return dict(retired=True, work=None, reason='OLD_WORK_CONFIRMED_COMPLETE')
        if self.work is not None and state['payoff'][roles.weak] >= self.work['anchor_floor']-EPS:
            self.events.append(dict(kind='RENEWED_WORK_CONFIRMED_COMPLETE',t=t,id=self.work['id'],
                anchor=self.work['anchor_floor'],confirmed_payoff=state['payoff'][roles.weak]))
            self.work = None
        if self.work is None:
            found = detect(self.previous,state,True,True,t)
            if found is not None:
                self.work = deepcopy(found)
                self.work['id'] = self.next_id; self.next_id += 1
                self.events.append(dict(kind='RENEWED_WORK_BORN',t=t,id=self.work['id'],
                    anchor=self.work['anchor_floor'],previous=deepcopy(self.previous),
                    current=deepcopy({k:state[k] for k in ('inv','cost','payoff')})))
        self.previous = deepcopy({k:state[k] for k in ('inv','cost','payoff')})
        return dict(retired=True,work=deepcopy(self.work),reason='WORK_OPEN' if self.work else 'WAIT_NEW_CONFIRMED_REEXPOSURE')


class RenewalProbe:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.memory = WorkMemory()
        self.rows = []
        self.submissions = []

    def apply(self, frame, producer, operations, validate, crossing, coordinator, detect, decide):
        if not frame['start'] <= frame['t'] < frame['end']:
            return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t'] != frame['t']:
            return operations
        ledger = frame['ledger']; state = self.snapshot(frame, ledger)
        old_states = [getattr(ledger.carriers.get(o['key']),'state',None) for o in coordinator.submissions[:2]]
        status = self.memory.advance(state,int(frame['t']),coordinator.episode,old_states,detect)
        ask = ((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
        bids = roles.weak_bid_book(frame); best = max(bids) if bids else None
        depth = float(bids[best]) if best is not None else 0.
        if ask is not None and best is not None:
            assert abs(ask-round(1-best,10)) < EPS
        row = dict(t=int(frame['t']),state=state,old_service_states=old_states,status=status,
            original_operations=deepcopy(operations),active_ask=ask,visible_depth=depth,
            gateway_state_id=frame['gateway_state_id'],decision=None,submitted=False)
        if status['work'] is not None and not self.submissions:
            # Keep the original first-service route rule: Passive15 has priority
            # whenever its minimum legal quote is strictly below current ask.
            decision = decide(state,operations,ask,depth,status['work'],True,crossing,
                frame['world_profile']['quantity_step'],frame['world_profile']['tick'],continuation=False)
            if decision['eligible'] and len(state['owners'])+sum(o['kind']=='NEW' for o in operations) >= frame['world_profile']['max_live_owners']:
                decision.update(eligible=False,reason='RESOURCE_OWNER_LIMIT')
            row['decision'] = decision
            if decision['eligible']:
                q=decision['quantity']
                validate(frame['world_profile']['asset'],'ACTIVE',ask,q,quantity_step=frame['world_profile']['quantity_step'])
                assert abs(ask/frame['world_profile']['tick']-round(ask/frame['world_profile']['tick']))<EPS
                index=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
                op=dict(kind='NEW',key=f'{roles.weak}_{index}',parent_id=roles.pid(roles.weak),
                    side=roles.weak,route='ACTIVE',price=ask,qty=q,role='ACTIVE_RENEWED_FINITE_REPAIR')
                submission=dict(t=int(frame['t']),**op)
                self.submissions.append(dict(work_id=status['work']['id'],anchor=status['work']['anchor_floor'],**submission))
                coordinator.submissions.append(submission)
                row['submitted']=True
                operations=[*operations,op]
        self.rows.append(row)
        return operations
