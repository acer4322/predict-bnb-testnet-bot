"""Worker-only fixed empirical event-memory comparison; no strategy tuning."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import statistics
import time


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');a=ap.parse_args()
    root=Path(__file__).resolve().parent;manifest=json.loads((root/'manifest.json').read_text())
    for f,h in manifest['files'].items():assert sha(root/f)==h
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF'
    rows=json.loads((root/'dataset.json').read_text())['rows'];previous={}
    for r in rows:
        f=r['features'];r['token']=('P' if f['last_had_payment'] else '')+('B' if f['last_had_birth'] else '') or 'D'
        prev=previous.get(r['market']);r['prior_token']=prev['token'] if prev and prev['next_fill_bucket']==r['last_fill_bucket'] else 'UNKNOWN'
        r['phase_bin']=min(2,int(f['phase']*3));previous[r['market']]=r
    if a.check_only:print(json.dumps(dict(check_pass=True,rows=len(rows),worker=socket.gethostname())));return
    start=time.monotonic();outdir=Path(os.environ['BTC5M_LAN_RESULT_DIR']);outdir.mkdir(parents=True,exist_ok=True)
    assert not(outdir/'result.json').exists()
    mids=sorted({r['market'] for r in rows})
    folds=[dict(name='LOMO_'+str(m),train=[x for x in mids if x!=m],test=[m]) for m in mids]
    folds.append(dict(name='FORWARD_TRAIN2',train=[2022527,2022538],test=[m for m in mids if m not in (2022527,2022538)]))
    keys={'INTERCEPT':lambda r:(), 'PHASE':lambda r:(r['phase_bin'],),
        'EVENT1':lambda r:(r['token'],), 'PHASE_EVENT1':lambda r:(r['phase_bin'],r['token']),
        'EVENT2':lambda r:(r['prior_token'],r['token'])}
    results=[];predrows=[]
    def score(items):
        n=len(items);assert n
        return dict(n=n,log_loss=sum(-y*math.log(p)-(1-y)*math.log(1-p) for y,p in items)/n,
                    brier=sum((y-p)**2 for y,p in items)/n)
    for label in ('next_weak_payment','next_active_payment'):
        for fold in folds:
            train=[r for r in rows if r['market'] in fold['train'] and r['labels'][label] is not None]
            test=[r for r in rows if r['market'] in fold['test'] and r['labels'][label] is not None]
            assert set(fold['train']).isdisjoint(fold['test'])
            if fold['name'].startswith('FORWARD'):assert max(r['next_fill_bucket'] for r in train)<min(r['anchor_ms'] for r in test)
            result=dict(label=label,fold=fold,models={},tables={})
            for name,key in keys.items():
                counts=collections.defaultdict(lambda:[0,0])
                for r in train:counts[key(r)][r['labels'][label]]+=1
                global_rate=(sum(r['labels'][label] for r in train)+1)/(len(train)+2)
                test_scores=[];by=collections.defaultdict(list)
                for r in test:
                    c=counts.get(key(r));p=(c[1]+1)/(sum(c)+2) if c else global_rate
                    y=r['labels'][label];test_scores.append((y,p));by[r['market']].append((y,p))
                    predrows.append(dict(model=name,fold=fold['name'],label=label,market=r['market'],anchor=r['anchor_ms'],y=y,p=p))
                result['models'][name]=score(test_scores)|dict(per_market={str(m):score(v) for m,v in by.items()})
                result['tables'][name]={str(k):dict(no=c[0],yes=c[1],p=(c[1]+1)/(sum(c)+2)) for k,c in counts.items()}
            results.append(result)
    summaries={}
    for label in ('next_weak_payment','next_active_payment'):
        local=[r for r in results if r['label']==label and r['fold']['name'].startswith('LOMO')]
        forward=next(r for r in results if r['label']==label and r['fold']['name'].startswith('FORWARD'))
        comparisons={}
        for candidate,base in [('EVENT1','INTERCEPT'),('PHASE_EVENT1','PHASE'),('EVENT2','EVENT1')]:
            gain=[r['models'][base]['log_loss']-r['models'][candidate]['log_loss'] for r in local]
            fw={m:forward['models'][base]['per_market'][m]['log_loss']-v['log_loss'] for m,v in forward['models'][candidate]['per_market'].items()}
            ok=sum(x>0 for x in gain)>=6 and statistics.median(gain)>0 and statistics.median(fw.values())>0
            ok=ok and forward['models'][candidate]['log_loss']<forward['models'][base]['log_loss'] and forward['models'][candidate]['brier']<=forward['models'][base]['brier']
            comparisons[candidate+'_vs_'+base]=dict(lomo_gains=gain,lomo_wins=sum(x>0 for x in gain),forward_gains=fw,
                gate='SUPPORTED_INFORMATION_CLUE' if ok else 'NOT_SUPPORTED_UNDER_FIXED_PROBE')
        summaries[label]=comparisons
    out=dict(version='BTC5M_TARGET_EVENT_MEMORY_V1',status='COMPLETE',worker=socket.gethostname(),
        manifest_sha256=sha(root/'manifest.json'),results=results,summaries=summaries,elapsed_seconds=time.monotonic()-start,
        native=False,runtime_promotion=False,causal_claim=False,
        limitations=['Conditional next observed fill; latent orders and decisions unknown.',
            'Phase thirds are offline baseline contexts, not runtime time gates.',
            'Repeated fills of one physical order may generate event persistence; must separate that before interpreting economic controller memory.',
            'All markets consumed. Table values are not proposed financial/strategy parameters.'])
    (outdir/'predictions.json').write_text(json.dumps(predrows,indent=2)+'\n');out['predictions_sha256']=sha(outdir/'predictions.json')
    (outdir/'result.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(dict(status='COMPLETE',summaries=summaries,elapsed=out['elapsed_seconds'])))


if __name__=='__main__':main()
