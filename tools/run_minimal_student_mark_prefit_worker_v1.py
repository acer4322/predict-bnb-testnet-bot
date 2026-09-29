"""One prespecified auxiliary prefit. Second worker; no controller/HFT authority."""
from pathlib import Path
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent
DATA=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-training-data-smoke3-20260910-v1')
HEADS=['MAKER_UP_BID','MAKER_DOWN_BID','TAKER_UP_BID','TAKER_DOWN_BID']
TIME_PRICE=['seconds_left','public_predictUpMid','up_bid','up_ask']
MANIFEST_SHA='3ef1638c642481ce0c390797fdb8ae271b4b1e466d5d7d00c61dfa095fc9ffb0'
BUILD_SHA='474a36c9c684fc9fd2df038657beeb769655c27b552e8b2513b95a7f452ad18a'
SUPPORT_SHA='8bf8e373ce822290036fdb61c7eb113754f8cdb58e47b46e97ad5dbcf169535d'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def import_exact(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        bound=import_exact('bounded_supervisor',helper)
        bound.bounded('minimal-student-mark-prefit',[sys.executable,str(Path(__file__).resolve()),'--child'],180)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'second LAN worker required'
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    r=dict(version='MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_V1',verdict='RUNNING',research_logistic_fits=0,
        synthetic_unit_fits=0,HFT=0,controller_changes=0,live_changes=0,new_markets=0,
        processing_location='SECOND_LAN_WORKER',hostname=os.environ.get('COMPUTERNAME'),
        max_threads=int(os.environ['OMP_NUM_THREADS']),execution_authority='NONE',
        full_imitation_trained=False,recent_same_scale_transfer_tested=False,
        source_manifest_sha256=MANIFEST_SHA,checks=[])
    def save():
        r['elapsed_seconds']=time.monotonic()-started
        b=json.dumps(r,indent=2,ensure_ascii=False,allow_nan=False).encode('utf-8')
        assert len(b)<180000
        (out/'COMPACT.json').write_bytes(b)
    def check(name,cond):
        assert cond,name;r['checks'].append(name)
    try:
        package=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,m in package['files'].items():
            p=(BUNDLE/rel).resolve();check('package:'+rel,p.is_relative_to(BUNDLE) and p.stat().st_size==m['bytes'] and sha(p)==m['sha256'])
        check('pinned_dataset_manifest',sha(DATA/'DATASET_MANIFEST.json')==MANIFEST_SHA)
        check('pinned_build_result',sha(BUNDLE/'BUILD_RESULT.json')==BUILD_SHA)
        check('pinned_task_support',sha(BUNDLE/'TASK_SUPPORT.json')==SUPPORT_SHA)
        a=json.loads((BUNDLE/'ACCEPTANCE.json').read_text(encoding='utf-8'))
        check('only_authorized_four_heads',a['target_observation_pretraining']['active_heads']==HEADS)
        build=json.loads((BUNDLE/'BUILD_RESULT.json').read_text(encoding='utf-8'))
        for name in ['minimal_student_dataset_reader_v1.py','TRAIN_ONLY_FEATURE_STATS.json']:
            check('source:'+name,sha(DATA/name)==build['artifact_hashes'][name])
        core=import_exact('prefit_core',BUNDLE/'minimal_student_mark_logistic_v1.py')
        import numpy as np
        tests=core.self_tests();r['synthetic_unit_fits']=tests['synthetic_fit_count'];r['unit_tests']=tests
        check('core_unit_tests_pass',tests['passed']>=20)
        print(json.dumps(dict(stage='unit_tests_pass',tests=tests['passed'],synthetic_fits=tests['synthetic_fit_count'])),flush=True)
        reader=import_exact('accepted_reader',DATA/'minimal_student_dataset_reader_v1.py')
        stats=json.loads((DATA/'TRAIN_ONLY_FEATURE_STATS.json').read_text(encoding='utf-8'))['observed_fill_marks']
        check('stats_train_only',stats['fit_partition']=='TRAIN' and stats['training_market_ids']==[2022527,2022538])
        # Read and optimize TRAIN only. PIPELINE_CHECK rows are not touched until freeze.
        train=list(reader.iter_task(DATA,'observed_fill_marks','TRAIN'))
        check('train_rows_and_markets',len(train)==332 and set(x['market_id'] for x in train)=={2022527,2022538})
        ix=[reader.MARKS.index(h) for h in HEADS]
        y=np.asarray([[t['y'][j] for j in ix] for t in train],dtype=float)
        z=np.asarray([reader.vectorize(t['x'],stats) for t in train],dtype=float)
        names=stats['feature_names'];vectors=names+['missing:'+n for n in names]
        check('expected_public_feature_schema',len(names)==27 and z.shape==(332,54))
        check('all_selected_heads_have_both_classes',bool(((y.sum(0)>0)&(y.sum(0)<len(y))).all()))
        check('no_own_or_private_features',not any(n.startswith(('own_','target_','action_','declared_')) for n in names))
        prior=y.mean(0);fits={};fitted_train_pred={}
        for family in ['TIME_PRICE','PUBLIC_FULL']:
            base=TIME_PRICE if family=='TIME_PRICE' else names
            proposed=[names.index(n) for n in base]+[len(names)+names.index(n) for n in base]
            kept=[j for j in proposed if float(np.ptp(z[:,j]))>1e-12]
            heads={}
            for k,h in enumerate(HEADS):
                heads[h]=core.fit(z[:,kept],y[:,k]);r['research_logistic_fits']+=1
            fits[family]=dict(feature_indices=kept,feature_names=[vectors[j] for j in kept],
                dropped_train_constant_features=[vectors[j] for j in proposed if j not in kept],heads=heads)
            fitted_train_pred[family]=np.column_stack([core.predict(heads[h],z[:,kept]) for h in HEADS])
            print(json.dumps(dict(stage='fit_complete',family=family,heads=4,retained_features=len(kept),
                max_newton_steps=max(h['newton_steps'] for h in heads.values()))),flush=True)
            save()
        model=dict(version='MINIMAL_STUDENT_OBSERVED_MARK_AUXILIARY_V1',execution_authority='NONE',
            use_scope='EVENT_CONDITIONED_MARK_PROBABILITIES_NOT_ORDER_ACTIONS',
            source_manifest_sha256=MANIFEST_SHA,train_market_ids=[2022527,2022538],train_row_count=332,
            check_rows_loaded_before_freeze=False,heads=HEADS,
            excluded_constant_ask_heads=a['target_observation_pretraining']['excluded_constant_heads'],
            train_prior=dict(zip(HEADS,map(float,prior))),feature_stats=stats,
            models=fits,learner='MEAN_BCE_L2_LOGISTIC_NEWTON',regularization=.1,
            numpy_version=np.__version__,python_version=sys.version,
            source_core_sha256=sha(BUNDLE/'minimal_student_mark_logistic_v1.py'),
            exact_original_quantity_teacher=False,OUR_expert_action_teacher=False)
        freeze=out/'FROZEN_AUXILIARY_MODELS.json';check('no_model_overwrite',not freeze.exists())
        freeze.write_text(json.dumps(model,indent=2,allow_nan=False),encoding='utf-8')
        frozen_hash=sha(freeze);r['frozen_model_sha256']=frozen_hash
        check('all_eight_research_fits_complete_before_check',r['research_logistic_fits']==8)
        loaded=json.loads(freeze.read_text(encoding='utf-8'))
        for family in fits:
            f=loaded['models'][family]
            pp=np.column_stack([core.predict(f['heads'][h],z[:,f['feature_indices']]) for h in HEADS])
            check('freeze_reload:'+family,float(np.max(np.abs(pp-fitted_train_pred[family])))<1e-12)
        print(json.dumps(dict(stage='all_models_frozen_before_check',sha256=frozen_hash)),flush=True)
        # Only now load the consumed pipeline-check rows. No subsequent fit allowed.
        test=list(reader.iter_task(DATA,'observed_fill_marks','PIPELINE_CHECK'))
        check('check_rows_and_market',len(test)==152 and set(x['market_id'] for x in test)=={2022602})
        check('no_row_overlap',set(t['row_id'] for t in train).isdisjoint(t['row_id'] for t in test))
        yt=np.asarray([[t['y'][j] for j in ix] for t in test],dtype=float)
        zt=np.asarray([reader.vectorize(t['x'],stats) for t in test],dtype=float)
        check('finite_check_vectors',bool(np.isfinite(zt).all()))
        all_rows=train+test;all_y=np.vstack([y,yt]);all_z=np.vstack([z,zt])
        prediction={'PRIOR':np.tile(prior,(len(all_rows),1))}
        for family,f in loaded['models'].items():
            prediction[family]=np.column_stack([core.predict(f['heads'][h],all_z[:,f['feature_indices']]) for h in HEADS])
        metric_keys=['log_loss','brier','roc_auc','average_precision']
        scopes={'TRAIN':np.arange(len(train)),'PIPELINE_CHECK':np.arange(len(train),len(all_rows))}
        for mid in [2022527,2022538,2022602]:scopes['MARKET_'+str(mid)]=np.array([i for i,x in enumerate(all_rows) if x['market_id']==mid])
        scores={}
        for scope,ii in scopes.items():
            tab={}
            for family,p in prediction.items():
                heads={h:core.metrics(all_y[ii,k],p[ii,k]) for k,h in enumerate(HEADS)}
                macro={key:float(np.mean([v[key] for v in heads.values() if v[key] is not None]))
                       if any(v[key] is not None for v in heads.values()) else None for key in metric_keys}
                tab[family]=dict(heads=heads,macro=macro)
            scores[scope]=tab
        def compare(baseline,candidate,scope='PIPELINE_CHECK'):
            t=scores[scope]
            return dict(baseline=baseline,candidate=candidate,
                macro_log_loss_improvement=t[baseline]['macro']['log_loss']-t[candidate]['macro']['log_loss'],
                macro_brier_improvement=t[baseline]['macro']['brier']-t[candidate]['macro']['brier'],
                per_head={h:dict(log_loss_improvement=t[baseline]['heads'][h]['log_loss']-t[candidate]['heads'][h]['log_loss'],
                    brier_improvement=t[baseline]['heads'][h]['brier']-t[candidate]['heads'][h]['brier']) for h in HEADS})
        comparisons=[compare('PRIOR','TIME_PRICE'),compare('PRIOR','PUBLIC_FULL'),compare('TIME_PRICE','PUBLIC_FULL')]
        cmp=comparisons[-1]
        cmp['heads_log_loss_better']=sum(x['log_loss_improvement']>1e-12 for x in cmp['per_head'].values())
        cmp['heads_log_loss_worse']=sum(x['log_loss_improvement']< -1e-12 for x in cmp['per_head'].values())
        # Export event-level probabilities for independent metric audit, not policy decisions.
        predictions=out/'PREDICTIONS.jsonl.gz'
        with gzip.open(predictions,'wt',encoding='utf-8',newline='\n') as f:
            for i,row in enumerate(all_rows):
                one=dict(row_id=row['row_id'],market_id=row['market_id'],partition=row['partition'],
                    heads=HEADS,labels=all_y[i].astype(int).tolist(),
                    probabilities={name:p[i].tolist() for name,p in prediction.items()})
                f.write(json.dumps(one,separators=(',',':'),allow_nan=False)+'\n')
        diag={}
        for name,f in fits.items():
            idx=f['feature_indices'];v=zt[:,idx]
            diag[name]=dict(retained_features=len(idx),check_max_abs_train_standardized=float(np.max(np.abs(v))),
                features_with_check_abs_gt10=[vectors[idx[j]] for j in range(len(idx)) if float(np.max(np.abs(v[:,j])))>10.],
                macro_train_log_loss=scores['TRAIN'][name]['macro']['log_loss'],
                macro_check_log_loss=scores['PIPELINE_CHECK'][name]['macro']['log_loss'])
        check('frozen_weights_unchanged_after_score',sha(freeze)==frozen_hash)
        check('no_post_check_fit',r['research_logistic_fits']==8)
        check('no_nonpermitted_modules',not any(n.startswith(('hftbacktest','torch')) for n in sys.modules))
        for file in ['minimal_student_mark_logistic_v1.py','PREREG.md']:
            shutil.copy2(BUNDLE/file,out/file)
        (out/'METRICS.json').write_text(json.dumps(dict(scores=scores,comparisons=comparisons,diagnostics=diag),indent=2),encoding='utf-8')
        r.update(verdict='AUXILIARY_PREFIT_COMPLETED_NOT_POLICY_PROMOTION',
            train_rows=len(train),pipeline_check_rows=len(test),heads=HEADS,
            pipeline_scores=scores['PIPELINE_CHECK'],comparisons=comparisons,diagnostics=diag,
            train_scores=scores['TRAIN'],model_freeze_before_check=True,
            checks_passed=len(r['checks']),numpy_version=np.__version__,
            incremental_public_result=('POSITIVE_LOSS_DIFFERENCE_ON_CONSUMED_CHECK_ONLY' if cmp['macro_log_loss_improvement']>0
                                       else 'NO_INCREMENTAL_PUBLIC_LOSS_BENEFIT_ON_CONSUMED_CHECK'),
            next_scope='Do not iterate this auxiliary classifier indefinitely; no action authority or original-order teacher created.',
            limitations=['Third market already consumed; this is not generalization or profitability evidence.',
                'Observed event-conditioned fills do not identify placement, HOLD, sizing, repair, expansion or timing.',
                'No fit on OUR sparse transitions or teacher mapping to OUR state; controller behavior unchanged.',
                'Old-era BTC observations unchanged; no recent same-size calibration or simulated quantity relabeling.',
                'Scores are conditional on being in Target fill-event sample; not a deployable action-frequency model.',
                'Only a linear prespecified learner was tested; negative results do not disprove all learning methods.'])
        r['artifact_hashes']={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name not in ('COMPACT.json','result.json')}
    except Exception as exc:
        r.update(verdict='AUXILIARY_PREFIT_ERROR_STOPPED',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(limit=10))
    save()
    print(json.dumps({k:v for k,v in r.items() if k in ('verdict','research_logistic_fits','incremental_public_result','elapsed_seconds','error')}),flush=True)
    if r['verdict']=='AUXILIARY_PREFIT_ERROR_STOPPED':raise SystemExit(2)


if __name__=='__main__':main()
