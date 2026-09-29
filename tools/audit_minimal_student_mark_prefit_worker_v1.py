"""Independent standard-library metric/inference audit; second worker, zero fit."""
from pathlib import Path
import gzip
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
import traceback

JOB=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-mark-prefit-20260910-v1')
DATA=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-training-data-smoke3-20260910-v1')
MODEL_SHA='341272f0a5a0590bdb9153a649df3903b5e86f9e2933b1166a1cfb35ee713763'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def independent_metrics(rows,probs):
    n=len(rows);pos=sum(rows);neg=n-pos
    pp=[min(1.-1e-12,max(1e-12,p)) for p in probs]
    loss=-math.fsum(y*math.log(p)+(1-y)*math.log1p(-p) for y,p in zip(rows,pp))/n
    brier=math.fsum((p-y)**2 for y,p in zip(rows,probs))/n
    rank_sum=0.;sorted_rows=sorted(zip(probs,rows));i=0
    while i<n:
        j=i+1
        while j<n and sorted_rows[j][0]==sorted_rows[i][0]:j+=1
        rank_sum+=((i+1+j)/2)*sum(y for _,y in sorted_rows[i:j]);i=j
    auc=(rank_sum-pos*(pos+1)/2)/(pos*neg) if pos and neg else None
    ap=None
    if pos:
        ordered=sorted(zip(probs,rows),reverse=True);i=0;tp=0;terms=[]
        while i<n:
            j=i+1
            while j<n and ordered[j][0]==ordered[i][0]:j+=1
            group_pos=sum(y for _,y in ordered[i:j]);tp+=group_pos
            terms.append((group_pos/pos)*(tp/j));i=j
        ap=math.fsum(terms)
    return dict(log_loss=loss,brier=brier,roc_auc=auc,average_precision=ap)


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('supervisor',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        m.bounded('minimal-student-mark-audit',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(version='MINIMAL_STUDENT_MARK_PREFIT_INDEPENDENT_AUDIT_V1',model_fits=0,HFT=0,worker_only=True)
    try:
        report=json.loads((JOB/'COMPACT.json').read_text(encoding='utf-8'))
        assert report['verdict']=='AUXILIARY_PREFIT_COMPLETED_NOT_POLICY_PROMOTION'
        immutable=['FROZEN_AUXILIARY_MODELS.json','METRICS.json','minimal_student_mark_logistic_v1.py','PREDICTIONS.jsonl.gz','PREREG.md']
        for f in immutable:
            p=JOB/f;assert p.stat().st_size<100000 and sha(p)==report['artifact_hashes'][f],f
        assert sha(JOB/'FROZEN_AUXILIARY_MODELS.json')==MODEL_SHA
        model=json.loads((JOB/'FROZEN_AUXILIARY_MODELS.json').read_text(encoding='utf-8'))
        scores=json.loads((JOB/'METRICS.json').read_text(encoding='utf-8'))['scores']
        rows=[]
        with gzip.open(JOB/'PREDICTIONS.jsonl.gz','rt',encoding='utf-8') as f:
            total=0
            for line in f:
                total+=len(line);assert total<1024**2;rows.append(json.loads(line))
        assert len(rows)==484 and len({r['row_id'] for r in rows})==484
        header=model['heads'];assert len(header)==4 and all(h.endswith('_BID') for h in header)
        assert [r['partition'] for r in rows].count('TRAIN')==332
        maxdiff=0.;checks=0
        for scope,tab in scores.items():
            selected=[r for r in rows if r['partition']==scope] if not scope.startswith('MARKET_') else [r for r in rows if r['market_id']==int(scope.split('_')[1])]
            assert selected
            for family,expected in tab.items():
                actuals=[]
                for k,h in enumerate(header):
                    actual=independent_metrics([r['labels'][k] for r in selected],[r['probabilities'][family][k] for r in selected]);actuals.append(actual)
                    for metric,value in actual.items():
                        want=expected['heads'][h][metric]
                        if value is None:assert want is None
                        else:diff=abs(value-want);assert diff<2e-12;maxdiff=max(maxdiff,diff)
                        checks+=1
                for metric,want in expected['macro'].items():
                    vals=[x[metric] for x in actuals if x[metric] is not None]
                    actual=math.fsum(vals)/len(vals) if vals else None
                    if actual is None:assert want is None
                    else:diff=abs(actual-want);assert diff<2e-12;maxdiff=max(maxdiff,diff)
                    checks+=1
        # Plain-Python reload inference uses the *accepted* rows, never future/audit fields.
        spec=importlib.util.spec_from_file_location('reader',DATA/'minimal_student_dataset_reader_v1.py')
        reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
        table={r['row_id']:r for r in rows};inference_diff=0.;predictions_checked=0;label_checks=0
        for split in ['TRAIN','PIPELINE_CHECK']:
            for item in reader.iter_task(DATA,'observed_fill_marks',split):
                saved=table[item['row_id']];vector=reader.vectorize(item['x'],model['feature_stats'])
                y=[item['y'][reader.MARKS.index(h)] for h in header]
                assert saved['labels']==y;label_checks+=1
                for family,m in model['models'].items():
                    for k,h in enumerate(header):
                        fit=m['heads'][h];z=fit['intercept']+math.fsum(fit['coefficients'][j]*vector[idx] for j,idx in enumerate(m['feature_indices']))
                        ex=math.exp(-abs(z));p=1/(1+ex) if z>=0 else ex/(1+ex)
                        diff=abs(p-saved['probabilities'][family][k]);assert diff<2e-12
                        inference_diff=max(inference_diff,diff);predictions_checked+=1
                for k,h in enumerate(header):assert saved['probabilities']['PRIOR'][k]==model['train_prior'][h]
        train=[r for r in rows if r['partition']=='TRAIN']
        for k,h in enumerate(header):assert abs(sum(r['labels'][k] for r in train)/len(train)-model['train_prior'][h])<1e-15
        assert sha(JOB/'FROZEN_AUXILIARY_MODELS.json')==MODEL_SHA
        result.update(verdict='INDEPENDENT_METRICS_AND_RELOADED_INFERENCE_PASS',rows=484,
            metric_values_checked=checks,metric_max_abs_difference=maxdiff,
            reloaded_model_probabilities_checked=predictions_checked,inference_max_abs_difference=inference_diff,
            label_rows_matched_to_accepted_dataset=label_checks,
            frozen_model_sha256=MODEL_SHA,immutable_artifacts_verified=immutable,
            final_source_result_sha256=sha(JOB/'COMPACT.json'),
            source_mutable_log_hashes_not_used=True,
            mutable_log_note='Worker stdout/stderr were still open when source artifact hashes were enumerated; only frozen model/metrics/predictions/code/prereg hashes are evidence. This audit pins finalized source COMPACT.',
            no_refit=True,no_metric_driven_selection=True)
    except Exception as exc:
        result.update(verdict='INDEPENDENT_AUDIT_ERROR',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(limit=8))
    assert not any(n.startswith(('numpy','sklearn','torch','hftbacktest')) for n in sys.modules)
    result['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if result['verdict']=='INDEPENDENT_AUDIT_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
