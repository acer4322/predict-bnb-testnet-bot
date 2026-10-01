"""Predeclared synthetic and paired-path acceptance. Missing values are not zeros."""
import gzip
import json
from pathlib import Path


def read(p):
    b=Path(p).read_bytes()
    return json.loads(gzip.decompress(b) if str(p).endswith('.gz') else b)


def differences(a,b,path='',limit=12):
    out=[]
    def walk(a,b,p):
        if len(out)>=limit:return
        if isinstance(a,dict) and isinstance(b,dict):
            if set(a)!=set(b):out.append(dict(path=p,keys_old=sorted(set(a)-set(b)),keys_new=sorted(set(b)-set(a))))
            for k in a.keys()&b.keys():walk(a[k],b[k],p+'/'+str(k))
        elif isinstance(a,list) and isinstance(b,list):
            if len(a)!=len(b):out.append(dict(path=p,len_old=len(a),len_new=len(b)))
            for i,(x,y) in enumerate(zip(a,b)):walk(x,y,p+'/'+str(i))
        elif isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
            if abs(a-b)>1e-9:out.append(dict(path=p,old=a,new=b))
        elif a!=b:out.append(dict(path=p,old=a,new=b))
    walk(a,b,path);return out


def native_contract(old,new):
    assert len(old['cases'])==len(new['cases'])==7
    rows=[]
    for a,b in zip(old['cases'],new['cases']):
        assert a['case']==b['case'];d=[]
        for x,y in zip(a['snapshots'],b['snapshots']):
            for k in ('label','rc','state','order','receipts'):
                d.extend(differences(x.get(k),y.get(k),a['case']+'/'+x['label']+'/'+k))
            assert y['clock_ns']>=x['clock_ns']
            if 'receipts' in y:assert y['receipts_within_clock'] and y['ledger_reconciles']
        assert not d,d
        rows.append(dict(case=a['case'],raw_state_receipt_parity=True,old_clocks=[x['clock_ns'] for x in a['snapshots']],new_clocks=[x['clock_ns'] for x in b['snapshots']]))
    by={x['case']:x for x in new['cases']}
    assert [x['clock_ns'] for x in by['BOUNDARY']['snapshots']][-3:]==[2_000_000_000]*3
    assert [x['clock_ns'] for x in by['MULTI_ASSET']['snapshots']][-3:]==[2_500_000_000,3_000_000_000,3_000_000_000]
    for key in ('RESTING','PARTIAL_TERMINAL','FILL_CANCEL','ACTIVE_TWO_PRICES'):
        x=by[key]['snapshots'];assert x[0]['clock_ns']==x[1]['clock_ns'] and by[key]['ack_once_empty']
    assert by['ACTIVE_TWO_PRICES']['snapshots'][0]['clock_ns']==1_600_000_000
    assert by['PARTIAL_TERMINAL']['snapshots'][0]['clock_ns']==2_250_000_000
    assert by['FILL_CANCEL']['snapshots'][0]['clock_ns']==2_300_000_000
    return dict(status='PASS',cases=rows)


def audit_path(arm,old_arm=None,recovery=False):
    arm=Path(arm);old_arm=Path(old_arm) if old_arm is not None else None
    r=read(arm/'result.json');clock=read(arm/'execution_clock.json')
    assert r['status']=='COMPLETE',r.get('error')
    assert not clock.get('capture_error') and not clock['invalid'],clock.get('capture_error')
    from frame_audit import audit as frame_audit
    frame_proof=frame_audit(arm,clock,read(arm/'clock_trace.json.gz'))
    assert clock['actual_sha']==clock['expected_sha']
    assert len(clock['calls']) == clock['source_updates'] + 2, 'MISSING_EXECUTION_GUARDS'
    assert clock['calls'][0]['stage'] == 'initial' and clock['calls'][-1]['stage'] == 'end2'
    assert [c['index'] for c in clock['calls'] if c['stage']=='source'] == list(range(clock['source_updates']))
    assert len(clock['loaded_patch_modules']) == len(clock['scratch_patch']) == 3
    assert all(c['reached'] for c in clock['calls'])
    assert all(x['receive_ts']<=clock['native_clock_ns'] for x in clock['receipts'])
    assert [x['sequence'] for x in clock['receipts']]==list(range(1,clock['receipt_sequence']+1))
    assert not differences(clock['receipt_native'],clock['native'])
    assert r['execution_accounting_valid'] and r['atomic_responsibility_summary']['pass']
    assert r['unresolved_owners']==0 and all(c['state']=='TERMINAL' for c in clock['carriers'].values())
    assert abs(r['active_responsibility_overfill'])<=1e-9 and abs(r['max_epoch_residual_overfill'])<=1e-9
    failed=[k for k,v in r['safety_gate'].items() if k!='pass' and not v]
    assert failed in ([],['active_matches_opportunity']),failed
    trace=read(arm/'clock_trace.json.gz');diff=[]
    if recovery:
        old=read(old_arm/'failure_trace.json.gz')
        for k in ('states','plans','native_actions','observations'):
            diff.extend(differences(old[k],trace[k][:len(old[k])],k))
        for k in ('opportunity_first','opportunity_submissions'):
            diff.extend(differences(old[k],trace[k],k))
        late=[x for x in clock['receipts'] if x['sequence'] in (73,74)]
        assert len(late)==2 and all(x['order_id']==153 for x in late)
        assert sum(x['qty'] for x in late)==10.
        assert clock['carriers']['UP_153']['state']=='TERMINAL'
    elif old_arm is not None:
        old_r=read(old_arm/('result.json.gz' if (old_arm/'result.json.gz').exists() else 'result.json'))
        for k in ('final_inventory','final_cost','submits','passive_native_submits','active_native_submits','active_fill_qty','active_requested_qty','passive_fill_qty','safety_gate','unresolved_owners','active_births'):
            diff.extend(differences(old_r.get(k),r.get(k),k))
        old=read(old_arm/'clock_trace.json.gz')
        # All frozen fields, including decisions and times. New execution audit is separate.
        diff.extend(differences(old,trace,'clock_trace'))
        if (old_arm/'restoration_trace.json.gz').exists():
            diff.extend(differences(read(old_arm/'restoration_trace.json.gz'),read(arm/'restoration_trace.json.gz'),'restoration'))
    inv=r['final_inventory'];cost=r['final_cost'];up=inv['UP']-cost;down=inv['DOWN']-cost
    gain=max(up,0)+max(down,0);loss=max(-up,0)+max(-down,0)
    return dict(status='PARITY_DIVERGED' if diff else 'PASS',differences=diff[:20],failed_checks=failed,
                frame_accounting=frame_proof,frames=clock['frames'],receipts=clock['receipt_sequence'],guard_calls=len(clock['calls']),eof_calls=[x for x in clock['calls'] if x['rc']==1],
                UP=up,DOWN=down,cost=cost,P=gain,L=loss,positive_exceeds_loss=(gain>loss and gain>1e-8),
                passive_submits=r['passive_native_submits'],active_submits=r['active_native_submits'])
