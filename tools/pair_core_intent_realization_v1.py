"""Behavior-inert Minimal Pair intent/admission/receipt observer. No policy."""
from collections import Counter, defaultdict
import hashlib
import json

from .hft244_pair_decision_lineage_v1 import pure_quotes
from .hft244_pair_route_legality_v1 import crossing_owners


def blob(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def make_sim(minimal):
    class Observed(minimal.MinimalPairRoleSim):
        def __init__(self,tape):
            self._ir_current=None;self._ir_frames=[];self._ir_bytes=0
            super().__init__(tape,4,False)

        def _open_one_option(self,t,qv,end):
            assert self._ir_current is None
            owners=[]
            for slot,key in self.slot_key.items():
                o=self.orders[key];s=self.snap(o)
                owners.append(dict(slot=slot,key=key,side=o['side'],price=o['price'],remaining=self._remaining(key),
                                   status=s['status'],cancelRequested=bool(o.get('cancelRequested')),
                                   role=self.key_role.get(key,'UNASSIGNED')))
            lots={s:[[float(q),float(p)] for q,p in self.un[s]] for s in ('UP','DOWN')}
            menu=pure_quotes(self.book,lots,owners)
            for side in menu:
                cand=menu[side]['first']
                menu[side]['potentialCrossOwners']=crossing_owners(side,cand[0],owners) if cand else []
            frame=dict(t=int(t),remainingMs=int(end)-int(t),qv=qv,inventory=dict(self.inv),cost=self.cost,
                       fifo=lots,owners=owners,menu=menu,decisions=[],candidates=[],submits=[])
            self._ir_current=frame;before=dict(self.veto)
            try:
                result=super()._open_one_option(t,qv,end)
                frame['vetoDelta']={k:v-before.get(k,0) for k,v in self.veto.items() if v!=before.get(k,0)}
                if frame['submits']:
                    frame['outcome']='ADMITTED' if frame['submits'][-1]['ok'] else 'SUBMIT_REJECTED'
                elif not frame['decisions']:frame['outcome']='INHERITED_LATE_BOUNDARY'
                elif not frame['candidates']:frame['outcome']='SIDE_CAPACITY'
                else:frame['outcome']='NO_SELECTED_QUOTE_CANDIDATE'
                data=blob(frame);self._ir_bytes+=len(data)
                assert self._ir_bytes<=32*1024**2 and len(self._ir_frames)<20000,'trace resource cap'
                self._ir_frames.append(json.loads(data))
                return result
            finally:self._ir_current=None

        def _role_decision(self,qv):
            decision=super()._role_decision(qv)
            if self._ir_current is not None:self._ir_current['decisions'].append(list(decision))
            return decision

        def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
            result=super()._candidate_from_levels(side,require_pair,require_budget)
            if self._ir_current is not None:
                expected=self._ir_current['menu'][side]['first']
                assert (list(result) if result is not None else None)==expected,'pure quote parity'
                self._ir_current['candidates'].append(dict(side=side,result=list(result) if result else None))
            return result

        def _submit_role(self,t,side,role,p,q,proj,source):
            n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source)
            if self._ir_current is not None:
                self._ir_current['submits'].append(dict(side=side,role=role,price=p,qty=q,ok=bool(ok),
                    orderId=n if ok else None,key=f'{side}_{n}' if ok else None))
            return ok
    return Observed


def summarize(frames,receipts):
    selected=Counter();outcomes=Counter();roles=Counter();availability=Counter()
    admitted=[];side_totals={s:dict(submits=0,requestedQty=0.,requestedNotional=0.,filledQty=0.,paid=0.,
                                 filledOwners=0,zeroFillOwners=0) for s in ('UP','DOWN')}
    allrows=defaultdict(list)
    for r in receipts:allrows[r['order_id']].append(r)
    used=set()
    for f in frames:
        outcomes[f['outcome']]+=1
        if f['decisions']:
            assert len(f['decisions'])==1,'more than one actual decision'
            side,role,_,_=f['decisions'][0];opp='DOWN' if side=='UP' else 'UP'
            selected[side]+=1;roles[role]+=1
            cm=f['menu'][side];om=f['menu'][opp]
            if cm['first'] is None and om['first'] is not None:
                availability['selectedMissingOtherQuoteExists']+=1
                if om['hasPhysicalSlot'] and not om['potentialCrossOwners']:
                    availability['selectedMissingOtherQuoteSlotAndNoPotentialCross']+=1
            if cm['first'] is not None and om['first'] is not None:availability['bothQuoteCandidates']+=1
        for a in f['submits']:
            if not a['ok']:continue
            assert a['orderId'] not in used;used.add(a['orderId'])
            rs=allrows.get(a['orderId'],[])
            assert all(('UP' if r['side']==1 else 'DOWN')==a['side'] for r in rs)
            qty=sum(r['qty'] for r in rs)
            paid=sum(r['qty']*(r['price'] if r['side']==1 else 1-r['price']) for r in rs)
            assert qty<=a['qty']+1e-8
            s=side_totals[a['side']];s['submits']+=1;s['requestedQty']+=a['qty'];s['requestedNotional']+=a['price']*a['qty']
            s['filledQty']+=qty;s['paid']+=paid;s['filledOwners']+=bool(rs);s['zeroFillOwners']+=not rs
            admitted.append(dict(t=f['t'],**a,filledQty=qty,paid=paid,nativeReceiptCount=len(rs)))
    assert set(allrows)<=used,'orphan native receipt'
    for s in side_totals.values():
        s['shareRealizationRate']=s['filledQty']/s['requestedQty'] if s['requestedQty'] else None
        s['ownerFillRate']=s['filledOwners']/s['submits'] if s['submits'] else None
    return dict(clocks=len(frames),actualDecisions=sum(selected.values()),selectedSide=dict(selected),roles=dict(roles),
                outcomes=dict(outcomes),quoteAvailability=dict(availability),sideTotals=side_totals,admitted=admitted,
                traceSha256=hashlib.sha256(blob(frames)).hexdigest(),
                warning='Quote availability is not complete alternate authority; submission rates are descriptive, not counterfactual fills')
