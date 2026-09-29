"""Research action probe, NOT an economic selector or production controller."""
from collections import deque
import hashlib
import json
import math

EPS=1e-9

def candidate(side,role,ask,average,unmatched,pending,occupied,used_prices):
    if role not in ('ECONOMIC_CORE','SATELLITE_REPAIR') or occupied>=4:return None
    if ask is None or not math.isfinite(ask) or not EPS<ask<1-EPS:return None
    if average is None or average+ask<=1.0000001:return None
    q=1./ask  # Inherit baseline ticket notional; do not scale up to close a gap.
    if q>12.+EPS or q>unmatched-pending+EPS:return None
    if any(abs(ask-p)<EPS for p in used_prices):return None
    return dict(side=side,price=ask,qty=q,notional=1.,oppositeAverage=average,
                oppositeUnmatched=unmatched,pendingRepairQty=pending,
                paidPairExcess=average+ask-1)

def normalize(value):
    if value is None or isinstance(value,(str,bool,int,float)):return value
    if isinstance(value,dict):
        return ['MAP',[[normalize(k),normalize(v)] for k,v in sorted(value.items(),key=lambda x:repr(x[0]))]]
    if isinstance(value,(list,tuple,deque)):return [normalize(x) for x in value]
    if isinstance(value,set):return ['SET',sorted([normalize(x) for x in value],key=repr)]
    if hasattr(value,'item'):return normalize(value.item())
    raise TypeError('unrepresented policy state: '+str(type(value)))

def digest(value):return hashlib.sha256(json.dumps(normalize(value),allow_nan=False).encode()).hexdigest()

def make_sim(minimal):
    class PaidProbe(minimal.MinimalPairRoleSim):
        def __init__(self,tape,arm):
            self._probe_arm=arm;self._probe_mark=None;self._probe_key=None
            self._probe_t=None;self._probe_prev=None
            self._probe_risk=dict(absNetIntegral=0.,grossIntegral=0.,peakAbsNet=0.,peakGross=0.,minEndpoint=0.)
            super().__init__(tape,4,False)

        def process(self,t):
            if self._probe_t is not None:
                dt=max(0,int(t)-self._probe_t)/1000
                self._probe_risk['absNetIntegral']+=dt*self._probe_prev[0]
                self._probe_risk['grossIntegral']+=dt*self._probe_prev[1]
            super().process(t)
            ab=abs(self.inv['UP']-self.inv['DOWN']);gross=sum(self.inv.values())
            self._probe_t=int(t);self._probe_prev=(ab,gross)
            self._probe_risk['peakAbsNet']=max(self._probe_risk['peakAbsNet'],ab)
            self._probe_risk['peakGross']=max(self._probe_risk['peakGross'],gross)
            self._probe_risk['minEndpoint']=min(self._probe_risk['minEndpoint'],min(self.inv.values())-self.cost)

        def _probe_prefix(self):
            excluded={'bt','payload','events','times','meta','_receipt_reader','_receipt_ledger'}
            state={k:v for k,v in vars(self).items() if k not in excluded and not k.startswith('_probe_')}
            return digest(dict(policy=state,ledger=vars(self._receipt_ledger),
                               nativeOrders={k:self.snap(o) for k,o in self.orders.items()},
                               nativeTime=int(self.bt.current_timestamp)))

        def _probe_select(self,qv):
            side,role,_,_=self._role_decision(qv);opp='DOWN' if side=='UP' else 'UP'
            # slot_key includes submit/NONE and cancel-pending; only explicit
            # terminal acknowledgement releases authority in the inherited base.
            pending=sum(self._remaining(k) for k in self.slot_key.values() if self.orders[k]['side']==side)
            used=[self.orders[k]['price'] for k in self.slot_key.values() if self.orders[k]['side']==side]
            return candidate(side,role,(qv.get(side) or {}).get('ask'),self.unmatched_avg(opp),
                             sum(q for q,p in self.un[opp]),pending,len(self.slot_key),used)

        def _open_one_option(self,t,qv,end):
            if self._probe_mark is None and end-t>minimal.v2.NO_NEW_EXPOSURE_MS:
                option=self._probe_select(qv)
                if option:
                    self._probe_mark=dict(t=int(t),option=option,prefix=self._probe_prefix(),
                        reserved=[dict(slot=s,key=k,side=self.orders[k]['side'],
                                       leaves=self._remaining(k),price=self.orders[k]['price'],
                                       status=self.snap(self.orders[k])['status'],
                                       cancelRequested=bool(self.orders[k].get('cancelRequested')))
                                  for s,k in self.slot_key.items()],
                        inventory=dict(self.inv),cost=self.cost)
                    if self._probe_arm=='PAID':
                        self._probe_submit(t,option)
                        return  # Substitute exactly this admission, then native continuation.
            return super()._open_one_option(t,qv,end)

        def _probe_submit(self,t,option):
            assert self._probe_key is None and len(self.slot_key)<4
            side=option['side'];p=option['price'];q=option['qty']
            sid=next(s for s in range(1,5) if s not in self.slot_key)
            n=self.n;native_side,native_p=minimal.v2.base.ex.native_order(side,p)
            assert abs((native_p if side=='UP' else 1-native_p)-p)<EPS
            method=self.bt.submit_buy_order if native_side=='BUY' else self.bt.submit_sell_order
            rc=int(method(0,n,native_p,q,minimal.v2.base.ex.hbt.GTC,minimal.v2.base.ex.LIMIT,False))
            assert rc==0,('paid submit failure',rc)
            self.n+=1;key=f'{side}_{n}';self._probe_key=key
            self.orders[key]=dict(n=n,side=side,price=p,qty=q,cum=0.,placed=int(t),status='NEW')
            self.slot_key[sid]=key;self.key_role[key]='PAID_REPAIR_PROBE'
            self.placeHist.append((int(t),side,q,p));self.submits+=1
            self.role_submits['PAID_REPAIR_PROBE']+=1
            self.slot_history.append(dict(t=int(t),event='PAID_REPAIR_PROBE_SUBMIT',key=key,
                                         slotId=sid,side=side,price=p,qty=q))
            # GTC marketable limit, unchanged expiry/reanchor afterwards. It may
            # partially fill, rest, or cancel; never reprice or retry this probe.
    return PaidProbe
