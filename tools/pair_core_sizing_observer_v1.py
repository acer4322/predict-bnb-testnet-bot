"""Bounded inert call-site observer for an explicitly supplied sizing factory."""
from collections import Counter
from .hft244_pair_paid_probe_v1 import digest


def make_observed(Parent, minimal):
    class Observed(Parent):
        def __init__(self, tape):
            self._sz_frames=[]; self._sz_current=None
            self._sz_risk=dict(peakAbsNet=0., absNetIntegral=0., minEndpoint=0.)
            self._sz_previous=None
            super().__init__(tape,4,False)

        def process(self,t):
            if self._sz_previous is not None:
                previous, exposure=self._sz_previous
                self._sz_risk['absNetIntegral']+=max(0,t-previous)/1000*exposure
            super().process(t)
            exposure=abs(self.inv['UP']-self.inv['DOWN'])
            self._sz_previous=(t,exposure)
            self._sz_risk['peakAbsNet']=max(self._sz_risk['peakAbsNet'],exposure)
            self._sz_risk['minEndpoint']=min(self._sz_risk['minEndpoint'],min(self.inv.values())-self.cost)

        def _open_one_option(self,t,qv,end):
            assert self._sz_current is None
            frame=dict(t=int(t),remainingMs=int(end)-int(t),roles=[],candidates=[],submits=[],
                       domain={}, occupied=len(self.slot_key))
            # Capture book support even when opener returns BEFORE role decision.
            for side in ('UP','DOWN'):
                raw=(sorted(self.book['bids'],reverse=True) if side=='UP'
                     else [1.-p for p in sorted(self.book['asks'])])
                prices=sorted({minimal.v2.kprice(p) for p in raw if 0<p<1})
                frame['domain'][side]=dict(visible=len(prices),
                    reject12=sum(1/p>12+1e-9 for p in prices),
                    reject18=sum(1/p>18+1e-9 for p in prices),
                    added18=[p for p in prices if 12+1e-9<1/p<=18+1e-9])
            before=dict(self.veto);self._sz_current=frame
            try:
                result=super()._open_one_option(t,qv,end)
                frame['veto']={k:v-before.get(k,0) for k,v in self.veto.items() if v!=before.get(k,0)}
                frame['outcome']=('BLOCK_BEFORE_ROLE' if not frame['roles'] else
                    'ADMITTED' if any(x['ok'] for x in frame['submits']) else
                    'REJECTED_AFTER_ROLE')
                assert len(self._sz_frames)<20000,'bounded trace exceeded'
                self._sz_frames.append(frame)
                return result
            finally:self._sz_current=None

        def _role_decision(self,qv):
            result=super()._role_decision(qv)
            if self._sz_current is not None:self._sz_current['roles'].append(list(result))
            return result

        def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
            result=super()._candidate_from_levels(side,require_pair,require_budget)
            if self._sz_current is not None:
                self._sz_current['candidates'].append(dict(side=side,result=result))
            return result

        def _submit_role(self,t,side,role,p,q,proj,source):
            n=self.n
            ok=super()._submit_role(t,side,role,p,q,proj,source)
            if self._sz_current is not None:
                self._sz_current['submits'].append(dict(side=side,role=role,price=p,qty=q,
                    orderId=n,key=f'{side}_{n}',ok=bool(ok)))
            return ok

        def behavior_digest(self):
            omitted={'bt','payload','events','times','meta','_receipt_reader','_receipt_ledger'}
            state={k:v for k,v in vars(self).items() if k not in omitted and not k.startswith('_sz_')}
            return digest(dict(policy=state,ledger=vars(self._receipt_ledger),
                nativeOrders={k:self.snap(o) for k,o in self.orders.items()},
                nativeTime=int(self.bt.current_timestamp)))

    return Observed


def summarize(sim):
    phase_role=Counter();domain=Counter();admitted=[]
    for f in sim._sz_frames:
        phase='EARLY' if f['remainingMs']>180000 else 'LATE'
        role=f['roles'][0][1] if f['roles'] else 'BLOCK_BEFORE_ROLE'
        phase_role[f'{phase}:{role}:{f["outcome"]}']+=1
        for side,d in f['domain'].items():
            domain[f'{phase}:{side}:clocksWithAdded18']+=bool(d['added18'])
        for a in f['submits']:
            if not a['ok']:continue
            order=sim.orders[a['key']];snap=sim.snap(order)
            qty=float(snap.get('cumExecQty') or 0.)
            assert qty<=a['qty']+1e-8
            admitted.append(dict(**a,t=f['t'],phase=phase,filledQty=qty,status=snap['status'],
                fillClass='ZERO' if qty<=1e-9 else 'FULL' if qty>=a['qty']-1e-8 else 'PARTIAL'))
    return dict(phaseRole=dict(phase_role),domain=dict(domain),admitted=admitted,
                risk=sim._sz_risk,behaviorDigest=sim.behavior_digest(),framesDigest=digest(sim._sz_frames))
