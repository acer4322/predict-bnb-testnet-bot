"""No-fit audit: does event-memory evidence survive first-seen parent fills?"""
from __future__ import annotations
import collections
import hashlib
import json
import math
from pathlib import Path
from audit_btc5m_target_core_loop_topology_v1 import ROOT,R,RET,MIDS,read,sha

STEM='BTC5M_TARGET_MEMORY_PARENT_CONTROLS_V1_20260912'


def main():
    dataset=read(ROOT/'.lan_worker_v1/target_next_event_progress_20260912_v1/dataset.json')
    by_anchor={};sources=[]
    for mid in MIDS:
        bundle='open_funding_recovery_train_20260911_v3' if mid in MIDS[:3] else 'v20_consumed_btc5_transfer5_20260912_v1'
        p=ROOT/'.lan_worker_v1'/bundle/f'input_{mid}.json.gz';source=read(p);sources.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
        by=collections.defaultdict(list)
        for a in source['targetActions']:
            assert a['order_hash']
            by[a['event_ms']].append((a['side'],a['role'],a['order_hash']))
        seen=set();ticks=sorted(by)
        for i,t in enumerate(ticks[:-1]):
            seen.update(by[t]);next_keys=set(by[ticks[i+1]])
            overlap=next_keys&seen
            label='ALL_FIRST_SEEN' if not overlap else 'ALL_PREVIOUSLY_SEEN' if overlap==next_keys else 'MIXED'
            by_anchor[(mid,t+999)]=dict(next_parent_relation=label,next_parent_count=len(next_keys),
                previously_seen_parent_count=len(overlap),past_observed_parent_count=len(seen),
                next_fill_bucket=ticks[i+1])
    annotations=[]
    for row in dataset['rows']:
        a=by_anchor[(row['market'],row['anchor_ms'])];assert a['next_fill_bucket']==row['next_fill_bucket']
        annotations.append(dict(market=row['market'],anchor=row['anchor_ms'],**a))
    groups={k:sum(a['next_parent_relation']==k for a in annotations) for k in ('ALL_FIRST_SEEN','ALL_PREVIOUSLY_SEEN','MIXED')}
    def metric(values):
        if not values:return None
        return dict(n=len(values),positive=sum(y for y,p in values),
            log_loss=sum(-y*math.log(p)-(1-y)*math.log(1-p) for y,p in values)/len(values),
            brier=sum((y-p)**2 for y,p in values)/len(values))
    job=RET/'target-event-memory-20260912-v1';result=read(job/'result.json');pred=read(job/'predictions.json')
    assert sha(job/'predictions.json')==result['predictions_sha256']
    buckets=collections.defaultdict(list)
    for p in pred:
        relation=by_anchor[(p['market'],p['anchor'])]['next_parent_relation']
        buckets[(p['label'],p['fold'],relation,p['model'])].append((p['y'],p['p']))
    rows=[]
    for label,fold,relation,model in buckets:
        if model!='INTERCEPT':continue
        scores={m:metric(buckets[(label,fold,relation,m)]) for m in ('INTERCEPT','PHASE','EVENT1','PHASE_EVENT1','EVENT2')}
        rows.append(dict(label=label,fold=fold,next_parent_relation=relation,models=scores,
            gains={c+'_vs_'+b:scores[b]['log_loss']-scores[c]['log_loss'] for c,b in [('EVENT1','INTERCEPT'),('PHASE_EVENT1','PHASE'),('EVENT2','EVENT1')]}))
    out=dict(version=STEM,status='COMPLETE',analysis='Post-fit parent-overlap sensitivity; no refit, threshold search or new native execution.',
        groups=groups,annotations=annotations,scored_groups=rows,
        provenance=sources+[dict(path=str(p.relative_to(ROOT)),sha256=sha(p)) for p in (job/'result.json',job/'predictions.json',Path(__file__).resolve())],
        limitations=['First-seen means first observed confirmed fill of an order hash, not new submission or acceptance.',
            'Previously unseen parents may already have been resting; cannot infer pending inventory or private trigger timing.',
            'Subgroup analysis is exploratory and conditional on the next observed fill, not a preregistered promotion gate.',
            'Cross-parent persistence does not prove an independent directional thesis or distinguish shared execution plan from economic manager memory.'])
    (R/(STEM+'.json')).write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(dict(groups=groups,forward=[r for r in rows if r['fold'].startswith('FORWARD')],
        first_seen_lomo={label:{k:sum(r['gains'][k]>0 for r in rows if r['label']==label and r['fold'].startswith('LOMO') and r['next_parent_relation']=='ALL_FIRST_SEEN') for k in ['EVENT1_vs_INTERCEPT','PHASE_EVENT1_vs_PHASE','EVENT2_vs_EVENT1']} for label in ['next_weak_payment','next_active_payment']}),indent=2))


if __name__=='__main__':main()
