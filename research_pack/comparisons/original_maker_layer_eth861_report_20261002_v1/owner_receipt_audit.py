"""Pure receipt-to-carrier audit, using the frozen adapter's side_n contract."""
from collections import defaultdict

def reconcile_owners(clock, trace):
    carriers=clock['carriers']; by_id={}
    for key,c in carriers.items():
        side,n=key.split('_')
        assert side in ('UP','DOWN') and c['key']==key
        n=int(n); assert n>0 and n not in by_id
        by_id[n]=(key,c,side)
    # validate_mixed_envelope explicitly enforces key == f'{side}_{n}',
    # consume uses the same monotonically increasing self.n for native sends.
    new={}
    for plan in trace['plans']:
        for op in plan['operations']:
            if op['kind']=='NEW':
                assert op['key'] not in new
                new[op['key']]=op
    assert set(new)==set(carriers)
    sends={}
    for a in trace['native_actions']:
        if a['kind'] in ('NEW','NEW_ACTIVE'):
            n=a['n'];assert n in by_id and n not in sends
            key,c,side=by_id[n];op=new[key]
            assert a['side']==op['side']==side
            assert abs(a['price']-c['limit'])<1e-6 and abs(op['price']-c['limit'])<1e-6
            assert abs(a['qty']-c['qty'])<1e-6 and abs(op['qty']-c['qty'])<1e-6
            assert op['parent_id']==c['parent_id'] and op['route']==c['route']
            if 'key' in a:assert a['key']==key
            sends[n]=a
        elif a['kind']=='CANCEL':
            assert a['n'] in by_id
            if 'key' in a:assert a['key']==by_id[a['n']][0]
    assert set(sends)==set(by_id)
    qty=defaultdict(float);payment=defaultdict(float);fees=defaultdict(float)
    for r in clock['receipts']:
        assert r['order_id'] in by_id
        key,c,side=by_id[r['order_id']]
        assert r['side']==(1 if side=='UP' else -1)
        price=r['price'] if side=='UP' else 1-r['price']
        qty[key]+=r['qty'];payment[key]+=r['qty']*price;fees[key]+=r['fee']
    for key,c in carriers.items():
        assert abs(qty[key]-c['filled'])<1e-6,(key,'filled')
        assert abs(payment[key]-c['payment'])<1e-6,(key,'payment')
        assert abs(fees[key]-c['fees'])<1e-6,(key,'fees')
    return dict(status='PASS',carriers=len(carriers),receipts=len(clock['receipts']),
                mapping='Frozen adapter key=side_nativeId, independently checked against complete NEW plans, native send IDs/side/price/qty, keyed cancels and per-carrier receipt quantity/payment/fees')
