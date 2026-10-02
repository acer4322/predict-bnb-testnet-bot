"""Boundary and canonical cancellation lifecycle tests, no native imports."""
from copy import deepcopy
from types import SimpleNamespace
import json
import hard_stop as h

SOURCE='''
def envelope(f,producer,ops):
    ops=producer.bridge.service('opportunity',f,producer,ops,None,None)
    ops=producer.bridge.service('commitment_repair',f,producer,ops,None,None)
    ops=producer.bridge.service('coordination',f,producer,ops,None,None)
    ops=producer.general_finite_active.apply(f,producer,ops,None,None)
    producer.bridge.on_plan(f,producer,ops)
    govern_plan(f,ops)
    return ops
class Policy:
    def produce(self,f):
        self.observed+=1
        ops=[];live={k:c for k,c in f['ledger'].carriers.items() if c.state!='TERMINAL'}
        if f['t']>=f['end']:
            for k,c in live.items():
                if c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):ops.append(dict(kind='CANCEL',key=k,reason='ACTUAL_MARKET_END'))
            return envelope(f,self,ops)
        self.raw_births+=1
        return envelope(f,self,[dict(kind='NEW',key='raw',route='PASSIVE')])
'''

def run():
    checks=[];h.ROWS.clear();oldmode=h.MODE;h.MODE='ON'
    try:
        for start in (0,1790447100000):
            for dt in (289999,290000,290001,300000):
                assert h.active(dict(t=start+dt,start=start))==(dt>=290000)
                assert not h.active(dict(t=start+dt,start=start),'OFF')
        checks.append('exact_boundary_including_290000_and_off')
        assert h.instrument(SOURCE,'OFF')==SOURCE
        def owner(state,filled=0.,route='PASSIVE'):return SimpleNamespace(state=state,qty=10.,filled=filled,limit=.4,route=route)
        class Ledger:
            def __init__(self):self.carriers=dict(rest=owner('ACKNOWLEDGED'),partial=owner('PARTIAL',3.),inflight=owner('SUBMITTED',route='ACTIVE'),unknown=owner('UNKNOWN'),cancel=owner('CANCEL_PENDING'),done=owner('TERMINAL'))
            def account(self,pid):return dict(reserved_qty=47.,reserved_cash=18.8)
        class Bridge:
            def __init__(self):self.calls=[];self.plans=[]
            def service(self,name,f,p,ops,*a):self.calls.append(name);return [*ops,dict(kind='NEW',key=name,route='ACTIVE')]
            def on_plan(self,f,p,ops):self.plans.append(deepcopy(ops))
        class General:
            def __init__(self):self.calls=0
            def apply(self,f,p,ops,*a):self.calls+=1;return [*ops,dict(kind='NEW',key='PADD_or_repair',route='ACTIVE')]
        ns=dict(govern_plan=lambda f,ops:None);exec(compile(h.instrument(SOURCE),'boundary_fixture','exec'),ns)
        p=ns['Policy']();p.bridge=Bridge();p.general_finite_active=General();p.observed=0;p.raw_births=0
        ledger=Ledger();initial=deepcopy(ledger.carriers)
        f=dict(start=1000,end=301000,t=290999,index=0,ledger=ledger,cancellable=dict(rest=True,partial=True,unknown=True,inflight=False,cancel=True,done=True))
        before=p.produce(f);assert len(before)==5 and p.raw_births==1 and len(p.bridge.calls)==3 and p.general_finite_active.calls==1
        f.update(t=291000,index=1);ops=p.produce(f)
        assert {o['key'] for o in ops}=={'rest','partial','unknown'} and all(o['kind']=='CANCEL' for o in ops)
        assert all(o['reason']=='USER_STOP290_CANCEL_ALL' for o in ops)
        assert p.raw_births==1 and len(p.bridge.calls)==3 and p.general_finite_active.calls==1
        assert vars(ledger.carriers['partial'])==vars(initial['partial']) and ledger.account(1)['reserved_cash']==18.8
        # Partial fills while cancellation travels are real; CANCEL_PENDING isn't release.
        ledger.carriers['rest'].state='CANCEL_PENDING';ledger.carriers['partial'].state='CANCEL_PENDING';ledger.carriers['partial'].filled=5.
        ledger.carriers['unknown'].state='TERMINAL';ledger.carriers['inflight'].state='ACKNOWLEDGED';f['cancellable']['inflight']=True
        f.update(t=291200,index=2);ops=p.produce(f);assert [o['key'] for o in ops]==['inflight']
        assert next(x for x in h.ROWS[-1]['owners'] if x['key']=='partial')['remaining']==5.
        checks.append('all_raw_active_passive_appenders_skipped_before_birth_and_no_false_release')
        checks.append('partial_unknown_cancel_pending_and_later_ack_cancellation')
        for bad in ([dict(kind='NEW',key='escape')],[],[dict(kind='CANCEL',key='inflight')]*2):
            try:h.verify(f,p,bad)
            except AssertionError:pass
            else:raise AssertionError('bad plan accepted')
        for badsrc in (SOURCE.replace('producer.general_finite_active.apply','producer.other.apply'),SOURCE.replace("f['t']>=f['end']","False")):
            try:h.instrument(badsrc)
            except AssertionError:pass
            else:raise AssertionError('missing seam accepted')
        checks.append('coverage_and_instrumentation_negative_tests')
        return dict(status='PASS',checks=checks,native_executed=0)
    finally:h.ROWS.clear();h.MODE=oldmode

if __name__=='__main__':print(json.dumps(run()))
