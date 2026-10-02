"""Independent scoring/accounting/support audit. Zero HFT or fitting; second worker."""
import bisect
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

JOB=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-whole-episode-train-20260911-v1')
PACKAGE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_whole_episode_train_20260911_v1')
MODEL_SHA='93c25c0f4ea2cf780923dbb832252a821455b297e77a6ea9035e0c7e76867a99'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def economic_vector(inv,cost):
    u,d=inv['UP'],inv['DOWN']
    return [(u-d)/(1+u+d),(min(u,d)-cost)/(1+cost),(max(u,d)-cost)/(1+cost),min(cost/100.,1.)]


def audit_trace(row,source):
    path=JOB/row['trace']['path'];assert sha(path)==row['trace']['sha256']
    states=[];actions={};fill_by_owner={};receipts_seen=set();plans=0;size=0;cost=0.;inv={'UP':0.,'DOWN':0.}
    action_count=0;late=0;maxdiff=0.;terms_names=['signed_exposure','worst_branch','best_branch','capped_cash_utilization']
    with gzip.open(path,'rb') as f:
        for line in f:
            size+=len(line);assert size<=32*1024**2
            z=json.loads(line);kind=z['kind'];d=z['data']
            if kind=='native_action':
                if d['kind']=='NEW':
                    key=d['side']+'_'+str(d['n']);assert key not in actions
                    actions[key]=d;fill_by_owner[key]=0.
                    assert source['market']['window_start_ms']<=d['t']<source['market']['window_end_ms']
                    late+=(source['market']['window_end_ms']-d['t']<=180000)
                action_count+=1
            elif kind=='canonical_receipt':
                assert d['sequence'] not in receipts_seen;receipts_seen.add(d['sequence'])
                key=d['key'];assert key in actions
                assert d['qty']>0 and 0<d['contractPrice']<1
                fill_by_owner[key]+=d['qty'];assert fill_by_owner[key]<=actions[key]['qty']+1e-7
                side=actions[key]['side'];inv[side]+=d['qty'];cost+=d['qty']*d['contractPrice']+float(d.get('fee',0.))
            elif kind=='own_state':
                for s in inv:
                    difference=abs(d['inv'][s]-inv[s]);assert difference<1e-7;maxdiff=max(maxdiff,difference)
                difference=abs(d['cost']-cost);assert difference<1e-7;maxdiff=max(maxdiff,difference)
                states.append(d)
            elif kind=='complete_policy_step':
                plans+=1;assert d['policy_supervision_mask'] is False and d['target_data_in_model_input'] is False
                assert all(abs(d['own_inventory'][s]-inv[s])<1e-7 for s in inv)
                assert abs(d['own_cost']-cost)<1e-7
                assert not any(k.startswith('target') for k in (d['features'] or {}))
            elif kind=='SCORING_ONLY_TARGET_AND_OUR':
                assert d['teacher_action'] is None
            else:raise AssertionError('unrecognized event')
    assert len(actions)==row['submits'] and len(receipts_seen)==row['native_receipts']
    assert plans==row['trace']['plans'] and late==row['late_new_orders']
    assert sum(q==0 for q in fill_by_owner.values())==row['zero_fill_orders']
    assert sum(0<q<actions[k]['qty']-1e-8 for k,q in fill_by_owner.items())==row['partial_orders']
    assert all(abs(inv[s]-row['final_inventory'][s])<1e-7 for s in inv)
    assert abs(cost-row['final_cost'])<1e-7
    times=[s['t'] for s in states];assert times==sorted(times)
    groups={}
    for leg in source['targetActions']:
        assert leg['quote_type']=='BID';groups.setdefault(leg['event_ms'],[]).append(leg)
    ti={'UP':0.,'DOWN':0.};tc=0.;squared=[[] for _ in range(4)];above=0
    for t,legs in sorted(groups.items()):
        for leg in legs:ti[leg['side']]+=leg['shares'];tc+=leg['shares']*leg['price']
        j=bisect.bisect_right(times,t)-1
        ours=states[j] if j>=0 else {'inv':{'UP':0.,'DOWN':0.},'cost':0.}
        tv=economic_vector(ti,tc);ov=economic_vector(ours['inv'],ours['cost'])
        for k in range(4):squared[k].append((ov[k]-tv[k])**2)
        above+=tc>100.
    coord=[math.fsum(x)/len(x) for x in squared]
    total=math.fsum(coord)/4.+.25*max(0.,cost-min(inv.values()))/100.+.10*row['pending_cash']/100.
    assert abs(total-row['loss']['total_loss'])<1e-12
    assert max(abs(a-b) for a,b in zip(coord,row['loss']['coordinate_mse']))<1e-12
    assert above==row['loss']['target_cost_above_our_cap_batches']
    return dict(market_id=row['market_id'],variant=row['variant'],split=row['split'],
        native_receipts=len(receipts_seen),native_submits=len(actions),late_new=late,
        exact_accounting_reconstruction=True,scoring_difference=abs(total-row['loss']['total_loss']),
        accounting_max_abs_difference=maxdiff,full_policy_frames=plans,
        target_capped_utilization_fraction=above/len(groups),
        target_observed_cost=tc,our_cost=cost,
        worst_settlement_branch=min(inv.values())-cost,
        coordinate_loss=dict(zip(terms_names,coord)))


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bounded',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        m.bounded('whole-episode-audit',[sys.executable,str(Path(__file__).resolve()),'--child'],90);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    r=dict(version='WHOLE_EPISODE_TRAIN_INDEPENDENT_AUDIT_V1',new_fits=0,new_native_runs=0,worker_only=True)
    try:
        p=JOB/'COMPACT.json';assert p.stat().st_size<150000
        src=json.loads(p.read_text(encoding='utf-8'))
        assert src['verdict']=='WHOLE_EPISODE_POLICY_TRAINING_AND_FROZEN_CHECK_COMPLETED'
        assert src['native_complete']==10 and src['joint_parameter_updates']==1
        frozen=JOB/'FROZEN_WHOLE_POLICY.json';assert sha(frozen)==MODEL_SHA
        model=json.loads(frozen.read_text(encoding='utf-8'))
        assert model['check_data_loaded'] is False and model['train_markets']==[2022527,2022538]
        pack=json.loads((PACKAGE/'MANIFEST.json').read_text(encoding='utf-8'))
        sources={}
        for mid in (2022527,2022538,2022602):
            name=f'input_{mid}.json.gz';file=PACKAGE/name;assert sha(file)==pack['files'][name]['sha256']
            with gzip.open(file,'rb') as f:raw=f.read(8*1024**2+1)
            assert len(raw)<=8*1024**2;sources[mid]=json.loads(raw)
        audited=[audit_trace(row,sources[row['market_id']]) for row in src['evaluations']]
        scores={}
        for name in model['candidates']:
            rows=[x for x in src['evaluations'] if x['split']=='TRAIN' and x['variant']==name]
            assert {x['market_id'] for x in rows}=={2022527,2022538}
            scores[name]=sum(x['loss']['total_loss'] for x in rows)/2.
            assert abs(scores[name]-model['mean_train_loss'][name])<1e-12
        assert min(scores,key=scores.get)==src['selected']==model['selected']=='PLUS'
        initial=[x for x in src['evaluations'] if x['split']=='TRAIN' and x['variant']=='INITIAL']
        selected=[x for x in src['evaluations'] if x['split']=='TRAIN' and x['variant']==src['selected']]
        means=lambda rows:[sum(x['loss']['coordinate_mse'][i] for x in rows)/len(rows) for i in range(4)]
        ib,sb=means(initial),means(selected)
        contribution=[(a-b)/4. for a,b in zip(ib,sb)]
        penalties=(sum(x['loss']['terminal_downside_penalty'] for x in initial)-sum(x['loss']['terminal_downside_penalty'] for x in selected))/2.
        assert abs(sum(contribution)+penalties-src['train_loss_improvement'])<1e-12
        saturation=sum(x['loss']['target_cost_above_our_cap_batches'] for x in initial)/sum(x['loss']['target_event_batches'] for x in initial)
        checkrows=[x for x in src['evaluations'] if x['split']=='PIPELINE_CHECK']
        assert [x['variant'] for x in checkrows]==['INITIAL','SELECTED_FROZEN']
        result_positive=checkrows[0]['loss']['total_loss']-checkrows[1]['loss']['total_loss']
        assert abs(result_positive-src['check_loss_improvement'])<1e-12
        assert sha(frozen)==MODEL_SHA
        r.update(verdict='ACCOUNTING_AND_SCORE_REPRODUCED_OBJECTIVE_SCALE_CONFOUND_IDENTIFIED',
            audited_native_runs=10,audited_policy_frames=sum(a['full_policy_frames'] for a in audited),
            audited_submits=sum(a['native_submits'] for a in audited),
            audited_receipts=sum(a['native_receipts'] for a in audited),
            max_scoring_abs_difference=max(a['scoring_difference'] for a in audited),
            max_accounting_abs_difference=max(a['accounting_max_abs_difference'] for a in audited),
            selected_checkpoint_train_only=True,frozen_model_sha256=MODEL_SHA,
            source_result_sha256=sha(p),evaluations=audited,
            train_objective_decomposition=dict(initial_coordinate_mse=ib,selected_coordinate_mse=sb,
                weighted_coordinate_improvement=contribution,downside_penalty_improvement=penalties,
                overall_improvement=src['train_loss_improvement'],
                cash_utilization_contribution=contribution[3],
                remaining_three_coordinates_improvement=sum(contribution[:3]),
                target_train_utilization_saturated_fraction=saturation),
            worst_branch_train_initial=[min(x['UP_branch'],x['DOWN_branch']) for x in initial],
            worst_branch_train_selected=[min(x['UP_branch'],x['DOWN_branch']) for x in selected],
            check_loss_improvement=result_positive,
            adopted_for_strategy=False,objective_needs_reformulation_before_scaling=True,
            interpretation='Most apparent train-loss gain comes from spending toward a capped Target-utilization coordinate; remaining economic geometry worsened in aggregate and TRAIN downside branches worsened. This is a scoring-scale confound, not an explanation of private Target policy.',
            no_retraining_after_check=True)
    except Exception as e:
        r.update(verdict='INDEPENDENT_AUDIT_ERROR',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(limit=10))
    r['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(r,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in r.items() if k in ('verdict','audited_native_runs','audited_submits','audited_receipts','error','elapsed_seconds')}),flush=True)
    if r['verdict']=='INDEPENDENT_AUDIT_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
