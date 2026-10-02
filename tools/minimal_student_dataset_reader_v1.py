"""Streaming, task-scoped reader. Does NOT expose an expert-policy dataset.

Run with --dataset <worker-result-dir> --task observed_fill_marks --split TRAIN.
The CLI is a loader dry-run, not a model fit. All source samples are consumed QA.
"""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import math

TASKS = {'observed_fill_marks': 'target_batches', 'own_inventory_delta': 'own_transitions'}
MARKS = tuple(f'{r}_{s}_{q}' for r in ('MAKER','TAKER') for s in ('UP','DOWN') for q in ('BID','ASK'))


def iter_jsonl(path, limit=32*1024**2):
    total=0
    with gzip.open(path,'rb') as f:
        for line in f:
            total+=len(line)
            if total>limit or len(line)>512*1024:
                raise ValueError('bounded dataset stream exceeded')
            yield json.loads(line)


def sample(row, task):
    """Separate x/y; no automatic flattening of audit, after-state or Target ids."""
    if task not in TASKS:
        raise ValueError('Unsupported task: original Target sizing/policy/cancel/HOLD labels are unknown')
    mask='observed_fill_marks' if task=='observed_fill_marks' else 'own_inventory_delta'
    if not row['loss_masks'][mask]:
        return None
    x=dict(row['x'])
    if task=='observed_fill_marks':
        y=[int(row['labels']['marks'][k]['legs']>0) for k in MARKS]
    else:
        acts=row['recorded_action']['new_submits']
        # These are *taken* actions for a transition auxiliary task, not expert labels.
        for side in ('UP','DOWN'):
            accepted=[a for a in acts if a['ok'] and a['side']==side]
            x['action_'+side.lower()+'_qty']=sum(a['submittedQty'] for a in accepted)
            x['action_'+side.lower()+'_notional']=sum(a['submittedQty']*a['price'] for a in accepted)
        x['action_submit_count']=sum(bool(a['ok']) for a in acts)
        y=[row['labels']['delta_inventory']['UP'],row['labels']['delta_inventory']['DOWN'],row['labels']['delta_cost']]
    if any(v is not None and (isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v)) for v in x.values()):
        raise ValueError('numeric feature allowlist violation')
    return {'x':x,'y':y,'row_id':row['row_id'],'market_id':row['market_id'],'partition':row['partition']}


def iter_task(dataset, task, split='TRAIN', verify_hash=True):
    if task not in TASKS:
        raise ValueError('Unsupported task: no original-order or OUR-expert action labels')
    if split not in ('TRAIN','PIPELINE_CHECK'):
        raise ValueError('Only consumed TRAIN/PIPELINE_CHECK exist; no fresh holdout')
    root=Path(dataset).resolve()
    meta=json.loads((root/'DATASET_MANIFEST.json').read_text(encoding='utf-8'))
    for entry in meta['data_files']:
        if entry['lane']!=TASKS[task] or entry['partition']!=split:
            continue
        p=(root/entry['path']).resolve()
        if not p.is_relative_to(root):raise ValueError('path outside dataset')
        if verify_hash:
            h=hashlib.sha256()
            with p.open('rb') as f:
                for block in iter(lambda:f.read(262144),b''):h.update(block)
            if h.hexdigest()!=entry['sha256']:raise ValueError('dataset integrity mismatch')
        for row in iter_jsonl(p):
            if row['partition']!=split:raise ValueError('partition mismatch')
            item=sample(row,task)
            if item is not None:yield item


def vectorize(x, stats):
    """TRAIN-only z-scores; missing values remain explicit with a second mask vector."""
    names=stats['feature_names']
    if set(x)!=set(names):raise ValueError('feature schema drift')
    vals=[];missing=[]
    for name in names:
        v=x[name];s=stats['features'][name]
        if v is None:
            vals.append(0.);missing.append(1.)
        else:
            vals.append((float(v)-s['mean'])/s['scale']);missing.append(0.)
    return vals+missing


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True)
    ap.add_argument('--task',choices=tuple(TASKS),required=True)
    ap.add_argument('--split',choices=('TRAIN','PIPELINE_CHECK'),default='TRAIN')
    args=ap.parse_args();root=Path(args.dataset)
    stats=json.loads((root/'TRAIN_ONLY_FEATURE_STATS.json').read_text(encoding='utf-8'))[args.task]
    n=0;shape=None
    for item in iter_task(root,args.task,args.split):
        v=vectorize(item['x'],stats)
        if not all(math.isfinite(x) for x in v):raise ValueError('non-finite model vector')
        n+=1;shape=[len(v),len(item['y'])]
    print(json.dumps(dict(task=args.task,split=args.split,rows=n,shape=shape,modelFits=0)))


if __name__=='__main__':main()
