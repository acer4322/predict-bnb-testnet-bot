"""Zero-engine, restricted completion-roundoff audit. Original verdict immutable."""
import copy
import hashlib
import json
from pathlib import Path
import sys

OLD_SHA='377301ea4bc11101522b3ed7b9fab9985e057d1f9b11bdd878fe1a021a74dd8d'
NEW_SHA='b96a5616d1ef2f1fcc899d5c440db44e3ae5ba848e845aca883d5e46664fd4e0'
NATIVE='909425438190637e2368fb6a516d306b21c50c6f64b4adfd47b6b345dd6e4129'


def audit(old,new):
    assert new['verdict']=='STOP_BUILD_OR_SMOKE_NOT_COMPARABLE'
    assert new['nativeSha256']==NATIVE and len(old['rows'])==len(new['rows'])==6
    differences=[]
    for r,o in zip(new['rows'],old['rows']):
        assert (r['side'],r['route'])==(o['side'],o['route'])
        assert r['exercised'] and len(r['snapshots'])==len(o['snapshots'])==4
        for a,b in zip(r['snapshots'],o['snapshots']):
            for k in ['label','nowNs','status','req','cum']: assert a[k]==b[k],k
            assert a['order'].keys()==b['order'].keys()
            for k in a['order']:
                if a['order'][k]==b['order'][k]: continue
                assert r['route']=='PARTIAL_FULL' and a['label'] in ['TERMINAL_RESPONSE','LATE_READ']
                assert a['status']==3 and k in ['leaves_qty','exec_qty']
                tol=8*sys.float_info.epsilon*abs(a['order']['qty'])
                assert abs(a['order'][k]-b['order'][k])<=tol
                assert a['order']['leaves_qty']==0
                assert a['order']['exec_qty']+a['order']['leaves_qty']==b['order']['exec_qty']+b['order']['leaves_qty']
                differences.append(dict(side=r['side'],label=a['label'],field=k,delta=a['order'][k]-b['order'][k]))
            q=a['cum']; p=.74 if r['side']=='BUY' else .26; sign=1 if r['side']=='BUY' else -1
            count=0 if a['label']=='ACK' else (2 if r['route']=='PARTIAL_FULL' and a['label'] in ['TERMINAL_RESPONSE','LATE_READ'] else 1)
            expect=dict(position=sign*q,balance=-sign*p*q,trading_volume=q,trading_value=p*q,num_trades=count,fee=0.)
            assert a['accountPass']
            for k,v in expect.items(): assert abs(a['native'][k]-v)<=8*sys.float_info.epsilon*max(1.,abs(v)),k
    assert len(differences)==8
    return dict(verdict='RESTRICTED_NUMERICAL_EQUIVALENCE_SUPPORTED',originalVerdict=new['verdict'],nativeSha256=NATIVE,snapshots=24,differences=differences,marketBE=0,newEngines=0,promotion=False)


def run(old_path,new_path):
    assert hashlib.sha256(Path(old_path).read_bytes()).hexdigest()==OLD_SHA
    assert hashlib.sha256(Path(new_path).read_bytes()).hexdigest()==NEW_SHA
    old=json.loads(Path(old_path).read_text()); new=json.loads(Path(new_path).read_text())
    result=audit(old,new); rejected=0
    for field in ['exec_price','leaves_qty','exch_timestamp']:
        bad=copy.deepcopy(new); bad['rows'][2]['snapshots'][2]['order'][field]+=.001
        try: audit(old,bad)
        except AssertionError: rejected+=1
    bad=copy.deepcopy(new); bad['rows'][0]['snapshots'][1]['native']['balance']+=.001
    try: audit(old,bad)
    except AssertionError: rejected+=1
    assert rejected==4
    result['negativeControlsRejected']=rejected
    return result


if __name__=='__main__':
    print(json.dumps(run(sys.argv[1],sys.argv[2]),indent=2))
