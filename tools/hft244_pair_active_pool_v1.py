"""User-requested passive4 + independent one-shot Active1 research fork.

The Active owner is never hidden from reservation, accounting or cross checks.
Only passive capacity accounting excludes that one identified owner.
"""
from types import SimpleNamespace
from .hft244_pair_confirmed_handoff_v1 import make_sim as handoff_class
from .hft244_pair_route_legality_v1 import crossing_owners


def pool_counts(slots, active_key):
    active = [(s,k) for s,k in slots.items() if k == active_key]
    passive = [(s,k) for s,k in slots.items() if k != active_key]
    if len(active)>1 or any(s!=5 for s,k in active):
        raise ValueError('Active pool ownership mismatch')
    if len(passive)>4 or any(s not in (1,2,3,4) for s,k in passive):
        raise ValueError('passive pool ownership mismatch')
    return len(passive),len(active)


def make_sim(minimal):
    class PoolMinimal(minimal.MinimalPairRoleSim):
        def _open_one_option(self,t,qv,end):
            if int(end)-int(t)<=minimal.v2.NO_NEW_EXPOSURE_MS:
                self.veto['LATE_180S']+=1;return
            side,role,require_pair,require_budget=self._role_decision(qv)
            rows=[r for r in self._live_role_rows(side=side) if r[1]!=self._probe_key]
            if len(rows)>=self.max_slots:
                self.veto['SIDE_SLOT_CAP_FULL']+=1;return
            cand=self._candidate_from_levels(side,require_pair,require_budget)
            if cand is None:
                self.role_budget_blocks[role]+=1;return
            p,q,proj=cand
            self._submit_role(t,side,role,p,q,proj,'CURRENT_STRICT_PAST_LIVE_BOOK_ROLE_SEPARATED')

        def _reanchor_stale(self,t):
            if len(self.slot_key)<=4:
                return super()._reanchor_stale(t)
            # Exact inherited reanchor policy; physical5 is not a passive-capacity
            # violation. No TTL/Pair/price preservation exception for Active.
            assert pool_counts(self.slot_key,self._probe_key)==(4,1)
            for sid,key in list(self.slot_key.items()):
                o=self.orders.get(key)
                if not o or o.get('cancelRequested'):continue
                role=self.key_role.get(key,'UNASSIGNED');side=str(o['side']);p=minimal.v2.kprice(o['price'])
                levels=[minimal.v2.kprice(x) for x in self._live_price_levels(side)]
                if role=='ECONOMIC_CORE':
                    if p in levels and self._pair_ok(side,p):
                        self.core_preserved_clocks+=1;continue
                    self._request_cancel(t,sid,'CORE_INVALIDATED');continue
                if p not in levels:
                    if self._request_cancel(t,sid,'SATELLITE_FRONTIER_REANCHOR'):self.reanchors+=1

    Parent=handoff_class(SimpleNamespace(MinimalPairRoleSim=PoolMinimal,v2=minimal.v2))
    class SeparatePool(Parent):
        def __init__(self,tape,arm='T'):
            self._probe_pool_peak=dict(passive=0,active=0,total=0)
            self._probe_pool_extra_admissions=0
            super().__init__(tape,arm)

        def _probe_submit(self,t,option):
            super()._probe_submit(t,option)
            sid=next(s for s,k in self.slot_key.items() if k==self._probe_key)
            assert sid in (1,2,3,4) and 5 not in self.slot_key
            del self.slot_key[sid];self.slot_key[5]=self._probe_key
            assert self.slot_history[-1]['event']=='PAID_REPAIR_PROBE_SUBMIT'
            self.slot_history[-1]['slotId']=5
            pool_counts(self.slot_key,self._probe_key)

        def _submit_role(self,t,side,role,p,q,proj,source):
            if self._probe_key not in self.slot_key.values():
                return super()._submit_role(t,side,role,p,q,proj,source)
            passive,active=pool_counts(self.slot_key,self._probe_key)
            if passive>=4:
                self.veto['GLOBAL_SLOT_CAP_FULL']+=1;return False
            # Include slot5, NONE, live and cancel-pending in crossing_owners.
            hits=crossing_owners(side,p,self._reservations())
            if hits:
                self._probe_cross_seen+=1
                if self._probe_arm!='A' and self._probe_stage!='UNSELECTED':
                    self._probe_cross_blocks+=1;return False
            if self.serialize_same_side and self._same_side_reserved(side):
                self.serialization_blocks+=1;self.veto['SAME_SIDE_SERIALIZATION']+=1;return False
            free=next((sid for sid in range(1,5) if sid not in self.slot_key),None)
            assert free is not None
            if passive==3 and active==1:self._probe_pool_extra_admissions+=1
            before_n=self.n;self.submit(int(t),side,float(p),float(q));key=f'{side}_{before_n}'
            self.slot_key[free]=key;self.key_role[key]=role;self.role_submits[role]+=1
            credit,spend=self._pair_edge(side,p,q)
            self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
            state,held,_=self._state()
            if state=='ONE_SIDED' and role=='ECONOMIC_CORE':self.prebase_core_submits+=1
            if state=='ONE_SIDED' and role=='SATELLITE_EXPAND' and side==held:self.prebase_same_side_satellite_submits+=1
            self.slot_history.append(dict(t=int(t),event='ROLE_SLOT_SUBMIT',slotId=free,key=key,role=role,
                side=side,price=float(p),qty=float(q),jointProjectedFloor=None,physicalFloor=self._physical_floor(),source=source))
            pool_counts(self.slot_key,self._probe_key)
            return True

        def _sample_occupancy(self):
            p,a=pool_counts(self.slot_key,self._probe_key)
            super()._sample_occupancy()
            for name,value in (('passive',p),('active',a),('total',p+a)):
                self._probe_pool_peak[name]=max(self._probe_pool_peak[name],value)
    return SeparatePool
