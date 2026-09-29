"""Independent worker-only trace/score audit; no replay or policy optimization."""
import bisect
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

JOB=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-open-funding-train-logfix-20260911-v2')
PACK=Path('C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_open_funding_train_logfix_20260911_v2')


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def source_path(s):
    batches={}
    for a in s['targetActions']:
        assert a['quote_type']=='BID';batches.setdefault(a['event_ms'],[]).append(a)
    u=d=c=0.;rows=[]
    for t,aa in sorted(batches.items()):
        for a in aa:
            if a['side']=='UP':u+=a['shares']
            else:d+=a['shares']
            c+=a['shares']*a['price']
        rows.append((t,u,d,c))
    return rows


def audit_one(e,teacher,scale):
    p=JOB/e['trace']['path'];assert sha(p)==e['trace']['sha256']
    owners={};fills={};receipt_ids=set();terminal_ids=set();states=[];u=d=c=0.;peak=0.;plans=0;size=0
    with gzip.open(p,'rb') as f:
        for line in f:
            size+=len(line);assert size<32*1024**2
            z=json.loads(line);k=z['kind'];x=z['data']
            if k=='native_action' and x['kind']=='NEW':
                key=x['side']+'_'+str(x['n']);assert key not in owners
                owners[key]=x;fills[key]=0.
            elif k=='canonical_receipt':
                assert x['sequence'] not in receipt_ids;receipt_ids.add(x['sequence'])
                key=x['key'];assert key in owners
                fills[key]+=x['qty'];assert fills[key]<=owners[key]['qty']+1e-7
                if owners[key]['side']=='UP':u+=x['qty']
                else:d+=x['qty']
                c+=x['qty']*x['contractPrice']+float(x.get('fee',0.))
            elif k=='own_state':
                assert abs(x['inv']['UP']-u)<1e-7 and abs(x['inv']['DOWN']-d)<1e-7 and abs(x['cost']-c)<1e-7
                funding=x['funding_demand'];assert funding['capital_cap'] is None
                assert abs(funding['actual_spent']-c)<1e-7
                assert abs(funding['current_cash_requirement']-c-funding['pending_commitment'])<1e-7
                peak=max(peak,funding['current_cash_requirement']);states.append((x['t'],u,d,c))
            elif k=='complete_policy_step':
                plans+=1;assert x['target_data_in_model_input'] is False and x['policy_supervision_mask'] is False
                assert not x['plan']['budget_allocations'],'no hidden monetary reallocation schedule'
                claim=sum(a['spent']+a['reserved_cash'] for a in x['accounts'].values())
                claim+=sum(a['price']*a['qty'] for a in x['operations'] if a['kind']=='NEW')
                peak=max(peak,claim)
            elif k=='terminal_owner_once':
                key=x['key'];o=x['owner'];assert key in owners and key not in terminal_ids
                assert o['state']=='TERMINAL' and abs(o['filled']-fills[key])<1e-7
                terminal_ids.add(key)
            elif k=='SCORING_ONLY_TARGET_AND_OUR':assert x['teacher_action'] is None
    assert len(owners)==e['submits'] and len(receipt_ids)==e['native_receipts']
    assert len(terminal_ids)==e['trace']['terminal_owner_records']
    assert sum(q==0 for q in fills.values())==e['zero_fill_orders']
    assert abs(peak-e['trace']['peak_cash_requirement'])<1e-7
    assert abs(c-e['final_cost'])<1e-7 and e['capital_cap'] is None
    times=[x[0] for x in states];assert times==sorted(times)
    terms=[[],[],[],[]]
    for t,tu,td,tc in teacher:
        j=bisect.bisect_right(times,t)-1
        _,ou,od,oc=states[j] if j>=0 else (t,0.,0.,0.)
        residual=[ou-tu,od-td,(ou-oc)-(tu-tc),(od-oc)-(td-tc)]
        for i,r in enumerate(residual):terms[i].append((r/scale)**2)
    mean=[math.fsum(x)/len(x) for x in terms];loss=math.fsum(mean)/4.
    assert abs(loss-e['loss']['total_loss'])<1e-11
    assert max(abs(a-b) for a,b in zip(mean,e['loss']['coordinate_mse']))<1e-11
    return dict(variant=e['variant'],market_id=e['market_id'],split=e['split'],loss=loss,
        final_cost=c,peak_cash_requirement=peak,above_previous100=e['trace']['peak_cash_requirement']>100.,
        native_submits=len(owners),native_receipts=len(receipt_ids),policy_frames=plans,
        resource_censor_events=e['resource_censor_events'],unresolved_owners=e['unresolved_owners'],
        minimum_branch=min(u,d)-c,maximum_branch=max(u,d)-c,
        exact_requested_qty_preserved=True,independent_scoring_error=abs(loss-e['loss']['total_loss']))


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        sp=importlib.util.spec_from_file_location('bounded',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
        m.bounded('open-funding-audit',[sys.executable,str(Path(__file__).resolve()),'--child'],90);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    r=dict(version='OPEN_FUNDING_INDEPENDENT_AUDIT_V1',new_native_runs=0,new_parameter_updates=0,worker_only=True)
    try:
        p=JOB/'COMPACT.json';assert p.stat().st_size<160000;s=json.loads(p.read_text(encoding='utf-8'))
        assert s['verdict']=='OPEN_FUNDING_WHOLE_POLICY_TRAINING_AND_FROZEN_CHECK_COMPLETED'
        assert s['native_complete']==10 and s['capital_cap'] is None and s['utilization_reward_present'] is False
        fp=JOB/'FROZEN_WHOLE_POLICY.json';assert sha(fp)==s['frozen_model_sha256']
        frozen=json.loads(fp.read_text(encoding='utf-8'));assert frozen['capital_cap'] is None and frozen['check_data_loaded'] is False
        manifest=json.loads((PACK/'MANIFEST.json').read_text(encoding='utf-8'));paths={}
        for mid in (2022527,2022538,2022602):
            name=f'input_{mid}.json.gz';ip=PACK/name;assert sha(ip)==manifest['files'][name]['sha256']
            with gzip.open(ip,'rb') as f:b=f.read(8*1024**2+1)
            assert len(b)<=8*1024**2;paths[mid]=source_path(json.loads(b))
        qref=float(statistics.median(u+d for mid in (2022527,2022538) for _,u,d,c in paths[mid]))
        assert qref==s['fixed_train_share_unit']==frozen['fixed_train_share_unit']
        rows=[audit_one(e,paths[e['market_id']],qref) for e in s['evaluations']]
        scores={}
        for variant in frozen['candidates']:
            rr=[x for x in rows if x['split']=='TRAIN' and x['variant']==variant]
            assert {x['market_id'] for x in rr}=={2022527,2022538}
            scores[variant]=sum(x['loss'] for x in rr)/2.
            assert abs(scores[variant]-s['train_losses'][variant])<1e-11
        assert min(scores,key=scores.get)==frozen['selected']==s['selected']
        selected=[x for x in rows if x['variant']=='SELECTED_FROZEN'][0]
        baseline=[x for x in rows if x['split']=='PIPELINE_CHECK' and x['variant']=='INITIAL'][0]
        assert abs(baseline['loss']-selected['loss']-s['check_loss_improvement'])<1e-11
        assert sha(fp)==s['frozen_model_sha256']
        r.update(verdict='NONBINDING_FUNDING_TRACE_SCORE_AND_FREEZE_AUDIT_PASS',
            source_result_sha256=sha(p),frozen_policy_sha256=sha(fp),fixed_train_share_unit=qref,
            selected_train_only=s['selected'],evaluations=rows,
            financial_cap_rejections=0,artificial_utilization_reward=False,
            episodes_above_previous100=sum(x['above_previous100'] for x in rows),
            largest_observed_cash_requirement=max(x['peak_cash_requirement'] for x in rows),
            resource_censor_events=sum(x['resource_censor_events'] for x in rows),
            unresolved_owner_count=sum(x['unresolved_owners'] for x in rows),
            native_submits_verified=sum(x['native_submits'] for x in rows),
            canonical_receipts_verified=sum(x['native_receipts'] for x in rows),
            full_policy_frames_verified=sum(x['policy_frames'] for x in rows),
            max_loss_reproduction_error=max(x['independent_scoring_error'] for x in rows),
            simulation_not_live=True,deployment_capital_feasibility_tested=False,
            native_active_implemented=False)
    except Exception as e:
        r.update(verdict='OPEN_FUNDING_AUDIT_ERROR',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(limit=9))
    r['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(r,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in r.items() if k in ('verdict','episodes_above_previous100','resource_censor_events','error','elapsed_seconds')}),flush=True)
    if r['verdict']=='OPEN_FUNDING_AUDIT_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
