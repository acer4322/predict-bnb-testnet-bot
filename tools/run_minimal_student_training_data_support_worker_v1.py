"""Independent bounded reader audit and task-support counts; no model fitting."""
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

DATA=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-training-data-smoke3-20260910-v1')
MANIFEST_SHA='3ef1638c642481ce0c390797fdb8ae271b4b1e466d5d7d00c61dfa095fc9ffb0'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert os.environ.get('BTC5M_LAN_RESULT_DIR')
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    started=time.monotonic();out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    result=dict(version='MINIMAL_STUDENT_TRAINING_DATA_SUPPORT_V1',model_fits=0,HFT=0,
                base_dataset_manifest_sha256=MANIFEST_SHA,worker_only=True)
    try:
        assert sha(DATA/'DATASET_MANIFEST.json')==MANIFEST_SHA
        m=json.loads((DATA/'DATASET_MANIFEST.json').read_text(encoding='utf-8'))
        spec=importlib.util.spec_from_file_location('reader',DATA/'minimal_student_dataset_reader_v1.py')
        reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
        stats=json.loads((DATA/'TRAIN_ONLY_FEATURE_STATS.json').read_text(encoding='utf-8'))
        support={};totals=Counter();probe_tests=Counter()
        for task in reader.TASKS:
            table={}
            for split in ('TRAIN','PIPELINE_CHECK'):
                n=0;ys=[];ids=set();sum_y=None
                for item in reader.iter_task(DATA,task,split):
                    assert item['market_id'] not in ({2022602} if split=='TRAIN' else {2022527,2022538})
                    assert item['row_id'] not in ids;ids.add(item['row_id'])
                    y=item['y'];ys.append(y);n+=1
                if task=='observed_fill_marks':
                    positive={k:sum(y[i]>0 for y in ys) for i,k in enumerate(reader.MARKS)}
                    table[split]=dict(rows=n,positive_by_head=positive,
                        constant_heads=[k for k,v in positive.items() if v in (0,n)],
                        active_heads=[k for k,v in positive.items() if 0<v<n])
                else:
                    nonzero=sum(any(abs(z)>1e-9 for z in y) for y in ys)
                    table[split]=dict(rows=n,nonzero_inventory_or_cost_delta=nonzero,
                        zero_delta_rows=n-nonzero,zero_delta_fraction=(n-nonzero)/n,
                        mean_abs_delta=[sum(abs(y[i]) for y in ys)/n for i in range(3)])
            if task=='observed_fill_marks':
                table['training_head_allowlist']=table['TRAIN']['active_heads']
                table['constant_heads_are_not_evaluated_as_accuracy_success']=True
                table['head_selection_uses_pipeline_check']=False
            else:
                table['required_baseline']='Predict zero inventory/cost change; report nonzero-transition metrics separately.'
                table['row_accuracy_is_not_sufficient']=True
            support[task]=table
        audited=0
        for e in m['data_files']:
            p=DATA/e['path'];assert sha(p)==e['sha256']
            for row in reader.iter_jsonl(p):
                if row['lane']=='own_transitions':
                    assert all(row['own_before']['inv'][s]==row['own_after_decision']['inv'][s] for s in ('UP','DOWN'))
                    assert row['own_before']['cost']==row['own_after_decision']['cost']
                    original=reader.sample(row,'own_inventory_delta')
                    changed=deepcopy(row)
                    changed['labels']['next_own_state']['inv']['UP']+=100000.
                    changed['labels']['delta_inventory']['UP']+=99999.
                    changed['teacher_action']={'future':'should never enter x'}
                    changed['teacher_original_qty']=99999.
                    altered=reader.sample(changed,'own_inventory_delta')
                    assert altered['x']==original['x']
                    assert not row['loss_masks']['expert_policy'] and not row['loss_masks']['target_original_qty']
                    totals['own_rows_audited']+=1
                    totals['decision_with_rejections']+=bool(row['recorded_action']['rejection_counts'])
                    totals['decision_with_submit']+=bool(row['recorded_action']['new_submits'])
                    totals['blocked_before_role']+=bool(row['recorded_action']['blocked_before_role'])
                    totals['raw_rejection_candidates']+=sum(row['recorded_action']['rejection_counts'].values())
                elif row['lane']=='target_batches':
                    original=reader.sample(row,'observed_fill_marks')
                    changed=deepcopy(row)
                    changed['labels']['marks']['MAKER_UP_BID']['legs']+=999
                    changed['audit']['target_order_ids']=['FUTURE_LEAK_TEST']
                    altered=reader.sample(changed,'observed_fill_marks')
                    if original is not None:assert altered['x']==original['x']
                    assert row['labels']['original_requested_qty'] is None
                    assert not row['loss_masks']['original_order_qty'] and not row['loss_masks']['our_expert_action']
                    totals['target_batches_audited']+=1
                else:continue
                for key in ('public_available_ms','book_available_ms'):
                    v=row['feature_provenance'][key]
                    assert v is None or v<row['t']
                sample_task='own_inventory_delta' if row['lane']=='own_transitions' else 'observed_fill_marks'
                usable=reader.sample(row,sample_task)
                if usable is not None:
                    vec=reader.vectorize(usable['x'],stats[sample_task])
                    n=len(stats[sample_task]['feature_names']);assert len(vec)==2*n
                    assert all(vec[n+i]==float(usable['x'][name] is None) for i,name in enumerate(stats[sample_task]['feature_names']))
                audited+=1
        assert totals['own_rows_audited']==4458 and totals['target_batches_audited']==484
        assert totals['decision_with_submit']==63
        result.update(verdict='READER_INPUT_ISOLATION_AND_TASK_SUPPORT_PASS',task_support=support,
            totals=dict(totals),rows_independently_audited=audited,
            mutation_tests=dict(future_own_label_and_teacher_fields_do_not_change_x=True,
                target_labels_and_audit_ids_do_not_change_x=True,missingness_masks_roundtrip=True),
            train_only_head_allowlist=support['observed_fill_marks']['training_head_allowlist'],
            warnings=['No-policy-change is a common OWN outcome: do not count zero-only prediction as successful control.',
                'Constant absent observed-fill heads cannot inflate accuracy or count as learned skill.',
                'This is an event-conditioned auxiliary dataset, not original placement or full lifecycle imitation.',
                'No training run or validation score was generated.'])
    except Exception as exc:
        result.update(verdict='SUPPORT_AUDIT_ERROR',error=type(exc).__name__+': '+str(exc),
                      traceback=traceback.format_exc(limit=8))
    result['elapsed_seconds']=time.monotonic()-started
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if result['verdict']=='SUPPORT_AUDIT_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
