"""Worker-only fixed regression probe for next observed cash allocation and scale."""
import argparse
import collections
import hashlib
import json
import math
import os
import socket
import statistics
import time
from pathlib import Path

LABELS = ('weak_cash_fraction','log_total_cash')
TOKENS = ('UNKNOWN','WEAK_ONLY','STRONG_ONLY','BOTH')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def score(rows, pred):
    if not rows: return {'n':0}
    return dict(n=len(rows), targets={key:dict(
        mse=math.fsum((r['amount_labels'][key]-float(p[i]))**2 for r,p in zip(rows,pred))/len(rows),
        mae=math.fsum(abs(r['amount_labels'][key]-float(p[i])) for r,p in zip(rows,pred))/len(rows))
        for i,key in enumerate(LABELS)})


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--check-only',action='store_true'); args=ap.parse_args()
    root=Path(__file__).resolve().parent; m=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(root/n)==h for n,h in m['files'].items())
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF'
    import numpy as np
    import sklearn
    from sklearn.ensemble import HistGradientBoostingRegressor
    data=json.loads((root/'dataset.json').read_text(encoding='utf-8')); rows=data['rows']
    assert len(rows)==1097 and all(r['book_received_ms']<r['anchor_ms']<r['next_fill_bucket'] for r in rows)
    if args.check_only:
        print(json.dumps(dict(status='PASS', rows=len(rows), worker=socket.gethostname(), sklearn=sklearn.__version__, fit=False)));return
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']); out.mkdir(parents=True,exist_ok=True);assert not(out/'result.json').exists()
    started=time.monotonic(); mids=sorted({r['market'] for r in rows})
    folds=[dict(name=f'LOMO_{mid}',train=[x for x in mids if x!=mid],test=[mid]) for mid in mids]
    folds.append(dict(name='FORWARD_TRAIN2',train=[2022527,2022538],test=[x for x in mids if x not in (2022527,2022538)]))
    def context(r): return (r['side_prior'],r['side_token'])
    def matrix(part,money):
        return np.asarray([[float(r['amount_state'][k]) for k in data['state_features']]
            +[float(v==token) for v in context(r) for token in TOKENS]
            +([float(r['amount_money'][k]) for k in data['money_features']] if money else []) for r in part])
    results=[]; predictions=[]
    for fold in folds:
        train=[r for r in rows if r['market'] in fold['train']]; test=[r for r in rows if r['market'] in fold['test']]
        assert set(fold['train']).isdisjoint(fold['test'])
        if fold['name'].startswith('FORWARD'):assert max(r['next_fill_bucket'] for r in train)<min(r['anchor_ms'] for r in test)
        y=np.asarray([[r['amount_labels'][k] for k in LABELS] for r in train]); average=y.mean(axis=0)
        table=collections.defaultdict(list)
        for r, value in zip(train,y):table[context(r)].append(value)
        outputs={'INTERCEPT':np.tile(average,(len(test),1)), 'EVENT2_MEAN':np.asarray([
            (np.asarray(table[context(r)]).sum(axis=0)+2*average)/(len(table[context(r)])+2) if context(r) in table else average for r in test])}
        for model,money in (('HISTORY_STATE',False),('HISTORY_MONEY',True)):
            x=matrix(train,money); xt=matrix(test,money); pred=[]
            for i,key in enumerate(LABELS):
                fit=HistGradientBoostingRegressor(**m['model_parameters']);fit.fit(x,y[:,i]);pred.append(fit.predict(xt))
            outputs[model]=np.column_stack(pred)
        entry=dict(fold=fold,train_rows=len(train),models={})
        for model,pred in outputs.items():
            pred[:,0]=np.clip(pred[:,0],0,1);pred[:,1]=np.maximum(pred[:,1],0)
            def subset(field,value):
                ids=[i for i,r in enumerate(test) if r[field]==value]
                return score([test[i] for i in ids],pred[ids])
            entry['models'][model]=dict(score=score(test,pred),per_market={str(mid):score(
                [r for r in test if r['market']==mid],pred[[i for i,r in enumerate(test) if r['market']==mid]]) for mid in fold['test']},
                both_only=subset('joint_label','BOTH'),first_observed_parents=subset('next_all_parents_first_observed_here',True),
                no_surplus_cross=subset('next_surplus_crossing',False))
            for r,p in zip(test,pred):predictions.append(dict(fold=fold['name'],model=model,market=r['market'],anchor=r['anchor_ms'],
                y=r['amount_labels'],p={k:float(p[i]) for i,k in enumerate(LABELS)}))
        results.append(entry)
    comparisons={};forward=results[-1]['models']
    for candidate,baseline in m['comparisons']:
        details={};passed=True
        for key in LABELS:
            gains=[r['models'][baseline]['score']['targets'][key]['mse']-r['models'][candidate]['score']['targets'][key]['mse'] for r in results[:-1]]
            fg={mid:forward[baseline]['per_market'][mid]['targets'][key]['mse']-value['targets'][key]['mse'] for mid,value in forward[candidate]['per_market'].items()}
            c,b=(forward[x]['score']['targets'][key] for x in (candidate,baseline))
            ok=sum(g>0 for g in gains)>=6 and statistics.median(gains)>0 and statistics.median(fg.values())>0 and c['mse']<b['mse'] and c['mae']<=b['mae']
            details[key]=dict(lomo_wins=sum(g>0 for g in gains),lomo_gains=gains,forward_gains=fg,gate=ok);passed &= ok
        c,b=(forward[x]['both_only']['targets']['weak_cash_fraction'] for x in (candidate,baseline))
        both_ok=c['mse']<=b['mse'] and c['mae']<=b['mae'];passed &= both_ok
        comparisons[candidate+'_vs_'+baseline]=dict(targets=details,both_only_fraction_gate=both_ok,
            verdict='SUPPORTED_AMOUNT_INFORMATION_CLUE' if passed else 'NOT_SUPPORTED_UNDER_FIXED_PROBE')
    (out/'predictions.json').write_text(json.dumps(predictions,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    result=dict(status='COMPLETE',version=m['version'],worker=socket.gethostname(),sklearn=sklearn.__version__,native=False,runtime_eligible=False,
        manifest_sha256=sha(root/'manifest.json'),elapsed_seconds=time.monotonic()-started,results=results,comparisons=comparisons,
        predictions_sha256=sha(out/'predictions.json'),limits=m['limits'])
    (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='COMPLETE',elapsed_seconds=result['elapsed_seconds'],comparisons=comparisons)))


if __name__=='__main__':main()
