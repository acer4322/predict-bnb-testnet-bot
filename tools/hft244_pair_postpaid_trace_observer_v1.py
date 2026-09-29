"""Behavior-inert decision tracing: delegate once, read Python fields only."""
import json
from pathlib import Path
MAX_CLOCKS=12000
MAX_BYTES=16*1024*1024


def python_state(sim):
    unmatched={}
    for side in ('UP','DOWN'):
        lots=[(float(q),float(p)) for q,p in sim.un[side]]
        qty=sum(q for q,p in lots);cost=sum(q*p for q,p in lots)
        unmatched[side]=dict(qty=qty,cost=cost,avg=cost/qty if qty>1e-9 else None,
                             lotCount=len(lots),firstLots=lots[:6])
    reserved=[]
    for sid,key in sim.slot_key.items():
        o=sim.orders[key]
        reserved.append(dict(slot=int(sid),key=key,n=o.get('n'),generation=o.get('_receiptGeneration'),
          side=o['side'],role=sim.key_role.get(key),price=o['price'],qty=o['qty'],cum=o.get('cum',0.),
          placed=o.get('placed'),pythonStatus=o.get('status'),cancelRequested=bool(o.get('cancelRequested'))))
    return dict(inv=dict(sim.inv),cost=float(sim.cost),unmatched=unmatched,reserved=reserved,
                stage=sim._probe_stage,receiptCount=len(sim._receipt_ledger.seen))


def make_observer(parent):
    class Observed(parent):
        def __init__(self,tape,arm,trace_path):
            self._trace_record=None;self._trace_clock=0;self._trace_bytes=0;self._trace_context='NONE'
            self._trace_stream=Path(trace_path).open('wb')
            try:super().__init__(tape,arm)
            except BaseException:self._trace_stream.close();raise

        def _record_event(self,kind,**data):
            if self._trace_record is not None:self._trace_record['events'].append(dict(kind=kind,**data))

        def _reanchor_stale(self,t):
            if self._trace_record is not None:raise RuntimeError('unfinished observation clock')
            if self._trace_clock>=MAX_CLOCKS:raise RuntimeError('CAPTURE_STOP_CLOCK_LIMIT')
            self._trace_clock+=1
            self._trace_record=dict(index=self._trace_clock,t=int(t),before=python_state(self),events=[])
            self._trace_context='REANCHOR'
            return super()._reanchor_stale(t)

        def _open_one_option(self,t,qv,end):
            if self._trace_record is None:raise RuntimeError('missing reanchor observation clock')
            self._trace_context='ADMISSION';self._trace_record['quotes']=qv
            self._trace_record['remainingMs']=int(end-t)
            ans=super()._open_one_option(t,qv,end)
            self._trace_record['after']=python_state(self)
            blob=(json.dumps(self._trace_record,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
            if self._trace_bytes+len(blob)>MAX_BYTES:raise RuntimeError('CAPTURE_STOP_BYTE_LIMIT')
            self._trace_stream.write(blob);self._trace_bytes+=len(blob)
            self._trace_record=None;self._trace_context='NONE'
            return ans

        def _role_decision(self,qv):
            ans=super()._role_decision(qv)
            self._record_event('ROLE',context=self._trace_context,choice=ans)
            return ans

        def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
            old=self._trace_context;self._trace_context='CANDIDATE'
            try:
                ans=super()._candidate_from_levels(side,require_pair,require_budget)
                self._record_event('CANDIDATE_RESULT',side=side,requirePair=bool(require_pair),
                                    requireBudget=bool(require_budget),candidate=ans)
                return ans
            finally:self._trace_context=old

        def _pair_ok(self,side,price):
            ans=super()._pair_ok(side,price)
            self._record_event('PAIR_CHECK',context=self._trace_context,side=side,price=float(price),accepted=bool(ans))
            return ans

        def _submit_role(self,t,side,role,price,qty,projection,source):
            before=set(self.orders);blocks=self._probe_cross_blocks
            ans=super()._submit_role(t,side,role,price,qty,projection,source)
            self._record_event('SUBMIT',side=side,role=role,price=float(price),qty=float(qty),source=source,
                                success=bool(ans),keys=sorted(set(self.orders)-before),
                                crossingBlocked=self._probe_cross_blocks>blocks)
            return ans

        def _request_cancel(self,t,sid,reason):
            key=self.slot_key.get(int(sid));order=dict(self.orders[key]) if key in self.orders else None
            ans=super()._request_cancel(t,sid,reason)
            self._record_event('CANCEL',key=key,reason=reason,success=bool(ans),
                order={k:order.get(k) for k in ('n','side','price','qty','cum','placed','status')} if order else None)
            return ans

        def _probe_submit(self,t,option):
            ans=super()._probe_submit(t,option)
            self._record_event('PAID_SUBMIT',key=self._probe_key,option=dict(option))
            return ans

        def close_trace(self):
            self._trace_stream.flush();self._trace_stream.close()
    return Observed
