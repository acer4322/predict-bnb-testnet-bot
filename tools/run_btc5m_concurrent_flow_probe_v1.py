"""Worker-only fixed joint observed-flow table probe, without trading authority."""
import argparse,collections,hashlib,json,math,os,socket,statistics,time
from pathlib import Path

CLASSES=('WEAK_ONLY','STRONG_ONLY','BOTH')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def score(items):
    n=len(items)
    if not n:return dict(n=0)
    logloss=brier=0.;marginal=dict(weak=0.,strong=0.,both=0.)
    for row,p in items:
        yi=CLASSES.index(row['joint_label']);logloss-=math.log(p[yi]);brier+=sum((int(i==yi)-v)**2 for i,v in enumerate(p))
        for k,v in dict(weak=p[0]+p[2],strong=p[1]+p[2],both=p[2]).items():marginal[k]+=(row['side_labels'][k]-v)**2
    return dict(n=n,joint_log_loss=logloss/n,joint_brier=brier/n,marginal_brier={k:v/n for k,v in marginal.items()})


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');a=ap.parse_args()
    root=Path(__file__).resolve().parent;m=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for name,h in m['files'].items():assert sha(root/name)==h
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF'
    rows=json.loads((root/'dataset.json').read_text(encoding='utf-8'))['rows']
    assert len(rows)==1097 and all(r['book_received_ms']<r['anchor_ms']<r['next_fill_bucket'] for r in rows)
    if a.check_only:print(json.dumps(dict(status='PASS',worker=socket.gethostname(),rows=len(rows),fit=False,native=False)));return
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    outdir=Path(os.environ['BTC5M_LAN_RESULT_DIR']);outdir.mkdir(parents=True,exist_ok=True)
    assert not(outdir/'result.json').exists();started=time.monotonic();mids=sorted({r['market'] for r in rows})
    folds=[dict(name='LOMO_'+str(mid),train=[x for x in mids if x!=mid],test=[mid]) for mid in mids]
    folds.append(dict(name='FORWARD_TRAIN2',train=[2022527,2022538],test=[x for x in mids if x not in (2022527,2022538)]))
    expected={(r['fold'],r['market'],r['anchor']):r for r in json.loads((root/'legacy_expected.json').read_text(encoding='utf-8'))}
    keys=dict(INTERCEPT=lambda r:(),LEGACY_EVENT2=lambda r:(r['legacy_prior'],r['legacy_token']),
        SIDE_EVENT1=lambda r:(r['side_token'],),SIDE_EVENT2=lambda r:(r['side_prior'],r['side_token']),
        SIDE_CASH_EVENT2=lambda r:(r['cash_prior'],r['cash_token']))
    results=[];predictions=[];parity_n=0;maxerr=0.
    for fold in folds:
        train=[r for r in rows if r['market'] in fold['train']];test=[r for r in rows if r['market'] in fold['test']]
        assert set(fold['train']).isdisjoint(fold['test'])
        if fold['name'].startswith('FORWARD'):assert max(r['next_fill_bucket'] for r in train)<min(r['anchor_ms'] for r in test)
        # Reproduce only the saved old baseline; this is a parity gate, not a resubmitted experiment.
        binary=collections.defaultdict(lambda:[0,0])
        for r in train:binary[keys['LEGACY_EVENT2'](r)][r['labels']['next_weak_payment']]+=1
        globalp=(sum(r['labels']['next_weak_payment'] for r in train)+1)/(len(train)+2)
        for r in test:
            c=binary.get(keys['LEGACY_EVENT2'](r));p=(c[1]+1)/(sum(c)+2) if c else globalp
            old=expected[(fold['name'],r['market'],r['anchor_ms'])]
            assert old['y']==r['labels']['next_weak_payment'];err=abs(old['p']-p);maxerr=max(maxerr,err);assert err<1e-15;parity_n+=1
        entry=dict(fold=fold,train_rows=len(train),test_rows=len(test),models={})
        for name,key in keys.items():
            counts=collections.defaultdict(lambda:[0,0,0]);total=[0,0,0]
            for r in train:i=CLASSES.index(r['joint_label']);counts[key(r)][i]+=1;total[i]+=1
            pairs=[];by=collections.defaultdict(list)
            for r in test:
                c=counts.get(key(r),total);p=[(v+1)/(sum(c)+3) for v in c];assert abs(sum(p)-1)<1e-12
                pairs.append((r,p));by[r['market']].append((r,p))
                predictions.append(dict(fold=fold['name'],model=name,market=r['market'],anchor=r['anchor_ms'],label=r['joint_label'],p=p))
            entry['models'][name]=dict(score=score(pairs),per_market={str(mid):score(ps) for mid,ps in by.items()},
                first_observed_parents=score([(r,p) for r,p in pairs if r['next_all_parents_first_observed_here']]),
                no_surplus_cross=score([(r,p) for r,p in pairs if not r['next_surplus_crossing']]),
                contexts=len(counts),unseen_contexts=sum(key(r) not in counts for r in test))
        results.append(entry)
    assert parity_n==len(expected)
    forward=results[-1];comparisons={}
    for candidate,baseline in m['comparisons']:
        gains=[r['models'][baseline]['score']['joint_log_loss']-r['models'][candidate]['score']['joint_log_loss'] for r in results[:-1]]
        c=forward['models'][candidate];b=forward['models'][baseline]
        fg={mid:b['per_market'][mid]['joint_log_loss']-value['joint_log_loss'] for mid,value in c['per_market'].items()}
        ok=sum(g>0 for g in gains)>=6 and statistics.median(gains)>0 and statistics.median(fg.values())>0
        ok=ok and c['score']['joint_log_loss']<b['score']['joint_log_loss'] and all(c['score']['marginal_brier'][k]<=b['score']['marginal_brier'][k] for k in ('weak','strong','both'))
        comparisons[candidate+'_vs_'+baseline]=dict(lomo_gains=gains,lomo_wins=sum(g>0 for g in gains),forward_market_gains=fg,
            verdict='SUPPORTED_INFORMATION_CLUE' if ok else 'NOT_SUPPORTED_UNDER_FIXED_PROBE')
    (outdir/'predictions.json').write_text(json.dumps(predictions,indent=2)+'\n',encoding='utf-8')
    result=dict(version=m['version'],status='COMPLETE',worker=socket.gethostname(),native=False,runtime_eligible=False,
        manifest_sha256=sha(root/'manifest.json'),legacy_parity=dict(n=parity_n,max_absolute_error=maxerr),
        results=results,comparisons=comparisons,elapsed_seconds=time.monotonic()-started,
        predictions_sha256=sha(outdir/'predictions.json'),limits=m['limits'])
    (outdir/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='COMPLETE',legacy_parity=result['legacy_parity'],comparisons=comparisons,elapsed_seconds=result['elapsed_seconds'])))


if __name__=='__main__':main()
