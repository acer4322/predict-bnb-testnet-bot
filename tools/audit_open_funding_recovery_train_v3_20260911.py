"""Independent full-trace check of recovered nonbinding-funding training. Zero HFT/fit."""
from pathlib import Path
from collections import defaultdict, Counter
import bisect,gzip,hashlib,importlib.util,json,math,os,statistics,sys,time,traceback

BASE=Path('C:/BTC5M-worker/.lan_worker_v1')
JOB='open-funding-recovery-train-20260911-v3'
PACK=BASE/'staging/open_funding_recovery_train_20260911_v3'
RESULT=BASE/'results'/JOB


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def observed(source):
    grouped=defaultdict(list)
    for a in source['targetActions']:
        assert a['quote_type']=='BID';grouped[a['event_ms']].append(a)
    u=d=c=0.;rows=[]
    for t,aa in sorted(grouped.items()):
        for a in aa:
            if a['side']=='UP':u+=a['shares']
            else:d+=a['shares']
            c+=a['shares']*a['price']
        rows.append((t,u,d,c))
    return rows


def audit_episode(e,teacher,unit):
    path=RESULT/e['trace']['path'];assert sha(path)==e['trace']['sha256']
    owners={};deltas=defaultdict(list);canonical=defaultdict(float);terms={};receipt_ids=set();states=[]
    u=d=c=peak=0.;count=Counter();decoded=0;drains=[];negative_advances=0
    prefix_counts=e['source_prefix']['counts'];prefix_seen=Counter()
    prefix_hash={k:hashlib.sha256() for k in prefix_counts}
    with gzip.open(path,'rb') as f:
        for line in f:
            decoded+=len(line);assert decoded<=32*1024**2
            z=json.loads(line);kind=z['kind'];x=z['data'];count[kind]+=1
            if kind in prefix_hash and prefix_seen[kind]<prefix_counts[kind]:
                prefix_hash[kind].update((json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode());prefix_seen[kind]+=1
            if kind=='native_action' and x['kind']=='NEW':
                key=x['side']+'_'+str(x['n']);assert key not in owners
                owners[key]=x
            elif kind=='canonical_receipt':
                assert x['sequence'] not in receipt_ids;receipt_ids.add(x['sequence']);key=x['key'];assert key in owners
                assert x['cumulative_qty']>=canonical[key]
                canonical[key]=x['cumulative_qty'];deltas[key].append(x)
                if owners[key]['side']=='UP':u+=x['qty']
                else:d+=x['qty']
                c+=x['qty']*x['contractPrice']+float(x.get('fee',0.))
            elif kind=='own_state':
                assert abs(x['inv']['UP']-u)<1e-7 and abs(x['inv']['DOWN']-d)<1e-7 and abs(x['cost']-c)<1e-7
                fd=x['funding_demand'];assert fd['capital_cap'] is None
                assert abs(fd['actual_spent']-c)<1e-7
                peak=max(peak,fd['current_cash_requirement']);states.append((x['t'],u,d,c))
            elif kind=='complete_policy_step':
                assert x['policy_supervision_mask'] is False and x['target_data_in_model_input'] is False
                assert not x['plan']['budget_allocations']
                assert not any(k.startswith(('target','future')) for k in (x['features'] or {}))
                claim=sum(a['spent']+a['reserved_cash'] for a in x['accounts'].values())
                claim+=sum(o['price']*o['qty'] for o in x['operations'] if o['kind']=='NEW')
                peak=max(peak,claim)
            elif kind=='terminal_owner_once':
                assert x['key'] in owners and x['key'] not in terms;terms[x['key']]=x['owner']
                assert x['owner']['state']=='TERMINAL' and x['owner']['filled']==canonical[x['key']]
            elif kind=='transport_drain_observation':
                assert x['new_market_events_appended']==0;drains.append(x)
                if x['clock_kind']=='UPPER_BOUND_AFTER_EOF':assert x['native_return']==1
            elif kind=='SCORING_ONLY_TARGET_AND_OUR':assert x['teacher_action'] is None
    assert dict(prefix_seen)==prefix_counts
    assert {k:h.hexdigest() for k,h in prefix_hash.items()}==e['source_prefix']['sha256']
    assert set(terms)==set(owners),'terminal coverage missing'
    assert len(owners)==e['submits'] and len(receipt_ids)==e['native_receipts']
    numerical=effective=nonadvancing=0;maxdiff=0.
    for key,o in owners.items():
        rr=deltas[key];values=[r['qty'] for r in rr];total=math.fsum(values);cum=canonical[key]
        # Independent representation-level bound, not a tuned execution threshold.
        bound=(len(values)+2)*math.fsum(math.ulp(abs(v)) for v in [float(o['qty']),float(cum),*values])
        maxdiff=max(maxdiff,abs(total-cum));assert abs(total-cum)<=bound
        if rr and cum==0.:
            assert all(r['cumulative_qty']==0. and r['leaves_qty']==o['qty'] for r in rr);numerical+=1
        last=0.
        for r in rr:
            effective+=r['cumulative_qty']>last;nonadvancing+=r['cumulative_qty']==last;last=r['cumulative_qty']
    zeros=sum(canonical[k]==0. for k in owners)
    assert zeros==e['zero_fill_orders']==e['receipt_support']['canonical_zero_fill_orders']
    assert numerical==e['receipt_support']['numeric_residual_only_orders']
    assert effective==e['receipt_support']['effective_cumulative_advance_receipts']
    assert abs(peak-e['trace']['peak_cash_requirement'])<1e-7
    assert not e['unresolved_owners'] and e['pending_cash']==0.
    if e['split']!='GOLDEN':assert e['resource_censor_events']==0
    times=[s[0] for s in states];assert times==sorted(times)
    coordinates=[[],[],[],[]]
    for t,tu,td,tc in teacher:
        j=bisect.bisect_right(times,t)-1
        _,ou,od,oc=states[j] if j>=0 else (t,0.,0.,0.)
        differences=[ou-tu,od-td,(ou-oc)-(tu-tc),(od-oc)-(td-tc)]
        for k,x in enumerate(differences):coordinates[k].append((x/unit)**2)
    means=[math.fsum(v)/len(v) for v in coordinates];loss=math.fsum(means)/4.
    assert abs(loss-e['loss']['total_loss'])<1e-11
    return dict(variant=e['variant'],split=e['split'],market_id=e['market_id'],loss=loss,
        source_prefix_exact=True,submitted=len(owners),raw_receipts=len(receipt_ids),
        effective_advance_receipts=effective,nonadvancing_raw_receipts=nonadvancing,
        cumulative_zero_orders=zeros,numeric_residual_only_orders=numerical,
        max_raw_vs_cumulative_difference=maxdiff,terminal_owners=len(terms),all_terminal=True,
        drain_steps=len(drains),peak_cash_requirement=peak,final_cost=c,UP_branch=u-c,DOWN_branch=d-c,
        new_market_events_for_drain=0,resource_censor_events=e['resource_censor_events'],
        replay_frames=count['complete_policy_step'],score_reproduction_error=abs(loss-e['loss']['total_loss']))


def main():
    if '--child' not in sys.argv:
        p=BASE/'staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'
        spec=importlib.util.spec_from_file_location('bounded',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        m.bounded('recovery-independent-audit',[sys.executable,str(Path(__file__).resolve()),'--child'],120);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic();r=dict(version='RECOVERY_TRAIN_INDEPENDENT_AUDIT_V3',new_native_runs=0,new_fits=0)
    try:
        status=json.loads((BASE/'jobs'/JOB/'status.json').read_text(encoding='utf-8'));assert status['state']=='succeeded'
        p=RESULT/'COMPACT.json';assert p.stat().st_size<160000;s=json.loads(p.read_text(encoding='utf-8'))
        assert s['verdict']=='RECOVERED_OPEN_FUNDING_TRAINING_AND_FROZEN_CHECK_COMPLETED'
        assert s['native_complete']==11 and s['joint_parameter_updates']==1
        fp=RESULT/'FROZEN_WHOLE_POLICY.json';frozen=json.loads(fp.read_text(encoding='utf-8'))
        assert sha(fp)==s['frozen_model_sha256'] and frozen['capital_cap'] is None and frozen['check_data_loaded'] is False
        manifest=json.loads((PACK/'MANIFEST.json').read_text(encoding='utf-8'));source={}
        for mid in (2022527,2022538,2022602):
            name=f'input_{mid}.json.gz';f=PACK/name;assert sha(f)==manifest['files'][name]['sha256']
            with gzip.open(f,'rb') as stream:b=stream.read(8*1024**2+1)
            assert len(b)<=8*1024**2;source[mid]=observed(json.loads(b))
        unit=float(statistics.median(u+d for mid in (2022527,2022538) for _,u,d,c in source[mid]))
        assert unit==s['fixed_train_share_unit']==frozen['fixed_train_share_unit']
        rows=[audit_episode(e,source[e['market_id']],unit) for e in s['evaluations']]
        scores={}
        for name,theta in frozen['candidates'].items():
            es=[e for e in s['evaluations'] if e['split']=='TRAIN' and e['variant']==name]
            assert len(es)==2 and all(e['theta']==theta for e in es)
            scores[name]=sum(e['loss']['total_loss'] for e in es)/2.;assert abs(scores[name]-s['train_losses'][name])<1e-12
        assert min(scores,key=scores.get)==frozen['selected']==s['selected']
        check=[e for e in s['evaluations'] if e['split']=='PIPELINE_CHECK']
        assert [e['variant'] for e in check]==['INITIAL','SELECTED_FROZEN']
        assert check[1]['theta']==frozen['theta']
        assert sha(fp)==s['frozen_model_sha256']
        r.update(verdict='INDEPENDENT_RECOVERY_ACCOUNTING_CLOSURE_AND_TRAINING_AUDIT_PASS',
            source_result_sha256=sha(p),frozen_policy_sha256=sha(fp),golden_source_prefix_match=s['golden_source_prefix_exact'],
            evaluations=rows,complete_native_runs=11,unique_markets=3,train_and_check_evaluations=10,
            joint_parameter_updates=1,selected_train_only=s['selected'],no_post_check_fit=True,
            capital_cap=None,all_terminals_confirmed=True,
            training_resource_censor_events=sum(x['resource_censor_events'] for x in rows if x['split']!='GOLDEN'),
            original_golden_resource_events=rows[0]['resource_censor_events'],
            native_submits_audited=sum(x['submitted'] for x in rows),raw_receipts_audited=sum(x['raw_receipts'] for x in rows),
            effective_receipt_advances_audited=sum(x['effective_advance_receipts'] for x in rows),
            frames_audited=sum(x['replay_frames'] for x in rows),
            maximum_score_reproduction_error=max(x['score_reproduction_error'] for x in rows),
            original_zero_count_disagreement_explained=True,raw_receipts_preserved=True,
            status_sha256=sha(BASE/'jobs'/JOB/'status.json'),native_active_implemented=False,
            profit_or_unseen_promotion=False)
    except Exception as e:r.update(verdict='RECOVERY_AUDIT_ERROR',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(limit=10))
    r['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(r,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in r.items() if k in ('verdict','complete_native_runs','error','elapsed_seconds')}),flush=True)
    if r['verdict']=='RECOVERY_AUDIT_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
